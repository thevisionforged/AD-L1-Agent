"""Major Ticket Tracker for the AD team: incidents, change requests and catalog tasks that need attention.

Selection rules (deterministic):
  * Incidents: P1/P2, VIP caller, wide impact, SLA breached or >= 75% used, or business approval stalled > 24 h.
  * Changes:   CAB approval pending, starting within 7 days, missing implementation/backout plan or test
               evidence, high risk, or a failed / backed-out outcome that needs a post-implementation review.
  * Tasks:     overdue, blocked, or unassigned.
Each row carries an owner, the blocker, the next action and when the next update is due (by priority/risk).
Publishing is approval-gated: the SharePoint list upsert and the Teams channel post are Approval Center items.
Both connectors are MOCKS shaped like Microsoft Graph (SharePoint list items, channel messages).
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .connectors import ServiceNowConnector

UPDATE_CADENCE_H = {"P1": 1, "P2": 2, "P3": 8, "P4": 24, "High": 4, "Moderate": 24, "Low": 48, "Task": 24}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


class SharePointConnector:
    """Mock of Graph `sites/{site}/lists/{list}/items`: upsert keyed by the ticket number."""

    def __init__(self, runtime_dir: Path, site: str, list_name: str):
        self.site, self.list_name = site, list_name
        self.path = runtime_dir / "sharepoint_tracker_list.json"
        self.items: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.items = {}

    def upsert(self, rows: list[dict[str, Any]], by: str) -> dict[str, Any]:
        created = updated = 0
        for r in rows:
            key = r["Number"]
            if key in self.items:
                updated += 1
            else:
                created += 1
            self.items[key] = {**r, "ModifiedBy": by, "Modified": _fmt(_now())}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=2), encoding="utf-8")
        return {"ok": True, "result": f"SharePoint list '{self.list_name}': {created} created, {updated} updated"}


class ChannelConnector:
    """Mock of Graph `teams/{team}/channels/{channel}/messages`."""

    def __init__(self, runtime_dir: Path, team: str, channel: str):
        self.team, self.channel = team, channel
        self.path = runtime_dir / "teams_channel_posts.jsonl"
        self.posts: list[dict[str, Any]] = []

    def post(self, text: str, by: str) -> dict[str, Any]:
        msg = {"at": _fmt(_now()), "team": self.team, "channel": self.channel, "approved_by": by, "text": text[:6000]}
        self.posts.append(msg)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")
        except OSError:
            pass
        return {"ok": True, "result": f"Posted to {self.team} > {self.channel}"}


class MajorTracker:
    def __init__(self, data_dir: Path, snow: ServiceNowConnector, out_dir: Path):
        raw = json.loads((data_dir / "servicenow_changes_tasks.json").read_text(encoding="utf-8"))
        self.snow, self.out_dir = snow, out_dir
        self.channel_cfg, self.sp_cfg = raw["teams_channel"], raw["sharepoint_list"]
        now = snow.now
        self.changes = []
        for c in raw["changes"]:
            c = dict(c)
            c["planned_start"] = now + timedelta(hours=c.pop("planned_start_in_hours"))
            h = c.pop("cab_requested_hours_ago")
            c["cab_pending_hours"] = h if c["cab_approval"] == "requested" else None
            self.changes.append(c)
        self.tasks = []
        for t in raw["tasks"]:
            t = dict(t)
            t["due"] = now + timedelta(hours=t.pop("due_in_hours"))
            self.tasks.append(t)

    def _eng(self, eid: str | None) -> str:
        return self.snow.engineers.get(eid or "", {}).get("name", "UNASSIGNED")

    def build(self, stalls: list[dict[str, Any]], vip_users: set[str] | None = None) -> dict[str, Any]:
        now = self.snow.now
        rows: list[dict[str, Any]] = []
        stall_by = {}
        for s in stalls:
            stall_by.setdefault(s["ticket"], []).append(s)

        # ---- incidents
        for t in self.snow.list_queue():
            text = f"{t['short_description']} {t['description']}".lower()
            reasons = []
            if t["priority"] in ("P1", "P2"):
                reasons.append(f"{t['priority']} incident")
            if "vip" in text or t["caller"] in (vip_users or set()):
                reasons.append("VIP caller")
            if any(w in text for w in ("several", "whole", "team", "users affected")):
                reasons.append("wide impact")
            if t["sla_used_pct"] >= 75:
                reasons.append(f"SLA {t['sla_used_pct']}%")
            a = t.get("approval")
            if a and a["state"] == "requested" and a["pending_hours"] > 24:
                reasons.append(f"approval stalled {a['pending_hours']} h ({a['approver_role']})")
            if not reasons:
                continue
            st = stall_by.get(t["number"], [])
            blocker = "; ".join(f"{s['kind']}: {s['detail']}" for s in st) or "none"
            kinds = {x["kind"] for x in st}
            nxt = ("Reject: privileged access goes through PIM/CAB" if "policy_violation" in kinds
                   else "Reroute to the owning team" if "misrouted" in kinds
                   else "Chase approver / escalate to their manager" if a and a["state"] == "requested"
                   else "Assign owner now" if not t["assigned_to"] else "Work the fix and update the ticket")
            rows.append(self._row("Incident", t["number"], t["short_description"], t["priority"], t["state"],
                                  self._eng(t["assigned_to"]), reasons, blocker, nxt, UPDATE_CADENCE_H[t["priority"]]))

        # ---- changes
        for c in self.changes:
            reasons, blockers = [], []
            hours_to_start = (c["planned_start"] - now).total_seconds() / 3600
            if c["cab_approval"] == "requested":
                reasons.append("awaiting CAB")
                if (c["cab_pending_hours"] or 0) > 24:
                    blockers.append(f"CAB approval pending {c['cab_pending_hours']} h")
            if c["cab_approval"] == "not requested":
                blockers.append("CAB approval not requested")
            if 0 <= hours_to_start <= 168:
                reasons.append(f"starts in {int(hours_to_start)} h")
            if c["risk"] == "High":
                reasons.append("high risk")
            missing = [n for n, k in (("implementation plan", "implementation_plan"), ("backout plan", "backout_plan"),
                                      ("test evidence", "test_evidence")) if not c[k]]
            if missing:
                blockers.append("missing " + ", ".join(missing))
            if c.get("outcome") in ("failed", "backed out"):
                reasons.append(f"change {c['outcome']}")
                blockers.append("post-implementation review required")
            if not reasons and not blockers:
                continue
            if not c["assigned_to"]:
                blockers.append("no owner")
            nxt = ("Complete the PIR and record lessons learned" if c.get("outcome") else
                   "Attach missing plans/evidence before CAB" if missing else
                   "Chase CAB approval" if c["cab_approval"] == "requested" else
                   "Raise for CAB" if c["cab_approval"] == "not requested" else "Confirm readiness and comms for the window")
            rows.append(self._row("Change", c["number"], c["short_description"], c["risk"], c["state"],
                                  self._eng(c["assigned_to"]), reasons, "; ".join(blockers) or "none", nxt,
                                  UPDATE_CADENCE_H[c["risk"]], extra={"Planned start": _fmt(c["planned_start"])}))

        # ---- tasks
        for t in self.tasks:
            reasons = []
            if t["due"] < now:
                reasons.append(f"overdue by {int((now - t['due']).total_seconds() // 3600)} h")
            if t["blocked_by"]:
                reasons.append("blocked")
            if not t["assigned_to"]:
                reasons.append("unassigned")
            if not reasons:
                continue
            nxt = ("Assign owner" if not t["assigned_to"] else
                   f"Resolve blocker: {t['blocked_by']}" if t["blocked_by"] else "Complete and close today")
            rows.append(self._row("Task", t["number"], t["short_description"], "Task", t["state"],
                                  self._eng(t["assigned_to"]), reasons, t["blocked_by"] or "none", nxt,
                                  UPDATE_CADENCE_H["Task"], extra={"Due": _fmt(t["due"]), "Parent": t["parent"]}))

        order = {"Incident": 0, "Change": 1, "Task": 2}
        sev = {"P1": 0, "P2": 1, "High": 1, "P3": 2, "Moderate": 2, "P4": 3, "Task": 3, "Low": 4}
        rows.sort(key=lambda r: (sev.get(r["Priority/Risk"], 5), order[r["Type"]]))
        return {"as_of": _fmt(now), "rows": rows,
                "counts": {k: sum(1 for r in rows if r["Type"] == k) for k in order},
                "sharepoint": self.sp_cfg, "teams_channel": self.channel_cfg}

    def _row(self, typ, number, title, prio, state, owner, reasons, blocker, nxt, cadence_h, extra=None):
        row = {"Type": typ, "Number": number, "Title": title[:120], "Priority/Risk": prio, "State": state,
               "Owner": owner, "Why tracked": "; ".join(reasons), "Blocker": blocker, "Next action": nxt,
               "Next update due": _fmt(self.snow.now + timedelta(hours=cadence_h))}
        row.update(extra or {})
        return row

    def export_csv(self, tracker: dict[str, Any]) -> dict[str, Any]:
        cols = ["Type", "Number", "Title", "Priority/Risk", "State", "Owner", "Why tracked", "Blocker", "Next action",
                "Next update due", "Planned start", "Due", "Parent"]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = f"AD_major_ticket_tracker_{_now():%Y%m%d}.csv"
        with (self.out_dir / name).open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(tracker["rows"])
        return {"file": name, "rows": len(tracker["rows"])}

    @staticmethod
    def channel_text(tracker: dict[str, Any]) -> str:
        c = tracker["counts"]
        lines = [f"AD Major Ticket Tracker - {tracker['as_of']}",
                 f"{c['Incident']} incident(s), {c['Change']} change(s), {c['Task']} task(s) need attention.", ""]
        for r in tracker["rows"]:
            lines.append(f"[{r['Type']}] {r['Number']} ({r['Priority/Risk']}, {r['State']}) {r['Title']}")
            lines.append(f"   Owner: {r['Owner']} | Why: {r['Why tracked']} | Blocker: {r['Blocker']}")
            lines.append(f"   Next: {r['Next action']} | Next update due: {r['Next update due']}")
        lines.append("")
        lines.append("Posted by the AD Ops agents (AI-assisted) after engineer approval. Full list in SharePoint.")
        return "\n".join(lines)
