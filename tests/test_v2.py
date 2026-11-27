"""v2 (OpenAI Agents SDK): live Graph connector parsing, configuration errors, API availability.
Live agent runs are verified with the real API (see docs/test_results.md); these tests need no network.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from l1agent.v2.graph_connector import GraphConfigError, GraphConnector


def _graph_transport(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "oauth2/v2.0/token" in str(req.url):
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            assert body["grant_type"] == "client_credentials" and "graph.microsoft.com" in body["scope"]
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer tok"
        if "/transitiveMemberOf" in req.url.path:
            return httpx.Response(200, json={"value": [{"displayName": "GG-VPN-Users"}, {"displayName": "SG-Finance"}]})
        if req.url.path.endswith("/auditLogs/signIns"):
            return httpx.Response(200, json={"value": [
                {"createdDateTime": "2026-11-27T10:00:00Z", "appDisplayName": "Outlook", "ipAddress": "10.0.0.5",
                 "status": {"errorCode": 50053, "failureReason": "Account is locked"}, "deviceDetail": {"displayName": "PHONE-1"}},
                {"createdDateTime": "2026-11-27T09:00:00Z", "appDisplayName": "Teams", "status": {"errorCode": 0}}]})
        if "/users/" in req.url.path:
            return httpx.Response(200, json={"displayName": "Real User", "userPrincipalName": "real.user@contoso.com",
                                             "accountEnabled": True, "department": "IT"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_graph_connector_reads_real_shapes():
    calls = []
    g = GraphConnector("tenant", "client", "secret", transport=_graph_transport(calls))
    r = g.account_status("real.user@contoso.com")
    assert r["ok"] and r["source"].startswith("microsoft-graph")
    assert r["user"]["accountEnabled"] is True and "GG-VPN-Users" in r["groups"]
    assert r["recent_signin_failures"] == [{"time": "2026-11-27T10:00:00Z", "app": "Outlook", "code": "AADSTS50053",
                                            "meaning": "account locked (too many attempts) or blocked sign-in",
                                            "ip": "10.0.0.5", "device": "PHONE-1"}]
    assert any("transitiveMemberOf" in c for c in calls) and any("auditLogs/signIns" in c for c in calls)


def test_graph_connector_requires_configuration(monkeypatch):
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(GraphConfigError):
        GraphConnector()


def test_graph_forbidden_is_reported_not_raised():
    def handler(req):
        if "token" in str(req.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403)
    r = GraphConnector("t", "c", "s", transport=httpx.MockTransport(handler)).account_status("x@y.com")
    assert r["ok"] is False and "permission" in r["error"]


def test_v2_api_needs_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    import l1agent.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.get("/api/v2/info").json()["available"] is False
    r = c.post("/api/v2/chat", json={"user_id": "u1001", "message": "hi"})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]
    assert c.get("/v2").status_code == 200


def test_v2_agent_definition():
    from l1agent.v2.agents_app import build_agents
    a = build_agents("gpt-4.1-mini")
    names = [t.name for t in a.tools]
    assert {"consult_identity_diagnostics", "search_kb", "draft_ticket", "submit_ticket", "my_tickets"} <= set(names)
    submit = next(t for t in a.tools if t.name == "submit_ticket")
    assert submit.needs_approval is True                      # SDK human-in-the-loop
    assert a.input_guardrails, "existing guardrails wired as an SDK input guardrail"
