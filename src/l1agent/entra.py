"""Microsoft Entra ID sign-in for organisation users (AUTH_MODE=entra).

The app signs people in itself with OpenID Connect (authorization code flow + PKCE) against your own
app registration, so it works on any host (a VM, a container, localhost) without Azure App Service.

* Who can sign in: users of your tenant. Turn on "Assignment required" on the Enterprise application and
  assign users/groups to control exactly who may use the agent.
* Roles: Entra app roles arrive in the ID token `roles` claim (AD.Engineer, AD.Approver).
* Token checks: signature against the tenant's published signing keys (JWKS), issuer, audience, expiry,
  nonce, tenant id. PKCE and state protect the redirect.
* Session: a signed (HMAC-SHA256), HttpOnly, SameSite=Lax cookie holding only upn/name/roles/expiry.
  No tokens are stored in the browser.

Settings: ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, ENTRA_REDIRECT_URI, SESSION_SECRET.
See docs/ORG_ROLLOUT_GUIDE.md.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

SESSION_COOKIE = "l1_session"
FLOW_COOKIE = "l1_auth_flow"
SESSION_HOURS = 8


class EntraConfigError(RuntimeError):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    """Tamper-proof cookie values: base64(json).base64(hmac)."""

    def __init__(self, secret: str):
        if len(secret) < 32:
            raise EntraConfigError("SESSION_SECRET must be at least 32 characters (random)")
        self._key = secret.encode()

    def dumps(self, data: dict[str, Any]) -> str:
        body = _b64(json.dumps(data, separators=(",", ":")).encode())
        return body + "." + _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def loads(self, value: str | None) -> dict[str, Any] | None:
        if not value or "." not in value:
            return None
        body, sig = value.rsplit(".", 1)
        good = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        try:
            data = json.loads(_unb64(body))
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("exp", 0) < time.time():
            return None
        return data


class EntraOIDC:
    def __init__(self, tenant_id: str | None = None, client_id: str | None = None, client_secret: str | None = None,
                 redirect_uri: str | None = None, session_secret: str | None = None,
                 transport: httpx.BaseTransport | None = None, jwks_client: Any = None):
        self.tenant = tenant_id or os.getenv("ENTRA_TENANT_ID", "")
        self.client_id = client_id or os.getenv("ENTRA_CLIENT_ID", "")
        self._secret = client_secret or os.getenv("ENTRA_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.getenv("ENTRA_REDIRECT_URI", "http://localhost:8000/auth/callback")
        missing = [n for n, v in (("ENTRA_TENANT_ID", self.tenant), ("ENTRA_CLIENT_ID", self.client_id),
                                  ("ENTRA_CLIENT_SECRET", self._secret)) if not v]
        if missing:
            raise EntraConfigError("AUTH_MODE=entra needs " + ", ".join(missing))
        if self.tenant.lower() in ("common", "organizations", "consumers"):
            raise EntraConfigError("ENTRA_TENANT_ID must be your tenant ID (single-tenant app), not 'common'")
        self.signer = Signer(session_secret or os.getenv("SESSION_SECRET", ""))
        self.authority = f"https://login.microsoftonline.com/{self.tenant}"
        self.issuer = f"{self.authority}/v2.0"
        self.secure_cookies = self.redirect_uri.startswith("https://")
        self._http = httpx.Client(timeout=15.0, transport=transport)
        self._jwks = jwks_client or jwt.PyJWKClient(f"{self.authority}/discovery/v2.0/keys", cache_keys=True)

    # ---- step 1: send the browser to Entra ID
    def begin(self, return_to: str = "/") -> tuple[str, str]:
        """Returns (authorize_url, flow_cookie_value)."""
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        if not return_to.startswith("/") or return_to.startswith("//"):
            return_to = "/"                                   # no open redirects
        flow = self.signer.dumps({"state": state, "nonce": nonce, "verifier": verifier, "return_to": return_to,
                                  "exp": time.time() + 600})
        q = {"client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
             "response_mode": "query", "scope": "openid profile email", "state": state, "nonce": nonce,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{self.authority}/oauth2/v2.0/authorize?{urlencode(q)}", flow

    # ---- step 2: Entra ID redirects back with ?code&state
    def complete(self, code: str, state: str, flow_cookie: str | None) -> tuple[str, str]:
        """Validates the callback and returns (session_cookie_value, return_to). Raises PermissionError."""
        flow = self.signer.loads(flow_cookie)
        if not flow or not hmac.compare_digest(flow.get("state", ""), state or ""):
            raise PermissionError("sign-in expired or state mismatch; please sign in again")
        r = self._http.post(f"{self.authority}/oauth2/v2.0/token", data={
            "grant_type": "authorization_code", "client_id": self.client_id, "client_secret": self._secret,
            "code": code, "redirect_uri": self.redirect_uri, "code_verifier": flow["verifier"],
            "scope": "openid profile email"})
        if r.status_code != 200:
            err = (r.json().get("error_codes") or ["?"]) if r.headers.get("content-type", "").startswith("application/json") else ["?"]
            raise PermissionError(f"Entra ID rejected the sign-in (AADSTS{err[0]})")
        claims = self.validate_id_token(r.json().get("id_token", ""), flow["nonce"])
        return self.signer.dumps(self.session_from_claims(claims)), flow.get("return_to", "/")

    def validate_id_token(self, token: str, nonce: str) -> dict[str, Any]:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.client_id, issuer=self.issuer,
                                options={"require": ["exp", "iat", "aud", "iss", "nonce"]}, leeway=120)
        except jwt.PyJWTError as e:
            raise PermissionError(f"invalid ID token: {e}") from None
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise PermissionError("invalid ID token: nonce mismatch")
        if claims.get("tid") and claims["tid"].lower() != self.tenant.lower():
            raise PermissionError("account is from another tenant")
        return claims

    @staticmethod
    def session_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
        upn = (claims.get("preferred_username") or claims.get("upn") or claims.get("email") or "").lower()
        if not upn:
            raise PermissionError("ID token has no user name (preferred_username)")
        return {"upn": upn, "name": claims.get("name", ""), "oid": claims.get("oid", ""),
                "roles": [r for r in claims.get("roles", []) if isinstance(r, str)][:20],
                "exp": time.time() + SESSION_HOURS * 3600}

    def logout_url(self, post_logout: str) -> str:
        return f"{self.authority}/oauth2/v2.0/logout?{urlencode({'post_logout_redirect_uri': post_logout})}"
