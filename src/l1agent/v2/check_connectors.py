"""Check every v2 connector with the settings in .env, without starting the app.

    python -m l1agent.v2.check_connectors [user@yourcompany.com]

Prints OK / NOT CONFIGURED / FAILED (with the reason) for Entra sign-in, Microsoft Graph, ServiceNow and Teams.
Secrets are never printed. Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import os
import sys

from ..config import get_settings  # noqa: F401  (loads .env)


def main(argv: list[str]) -> int:
    upn = argv[1] if len(argv) > 1 else os.getenv("GRAPH_DEMO_UPN", "")
    bad = 0

    def line(name: str, status: str, detail: str = "") -> None:
        print(f"  {name:<22} {status:<15} {detail}")

    print("\nAD L1 Agent v2: connector check\n")
    # 1. Entra sign-in
    if os.getenv("AUTH_MODE", "demo").lower() == "entra":
        try:
            from ..entra import EntraOIDC
            o = EntraOIDC()
            o._jwks.get_signing_keys()
            line("Entra ID sign-in", "OK", f"tenant {o.tenant[:8]}..., redirect {o.redirect_uri}")
        except Exception as e:  # noqa: BLE001
            bad += 1; line("Entra ID sign-in", "FAILED", str(e)[:160])
    else:
        line("Entra ID sign-in", "NOT ENABLED", "set AUTH_MODE=entra")
    # 2. Graph
    if os.getenv("CONNECTOR_MODE", "mock").lower() == "live":
        try:
            from .graph_connector import GraphConnector
            g = GraphConnector()
            g._auth()
            detail = "token OK"
            if upn:
                r = g.account_status(upn)
                detail += f"; {upn}: " + ("read OK, " + str(len(r.get("groups", []))) + " groups" if r.get("ok") else r.get("error", "?"))
            line("Microsoft Graph", "OK", detail)
        except Exception as e:  # noqa: BLE001
            bad += 1; line("Microsoft Graph", "FAILED", str(e)[:160])
    else:
        line("Microsoft Graph", "NOT ENABLED", "set CONNECTOR_MODE=live")
    # 3. ServiceNow
    from .snow_connector import ServiceNowClient
    if ServiceNowClient.configured():
        try:
            c = ServiceNowClient()
            c.ping()
            detail = c.base
            if upn:
                detail += f"; caller {upn}: " + ("found" if c.caller(upn) else "NOT FOUND in sys_user")
            line("ServiceNow", "OK", detail)
        except Exception as e:  # noqa: BLE001
            bad += 1; line("ServiceNow", "FAILED", str(e)[:160])
    else:
        line("ServiceNow", "NOT CONFIGURED", "set SNOW_INSTANCE + credentials")
    # 4. Teams
    from .teams_connector import TeamsNotifier
    if TeamsNotifier.configured():
        try:
            TeamsNotifier()
            line("Teams channel", "OK", "webhook URL valid (a test card is sent only when a ticket is submitted)")
        except Exception as e:  # noqa: BLE001
            bad += 1; line("Teams channel", "FAILED", str(e)[:160])
    else:
        line("Teams channel", "NOT CONFIGURED", "set TEAMS_WEBHOOK_URL")
    print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
