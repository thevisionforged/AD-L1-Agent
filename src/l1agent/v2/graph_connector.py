"""LIVE Microsoft Graph connector for v2 (CONNECTOR_MODE=live). Read-only.

Uses the OAuth 2.0 client-credentials flow (app registration with admin-consented APPLICATION permissions
User.Read.All, GroupMember.Read.All and, for sign-in failures, AuditLog.Read.All; see
docs/ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md section 4). Secrets come only from environment variables:
GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET.

Endpoints (Microsoft Graph v1.0):
  token:   POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token  (scope https://graph.microsoft.com/.default)
  user:    GET  /users/{upn}?$select=...
  groups:  GET  /users/{upn}/transitiveMemberOf?$select=displayName
  signins: GET  /auditLogs/signIns?$filter=userPrincipalName eq '{upn}'&$top=10
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
# Entra sign-in error codes -> meaning (source: Microsoft Entra error code reference, see KB-MS-001)
AADSTS = {50053: "account locked (too many attempts) or blocked sign-in", 50126: "wrong user name or password",
          50055: "password expired", 50144: "on-premises AD password expired", 50057: "account disabled",
          50076: "MFA required", 50079: "MFA registration required", 50074: "MFA challenge not passed",
          53003: "blocked by Conditional Access"}


class GraphConfigError(RuntimeError):
    pass


class GraphConnector:
    def __init__(self, tenant_id: str | None = None, client_id: str | None = None, client_secret: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 15.0):
        # One app registration can serve both sign-in (ENTRA_*) and Graph reads; GRAPH_* overrides if set.
        self.tenant = tenant_id or os.getenv("GRAPH_TENANT_ID") or os.getenv("ENTRA_TENANT_ID", "")
        self.client_id = client_id or os.getenv("GRAPH_CLIENT_ID") or os.getenv("ENTRA_CLIENT_ID", "")
        self._secret = client_secret or os.getenv("GRAPH_CLIENT_SECRET") or os.getenv("ENTRA_CLIENT_SECRET", "")
        if not (self.tenant and self.client_id and self._secret):
            raise GraphConfigError("CONNECTOR_MODE=live needs ENTRA_TENANT_ID, ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET (or GRAPH_*)")
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._exp = 0.0

    def _auth(self) -> dict[str, str]:
        if not self._token or time.time() > self._exp - 60:
            r = self._http.post(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token",
                                data={"grant_type": "client_credentials", "client_id": self.client_id,
                                      "client_secret": self._secret, "scope": "https://graph.microsoft.com/.default"})
            if r.status_code != 200:
                raise GraphConfigError(f"token request failed (HTTP {r.status_code}); check app registration/consent")
            body = r.json()
            self._token, self._exp = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str) -> dict[str, Any]:
        r = self._http.get(GRAPH + path, headers=self._auth())
        if r.status_code == 403:
            return {"error": "forbidden: the app lacks the Graph permission for this call (see access guide)"}
        if r.status_code == 404:
            return {"error": "not found"}
        r.raise_for_status()
        return r.json()

    def account_status(self, upn: str) -> dict[str, Any]:
        """Read-only account facts for the signed-in user (real data)."""
        u = quote(upn)
        user = self._get(f"/users/{u}?$select=displayName,userPrincipalName,accountEnabled,department,jobTitle,"
                         "lastPasswordChangeDateTime,onPremisesSyncEnabled")
        if "error" in user:
            return {"ok": False, "source": "microsoft-graph", **user}
        groups = self._get(f"/users/{u}/transitiveMemberOf?$select=displayName&$top=50")
        signins = self._get(f"/auditLogs/signIns?$filter=userPrincipalName eq '{upn}'&$top=10")
        failures = []
        for s in (signins.get("value") or []):
            code = (s.get("status") or {}).get("errorCode") or 0
            if code:
                failures.append({"time": s.get("createdDateTime"), "app": s.get("appDisplayName"), "code": f"AADSTS{code}",
                                 "meaning": AADSTS.get(code, (s.get("status") or {}).get("failureReason", "")),
                                 "ip": s.get("ipAddress"), "device": (s.get("deviceDetail") or {}).get("displayName")})
        return {"ok": True, "source": "microsoft-graph (live)",
                "user": {k: user.get(k) for k in ("displayName", "userPrincipalName", "accountEnabled", "department",
                                                   "jobTitle", "lastPasswordChangeDateTime", "onPremisesSyncEnabled")},
                "groups": [g.get("displayName") for g in (groups.get("value") or []) if g.get("displayName")],
                "recent_signin_failures": failures,
                "signin_log_note": signins.get("error")}
