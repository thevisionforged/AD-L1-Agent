"""LIVE Microsoft Teams connector for v2: posts an Adaptive Card to an AD team channel.

Uses a Teams *Workflows* webhook (Teams > channel > ... > Workflows > "Post to a channel when a webhook request
is received"). The webhook URL is a secret: set TEAMS_WEBHOOK_URL in .env only. No Graph permission is needed and
the agent can only post to that one channel. (Office 365 incoming-webhook URLs also accept this payload.)
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

ALLOWED_SUFFIXES = (".logic.azure.com", ".powerplatform.com", ".webhook.office.com", ".powerautomate.com")


class TeamsConfigError(RuntimeError):
    pass


class TeamsNotifier:
    def __init__(self, url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.url = url or os.getenv("TEAMS_WEBHOOK_URL", "")
        host = urlparse(self.url).hostname or ""
        if not self.url.startswith("https://") or not host.endswith(ALLOWED_SUFFIXES):
            raise TeamsConfigError("TEAMS_WEBHOOK_URL must be an https Teams Workflows / Power Automate webhook URL")
        self._http = httpx.Client(timeout=15.0, transport=transport)

    @staticmethod
    def configured() -> bool:
        return bool(os.getenv("TEAMS_WEBHOOK_URL"))

    def post_ticket(self, ticket: dict[str, Any], requester: str) -> dict[str, Any]:
        facts = [{"title": "Ticket", "value": ticket["number"]}, {"title": "Requester", "value": requester},
                 {"title": "Priority", "value": str(ticket.get("priority", ""))},
                 {"title": "Team", "value": str(ticket.get("assignment_group", ""))},
                 {"title": "Type", "value": str(ticket.get("subcategory", ""))}]
        body: list[dict[str, Any]] = [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "New ticket from the AD L1 Agent v2"},
            {"type": "TextBlock", "wrap": True, "text": str(ticket.get("short_description", ""))[:300]},
            {"type": "FactSet", "facts": facts},
            {"type": "TextBlock", "isSubtle": True, "wrap": True, "size": "Small",
             "text": "AI-drafted, submitted after the user approved it. Built by Samarth Pandey (hack-grp-22)."}]
        card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4", "body": body}
        if ticket.get("url"):
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in ServiceNow", "url": ticket["url"]}]
        payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                       "contentUrl": None, "content": card}]}
        r = self._http.post(self.url, json=payload)
        if r.status_code >= 300:
            return {"ok": False, "error": f"Teams webhook returned HTTP {r.status_code}"}
        return {"ok": True}
