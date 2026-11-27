"""Conversation follow-ups about tickets, handled deterministically before any model call.

Real users talk about their ticket after it exists: "what's the status of INC0100001?", "I confirm, submit it",
"I've already submitted". These must never start a new escalation or a duplicate draft.

* Status:     the user's OWN tickets only (another user's number is reported as not found).
* Confirm:    typed, explicit confirmation by the signed-in owner submits their pending draft, exactly like the
              Confirm button (it is the same human decision, made in words).
* Submitted:  acknowledge and point to the existing ticket; never draft again.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import re
from typing import Any

from .schemas import AgentReply
from .tickets import TicketStore

INC = re.compile(r"\bINC\d{7}\b", re.I)
STATUS = re.compile(r"(?i)\b(status|update|progress|any news|where is|what happened|eta|when will)\b.{0,60}"
                    r"\b(ticket|inc\d{7}|request|case|incident)\b|\b(ticket|inc\d{7}|request|case)\b.{0,40}\b(status|update|progress)\b")
ALREADY = re.compile(r"(?i)(\b(already|have|i've|i have)\s+(submitted|confirmed|raised|logged|created|sent)\b"
                     r"|^\W*(submitted|submited|done|i did|confirmed|raised it|it'?s submitted)\W*$)")
CONFIRM = re.compile(r"(?i)^\W*((yes|yep|ok(ay)?|sure|great)[\s,!.]*)?(please\s+)?(i\s+)?(confirm|submit|go ahead|raise it|"
                     r"log it|create it|open it|send it)(\s+(it|the ticket|the draft|submit|and submit|please))*[\s!.]*$")


def _name(directory, user_id: str) -> str:
    return (directory.get(user_id) or {}).get("display_name", "").split(" (")[0].split(" ")[0]


def _describe(t: dict[str, Any]) -> str:
    state = {"new": "New (waiting to be picked up)", "in_progress": "In progress", "resolved": "Resolved"}.get(
        t.get("status", "new"), t.get("status", "new"))
    return (f"{t['number']} ({t['subcategory']}, {t['priority']}) is with {t['assignment_group']}. "
            f"Status: {state}. Raised {t.get('created_at', '')}.")


def handle_followup(tickets: TicketStore, directory, user_id: str, text: str) -> dict[str, Any] | None:
    """Returns {"reply": AgentReply, "ticket_submitted": ticket|None} or None if this is not a follow-up."""
    name = _name(directory, user_id)
    mentioned = INC.findall(text)

    if mentioned or STATUS.search(text):
        if mentioned:
            parts = []
            for num in dict.fromkeys(n.upper() for n in mentioned):
                t = tickets.find_for_user(user_id, num)
                parts.append(_describe(t) if t else f"I can't find {num} under your account. Please check the number.")
            msg = " ".join(parts)
        else:
            mine = [t for t in tickets.for_user(user_id) if t.get("status") not in ("resolved", "closed")]
            pending = tickets.pending_for(user_id)
            if mine:
                msg = "Here's where things stand: " + " ".join(_describe(t) for t in mine[-3:])
            elif pending:
                msg = (f"You don't have a submitted ticket yet. There is a draft waiting for you ({pending['subcategory']}, "
                       f"{pending['priority']}): review it and press Confirm, or just type \"confirm\".")
            else:
                msg = "You don't have any open tickets. Tell me what's going on and I'll help."
        if pending := tickets.pending_for(user_id):
            if mentioned:
                msg += f" You also have an unsubmitted draft ({pending['subcategory']}); type \"confirm\" if you want it raised."
        return {"reply": AgentReply(response_type="answer", message=f"Hi {name}! " + msg if name else msg,
                                    confidence="high"), "ticket_submitted": None}

    if ALREADY.search(text):
        t = tickets.open_ticket(user_id)
        pending = tickets.pending_for(user_id)
        if t:
            msg = (f"Thanks {name}, I can see it. {_describe(t)} You don't need to submit it again; the team will "
                   "update you on the ticket.")
        elif pending:
            msg = (f"I can't see a submitted ticket yet: the {pending['subcategory']} draft is still waiting. "
                   "Press Confirm in the Ticket draft tab, or type \"confirm\" and I'll submit it for you.")
        else:
            msg = "I can't find a ticket for you yet. Tell me what's going on and I'll prepare one if needed."
        return {"reply": AgentReply(response_type="answer", message=msg, confidence="high"), "ticket_submitted": None}

    if CONFIRM.search(text):
        pending = tickets.pending_for(user_id)
        if pending:
            t = tickets.confirm(pending["draft_id"], user_id)
            msg = (f"Done, {name}. I've submitted your ticket {t['number']} ({t['subcategory']}, {t['priority']}) to "
                   f"{t['assignment_group']}. They'll pick it up from here. You can ask me for its status any time.")
            return {"reply": AgentReply(response_type="answer", message=msg, confidence="high"), "ticket_submitted": t}
        t = tickets.open_ticket(user_id)
        msg = (f"There's nothing waiting to submit. {_describe(t)}" if t
               else "There's no ticket draft waiting to submit. Tell me what the problem is and I'll help.")
        return {"reply": AgentReply(response_type="answer", message=msg, confidence="high"), "ticket_submitted": None}
    return None
