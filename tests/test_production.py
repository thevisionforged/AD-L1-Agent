"""Production mode: Entra ID Easy Auth principal, app roles, probes, request limits.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import base64
import importlib
import json

import pytest
from fastapi.testclient import TestClient


def principal(upn: str, roles: list[str] | None = None) -> dict[str, str]:
    claims = [{"typ": "preferred_username", "val": upn}, {"typ": "name", "val": upn.split("@")[0]}]
    claims += [{"typ": "roles", "val": r} for r in roles or []]
    raw = json.dumps({"auth_typ": "aad", "claims": claims, "name_typ": "name", "role_typ": "roles"}).encode()
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(raw).decode()}


@pytest.fixture
def prod(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("AUTH_MODE", "easyauth")
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    import l1agent.api as api
    importlib.reload(api)
    yield TestClient(api.app)
    monkeypatch.setenv("AUTH_MODE", "demo")
    importlib.reload(api)


ASHA = principal("arao@corp.example.com")
ENG_A = principal("ad.engineer.a@corp.example.com", ["AD.Engineer", "AD.Approver"])
ENG_READONLY = principal("ad.engineer.b@corp.example.com", ["AD.Engineer"])


def test_unauthenticated_requests_rejected(prod):
    assert prod.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert prod.get("/api/ops/queue").status_code == 401


def test_end_user_identity_comes_from_token_not_body(prod):
    r = prod.post("/api/chat", json={"message": "How do I add a printer?"}, headers=ASHA)
    assert r.status_code == 200 and r.json()["reply"]["response_type"] == "answer"
    assert prod.post("/api/chat", json={"user_id": "u1002", "message": "hi"}, headers=ASHA).status_code == 403
    assert prod.get("/api/users", headers=ASHA).json() == [{"user_id": "u1001", "display_name": "Asha Rao (synthetic)",
                                                             "department": "Finance"}]
    assert prod.post("/api/chat", json={"message": "hi"}, headers=principal("stranger@other.com")).status_code == 403


def test_engineer_app_roles_enforced(prod):
    assert prod.get("/api/ops/queue", headers=ASHA).status_code == 403                 # no AD.Engineer role
    assert prod.get("/api/ops/queue", headers=ENG_READONLY).status_code == 200
    t = prod.post("/api/ops/tickets/INC0010001/triage", headers=ENG_READONLY).json()
    item = next(a for a in t["proposed_actions"] if a["type"] == "servicenow_work_note")
    assert prod.post(f"/api/ops/approvals/{item['id']}/approve", json={}, headers=ENG_READONLY).status_code == 403
    assert prod.post(f"/api/ops/approvals/{item['id']}/approve", json={}, headers=ENG_A).json()["status"] == "executed"
    rogue = principal("someone@corp.example.com", ["AD.Engineer", "AD.Approver"])     # role but not on roster
    assert prod.get("/api/ops/queue", headers=rogue).status_code == 403


def test_demo_only_endpoint_disabled_and_docs_hidden(prod):
    assert prod.post("/api/ops/tickets/INC0010002/business-approval", json={}, headers=ENG_A).status_code == 404
    assert prod.get("/docs").status_code == 404


def test_probes_headers_and_limits(prod):
    assert prod.get("/healthz").json() == {"status": "ok"}
    r = prod.get("/readyz")
    assert r.status_code == 200 and r.json()["auth_mode"] == "easyauth"
    r = prod.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123" and "max-age" in r.headers["Strict-Transport-Security"]
    big = prod.post("/api/chat", content=b"x" * 70000, headers={**ASHA, "Content-Type": "application/json"})
    assert big.status_code == 413


def test_malformed_principal_is_rejected(prod):
    assert prod.post("/api/chat", json={"message": "hi"}, headers={"X-MS-CLIENT-PRINCIPAL": "not-base64!!"}).status_code == 401
