"""FastAPI backend. It is the only component that talks to OpenAI; the browser never sees the key.

Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import AgentService
from .approvals import ApprovalError
from .config import get_settings
from .ops import OpsService

STATIC = Path(__file__).parent / "static"
settings = get_settings()
service = AgentService(settings)
ops = OpsService(service)
app = FastAPI(title="AD Identity L1 Virtual Agent", version=__version__,
              description="UC-05 L1 Virtual Agent for Active Directory. Built by Samarth Pandey (hack-grp-22).")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Simple per-user rate limit (cost control): N chat requests per rolling minute.
RATE_PER_MIN = 15
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


class ChatIn(BaseModel):
    user_id: str = Field(max_length=16, description="Signed-in user (simulated SSO session)")
    message: Any = None
    conversation_id: str | None = Field(default=None, max_length=40)


class TicketAction(BaseModel):
    user_id: str = Field(max_length=16)


def _check_user(user_id: str) -> None:
    if not service.directory.get(user_id):
        raise HTTPException(status_code=403, detail="unknown or unauthorised user")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "mode": service.mode,
            "model": settings.model if service.mode == "online" else None,
            "kb_sections": len(service.kb.chunks), "retrieval": service.kb.retrieval_mode, "users": len(service.directory.users),
            "built_by": "Samarth Pandey (hack-grp-22)"}


@app.get("/api/users")
def users() -> list[dict[str, str]]:
    return service.directory.list_users()


@app.get("/api/me/{user_id}")
def me(user_id: str) -> dict[str, Any]:
    _check_user(user_id)
    return service.directory.public_profile(user_id) or {}


@app.post("/api/chat")
def chat(body: ChatIn) -> JSONResponse:
    _check_user(body.user_id)
    now = time.time()
    q = _hits[body.user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(status_code=429, detail="Too many messages. Please wait a minute and try again.")
    q.append(now)
    try:
        result = service.handle(body.user_id, body.message, body.conversation_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    return JSONResponse(result)


@app.post("/api/tickets/{draft_id}/confirm")
def confirm(draft_id: str, body: TicketAction) -> dict[str, Any]:
    """Human approval gate: the only way a ticket is ever created."""
    _check_user(body.user_id)
    try:
        return service.tickets.confirm(draft_id, body.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found or already processed") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="this draft belongs to another user") from None


@app.post("/api/tickets/{draft_id}/cancel")
def cancel(draft_id: str, body: TicketAction) -> dict[str, str]:
    _check_user(body.user_id)
    try:
        service.tickets.cancel(draft_id, body.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found or already processed") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="this draft belongs to another user") from None
    return {"status": "cancelled"}


@app.get("/api/tickets")
def my_tickets(user_id: str) -> list[dict[str, Any]]:
    _check_user(user_id)
    return service.tickets.for_user(user_id)


# =============================================================================== AD Ops Console (engineers only)
class ApproveIn(BaseModel):
    identity_verified: bool = False
    edited_text: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class BulkIn(BaseModel):
    ids: list[str] = Field(max_length=50)


class BusinessApprovalIn(BaseModel):
    approved: bool = True


def _eng(x_engineer_id: str | None) -> dict[str, Any]:
    try:
        return ops.require_engineer(x_engineer_id or "")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


def _approval_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ApprovalError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from None


@app.get("/ops")
def ops_page() -> FileResponse:
    return FileResponse(STATIC / "ops.html")


@app.get("/api/ops/engineers")
def engineers() -> list[dict[str, Any]]:
    return list(ops.snow.engineers.values())


@app.get("/api/ops/queue")
def queue(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return {"assignment_group": ops.snow.assignment_group, "tickets": ops.snow.list_queue()}


@app.get("/api/ops/tickets/{number}")
def ticket(number: str, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    t = ops.snow.get(number)
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    return {"ticket": t, "caller": ops.entra.get_user(t["caller"]), "actions": ops.approvals.pending(number),
            "last_triage": ops.last_triage.get(number)}


@app.post("/api/ops/tickets/{number}/triage")
def triage(number: str, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    try:
        return ops.triage(number)
    except KeyError:
        raise HTTPException(status_code=404, detail="ticket not found") from None


@app.post("/api/ops/triage-all")
def triage_all(x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _eng(x_engineer_id)
    return ops.triage_all_new()


@app.post("/api/ops/tickets/{number}/business-approval")
def business_approval(number: str, body: BusinessApprovalIn, x_engineer_id: str | None = Header(default=None)) -> dict:
    """DEMO ONLY: simulates the group owner / manager answering the ServiceNow approval request."""
    _eng(x_engineer_id)
    try:
        ops.snow.record_business_approval(number, body.approved, "business approver (simulated)")
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ticket": ops.snow.get(number)}


@app.post("/api/ops/tickets/{number}/kb-proposal")
def kb_proposal(number: str, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return ops.propose_kb_from_ticket(number)


@app.get("/api/ops/approvals")
def approvals(status: str | None = None, x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _eng(x_engineer_id)
    items = [i for i in ops.approvals.items.values() if status is None or i["status"] == status]
    return sorted(items, key=lambda i: i["proposed_at"], reverse=True)


@app.post("/api/ops/approvals/{item_id}/approve")
def approve(item_id: str, body: ApproveIn, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return _approval_call(ops.approvals.approve, item_id, x_engineer_id, body.identity_verified, body.edited_text)


@app.post("/api/ops/approvals/{item_id}/reject")
def reject(item_id: str, body: RejectIn, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return _approval_call(ops.approvals.reject, item_id, x_engineer_id, body.reason)


@app.post("/api/ops/approvals/bulk-approve")
def bulk(body: BulkIn, x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _eng(x_engineer_id)
    return ops.approvals.approve_many(body.ids, x_engineer_id)


@app.get("/api/ops/insights")
def insights(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return ops.insights.digest()


@app.get("/api/ops/health")
def health_check(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return ops.health.run()


@app.post("/api/ops/daily-report")
def daily_report(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return ops.daily_report()


@app.post("/api/ops/eod")
def eod(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return ops.eod_updates()


@app.get("/api/ops/reports/{name}")
def report_file(name: str, x_engineer_id: str | None = Header(default=None), eng: str | None = None) -> FileResponse:
    _eng(x_engineer_id or eng)
    folder = (settings.runtime_dir / "reports").resolve()
    p = (folder / name).resolve()
    if p.parent != folder or not p.is_file() or p.suffix != ".csv":
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(p, media_type="text/csv", filename=name)


@app.get("/api/ops/audit")
def audit(x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(x_engineer_id)
    return {"approvals": ops.approvals.audit_log(), "admin_changes": ops.approvals.admin.audit,
            "teams_outbox": ops.teams.outbox[-20:], "mail_outbox": ops.mail.outbox[-10:]}
