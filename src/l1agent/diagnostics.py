"""Deterministic Active Directory account diagnostics.

Codifies the checks an AD L2 engineer runs first (Get-ADUser lockout/expiry
attributes, msDS-UserPasswordExpiryTimeComputed, 4740 lockout events) so the model
never has to compute dates or guess account state. Every finding carries its
evidence (attribute values or event records) so it can be cited.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from .directory import Directory

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


def run_diagnostics(directory: Directory, user_id: str) -> dict[str, Any]:
    u = directory.get(user_id)
    if not u:
        return {"ok": False, "error": "user_not_found"}
    p = directory.policy
    now = directory.now
    findings: list[dict[str, Any]] = []

    def add(code: str, severity: str, summary: str, evidence: dict, kb: str | None, privileged_fix: bool):
        findings.append({"code": code, "severity": severity, "summary": summary, "evidence": evidence,
                         "kb_article": kb, "fix_requires_privileged_action": privileged_fix})

    # 1. Account disabled
    if not u["enabled"]:
        add("ACCOUNT_DISABLED", "critical", "The account is disabled in Active Directory.",
            {"enabled": False}, "KB-AD-010", True)

    # 2. Account expired (accountExpires in the past)
    if u["account_expires"] is not None:
        if u["account_expires"] <= now:
            add("ACCOUNT_EXPIRED", "critical",
                f"The account expired on {_iso(u['account_expires'])} (accountExpires).",
                {"account_expires": _iso(u["account_expires"])}, "KB-AD-010", True)
        elif u["account_expires"] - now <= timedelta(days=7):
            add("ACCOUNT_EXPIRING_SOON", "medium",
                f"The account expires on {_iso(u['account_expires'])}.",
                {"account_expires": _iso(u["account_expires"])}, "KB-AD-010", True)

    # 3. Lockout state and auto-unlock time
    lockouts_24h = directory.events_for(user_id, 4740, timedelta(hours=24))
    if u["locked_out"] and u["lockout_time"] is not None:
        unlock_at = u["lockout_time"] + timedelta(minutes=p["lockout_duration_minutes"])
        mins_left = max(0, int((unlock_at - now).total_seconds() // 60))
        add("LOCKED_OUT", "high",
            f"The account is locked out ({u['bad_pwd_count']} bad password attempts, threshold "
            f"{p['lockout_threshold']}). It unlocks automatically at {_iso(unlock_at)} (about {mins_left} min).",
            {"lockout_time": _iso(u["lockout_time"]), "bad_pwd_count": u["bad_pwd_count"],
             "auto_unlock_at": _iso(unlock_at), "minutes_until_unlock": mins_left},
            "KB-AD-001", False)

    # 4. Repeated lockouts and the probable source (4740 caller computer)
    if len(lockouts_24h) >= 3:
        sources = Counter(e["caller_computer"] for e in lockouts_24h)
        top, count = sources.most_common(1)[0]
        own_devices = {d["name"]: d for d in u["devices"]}
        src_desc = f"{top} ({own_devices[top]['type']})" if top in own_devices else top
        add("REPEATED_LOCKOUTS", "high",
            f"{len(lockouts_24h)} lockouts in the last 24 hours. {count} came from {src_desc}, which is probably "
            f"still using an old password.",
            {"lockouts_24h": len(lockouts_24h),
             "events": [{"event_id": e["event_id"], "time": _iso(e["time"]), "dc": e["dc"],
                         "caller_computer": e["caller_computer"]} for e in lockouts_24h],
             "probable_source": top},
            "KB-AD-001", True)

    # 5. Password expiry (pwdLastSet + maxPwdAge)
    if not u["password_never_expires"]:
        expires = u["pwd_last_set"] + timedelta(days=p["max_password_age_days"])
        days_left = (expires - now).days
        ev = {"pwd_last_set": _iso(u["pwd_last_set"]), "max_password_age_days": p["max_password_age_days"],
              "password_expires": _iso(expires)}
        if expires <= now:
            add("PASSWORD_EXPIRED", "high", f"The password expired on {_iso(expires)}.", ev, "KB-AD-002", False)
        elif days_left <= p["password_expiry_warning_days"]:
            add("PASSWORD_EXPIRING_SOON", "medium",
                f"The password expires in {days_left} day(s), on {_iso(expires)}.", ev, "KB-AD-002", False)

    # 6. Self-service readiness
    if not u["mfa_registered"]:
        add("MFA_NOT_REGISTERED", "medium", "No MFA method is registered.", {"mfa_registered": False},
            "KB-AD-003", True)
    if not u["sspr_registered"]:
        add("SSPR_NOT_REGISTERED", "info",
            "Not registered for self-service password reset, so self-service unlock and reset are not available.",
            {"sspr_registered": False}, "KB-AD-002", False)

    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
    codes = {f["code"] for f in findings}
    return {
        "escalation_policy": escalation_policy(codes),
        "ok": True,
        "user_id": user_id,
        "checked_at": _iso(now),
        "healthy": not any(f["severity"] in ("critical", "high") for f in findings),
        "findings": findings,
        "checks_run": ["account_enabled", "account_expires", "lockout_state", "lockout_events_4740_24h",
                       "password_expiry", "mfa_registration", "sspr_registration"],
    }


def check_group_access(directory: Directory, resources: list[dict], user_id: str, resource: str) -> dict[str, Any]:
    u = directory.get(user_id)
    if not u:
        return {"ok": False, "error": "user_not_found"}
    q = resource.lower().strip()
    match = None
    for r in resources:
        if q == r["resource_id"] or any(n in q or q in n for n in r["names"]):
            match = r
            break
    if not match:
        return {"ok": False, "error": "resource_not_found",
                "known_resources": [r["resource_id"] for r in resources]}
    eff = directory.effective_groups(user_id)
    is_member = match["group"] in eff
    path = eff.get(match["group"])
    return {"ok": True, "resource_id": match["resource_id"], "required_group": match["group"],
            "is_member": is_member, "membership": ("direct" if path and len(path) == 1 else
                                                    ("nested via " + " -> ".join(path) if path else "none")),
            "approver": match["approver"],
            "next_step": ("Already a member. Sign out and back in to refresh the Kerberos token (KB-AD-004#s3)."
                          if is_member else
                          "Not a member. Needs an access request approved by the group owner (KB-AD-004#s2).")}


# Deterministic escalation policy (KB "When to escalate" sections). The model cannot override it.
_POLICY = [
    ("ACCOUNT_DISABLED", "Account & Access", "Account disabled / expired", "Re-enabling a disabled account is privileged (KB-AD-010)."),
    ("ACCOUNT_EXPIRED", "Account & Access", "Account disabled / expired", "Extending an expired account needs manager approval (KB-AD-010)."),
    ("REPEATED_LOCKOUTS", "Account & Access", "Account lockout", "3+ lockouts in 24h must be investigated by Identity Ops (KB-AD-001#s4)."),
]


def escalation_policy(codes: set[str]) -> dict:
    for code, cat, sub, why in _POLICY:
        if code in codes:
            return {"escalation_required": True, "trigger": code, "category": cat, "subcategory": sub, "reason": why}
    if {"PASSWORD_EXPIRED", "SSPR_NOT_REGISTERED"} <= codes:
        return {"escalation_required": True, "trigger": "PASSWORD_EXPIRED+SSPR_NOT_REGISTERED", "category": "Password & MFA",
                "subcategory": "Password reset / change",
                "reason": "Expired password and no self-service registration: identity verification needed (KB-AD-002#s4)."}
    return {"escalation_required": False}
