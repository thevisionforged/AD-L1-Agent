"""FastAPI backend. It is the only component that talks to OpenAI; the browser never sees the key.

Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import AgentService
from .config import get_settings

STATIC = Path(__file__).parent / "static"
settings = get_settings()
service = AgentService(settings)
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
