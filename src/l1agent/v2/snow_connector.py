"""LIVE ServiceNow connector for v2 (Table API). Enabled when SNOW_INSTANCE is set.

Auth (pick one; secrets only from environment variables):
  * OAuth client credentials: SNOW_CLIENT_ID + SNOW_CLIENT_SECRET  (POST /oauth_token.do, grant_type=client_credentials;
    needs an OAuth "Application Registry" entry with client credentials enabled on the instance)
  * Basic auth for an integration user:       SNOW_USERNAME + SNOW_PASSWORD
The integration user needs only the `itil` role (create/read incidents) and must be allowed to use REST.

Calls (ServiceNow Table API):
  GET  /api/now/table/sys_user?sysparm_query=email=<upn>^ORuser_name=<upn>      caller lookup
  GET  /api/now/table/sys_user_group?sysparm_query=name=<SNOW_ASSIGNMENT_GROUP>  group lookup (optional)
  POST /api/now/table/incident                                                  create incident
  GET  /api/now/table/incident?sysparm_query=caller_id=<sys_id>^active=true     caller's open incidents
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

LEVEL = {"high": "1", "medium": "2", "low": "3"}


class SnowConfigError(RuntimeError):
    pass


class ServiceNowClient:
    def __init__(self, instance: str | None = None, transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        inst = (instance or os.getenv("SNOW_INSTANCE", "")).strip().rstrip("/")
        if not inst:
            raise SnowConfigError("SNOW_INSTANCE is not set")
        self.base = inst if inst.startswith("https://") else f"https://{inst}.service-now.com"
        self._cid, self._csec = os.getenv("SNOW_CLIENT_ID", ""), os.getenv("SNOW_CLIENT_SECRET", "")
        self._user, self._pwd = os.getenv("SNOW_USERNAME", ""), os.getenv("SNOW_PASSWORD", "")
        if not ((self._cid and self._csec) or (self._user and self._pwd)):
            raise SnowConfigError("set SNOW_CLIENT_ID/SNOW_CLIENT_SECRET or SNOW_USERNAME/SNOW_PASSWORD")
        self.group_name = os.getenv("SNOW_ASSIGNMENT_GROUP", "")
        self._http = httpx.Client(timeout=timeout, transport=transport, headers={"Accept": "application/json"})
        self._token, self._exp = None, 0.0
        self._users: dict[str, str | None] = {}

    @staticmethod
    def configured() -> bool:
        return bool(os.getenv("SNOW_INSTANCE"))

    def _kw(self) -> dict[str, Any]:
        if self._cid and self._csec:
            if not self._token or time.time() > self._exp - 60:
                r = self._http.post(self.base + "/oauth_token.do", data={
                    "grant_type": "client_credentials", "client_id": self._cid, "client_secret": self._csec})
                if r.status_code != 200:
                    raise SnowConfigError(f"ServiceNow OAuth token failed (HTTP {r.status_code})")
                b = r.json()
                self._token, self._exp = b["access_token"], time.time() + int(b.get("expires_in", 1800))
            return {"headers": {"Authorization": f"Bearer {self._token}"}}
        return {"auth": (self._user, self._pwd)}

    def _req(self, method: str, path: str, **kw) -> dict[str, Any]:
        r = self._http.request(method, self.base + path, **kw, **self._kw())
        if r.status_code in (401, 403):
            raise SnowConfigError(f"ServiceNow refused the request (HTTP {r.status_code}); check the integration user's roles")
        r.raise_for_status()
        return r.json()

    def _sys_id(self, table: str, query: str) -> str | None:
        rows = self._req("GET", f"/api/now/table/{table}", params={
            "sysparm_query": query, "sysparm_fields": "sys_id", "sysparm_limit": "1"}).get("result", [])
        return rows[0]["sys_id"] if rows else None

    def caller(self, upn: str) -> str | None:
        if upn not in self._users:
            self._users[upn] = self._sys_id("sys_user", f"email={upn}^ORuser_name={upn}")
        return self._users[upn]

    def create_incident(self, upn: str | None, ticket: dict[str, Any]) -> dict[str, Any]:
        body = {"short_description": ticket["short_description"][:160],
                "description": (ticket.get("description", "") + "\n\nCreated by the AD L1 Agent v2 after the user approved it."
                                + (f"\nKB: {', '.join(ticket.get('kb_references', []))}" if ticket.get("kb_references") else "")),
                "category": "inquiry", "contact_type": "virtual_agent",
                "impact": LEVEL.get(ticket.get("impact", "medium"), "2"), "urgency": LEVEL.get(ticket.get("urgency", "medium"), "2")}
        caller = self.caller(upn) if upn else None
        if caller:
            body["caller_id"] = caller
        if self.group_name:
            gid = self._sys_id("sys_user_group", f"name={self.group_name}")
            if gid:
                body["assignment_group"] = gid
        res = self._req("POST", "/api/now/table/incident", json=body,
                        params={"sysparm_fields": "number,sys_id,priority,state", "sysparm_display_value": "true"})["result"]
        return {"number": res["number"], "sys_id": res["sys_id"], "priority": res.get("priority"), "state": res.get("state"),
                "url": f"{self.base}/nav_to.do?uri=incident.do?sys_id={res['sys_id']}", "caller_matched": bool(caller)}

    def open_incidents(self, upn: str) -> list[dict[str, Any]]:
        caller = self.caller(upn)
        if not caller:
            return []
        rows = self._req("GET", "/api/now/table/incident", params={
            "sysparm_query": f"caller_id={caller}^active=true^ORDERBYDESCsys_created_on",
            "sysparm_fields": "number,short_description,state,priority,assignment_group,sys_updated_on",
            "sysparm_display_value": "true", "sysparm_limit": "10"}).get("result", [])
        return [{"number": r["number"], "short_description": r.get("short_description"), "state": r.get("state"),
                 "priority": r.get("priority"), "assignment_group": (r.get("assignment_group") or {}).get("display_value")
                 if isinstance(r.get("assignment_group"), dict) else r.get("assignment_group"),
                 "updated": r.get("sys_updated_on")} for r in rows]

    def ping(self) -> dict[str, Any]:
        self._req("GET", "/api/now/table/incident", params={"sysparm_limit": "1", "sysparm_fields": "number"})
        return {"ok": True, "instance": self.base}
