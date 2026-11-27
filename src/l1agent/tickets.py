"""Mock ITSM: deterministic ticket drafting plus a confirmation-gated create step.

The model may only *draft* a ticket. Creating it is not a model tool at all. It
happens only when the signed-in user presses Confirm in the UI (POST
/api/tickets/{draft_id}/confirm), and only for their own draft.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .directory import Directory

LEVELS = ["low", "medium", "high"]


class TicketValidationError(ValueError):
    pass


class TicketStore:
    def __init__(self, data_dir: Path, runtime_dir: Path, directory: Directory):
        self.routing = json.loads((data_dir / "routing.json").read_text(encoding="utf-8"))
        self.directory = directory
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.path = runtime_dir / "tickets.json"
        self._lock = threading.Lock()
        self.drafts: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.created, indent=2), encoding="utf-8")

    # ---- deterministic rules -------------------------------------------------
    def compute_priority(self, impact: str, urgency: str, vip: bool) -> tuple[str, str]:
        eff_urgency = urgency
        reason = f"impact={impact}, urgency={urgency}"
        if vip and urgency != "high":
            eff_urgency = LEVELS[LEVELS.index(urgency) + 1]
            reason += f" -> raised to {eff_urgency} (VIP requester)"
        prio = self.routing["priority_matrix"][impact][eff_urgency]
        return prio, f"{prio} from ITIL matrix ({reason})"

    def route(self, subcategory: str) -> str:
        return self.routing["routing"].get(subcategory, "SD-L1 Service Desk")

    def validate_category(self, category: str, subcategory: str) -> None:
        cats = self.routing["categories"]
        if category not in cats:
            raise TicketValidationError(f"category must be one of {list(cats)}")
        if subcategory not in cats[category]:
            raise TicketValidationError(f"subcategory for '{category}' must be one of {cats[category]}")

    # ---- draft / confirm -----------------------------------------------------
    def draft(self, *, requester_id: str, category: str, subcategory: str, short_description: str,
              description: str, impact: str, urgency: str, steps_attempted: list[str],
              diagnostic_codes: list[str], kb_references: list[str], device: str | None = None) -> dict[str, Any]:
        self.validate_category(category, subcategory)
        if impact not in LEVELS or urgency not in LEVELS:
            raise TicketValidationError("impact and urgency must be low, medium or high")
        short_description = short_description.strip()[:120]
        if not short_description:
            raise TicketValidationError("short_description is required")
        user = self.directory.get(requester_id)
        if not user:
            raise TicketValidationError("unknown requester")
        # No draft spam: reuse a draft for the same issue that is still waiting for this user's confirmation.
        existing = self.pending_for(requester_id, subcategory)
        if existing:
            return existing
        priority, reason = self.compute_priority(impact, urgency, user["vip"])
        devices = [d["name"] for d in user["devices"]]
        if device not in devices:
            device = next((d["name"] for d in user["devices"] if d["type"] == "laptop"), devices[0] if devices else None)
        draft_id = "DRAFT-" + uuid.uuid4().hex[:8].upper()
        payload = {
            "draft_id": draft_id,
            "status": "pending_user_confirmation",
            "category": category,
            "subcategory": subcategory,
            "short_description": short_description,
            "description": description.strip()[:1500],
            "impact": impact,
            "urgency": urgency,
            "priority": priority,
            "priority_reason": reason,
            "assignment_group": self.route(subcategory),
            "requester": {"user_id": user["user_id"], "display_name": user["display_name"],
                          "department": user["department"], "vip": user["vip"]},
            "device": device,
            "steps_attempted": [s.strip()[:200] for s in steps_attempted][:10],
            "diagnostic_codes": diagnostic_codes[:10],
            "kb_references": kb_references[:10],
            "created_by": "L1 Virtual Agent (AI-drafted, pending human confirmation)",
        }
        with self._lock:
            self.drafts[draft_id] = payload
        return payload

    def confirm(self, draft_id: str, user_id: str) -> dict[str, Any]:
        with self._lock:
            d = self.drafts.get(draft_id)
            if not d:
                raise KeyError("draft_not_found")
            if d["requester"]["user_id"] != user_id:
                raise PermissionError("draft belongs to another user")
            ticket = dict(d)
            ticket.pop("draft_id")
            ticket["number"] = f"INC{len(self.created) + 100001:07d}"
            ticket["status"] = "new"
            ticket["confirmed_by"] = user_id
            ticket["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            self.created.append(ticket)
            del self.drafts[draft_id]
            self._save()
        return ticket

    def cancel(self, draft_id: str, user_id: str) -> None:
        with self._lock:
            d = self.drafts.get(draft_id)
            if not d:
                raise KeyError("draft_not_found")
            if d["requester"]["user_id"] != user_id:
                raise PermissionError("draft belongs to another user")
            del self.drafts[draft_id]

    def for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [t for t in self.created if t["requester"]["user_id"] == user_id]

    # ---- conversation follow-ups and duplicate prevention ---------------------------
    def pending_for(self, user_id: str, subcategory: str | None = None) -> dict[str, Any] | None:
        """Latest draft still waiting for this user's confirmation (optionally for one subcategory)."""
        with self._lock:
            ds = [d for d in self.drafts.values() if d["requester"]["user_id"] == user_id
                  and (subcategory is None or d["subcategory"] == subcategory)]
        return ds[-1] if ds else None

    def open_ticket(self, user_id: str, subcategory: str | None = None) -> dict[str, Any] | None:
        """Latest submitted ticket of this user that is still open (optionally for one subcategory)."""
        ts = [t for t in self.for_user(user_id) if t.get("status") not in ("resolved", "closed", "cancelled")
              and (subcategory is None or t["subcategory"] == subcategory)]
        return ts[-1] if ts else None

    def find_for_user(self, user_id: str, number: str) -> dict[str, Any] | None:
        """A user's own ticket by number. Other users' tickets are never returned (access control)."""
        return next((t for t in self.for_user(user_id) if t["number"].upper() == number.upper()), None)
