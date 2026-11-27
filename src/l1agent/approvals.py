"""Approval Center: the single gate for every consequential action proposed by the agents.

Agents can only PROPOSE. A human AD engineer (approver role) must approve each item;
only then is a single-use approval token minted and the connector call executed.

Extra controls on admin changes:
  * Two-key rule: group membership, account extension and re-enable also need the BUSINESS
    approval (group owner / manager) recorded on the ServiceNow ticket.
  * MFA reset needs the approving engineer to attest that the caller's identity was verified.
  * Privileged groups (Domain Admins, ...) are denied by policy and can never be approved here.
Every decision and execution is written to the audit log.  Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connectors import PRIVILEGED_GROUPS, ControlledAdmin, KBWriter, ServiceNowConnector, TeamsConnector

ACTION_TYPES = {"servicenow_work_note", "servicenow_update", "servicenow_reassign", "servicenow_request_approval",
                "teams_message", "admin_change", "kb_publish", "email_report",
                "sharepoint_update", "teams_channel_post"}
NEEDS_BUSINESS_APPROVAL = {"add_group_member", "extend_account_expiry", "enable_account"}
NEEDS_IDENTITY_ATTESTATION = {"reset_mfa_methods", "enable_account"}
RISK = {"servicenow_work_note": "low", "servicenow_update": "low", "servicenow_request_approval": "low",
        "servicenow_reassign": "low", "teams_message": "medium", "kb_publish": "medium", "admin_change": "high",
        "email_report": "medium", "sharepoint_update": "low", "teams_channel_post": "medium"}


class ApprovalError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class ApprovalCenter:
    def __init__(self, snow: ServiceNowConnector, teams: TeamsConnector, kbw: KBWriter, directory, runtime_dir: Path,
                 mail=None, sharepoint=None, channel=None):
        self.snow, self.teams, self.kbw, self.mail = snow, teams, kbw, mail
        self.sharepoint, self.channel = sharepoint, channel
        self.actions_enabled = True
        self._tokens: dict[str, tuple[str, str]] = {}          # token -> (action, user_id), single use
        self.admin = ControlledAdmin(directory, self._consume_token)
        self.items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.log_path = runtime_dir / "approvals_audit.jsonl"
        runtime_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ tokens
    def _mint(self, action: str, user_id: str) -> str:
        tok = "APR-" + uuid.uuid4().hex[:12]
        self._tokens[tok] = (action, user_id)
        return tok

    def _consume_token(self, token: str, action: str, user_id: str) -> bool:
        return self._tokens.pop(token, None) == (action, user_id)

    # ------------------------------------------------------------------ propose
    def propose(self, *, type: str, ticket: str | None, summary: str, payload: dict[str, Any],
                proposed_by: str, rationale: str = "") -> dict[str, Any]:
        if type not in ACTION_TYPES:
            raise ValueError(f"unknown action type {type}")
        status = "pending"
        policy_note = None
        if type == "admin_change":
            act = payload.get("action")
            if act not in ControlledAdmin.ROLE_FOR:
                raise ValueError(f"admin action {act} not in allow-list")
            if act == "add_group_member" and payload.get("group") in PRIVILEGED_GROUPS:
                status, policy_note = "denied_by_policy", "Privileged group: needs PIM/CAB, never via this agent."
        item = {
            "id": "ACT-" + uuid.uuid4().hex[:8].upper(), "type": type, "ticket": ticket, "summary": summary[:300],
            "payload": payload, "rationale": rationale[:600], "risk": RISK[type], "status": status,
            "policy_note": policy_note, "proposed_by": proposed_by, "proposed_at": _ts(),
            "required_role": ControlledAdmin.ROLE_FOR.get(payload.get("action", "")) if type == "admin_change" else None,
            "needs_business_approval": type == "admin_change" and payload.get("action") in NEEDS_BUSINESS_APPROVAL,
            "needs_identity_attestation": type == "admin_change" and payload.get("action") in NEEDS_IDENTITY_ATTESTATION,
            "decided_by": None, "decided_at": None, "result": None,
        }
        with self._lock:
            self.items[item["id"]] = item
        self._audit("proposed", item)
        return item

    def pending(self, ticket: str | None = None) -> list[dict[str, Any]]:
        return [i for i in self.items.values() if (ticket is None or i["ticket"] == ticket)]

    # ------------------------------------------------------------------ decide
    def approve(self, item_id: str, engineer_id: str, identity_verified: bool = False,
                edited_text: str | None = None) -> dict[str, Any]:
        if not self.actions_enabled:
            raise ApprovalError(503, "action execution is frozen (ACTIONS_ENABLED=false); proposals are kept pending")
        eng = self.snow.engineers.get(engineer_id)
        if not eng or "approver" not in eng["roles"]:
            raise ApprovalError(403, "only an AD engineer with the approver role can approve actions")
        with self._lock:
            item = self.items.get(item_id)
            if not item:
                raise ApprovalError(404, "action not found")
            if item["status"] != "pending":
                raise ApprovalError(409, f"action is {item['status']}, not pending")
            p = item["payload"]
            if item["type"] == "admin_change":
                if item["needs_business_approval"]:
                    t = self.snow.get(item["ticket"]) or {}
                    if not t.get("approval") or t["approval"]["state"] != "approved":
                        raise ApprovalError(409, "two-key rule: the business approval on the ticket is not granted yet")
                if item["needs_identity_attestation"] and not identity_verified:
                    raise ApprovalError(409, "confirm that you verified the caller's identity (manager call-back) first")
            if edited_text and item["type"] in ("teams_message", "servicenow_work_note", "teams_channel_post"):
                p["text"] = edited_text[:6000 if item["type"] == "teams_channel_post" else 2000]
            item["status"] = "approved"
            item["decided_by"], item["decided_at"] = engineer_id, _ts()
        try:
            item["result"] = self._execute(item, engineer_id)
            item["status"] = "executed"
        except (PermissionError, ValueError, KeyError) as e:
            item["status"], item["result"] = "failed", {"ok": False, "error": str(e)}
        self._audit(item["status"], item)
        if item["ticket"] and item["type"] != "servicenow_work_note" and item["status"] == "executed":
            self.snow.add_work_note(item["ticket"], f"[{item['id']}] {item['summary']} - approved by "
                                                    f"{eng['name']}; result: {item['result'].get('result')}", "approval-center")
        return item

    def approve_many(self, item_ids: list[str], engineer_id: str) -> list[dict[str, Any]]:
        """Bulk approval for low-risk batches (e.g. EOD work notes). Admin changes are never bulk-approved."""
        out = []
        for iid in item_ids[:50]:
            item = self.items.get(iid)
            if not item or item["type"] == "admin_change":
                out.append({"id": iid, "status": "skipped", "result": {"ok": False, "error": "not eligible for bulk approval"}})
                continue
            try:
                out.append(self.approve(iid, engineer_id))
            except ApprovalError as e:
                out.append({"id": iid, "status": "error", "result": {"ok": False, "error": str(e)}})
        return out

    def reject(self, item_id: str, engineer_id: str, reason: str = "") -> dict[str, Any]:
        eng = self.snow.engineers.get(engineer_id)
        if not eng or "approver" not in eng["roles"]:
            raise ApprovalError(403, "only an AD engineer with the approver role can reject actions")
        with self._lock:
            item = self.items.get(item_id)
            if not item:
                raise ApprovalError(404, "action not found")
            if item["status"] != "pending":
                raise ApprovalError(409, f"action is {item['status']}, not pending")
            item["status"], item["decided_by"], item["decided_at"] = "rejected", engineer_id, _ts()
            item["result"] = {"ok": True, "result": f"rejected: {reason[:200]}"}
        self._audit("rejected", item)
        return item

    # ------------------------------------------------------------------ execute
    def _execute(self, item: dict[str, Any], by: str) -> dict[str, Any]:
        p, t = item["payload"], item["ticket"]
        typ = item["type"]
        if typ == "servicenow_work_note":
            self.snow.add_work_note(t, p["text"], by)
            return {"ok": True, "result": "work note added"}
        if typ == "servicenow_update":
            self.snow.update(t, p["fields"], by)
            return {"ok": True, "result": f"ticket updated {p['fields']}"}
        if typ == "servicenow_reassign":
            self.snow.reassign(t, p["group"], by)
            return {"ok": True, "result": f"reassigned to {p['group']}"}
        if typ == "servicenow_request_approval":
            self.snow.request_approval(t, p["approver_role"], p.get("approver_user"), by)
            return {"ok": True, "result": f"approval request/reminder sent to {p['approver_role']}"}
        if typ == "teams_message":
            return self.teams.send_chat(p["user_id"], p["text"], by)
        if typ == "sharepoint_update":
            return self.sharepoint.upsert(p["rows"], by)
        if typ == "teams_channel_post":
            return self.channel.post(p["text"], by)
        if typ == "email_report":
            return self.mail.send(p["to"], p["subject"], p["body"], p.get("attachments", []), by)
        if typ == "kb_publish":
            return self.kbw.publish(p["article_id"], p["title"], p["sections"], by)
        if typ == "admin_change":
            act, uid = p["action"], p["user_id"]
            tok = self._mint(act, uid)
            if act == "add_group_member":
                return self.admin.add_group_member(uid, p["group"], tok)
            if act == "extend_account_expiry":
                return self.admin.extend_account_expiry(uid, int(p.get("days", 90)), tok)
            return getattr(self.admin, act)(uid, tok)
        raise ValueError(f"no executor for {typ}")

    def _audit(self, event: str, item: dict[str, Any]) -> None:
        rec = {"at": _ts(), "event": event, "id": item["id"], "type": item["type"], "ticket": item["ticket"],
               "summary": item["summary"], "decided_by": item["decided_by"], "result": item["result"]}
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in reversed(lines)]
