"""Organisation integration: Entra ID sign-in (AUTH_MODE=entra), ServiceNow and Teams connectors.
No network: Entra, ServiceNow and Teams are simulated with httpx.MockTransport and a locally generated RSA key.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import base64
import importlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from l1agent.entra import SESSION_COOKIE, EntraConfigError, EntraOIDC, Signer
from l1agent.v2.snow_connector import ServiceNowClient, SnowConfigError
from l1agent.v2.teams_connector import TeamsConfigError, TeamsNotifier

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = "s" * 40   # fake client secret
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())

    def get_signing_keys(self):
        return [1]


def id_token(nonce, aud=CLIENT, tid=TENANT, upn="alex.real@contoso.com", roles=("AD.Engineer",), key=KEY):
    now = int(time.time())
    return jwt.encode({"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": aud, "tid": tid, "iat": now,
                       "exp": now + 3600, "nonce": nonce, "preferred_username": upn, "name": "Alex Real",
                       "oid": "o1", "roles": list(roles)}, key, algorithm="RS256")


def make_oidc(token_fn):
    def handler(req: httpx.Request):
        assert req.url.path.endswith("/oauth2/v2.0/token")
        form = parse_qs(req.content.decode())
        assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0]
        return httpx.Response(200, json={"id_token": token_fn()})
    return EntraOIDC(TENANT, CLIENT, SECRET, "http://localhost:8000/auth/callback", "x" * 40,
                     transport=httpx.MockTransport(handler), jwks_client=FakeJWKS())


def _begin(o, return_to="/v2"):
    url, flow = o.begin(return_to)
    q = parse_qs(urlparse(url).query)
    return q, flow


# ------------------------------------------------------------------ Entra sign-in
def test_signin_flow_pkce_and_valid_token():
    holder = {}
    o = make_oidc(lambda: id_token(holder["nonce"]))
    q, flow = _begin(o)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid profile email"]
    assert urlparse(o.begin()[0]).netloc == "login.microsoftonline.com"
    holder["nonce"] = q["nonce"][0]
    cookie, ret = o.complete("code123", q["state"][0], flow)
    sess = o.signer.loads(cookie)
    assert ret == "/v2" and sess["upn"] == "alex.real@contoso.com" and sess["roles"] == ["AD.Engineer"]


@pytest.mark.parametrize("bad", ["nonce", "aud", "tenant", "key"])
def test_signin_rejects_bad_tokens(bad):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    holder = {}
    o = make_oidc(lambda: id_token("WRONG" if bad == "nonce" else holder["nonce"],
                                   aud="someone-else" if bad == "aud" else CLIENT,
                                   tid="99999999-0000-0000-0000-000000000000" if bad == "tenant" else TENANT,
                                   key=other if bad == "key" else KEY))
    q, flow = _begin(o)
    holder["nonce"] = q["nonce"][0]
    with pytest.raises(PermissionError):
        o.complete("c", q["state"][0], flow)


def test_signin_rejects_state_mismatch_and_open_redirect():
    o = make_oidc(lambda: "")
    _, flow = _begin(o)
    with pytest.raises(PermissionError):
        o.complete("c", "forged-state", flow)
    _, flow2 = o.begin("//evil.example.com/x")
    assert o.signer.loads(flow2)["return_to"] == "/"


def test_session_cookie_tamper_and_expiry():
    s = Signer("k" * 40)
    v = s.dumps({"upn": "a@b.com", "exp": time.time() + 60})
    assert s.loads(v)["upn"] == "a@b.com"
    _, sig = v.rsplit(".", 1)
    forged = base64.urlsafe_b64encode(json.dumps({"upn": "admin@b.com", "exp": time.time() + 60}).encode()).decode().rstrip("=")
    assert s.loads(forged + "." + sig) is None
    assert s.loads(s.dumps({"upn": "a@b.com", "exp": time.time() - 1})) is None
    assert Signer("z" * 40).loads(v) is None


def test_entra_config_errors():
    with pytest.raises(EntraConfigError):
        EntraOIDC("common", CLIENT, SECRET, session_secret="x" * 40, jwks_client=FakeJWKS())
    with pytest.raises(EntraConfigError):
        EntraOIDC(TENANT, CLIENT, SECRET, session_secret="short", jwks_client=FakeJWKS())


# ------------------------------------------------------------------ API in entra mode
@pytest.fixture
def entra_api(tmp_path, monkeypatch):
    for k, v in {"AUTH_MODE": "entra", "AGENT_MODE": "offline", "RUNTIME_DIR": str(tmp_path), "ENTRA_TENANT_ID": TENANT,
                 "ENTRA_CLIENT_ID": CLIENT, "ENTRA_CLIENT_SECRET": SECRET, "SESSION_SECRET": "x" * 40}.items():
        monkeypatch.setenv(k, v)
    from l1agent import api
    importlib.reload(api)
    yield api
    monkeypatch.setenv("AUTH_MODE", "demo")
    importlib.reload(api)


def test_entra_mode_pages_redirect_to_microsoft(entra_api):
    c = TestClient(entra_api.app, follow_redirects=False)
    r = c.get("/v2")
    assert r.status_code == 302 and r.headers["location"] == "/auth/login?return_to=/v2"
    r = c.get("/auth/login?return_to=/v2")
    assert r.status_code == 302 and r.headers["location"].startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize")
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.post("/api/v2/chat", json={"message": "hi"}).status_code == 401
    assert c.get("/api/session").json() == {"auth_mode": "entra", "signed_in": False}


def test_entra_mode_org_user_signed_in(entra_api):
    c = TestClient(entra_api.app, follow_redirects=False)
    cookie = entra_api.oidc.signer.dumps({"upn": "alex.real@contoso.com", "name": "Alex Real", "roles": [],
                                         "exp": time.time() + 600})
    c.cookies.set(SESSION_COOKIE, cookie)
    assert c.get("/v2").status_code == 200
    s = c.get("/api/session").json()
    assert s["signed_in"] and s["org_user"] and s["user_id"].startswith("org-")
    assert c.get("/api/users").json()[0]["display_name"] == "Alex Real"
    r = c.post("/api/v2/chat", json={"message": "hi", "user_id": "u1001"})   # cannot claim another identity
    assert r.status_code == 403
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 403    # v1 is for directory users only
    c.cookies.set(SESSION_COOKIE, cookie[:-3] + "AAA")                       # tampered cookie
    assert c.get("/api/session").json()["signed_in"] is False


def test_org_user_can_draft_and_submit_ticket(tmp_path):
    import os

    from l1agent.agent import AgentService
    from l1agent.config import get_settings
    os.environ["RUNTIME_DIR"] = str(tmp_path)
    svc = AgentService(get_settings())
    svc.tickets.register_org_user("org-abc", "Alex Real", "alex.real@contoso.com")
    d = svc.tickets.draft(requester_id="org-abc", category="Account & Access", subcategory="Account lockout",
                          short_description="Locked out", description="d", impact="medium", urgency="medium",
                          steps_attempted=[], diagnostic_codes=[], kb_references=[])
    t = svc.tickets.confirm(d["draft_id"], "org-abc")
    assert t["number"].startswith("INC") and t["requester"]["display_name"] == "Alex Real"


# ------------------------------------------------------------------ ServiceNow
def test_servicenow_create_and_list(monkeypatch):
    monkeypatch.setenv("SNOW_USERNAME", "svc_l1agent")
    monkeypatch.setenv("SNOW_PASSWORD", "pw")
    monkeypatch.setenv("SNOW_ASSIGNMENT_GROUP", "IAM-L2 Identity Ops (AD)")
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        assert req.headers["Authorization"].startswith("Basic ")
        p = req.url.path
        if p.endswith("/sys_user"):
            return httpx.Response(200, json={"result": [{"sys_id": "U1"}]})
        if p.endswith("/sys_user_group"):
            return httpx.Response(200, json={"result": [{"sys_id": "G1"}]})
        if p.endswith("/incident") and req.method == "POST":
            body = json.loads(req.content)
            assert body["caller_id"] == "U1" and body["assignment_group"] == "G1" and body["impact"] == "1"
            return httpx.Response(201, json={"result": {"number": "INC0012345", "sys_id": "S1", "priority": "2 - High", "state": "New"}})
        return httpx.Response(200, json={"result": [{"number": "INC0012345", "short_description": "x", "state": "New",
                                                     "priority": "2 - High", "assignment_group": {"display_value": "IAM"}}]})
    c = ServiceNowClient("contoso", transport=httpx.MockTransport(handler))
    inc = c.create_incident("alex.real@contoso.com", {"short_description": "Locked out", "description": "d",
                                                      "impact": "high", "urgency": "medium"})
    assert inc["number"] == "INC0012345" and inc["url"].startswith("https://contoso.service-now.com/")
    assert c.open_incidents("alex.real@contoso.com")[0]["assignment_group"] == "IAM"
    assert seen[0].url.params["sysparm_query"] == "email=alex.real@contoso.com^ORuser_name=alex.real@contoso.com"


def test_servicenow_needs_credentials(monkeypatch):
    for k in ("SNOW_USERNAME", "SNOW_PASSWORD", "SNOW_CLIENT_ID", "SNOW_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SnowConfigError):
        ServiceNowClient("contoso")


# ------------------------------------------------------------------ Teams
def test_teams_card_and_url_allowlist():
    got = {}

    def handler(req):
        got.update(json.loads(req.content))
        return httpx.Response(202)
    t = TeamsNotifier("https://prod-01.westeurope.logic.azure.com/workflows/abc", transport=httpx.MockTransport(handler))
    assert t.post_ticket({"number": "INC1", "priority": "P3", "assignment_group": "IAM", "subcategory": "Account lockout",
                          "short_description": "Locked out", "url": "https://contoso.service-now.com/x"}, "Alex")["ok"]
    card = got["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"].startswith("https://contoso")
    with pytest.raises(TeamsConfigError):
        TeamsNotifier("https://evil.example.com/hook")
    with pytest.raises(TeamsConfigError):
        TeamsNotifier("http://prod-01.westeurope.logic.azure.com/x")
