"""FastAPI backend. It is the only component that talks to OpenAI; the browser never sees the key.

Production features: AUTH_MODE (demo | easyauth with Entra ID app roles), structured JSON access logs with
request IDs, /healthz and /readyz probes, request-size limit, security headers, per-user rate limit,
generic error responses (no stack traces), demo-only endpoints disabled outside demo mode.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
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
from .auth import Auth
from .config import get_settings
from .ops import OpsService

STATIC = Path(__file__).parent / "static"
MAX_BODY_BYTES = 64 * 1024
RATE_PER_MIN = 15

log = logging.getLogger("l1agent.access")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False

settings = get_settings()
service = AgentService(settings)
ops = OpsService(service)
auth = Auth(settings.auth_mode, service.directory, ops.snow.engineers)
app = FastAPI(title="AD Identity L1 Virtual Agent", version=__version__,
              description="UC-05 L1 Virtual Agent for Active Directory. Built by Samarth Pandey (hack-grp-22).",
              docs_url="/docs" if auth.demo else None, redoc_url=None, openapi_url="/openapi.json" if auth.demo else None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def platform_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.perf_counter()
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        resp = JSONResponse({"detail": "request too large"}, status_code=413)
    else:
        try:
            resp = await call_next(request)
        except Exception:  # noqa: BLE001 - never leak internals; log with request id
            logging.getLogger("l1agent.error").exception("unhandled error rid=%s path=%s", rid, request.url.path)
            resp = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
    resp.headers["X-Request-ID"] = rid
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    if not auth.demo:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if not request.url.path.startswith("/static"):
        log.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rid": rid,
                             "method": request.method, "path": request.url.path, "status": resp.status_code,
                             "ms": int((time.perf_counter() - t0) * 1000)}))
    return resp


# ------------------------------------------------------------------ models
class ChatIn(BaseModel):
    user_id: str | None = Field(default=None, max_length=16, description="demo mode only; ignored in easyauth")
    message: Any = None
    conversation_id: str | None = Field(default=None, max_length=40)


class TicketAction(BaseModel):
    user_id: str | None = Field(default=None, max_length=16)


class ApproveIn(BaseModel):
    identity_verified: bool = False
    edited_text: str | None = Field(default=None, max_length=8000)


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class BulkIn(BaseModel):
    ids: list[str] = Field(max_length=50)


class BusinessApprovalIn(BaseModel):
    approved: bool = True


def _approval_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ApprovalError as e:
        raise HTTPException(status_code=e.code, detail=str(e)) from None


# ------------------------------------------------------------------ probes and session
@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: KB indexed, directory loaded, auth configured."""
    ok = bool(service.kb.chunks) and bool(service.directory.users)
    return JSONResponse({"status": "ready" if ok else "not ready", "kb_sections": len(service.kb.chunks),
                         "mode": service.mode, "auth_mode": auth.mode}, status_code=200 if ok else 503)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "mode": service.mode, "auth_mode": auth.mode,
            "actions_enabled": ops.approvals.actions_enabled,
            "model": settings.model if service.mode == "online" else None,
            "kb_sections": len(service.kb.chunks), "retrieval": service.kb.retrieval_mode,
            "users": len(service.directory.users), "built_by": "Samarth Pandey (hack-grp-22)"}


@app.get("/api/session")
def session(request: Request) -> dict[str, Any]:
    return auth.session(request)


# ------------------------------------------------------------------ end-user portal
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/users")
def users(request: Request) -> list[dict[str, str]]:
    if auth.demo:
        return service.directory.list_users()
    uid = auth.end_user(request, None)
    return [u for u in service.directory.list_users() if u["user_id"] == uid]


@app.get("/api/me/{user_id}")
def me(user_id: str, request: Request) -> dict[str, Any]:
    uid = auth.end_user(request, user_id)
    return service.directory.public_profile(uid) or {}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> JSONResponse:
    uid = auth.end_user(request, body.user_id)
    now = time.time()
    q = _hits[uid]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(status_code=429, detail="Too many messages. Please wait a minute and try again.")
    q.append(now)
    try:
        result = service.handle(uid, body.message, body.conversation_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    return JSONResponse(result)


@app.post("/api/tickets/{draft_id}/confirm")
def confirm(draft_id: str, body: TicketAction, request: Request) -> dict[str, Any]:
    """Human approval gate: the only way a ticket is ever created."""
    uid = auth.end_user(request, body.user_id)
    try:
        return service.tickets.confirm(draft_id, uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found or already processed") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="this draft belongs to another user") from None


@app.post("/api/tickets/{draft_id}/cancel")
def cancel(draft_id: str, body: TicketAction, request: Request) -> dict[str, str]:
    uid = auth.end_user(request, body.user_id)
    try:
        service.tickets.cancel(draft_id, uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="draft not found or already processed") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="this draft belongs to another user") from None
    return {"status": "cancelled"}


@app.get("/api/tickets")
def my_tickets(request: Request, user_id: str | None = None) -> list[dict[str, Any]]:
    uid = auth.end_user(request, user_id)
    return service.tickets.for_user(uid)


# ------------------------------------------------------------------ AD Ops Console (engineers only)
def _eng(request: Request, x_engineer_id: str | None, approver: bool = False) -> str:
    return auth.engineer(request, x_engineer_id, need_approver=approver)


@app.get("/ops")
def ops_page() -> FileResponse:
    return FileResponse(STATIC / "ops.html")


@app.get("/api/ops/engineers")
def engineers(request: Request) -> list[dict[str, Any]]:
    if auth.demo:
        return list(ops.snow.engineers.values())
    eid = _eng(request, None)
    return [ops.snow.engineers[eid]]


@app.get("/api/ops/queue")
def queue(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return {"assignment_group": ops.snow.assignment_group, "tickets": ops.snow.list_queue()}


@app.get("/api/ops/tickets/{number}")
def ticket(number: str, request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    t = ops.snow.get(number)
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    return {"ticket": t, "caller": ops.entra.get_user(t["caller"]), "actions": ops.approvals.pending(number),
            "last_triage": ops.last_triage.get(number)}


@app.post("/api/ops/tickets/{number}/triage")
def triage(number: str, request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    try:
        return ops.triage(number)
    except KeyError:
        raise HTTPException(status_code=404, detail="ticket not found") from None


@app.post("/api/ops/triage-all")
def triage_all(request: Request, x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _eng(request, x_engineer_id)
    return ops.triage_all_new()


@app.post("/api/ops/tickets/{number}/business-approval")
def business_approval(number: str, body: BusinessApprovalIn, request: Request,
                      x_engineer_id: str | None = Header(default=None)) -> dict:
    """DEMO ONLY: simulates the group owner / manager answering the ServiceNow approval request.
    Disabled outside demo mode: production business approvals arrive from ServiceNow."""
    if not auth.demo:
        raise HTTPException(status_code=404, detail="not available")
    _eng(request, x_engineer_id)
    try:
        ops.snow.record_business_approval(number, body.approved, "business approver (simulated)")
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"ticket": ops.snow.get(number)}


@app.post("/api/ops/tickets/{number}/kb-proposal")
def kb_proposal(number: str, request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    try:
        return ops.propose_kb_from_ticket(number)
    except KeyError:
        raise HTTPException(status_code=404, detail="ticket not found") from None


@app.get("/api/ops/approvals")
def approvals(request: Request, status: str | None = None,
              x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _eng(request, x_engineer_id)
    items = [i for i in ops.approvals.items.values() if status is None or i["status"] == status]
    return sorted(items, key=lambda i: i["proposed_at"], reverse=True)


@app.post("/api/ops/approvals/{item_id}/approve")
def approve(item_id: str, body: ApproveIn, request: Request,
            x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    eid = _eng(request, x_engineer_id, approver=True)
    return _approval_call(ops.approvals.approve, item_id, eid, body.identity_verified, body.edited_text)


@app.post("/api/ops/approvals/{item_id}/reject")
def reject(item_id: str, body: RejectIn, request: Request,
           x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    eid = _eng(request, x_engineer_id, approver=True)
    return _approval_call(ops.approvals.reject, item_id, eid, body.reason)


@app.post("/api/ops/approvals/bulk-approve")
def bulk(body: BulkIn, request: Request, x_engineer_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    eid = _eng(request, x_engineer_id, approver=True)
    return ops.approvals.approve_many(body.ids, eid)


@app.get("/api/ops/insights")
def insights(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.insights.digest()


@app.get("/api/ops/health")
def health_check(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.health.run()


@app.post("/api/ops/daily-report")
def daily_report(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.daily_report()


@app.post("/api/ops/eod")
def eod(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.eod_updates()


@app.get("/api/ops/reports/{name}")
def report_file(name: str, request: Request, x_engineer_id: str | None = Header(default=None)) -> FileResponse:
    _eng(request, x_engineer_id)
    folder = (settings.runtime_dir / "reports").resolve()
    p = (folder / name).resolve()
    if p.parent != folder or not p.is_file() or p.suffix != ".csv":
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(p, media_type="text/csv", filename=name)


@app.get("/api/ops/audit")
def audit(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return {"approvals": ops.approvals.audit_log(), "admin_changes": ops.approvals.admin.audit,
            "teams_outbox": ops.teams.outbox[-20:], "mail_outbox": ops.mail.outbox[-10:],
            "channel_posts": ops.channel.posts[-10:], "sharepoint_items": len(ops.sharepoint.items)}


@app.get("/api/ops/tracker")
def tracker(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.major_tracker()


@app.post("/api/ops/tracker/publish")
def tracker_publish(request: Request, x_engineer_id: str | None = Header(default=None)) -> dict[str, Any]:
    _eng(request, x_engineer_id)
    return ops.publish_tracker()
