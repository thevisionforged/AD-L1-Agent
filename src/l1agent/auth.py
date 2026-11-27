"""Authentication / authorisation modes.

AUTH_MODE=demo (default)
    Identity comes from the UI pickers (simulated SSO). For the hackathon demo only.
AUTH_MODE=easyauth (production)
    The app runs behind Azure App Service / Azure Container Apps built-in authentication
    ("Easy Auth") configured with Microsoft Entra ID. Easy Auth validates the token and forwards the
    signed-in principal in the X-MS-CLIENT-PRINCIPAL header (base64 JSON). Requests without it are
    rejected (401). The platform strips this header from client requests, so it cannot be forged
    from the browser; the app must therefore ONLY be reachable through Easy Auth (no public direct
    ingress).
      * End users: the UPN claim is mapped to the directory user.
      * Engineers: must hold the Entra app role AD.Engineer (read the Ops Console, triage) and
        AD.Approver (approve/reject actions), AND be registered in the engineer roster.
AUTH_MODE=entra (organisation roll-out, any host)
    The app itself signs users in with Microsoft Entra ID (OIDC + PKCE, see entra.py) using your app
    registration; identity comes from a signed session cookie. Same app roles as easyauth. Signed-in people
    who are not in the synthetic directory are "organisation users": they use the v2 agent, which reads
    their real account from Microsoft Graph.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

ENGINEER_ROLE = "AD.Engineer"
APPROVER_ROLE = "AD.Approver"
UPN_CLAIMS = ("preferred_username", "upn", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
              "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress", "email")


@dataclass
class Principal:
    upn: str
    name: str = ""
    roles: set[str] = field(default_factory=set)


def parse_easyauth(header_value: str | None) -> Principal | None:
    """Decode X-MS-CLIENT-PRINCIPAL. Returns None if missing or malformed."""
    if not header_value:
        return None
    try:
        data = json.loads(base64.b64decode(header_value + "=" * (-len(header_value) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    claims = data.get("claims") or []
    role_typ = data.get("role_typ", "roles")
    upn, name, roles = "", "", set()
    for c in claims:
        typ, val = c.get("typ"), c.get("val")
        if not isinstance(val, str):
            continue
        if typ in UPN_CLAIMS and not upn:
            upn = val.lower()
        elif typ == "name":
            name = val
        elif typ in (role_typ, "roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"):
            roles.add(val)
    return Principal(upn=upn, name=name, roles=roles) if upn else None


class Auth:
    def __init__(self, mode: str, directory, engineers: dict, oidc=None):
        if mode not in ("demo", "easyauth", "entra"):
            raise ValueError("AUTH_MODE must be 'demo', 'easyauth' or 'entra'")
        if mode == "entra" and oidc is None:
            raise ValueError("AUTH_MODE=entra needs the Entra OIDC configuration")
        self.mode = mode
        self.oidc = oidc
        self.org_users: dict[str, dict] = {}   # org user id -> {upn, name}
        self.directory = directory
        self.engineers = engineers
        fqdn = directory.policy.get("fqdn", "corp.example.com")
        self.upn_to_user = {f"{u['sam_account_name']}@{fqdn}".lower(): uid for uid, u in directory.users.items()}
        self.upn_to_eng = {e.get("upn", "").lower(): eid for eid, e in engineers.items() if e.get("upn")}

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    def principal(self, request: Request) -> Principal | None:
        if self.mode == "easyauth":
            return parse_easyauth(request.headers.get("x-ms-client-principal"))
        if self.mode == "entra":
            from .entra import SESSION_COOKIE
            d = self.oidc.signer.loads(request.cookies.get(SESSION_COOKIE))
            return Principal(upn=d["upn"], name=d.get("name", ""), roles=set(d.get("roles", []))) if d else None
        return None

    def _principal(self, request: Request) -> Principal:
        p = self.principal(request)
        if not p:
            raise HTTPException(status_code=401, detail="authentication required")
        return p

    @staticmethod
    def org_user_id(upn: str) -> str:
        return "org-" + hashlib.sha256(upn.lower().encode()).hexdigest()[:12]

    def end_user(self, request: Request, claimed_user_id: str | None, allow_org: bool = False) -> str:
        """allow_org: signed-in tenant users who are not in the synthetic directory get an org user id (v2 only)."""
        if self.demo:
            if not claimed_user_id or not self.directory.get(claimed_user_id):
                raise HTTPException(status_code=403, detail="unknown or unauthorised user")
            return claimed_user_id
        p = self._principal(request)
        uid = self.upn_to_user.get(p.upn)
        if not uid and allow_org:
            uid = self.org_user_id(p.upn)
            self.org_users[uid] = {"upn": p.upn, "name": p.name or p.upn}
        if not uid:
            raise HTTPException(status_code=403, detail="signed-in account is not in the CORP directory; use the v2 agent at /v2")
        if claimed_user_id and claimed_user_id != uid:
            raise HTTPException(status_code=403, detail="cannot act as another user")
        return uid

    def engineer(self, request: Request, claimed_engineer_id: str | None, need_approver: bool = False) -> str:
        if self.demo:
            eng = self.engineers.get(claimed_engineer_id or "")
            if not eng or "ad_engineer" not in eng["roles"]:
                raise HTTPException(status_code=403, detail="AD Ops Console is restricted to AD engineers")
            return claimed_engineer_id
        p = self._principal(request)
        if ENGINEER_ROLE not in p.roles:
            raise HTTPException(status_code=403, detail=f"Entra app role {ENGINEER_ROLE} required")
        if need_approver and APPROVER_ROLE not in p.roles:
            raise HTTPException(status_code=403, detail=f"Entra app role {APPROVER_ROLE} required")
        eid = self.upn_to_eng.get(p.upn)
        if not eid:
            raise HTTPException(status_code=403, detail="engineer is not in the AD team roster")
        return eid

    def session(self, request: Request) -> dict:
        if self.demo:
            return {"auth_mode": "demo"}
        p = self.principal(request)
        if not p:
            return {"auth_mode": self.mode, "signed_in": False}
        return {"auth_mode": self.mode, "signed_in": True, "upn": p.upn, "name": p.name,
                "user_id": self.upn_to_user.get(p.upn) or self.org_user_id(p.upn),
                "org_user": p.upn not in self.upn_to_user, "roles": sorted(p.roles), "engineer_id": self.upn_to_eng.get(p.upn)
                if ENGINEER_ROLE in p.roles else None, "approver": APPROVER_ROLE in p.roles}
