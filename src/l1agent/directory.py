"""Mock Active Directory: synthetic users, domain policy and DC security events.

This stands in for read-only LDAP / Microsoft Graph lookups. Relative dates in the
sample files are turned into absolute UTC timestamps at load time so the demo data
never goes stale.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Directory:
    def __init__(self, data_dir: Path, now: datetime | None = None):
        self.now = now or utcnow()
        d = json.loads((data_dir / "directory.json").read_text(encoding="utf-8"))
        ev = json.loads((data_dir / "security_events.json").read_text(encoding="utf-8"))
        self.policy: dict[str, Any] = d["domain"]
        self.nesting: dict[str, list[str]] = {k: v for k, v in d.get("group_nesting", {}).items() if not k.startswith("_")}
        self.users: dict[str, dict[str, Any]] = {}
        for u in d["users"]:
            u = dict(u)
            u["pwd_last_set"] = self.now - timedelta(days=u["pwd_last_set_days_ago"])
            u["lockout_time"] = (self.now - timedelta(minutes=u["lockout_minutes_ago"])
                                 if u.get("lockout_minutes_ago") is not None else None)
            exp = u.get("account_expires_days_from_now")
            u["account_expires"] = self.now + timedelta(days=exp) if exp is not None else None
            self.users[u["user_id"]] = u
        self.events: list[dict[str, Any]] = []
        for e in ev["events"]:
            e = dict(e)
            e["time"] = self.now - timedelta(minutes=e["minutes_ago"])
            self.events.append(e)

    def get(self, user_id: str) -> dict[str, Any] | None:
        return self.users.get(user_id)

    def events_for(self, user_id: str, event_id: int | None = None, within: timedelta | None = None) -> list[dict]:
        out = [e for e in self.events if e["user_id"] == user_id]
        if event_id is not None:
            out = [e for e in out if e["event_id"] == event_id]
        if within is not None:
            out = [e for e in out if e["time"] >= self.now - within]
        return sorted(out, key=lambda e: e["time"], reverse=True)

    def effective_groups(self, user_id: str) -> dict[str, list[str]]:
        """Resolve nested group membership (like tokenGroups). Returns group -> path from a direct group."""
        u = self.get(user_id)
        if not u:
            return {}
        paths: dict[str, list[str]] = {g: [g] for g in u["member_of"]}
        queue = list(u["member_of"])
        while queue:  # breadth-first; the visited set protects against circular nesting
            g = queue.pop(0)
            for parent in self.nesting.get(g, []):
                if parent not in paths:
                    paths[parent] = paths[g] + [parent]
                    queue.append(parent)
        return paths

    def public_profile(self, user_id: str) -> dict[str, Any] | None:
        """Minimal context for the agent: no email, phone numbers or other PII beyond what triage needs."""
        u = self.get(user_id)
        if not u:
            return None
        manager = self.get(u["manager_id"]) if u.get("manager_id") else None
        return {
            "user_id": u["user_id"],
            "display_name": u["display_name"],
            "department": u["department"],
            "title": u["title"],
            "vip": u["vip"],
            "manager": manager["display_name"] if manager else None,
            "devices": u["devices"],
            "member_of": u["member_of"],
            "effective_groups": sorted(self.effective_groups(user_id)),
            "mfa_registered": u["mfa_registered"],
            "sspr_registered": u["sspr_registered"],
        }

    def list_users(self) -> list[dict[str, str]]:
        return [{"user_id": u["user_id"], "display_name": u["display_name"], "department": u["department"]}
                for u in self.users.values()]
