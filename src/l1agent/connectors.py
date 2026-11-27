"""Enterprise connectors (MOCKED over synthetic data, same shape as the real APIs).

| Connector            | Real system it stands in for                   | Permission model                                  |
|----------------------|------------------------------------------------|---------------------------------------------------|
| EntraReader          | Microsoft Graph with the Entra *Global Reader* role | Read-only: the class has no write methods     |
| ServiceNowConnector  | ServiceNow Table API (incident, sysapproval)   | Reads are free; writes only via ApprovalCenter    |
| ControlledAdmin      | Graph / AD cmdlets under least-privilege roles | Every method needs an approved ApprovalItem token |
| TeamsConnector       | Microsoft Graph chatMessage (1:1 chat)         | Send only via ApprovalCenter                      |
| KBWriter             | Knowledge base (ServiceNow KB / SharePoint)    | Publish only via ApprovalCenter                   |

Swapping a mock for the real API means re-implementing these methods; agents and the
approval flow do not change. Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .directory import Directory

PRIVILEGED_GROUPS = {"Domain Admins", "Enterprise Admins", "Schema Admins", "Administrators", "Account Operators",
                     "Backup Operators", "Server Operators", "Print Operators", "Group Policy Creator Owners"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


# ------------------------------------------------------------------------------ Entra (read only)
class EntraReader:
    """Global Reader view of the tenant. Deliberately exposes NO mutating methods."""

    def __init__(self, directory: Directory):
        self._d = directory

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        u = self._d.get(user_id)
        if not u:
            return None
        prof = self._d.public_profile(user_id) or {}
        prof.update({"account_enabled": u["enabled"], "locked_out": u["locked_out"],
                     "account_expires": _fmt(u["account_expires"]), "password_last_set": _fmt(u["pwd_last_set"])})
        return prof

    def sign_in_failures(self, user_id: str, hours: int = 24) -> list[dict[str, Any]]:
        evs = self._d.events_for(user_id, within=timedelta(hours=hours))
        return [{"time": _fmt(e["time"]), "event_id": e["event_id"], "dc": e["dc"], "source": e["caller_computer"],
                 "failure": e.get("failure") or ("account locked out" if e["event_id"] == 4740 else "")} for e in evs]

    def group_members(self, group: str) -> list[str]:
        return [uid for uid in self._d.users if group in self._d.effective_groups(uid)]


# ------------------------------------------------------------------------------ ServiceNow
class ServiceNowConnector:
    def __init__(self, data_dir: Path, runtime_dir: Path, now: datetime | None = None):
        raw = json.loads((data_dir / "servicenow_queue.json").read_text(encoding="utf-8"))
        self.now = now or _now()
        self.assignment_group = raw["assignment_group"]
        self.sla_hours: dict[str, int] = raw["sla_hours"]
        self.engineers = {e["engineer_id"]: e for e in raw["engineers"]}
        self._lock = threading.Lock()
        self.tickets: dict[str, dict[str, Any]] = {}
        for t in raw["tickets"]:
            t = copy.deepcopy(t)
            t["assignment_group"] = self.assignment_group
            t["opened_at"] = self.now - timedelta(hours=t.pop("opened_hours_ago"))
            t["updated_at"] = self.now - timedelta(hours=t.pop("updated_hours_ago"))
            if t.get("approval"):
                t["approval"]["requested_at"] = self.now - timedelta(hours=t["approval"].pop("requested_hours_ago"))
            t["work_notes"] = [{"at": _fmt(t["updated_at"]), "by": "history", "text": n} for n in t["work_notes"]]
            self.tickets[t["number"]] = t
        self.outbox_path = runtime_dir / "servicenow_changes.jsonl"

    # reads ------------------------------------------------------------------
    def list_queue(self) -> list[dict[str, Any]]:
        return [self.view(n) for n in sorted(self.tickets)
                if self.tickets[n]["state"] not in ("Resolved", "Closed")
                and self.tickets[n]["assignment_group"] == self.assignment_group]

    def view(self, number: str) -> dict[str, Any]:
        t = self.tickets[number]
        v = {k: v for k, v in t.items() if k not in ("opened_at", "updated_at", "approval")}
        v["opened_at"], v["updated_at"] = _fmt(t["opened_at"]), _fmt(t["updated_at"])
        v["age_hours"] = round((self.now - t["opened_at"]).total_seconds() / 3600, 1)
        v["idle_hours"] = round((self.now - t["updated_at"]).total_seconds() / 3600, 1)
        sla = self.sla_hours[t["priority"]]
        v["sla_hours"], v["sla_used_pct"] = sla, round(100 * v["age_hours"] / sla)
        if t.get("approval"):
            a = dict(t["approval"])
            a["pending_hours"] = round((self.now - a.pop("requested_at")).total_seconds() / 3600, 1)
            v["approval"] = a
        else:
            v["approval"] = None
        return v

    def get(self, number: str) -> dict[str, Any] | None:
        return self.view(number) if number in self.tickets else None

    # writes (called ONLY by ApprovalCenter executors) --------------------------
    def _log(self, number: str, change: dict) -> None:
        try:
            self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
            with self.outbox_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": _fmt(_now()), "ticket": number, **change}) + "\n")
        except OSError:
            pass

    def add_work_note(self, number: str, text: str, by: str) -> None:
        with self._lock:
            t = self.tickets[number]
            t["work_notes"].append({"at": _fmt(_now()), "by": by, "text": text[:2000]})
            t["updated_at"] = self.now
        self._log(number, {"op": "work_note", "by": by})

    def update(self, number: str, fields: dict[str, Any], by: str) -> None:
        allowed = {"state", "priority", "assigned_to", "short_description", "category", "subcategory"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"fields not editable: {sorted(bad)}")
        with self._lock:
            t = self.tickets[number]
            t.update(fields)
            t["updated_at"] = self.now
        self._log(number, {"op": "update", "fields": fields, "by": by})

    def reassign(self, number: str, group: str, by: str) -> None:
        with self._lock:
            t = self.tickets[number]
            t["assignment_group"] = group
            t["assigned_to"] = None
            t["reassignment_count"] += 1
            t["state"] = "New"
            t["updated_at"] = self.now
        self.add_work_note(number, f"Reassigned to {group}.", by)

    def request_approval(self, number: str, approver_role: str, approver_user: str | None, by: str) -> None:
        with self._lock:
            t = self.tickets[number]
            a = t.get("approval")
            if a and a["state"] == "requested":
                a["reminders_sent"] = a.get("reminders_sent", 0) + 1
            else:
                t["approval"] = {"state": "requested", "approver_role": approver_role, "approver_user": approver_user,
                                 "requested_at": self.now, "reminders_sent": 0}
                t["state"] = "Awaiting Approval"
            t["updated_at"] = self.now
        self._log(number, {"op": "approval_request", "approver_role": approver_role, "by": by})

    def record_business_approval(self, number: str, approved: bool, by: str) -> None:
        """Simulates the business approver (group owner / manager) answering the ServiceNow approval."""
        with self._lock:
            t = self.tickets[number]
            if not t.get("approval"):
                raise ValueError("no approval on this ticket")
            t["approval"]["state"] = "approved" if approved else "rejected"
            t["state"] = "In Progress"
            t["updated_at"] = self.now
        self.add_work_note(number, f"Business approval {'granted' if approved else 'rejected'} by {by}.", "approval-workflow")


# ------------------------------------------------------------------------------ Controlled admin
class ControlledAdmin:
    """Least-privilege admin actions. Each needs `approval_token` minted by ApprovalCenter after a human approves.

    ROLE_FOR documents the minimum Entra/AD role the production service principal would hold per action
    (assigned just-in-time through PIM in a real deployment)."""

    # Least-privileged roles per Microsoft "Least privileged roles by task" (KB-MS-007), checked 27-Nov-2026.
    ROLE_FOR = {
        "unlock_account": "Helpdesk Administrator / Password Administrator for Entra cloud lockout (via password reset); "
                          "AD DS: delegated 'Unlock account' (write lockoutTime) on user OUs",
        "reset_mfa_methods": "Authentication Administrator (non-admin users; Privileged Authentication Administrator if "
                             "the user holds an admin role)",
        "add_group_member": "Groups Administrator (or group owner); AD DS: delegated 'write member' on the share-group OU",
        "extend_account_expiry": "User Administrator (AD DS: delegated write accountExpires on user OUs)",
        "enable_account": "User Administrator (AD DS: delegated enable/disable on user OUs)",
        "revoke_sessions": "Helpdesk Administrator (least-privileged role to invalidate refresh tokens of non-admins)",
    }

    def __init__(self, directory: Directory, token_check):
        self._d = directory
        self._token_check = token_check
        self.audit: list[dict[str, Any]] = []

    def _guard(self, action: str, token: str, user_id: str) -> dict[str, Any]:
        if not self._token_check(token, action, user_id):
            raise PermissionError("admin action requires a valid human approval token")
        u = self._d.get(user_id)
        if not u:
            raise ValueError("unknown user")
        return u

    def _record(self, action, user_id, before, after, token):
        self.audit.append({"at": _fmt(_now()), "action": action, "user_id": user_id, "before": before, "after": after,
                           "approval": token, "role_used": self.ROLE_FOR[action]})

    def unlock_account(self, user_id: str, approval_token: str) -> dict:
        u = self._guard("unlock_account", approval_token, user_id)
        before = {"locked_out": u["locked_out"], "bad_pwd_count": u["bad_pwd_count"]}
        u["locked_out"], u["bad_pwd_count"], u["lockout_time"] = False, 0, None
        self._record("unlock_account", user_id, before, {"locked_out": False, "bad_pwd_count": 0}, approval_token)
        return {"ok": True, "result": "account unlocked"}

    def reset_mfa_methods(self, user_id: str, approval_token: str) -> dict:
        u = self._guard("reset_mfa_methods", approval_token, user_id)
        before = {"mfa_registered": u["mfa_registered"]}
        u["mfa_registered"] = False   # user must re-register; temporary access pass issued out of band
        self._record("reset_mfa_methods", user_id, before, {"mfa_registered": False, "require_reregistration": True}, approval_token)
        return {"ok": True, "result": "MFA methods cleared; user must re-register"}

    def add_group_member(self, user_id: str, group: str, approval_token: str) -> dict:
        u = self._guard("add_group_member", approval_token, user_id)
        if group in PRIVILEGED_GROUPS:
            raise PermissionError(f"{group} is a privileged group; changes need PIM/CAB, not this agent")
        if group in u["member_of"]:
            return {"ok": True, "result": "already a member"}
        before = {"member_of": list(u["member_of"])}
        u["member_of"].append(group)
        self._record("add_group_member", user_id, before, {"member_of": list(u["member_of"])}, approval_token)
        return {"ok": True, "result": f"added to {group}; user must sign out/in to refresh Kerberos token"}

    def extend_account_expiry(self, user_id: str, days: int, approval_token: str) -> dict:
        u = self._guard("extend_account_expiry", approval_token, user_id)
        if not 1 <= days <= 180:
            raise ValueError("extension must be 1-180 days")
        before = {"account_expires": _fmt(u["account_expires"])}
        u["account_expires"] = self._d.now + timedelta(days=days)
        self._record("extend_account_expiry", user_id, before, {"account_expires": _fmt(u["account_expires"])}, approval_token)
        return {"ok": True, "result": f"account extended to {_fmt(u['account_expires'])}"}

    def enable_account(self, user_id: str, approval_token: str) -> dict:
        u = self._guard("enable_account", approval_token, user_id)
        before = {"enabled": u["enabled"]}
        u["enabled"] = True
        self._record("enable_account", user_id, before, {"enabled": True}, approval_token)
        return {"ok": True, "result": "account enabled"}


# ------------------------------------------------------------------------------ Teams
class TeamsConnector:
    def __init__(self, runtime_dir: Path):
        self.outbox: list[dict[str, Any]] = []
        self.path = runtime_dir / "teams_outbox.jsonl"

    def send_chat(self, user_id: str, text: str, by: str) -> dict:
        msg = {"at": _fmt(_now()), "to": user_id, "from": "CORP AD Support (AI-assisted)", "approved_by": by,
               "text": text[:2000] + "\n\n(Sent by the AD support team with AI assistance. IT never asks for your password.)"}
        self.outbox.append(msg)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")
        except OSError:
            pass
        return {"ok": True, "result": f"Teams message delivered to {user_id}"}


# ------------------------------------------------------------------------------ KB write
class KBWriter:
    _ID = re.compile(r"^KB-AD-\d{3}$")

    def __init__(self, overlay_dir: Path, kb):
        self.dir = overlay_dir
        self.kb = kb

    def publish(self, article_id: str, title: str, sections: list[dict[str, str]], by: str) -> dict:
        if not self._ID.match(article_id):
            raise ValueError("article_id must look like KB-AD-0NN")
        if not sections:
            raise ValueError("article needs at least one section")
        self.dir.mkdir(parents=True, exist_ok=True)
        body = [f"# {article_id}: {title}", "Category: Account & Access | Subcategory: Other",
                f"Applies to: CORP | Published by {by} via approval workflow (AI-drafted)", ""]
        for i, s in enumerate(sections[:8], 1):
            body += [f"## [s{i}] {s['heading'][:80]}", s["text"][:1500], ""]
        (self.dir / f"{article_id}.md").write_text("\n".join(body), encoding="utf-8")
        self.kb.reload()
        return {"ok": True, "result": f"{article_id} published ({len(sections)} sections)"}
