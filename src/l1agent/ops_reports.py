"""AD team routines: health check, daily/weekly discussion sheets, DL report mail, EOD ServiceNow updates.

* HealthCheck     - deterministic DC health (replication, services, time skew, SYSVOL, disk, dcdiag, FSMO)
                    plus directory hygiene (disabled users still in groups, stale/never-expiring privileged
                    service accounts, privileged group membership, lockout hot-spots, password expiry wave).
* SheetBuilder    - daily sheet (one row per open ticket with stall and next step) and weekly flow sheet
                    (opened/resolved/backlog/approvals/SLA per day) as CSV, opens directly in Excel.
* MailConnector   - mock Outlook / Graph sendMail to the team DL; sending is approval-gated.
* EODUpdater      - drafts an end-of-day status work note for every open ticket as one batch of
                    approval items, so an engineer can review and bulk-approve.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .connectors import PRIVILEGED_GROUPS, ServiceNowConnector
from .diagnostics import run_diagnostics
from .directory import Directory


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class HealthCheck:
    def __init__(self, data_dir: Path, directory: Directory):
        self.data = json.loads((data_dir / "ad_health.json").read_text(encoding="utf-8"))
        self.directory = directory

    def run(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []

        def add(sev, area, target, finding, action):
            issues.append({"severity": sev, "area": area, "target": target, "finding": finding, "recommended_action": action})

        for dc in self.data["domain_controllers"]:
            for svc, st in dc["services"].items():
                if st != "Running":
                    add("high" if svc in ("NTDS", "KDC", "Netlogon", "DNS") else "medium", "Services", dc["name"],
                        f"{svc} service is {st}", f"Start {svc} on {dc['name']} and check why it stopped (System log).")
            for r in dc["replication"]:
                if r["consecutive_failures"] > 0:
                    add("critical" if r["last_success_minutes_ago"] > 180 else "high", "Replication", f"{dc['name']} <- {r['partner']}",
                        f"{r['consecutive_failures']} consecutive failures, last success {r['last_success_minutes_ago']} min ago"
                        + (f" ({r['last_error']})" if r.get("last_error") else ""),
                        "Check RPC/firewall connectivity between the DCs, then run repadmin /syncall /AdeP.")
            if abs(dc["time_offset_seconds"]) > 300:
                add("critical", "Time sync", dc["name"], f"Clock offset {dc['time_offset_seconds']} s (Kerberos limit is 300 s)",
                    "Start W32Time and resync with the PDC emulator (w32tm /resync). Kerberos sign-ins fail beyond 5 minutes.")
            elif abs(dc["time_offset_seconds"]) > 60:
                add("medium", "Time sync", dc["name"], f"Clock offset {dc['time_offset_seconds']} s", "Resync time with the PDC emulator.")
            if not dc["sysvol_shared"]:
                add("critical", "SYSVOL", dc["name"], "SYSVOL not shared", "Check DFSR replication of SYSVOL.")
            if dc["free_disk_pct"] < 15:
                add("medium", "Capacity", dc["name"], f"Only {dc['free_disk_pct']}% free disk", "Free space on the NTDS / log volume.")
            for test in dc["dcdiag_failed_tests"]:
                add("high", "dcdiag", dc["name"], f"dcdiag test '{test}' failed", "Review the dcdiag output for this test.")

        for grp, members in self.data["privileged_group_members"].items():
            for m in members:
                if m.startswith("svc-"):
                    add("high", "Privileged access", grp, f"Service account {m} is a member of {grp}",
                        "Remove the service account from the privileged group; use a gMSA with delegated rights.")
        for sa in self.data["service_accounts"]:
            if sa["password_never_expires"] and sa["pwd_last_set_days_ago"] > 365:
                add("high" if sa["privileged"] else "medium", "Hygiene", sa["name"],
                    f"Password never expires and was last set {sa['pwd_last_set_days_ago']} days ago",
                    "Rotate the password or convert to a gMSA.")

        expiring, locked = [], []
        for uid, u in self.directory.users.items():
            if not u["enabled"] and len([g for g in u["member_of"] if g != "Domain Users"]) > 0:
                add("medium", "Hygiene", uid, f"Disabled account still in {len(u['member_of']) - 1} security group(s)",
                    "Remove group memberships from disabled accounts (leaver process).")
            for f in run_diagnostics(self.directory, uid)["findings"]:
                if f["code"] in ("PASSWORD_EXPIRING_SOON", "PASSWORD_EXPIRED"):
                    expiring.append(uid)
                if f["code"] == "REPEATED_LOCKOUTS":
                    locked.append(uid)
        if locked:
            add("medium", "Lockouts", ", ".join(locked), "Repeated lockouts (3+ in 24 h)", "Run lockout-source analysis (4740) and fix devices.")
        if expiring:
            add("info", "Passwords", ", ".join(expiring), f"{len(expiring)} account(s) with expired or soon-expiring passwords",
                "Send reminder with KB-AD-002 self-service steps.")

        order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
        issues.sort(key=lambda i: order[i["severity"]])
        sev_count = {s: sum(1 for i in issues if i["severity"] == s) for s in order}
        status = "RED" if sev_count["critical"] else "AMBER" if sev_count["high"] else "GREEN"
        return {"as_of": _now().strftime("%Y-%m-%d %H:%M UTC"), "overall": status, "counts": sev_count,
                "dcs_checked": [d["name"] for d in self.data["domain_controllers"]],
                "fsmo": {d["name"]: d["fsmo_roles"] for d in self.data["domain_controllers"] if d["fsmo_roles"]},
                "issues": issues}


class SheetBuilder:
    def __init__(self, data_dir: Path, snow: ServiceNowConnector, out_dir: Path):
        self.history = json.loads((data_dir / "ticket_history.json").read_text(encoding="utf-8"))["days"]
        self.snow = snow
        self.out_dir = out_dir

    def _write(self, name: str, rows: list[list[Any]]) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        p = self.out_dir / name
        with p.open("w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel opens UTF-8 correctly
            csv.writer(f).writerows(rows)
        return p

    def daily(self, metrics: dict[str, Any]) -> dict[str, Any]:
        stalls: dict[str, list[str]] = {}
        for s in metrics["stalls"]:
            stalls.setdefault(s["ticket"], []).append(f"{s['kind']}: {s['detail']}")
        rows = [["Ticket", "Priority", "State", "Assigned to", "Caller", "Short description", "Age (h)", "Idle (h)",
                 "SLA used %", "Approval", "Stalled because", "Owner of next step", "Discussion / next step"]]
        nxt = {s["ticket"]: s for s in metrics["stalls"]}
        for t in sorted(self.snow.list_queue(), key=lambda x: (x["priority"], -x["sla_used_pct"])):
            a = t.get("approval")
            appr = f"{a['state']} by {a['approver_role']} ({a['pending_hours']} h)" if a else ""
            eng = self.snow.engineers.get(t["assigned_to"] or "", {}).get("name", "UNASSIGNED")
            s = nxt.get(t["number"])
            rows.append([t["number"], t["priority"], t["state"], eng, t["caller"], t["short_description"], t["age_hours"],
                         t["idle_hours"], t["sla_used_pct"], appr, " | ".join(stalls.get(t["number"], [])) or "on track",
                         s["owner"] if s else "assigned engineer", ""])
        name = f"AD_daily_ticket_sheet_{_now():%Y%m%d}.csv"
        p = self._write(name, rows)
        return {"file": name, "path": str(p), "rows": len(rows) - 1, "preview": rows[:6]}

    def weekly(self) -> dict[str, Any]:
        rows = [["Date", "Opened", "Resolved", "Net backlog change", "Reassigned in", "Approvals requested",
                 "Approvals completed", "SLA breaches"]]
        backlog = 0
        tot = {"opened": 0, "resolved": 0, "approvals_requested": 0, "approvals_completed": 0, "sla_breaches": 0, "reassigned_in": 0}
        for d in sorted(self.history, key=lambda x: -x["days_ago"]):
            day = (_now() - timedelta(days=d["days_ago"])).strftime("%Y-%m-%d %a")
            net = d["opened"] - d["resolved"]
            backlog += net
            for k in tot:
                tot[k] += d[k]
            rows.append([day, d["opened"], d["resolved"], net, d["reassigned_in"], d["approvals_requested"],
                         d["approvals_completed"], d["sla_breaches"]])
        rows.append(["WEEK TOTAL", tot["opened"], tot["resolved"], backlog, tot["reassigned_in"], tot["approvals_requested"],
                     tot["approvals_completed"], tot["sla_breaches"]])
        approval_rate = round(100 * tot["approvals_completed"] / max(1, tot["approvals_requested"]))
        rows += [[], ["Discussion points"],
                 [f"Backlog grew by {backlog} tickets this week" if backlog > 0 else f"Backlog reduced by {-backlog} tickets"],
                 [f"Only {approval_rate}% of approvals requested were completed: approvals are the main bottleneck"
                  if approval_rate < 70 else f"Approval completion {approval_rate}%"],
                 [f"{tot['reassigned_in']} tickets were reassigned into the queue: review routing rules with SD-L1"],
                 [f"{tot['sla_breaches']} SLA breaches this week"]]
        name = f"AD_weekly_flow_sheet_{_now():%Y%m%d}.csv"
        p = self._write(name, rows)
        return {"file": name, "path": str(p), "totals": tot, "net_backlog_change": backlog,
                "approval_completion_pct": approval_rate, "preview": rows[:9]}


class MailConnector:
    """Mock Outlook / Microsoft Graph sendMail. Only called by the Approval Center."""

    def __init__(self, runtime_dir: Path):
        self.path = runtime_dir / "mail_outbox.jsonl"
        self.outbox: list[dict[str, Any]] = []

    def send(self, to: str, subject: str, body: str, attachments: list[str], by: str) -> dict:
        if not to.endswith("@corp.example.com"):
            raise ValueError("reports may only be sent to internal CORP distribution lists")
        msg = {"at": _now().strftime("%Y-%m-%d %H:%M UTC"), "to": to, "subject": subject[:200], "body": body[:8000],
               "attachments": attachments, "approved_by": by}
        self.outbox.append(msg)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")
        except OSError:
            pass
        return {"ok": True, "result": f"report mailed to {to} ({len(attachments)} attachment(s))"}


def report_body(digest: dict[str, Any], health: dict[str, Any] | None) -> str:
    d, m = digest["digest"], digest["metrics"]
    out = io.StringIO()
    out.write(f"AD Identity Ops daily report - {m['as_of']}\n\n{d['headline']}\n{d['summary']}\n\n")
    out.write("Stuck tickets:\n")
    for s in d["stuck_items"]:
        out.write(f"- {s['ticket']}: {s['why_stuck']} -> {s['next_step']} (owner: {s['owner']})\n")
    if d["actions_for_today"]:
        out.write("\nActions for today:\n" + "".join(f"- {a}\n" for a in d["actions_for_today"]))
    if health:
        out.write(f"\nAD health: {health['overall']} ({health['counts']['critical']} critical, {health['counts']['high']} high)\n")
        for i in health["issues"][:6]:
            out.write(f"- [{i['severity'].upper()}] {i['area']} {i['target']}: {i['finding']}\n")
    out.write("\nGenerated by the AD Ops agents (AI-assisted) and approved by an AD engineer before sending.\n")
    return out.getvalue()


def eod_note(t: dict[str, Any], stalls: list[dict[str, Any]], actions_today: list[dict[str, Any]]) -> str:
    done = [a for a in actions_today if a["status"] == "executed"]
    waiting = [a for a in actions_today if a["status"] == "pending"]
    lines = [f"EOD status {_now():%Y-%m-%d}: state {t['state']}, {t['priority']}, age {t['age_hours']} h ({t['sla_used_pct']}% SLA)."]
    if done:
        lines.append("Done today: " + "; ".join(a["summary"] for a in done[:5]) + ".")
    if waiting:
        lines.append("Awaiting engineer approval: " + "; ".join(a["summary"] for a in waiting[:3]) + ".")
    if t.get("approval") and t["approval"]["state"] == "requested":
        lines.append(f"Waiting on business approval from {t['approval']['approver_role']} for {t['approval']['pending_hours']} h.")
    if stalls:
        lines.append("Blockers: " + "; ".join(f"{s['kind']} ({s['detail']})" for s in stalls) + ".")
    if not (done or waiting or stalls):
        lines.append("No blockers; work continues tomorrow.")
    return " ".join(lines)
