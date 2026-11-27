"""Microsoft Foundry hosted-agent entrypoint (optional; Responses protocol on http://localhost:8088).

This follows the organizers' Foundry template (azure.yaml + Microsoft Agent Framework
`ResponsesHostServer`) and exposes the SAME tools, knowledge base, AD diagnostics,
guardrails and ticket-draft rules as the main web app. Run it locally with
`azd ai agent run` or `python src/foundry_host.py`. No Azure deployment is needed.

The hosted Responses protocol carries no end-user identity, so the signed-in user is
fixed by FOUNDRY_DEMO_USER_ID (default u1001). The browser demo (run.ps1) remains
the primary path because it provides per-user sessions and the Confirm button.

Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient  # noqa: E402
from agent_framework_foundry_hosting import ResponsesHostServer  # noqa: E402

from l1agent import guardrails  # noqa: E402
from l1agent.agent import PROMPT_PATH, AgentService  # noqa: E402
from l1agent.config import get_settings  # noqa: E402
from l1agent.tools import ToolContext  # noqa: E402

settings = get_settings()
if not settings.openai_api_key:
    raise RuntimeError("Set OPENAI_API_KEY in .env")
service = AgentService(settings)
USER_ID = os.getenv("FOUNDRY_DEMO_USER_ID", "u1001")


def _run(name: str, args: dict) -> str:
    ctx = ToolContext(user_id=USER_ID)
    return json.dumps(service.toolbox.call(ctx, name, args), default=str)[:6000]


def search_kb(query: Annotated[str, "Short search query"]) -> str:
    """Search the IT knowledge base; returns sections with source_id to cite."""
    return _run("search_kb", {"query": query})


def consult_identity_diagnostics(problem: Annotated[str, "One-sentence summary of the user's problem"]) -> str:
    """AD L3 diagnostics for the signed-in user: lockout source (4740), password expiry, expiry/disabled, MFA/SSPR, escalation policy."""
    if not guardrails.check_input(problem, USER_ID, settings.max_input_chars).allowed:
        return json.dumps({"ok": False, "error": "input rejected by guardrails"})
    return _run("consult_identity_diagnostics", {"problem": problem})


def check_group_access(resource: Annotated[str, "Resource name, e.g. 'finance share'"]) -> str:
    """Which AD group grants access to a resource and whether the signed-in user is a member (nested groups resolved)."""
    return _run("check_group_access", {"resource": resource})


def get_user_context() -> str:
    """Directory profile of the signed-in user (department, devices, groups)."""
    return _run("get_user_context", {})


agent = Agent(
    client=OpenAIChatClient(model=settings.model, api_key=settings.openai_api_key),
    name="ad-identity-l1-agent",
    description="UC-05 L1 Virtual Agent for Active Directory (hack-grp-22, Samarth Pandey)",
    instructions=PROMPT_PATH.read_text(encoding="utf-8")
    + "\n\nIn this hosted mode you cannot create ticket drafts. When escalation is needed, describe the pre-filled "
      "ticket fields and tell the user to confirm it in the service desk portal. Reply in plain text, not JSON.",
    tools=[search_kb, consult_identity_diagnostics, check_group_access, get_user_context],
)
server = ResponsesHostServer(agent)

if __name__ == "__main__":
    server.run(host="127.0.0.1", port=int(os.getenv("FOUNDRY_PORT", "8088")))
