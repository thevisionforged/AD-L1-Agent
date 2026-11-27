"""AD L1 Agent v2 on the OpenAI Agents SDK (openai-agents 0.26.0).

* The MODEL drives the loop: it decides which tools to call and when to consult the Identity Diagnostics
  sub-agent (Agent.as_tool). Nothing is pre-scripted.
* Human approval is the SDK's own mechanism: `submit_ticket` is a function_tool(needs_approval=True). The run
  pauses with `result.interruptions`; the user approves or rejects in the UI; we call RunState.approve/reject and
  resume with Runner.run(agent, state).
* Existing guardrails run as an SDK input_guardrail; conversation memory is an SDK SQLiteSession; every run is
  traced (platform.openai.com > Logs > Traces, workflow "AD L1 Agent v2 (hack-grp-22)").
* Data: CONNECTOR_MODE=mock (default) uses the synthetic directory; CONNECTOR_MODE=live reads the signed-in
  user's real account from Microsoft Graph (graph_connector.py).
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agents import (Agent, GuardrailFunctionOutput, InputGuardrailTripwireTriggered, ModelSettings,
                    RunConfig, RunContextWrapper, Runner, SQLiteSession, function_tool, input_guardrail,
                    set_default_openai_key)
from agents.items import ToolCallItem, ToolCallOutputItem

from .. import guardrails
from ..agent import AgentService
from ..tools import ToolContext
from .graph_connector import GraphConnector

WORKFLOW = "AD L1 Agent v2 (hack-grp-22)"


@dataclass
class V2Context:
    user_id: str
    svc: AgentService
    tctx: ToolContext
    upn: str | None = None
    graph: GraphConnector | None = None
    guard_message: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


def _j(x: Any, n: int = 6000) -> str:
    return json.dumps(x, default=str)[:n]


# ------------------------------------------------------------------------------------------- tools (read)
@function_tool
def search_kb(w: RunContextWrapper[V2Context], query: str) -> str:
    """Search the IT knowledge base (CORP articles and Microsoft Entra/AD guidance). Returns sections with source_id to cite."""
    return _j(w.context.svc.toolbox.call(w.context.tctx, "search_kb", {"query": query}))


@function_tool
def check_my_account(w: RunContextWrapper[V2Context]) -> str:
    """Check the signed-in user's own account: locked out, password/account expiry, disabled, MFA/SSPR registration,
    recent sign-in failures. Live mode reads Microsoft Graph; otherwise the synthetic directory."""
    c = w.context
    if c.graph and c.upn:
        return _j(c.graph.account_status(c.upn))
    diag = c.svc.toolbox.call(c.tctx, "run_ad_diagnostics", {})
    events = c.svc.directory.events_for(c.user_id, within=timedelta(hours=24))
    diag["dc_events_24h"] = [{"event_id": e["event_id"], "time": e["time"].strftime("%Y-%m-%d %H:%M UTC"),
                              "dc": e["dc"], "source_device": e["caller_computer"], "failure": e.get("failure")} for e in events]
    diag["source"] = "synthetic directory (CONNECTOR_MODE=mock)"
    return _j(diag)


@function_tool
def get_my_profile(w: RunContextWrapper[V2Context]) -> str:
    """The signed-in user's directory profile (department, devices, groups)."""
    return _j(w.context.svc.toolbox.call(w.context.tctx, "get_user_context", {}))


@function_tool
def check_group_access(w: RunContextWrapper[V2Context], resource: str) -> str:
    """Which AD security group grants access to a share/app and whether the signed-in user is a member (nested groups resolved)."""
    return _j(w.context.svc.toolbox.call(w.context.tctx, "check_group_access", {"resource": resource}))


@function_tool
def my_tickets(w: RunContextWrapper[V2Context]) -> str:
    """The signed-in user's own tickets (submitted and drafts awaiting confirmation)."""
    t = w.context.svc.tickets
    return _j({"submitted": [{k: x[k] for k in ("number", "subcategory", "priority", "assignment_group", "status", "created_at")}
                             for x in t.for_user(w.context.user_id)],
               "draft_waiting": t.pending_for(w.context.user_id)})


# ------------------------------------------------------------------------------------------- tools (write)
@function_tool
def draft_ticket(w: RunContextWrapper[V2Context], category: str, subcategory: str, short_description: str,
                 description: str, impact: str, urgency: str) -> str:
    """Prepare a ticket DRAFT (not submitted). category one of: Account & Access, Password & MFA, Network & VPN,
    Email & Collaboration, Printing, Software, Hardware/Endpoint. subcategory one of: Account lockout, Access request,
    Account disabled / expired, Password reset / change, MFA, VPN, Outlook, Printer setup, Software request, Domain join,
    Other. impact/urgency: low|medium|high. Priority and team are computed by the system."""
    args = {"category": category, "subcategory": subcategory, "short_description": short_description,
            "description": description, "impact": impact, "urgency": urgency, "steps_attempted": [],
            "diagnostic_codes": [], "kb_references": sorted(w.context.tctx.retrieved_ids)[:5]}
    return _j(w.context.svc.toolbox.call(w.context.tctx, "draft_ticket", args))


@function_tool(needs_approval=True)
def submit_ticket(w: RunContextWrapper[V2Context], draft_id: str) -> str:
    """Submit a ticket draft to the support team. REQUIRES THE USER'S APPROVAL (the run pauses until they approve)."""
    try:
        t = w.context.svc.tickets.confirm(draft_id, w.context.user_id)
    except (KeyError, PermissionError) as e:
        return _j({"ok": False, "error": str(e)})
    return _j({"ok": True, "ticket": {k: t[k] for k in ("number", "subcategory", "priority", "assignment_group", "status")}})


# ------------------------------------------------------------------------------------------- guardrail
@input_guardrail
async def safety_guardrail(w: RunContextWrapper[V2Context], agent: Agent, user_input: Any) -> GuardrailFunctionOutput:
    text = user_input if isinstance(user_input, str) else next(
        (m.get("content") for m in reversed(user_input) if isinstance(m, dict) and m.get("role") == "user"), "")
    if not isinstance(text, str):
        text = str(text)
    others = [u["display_name"].split(" (")[0] for uid, u in w.context.svc.directory.users.items() if uid != w.context.user_id]
    g = guardrails.check_input(text, w.context.user_id, w.context.svc.settings.max_input_chars, others)
    w.context.guard_message = g.refusal_message
    return GuardrailFunctionOutput(output_info={"flags": g.flags}, tripwire_triggered=not g.allowed)


# ------------------------------------------------------------------------------------------- agents
IDENTITY_INSTRUCTIONS = """You are the Identity Diagnostics Agent, an Active Directory / Entra ID L3 specialist.
Call check_my_account, then explain the ROOT CAUSE using only the returned facts (quote times, devices, error codes),
say whether the fix needs an administrator (unlock after repeated lockouts, expired/disabled account, MFA reset
after a lost phone, group membership) and which team (IAM-L2 Identity Ops (AD) for identity). Be concise."""

L1_INSTRUCTIONS = """You are the CORP AD L1 Virtual Agent (v2), a friendly IT service-desk assistant for the signed-in user.
- Answer the question actually asked, warmly and briefly. Greetings or "what can you do" need no tools.
- For sign-in, lockout, password, expiry, MFA or VPN problems: consult the Identity Diagnostics Agent first.
- Give fix steps only from search_kb results and cite their source_id in brackets, e.g. [KB-AD-001#s3].
- If an administrator is needed: check my_tickets first (never create a duplicate); otherwise draft_ticket, show the
  user its priority and team, and ask whether to submit. Only call submit_ticket when the user says yes; the system
  will also ask them to approve it.
- For ticket status questions use my_tickets. Only the user's own account and tickets; never ask for passwords;
  IT topics only. You cannot unlock, reset or change anything yourself."""


def build_agents(model: str) -> Agent:
    settings = ModelSettings(temperature=0.2, max_tokens=700)
    identity = Agent(name="Identity Diagnostics Agent", instructions=IDENTITY_INSTRUCTIONS,
                     tools=[check_my_account], model=model, model_settings=settings)
    return Agent(
        name="AD L1 Agent v2", instructions=L1_INSTRUCTIONS, model=model, model_settings=settings,
        tools=[identity.as_tool(tool_name="consult_identity_diagnostics",
                                tool_description="Ask the AD L3 specialist sub-agent to diagnose the signed-in user's account."),
               search_kb, get_my_profile, check_group_access, my_tickets, draft_ticket, submit_ticket],
        input_guardrails=[safety_guardrail])


# ------------------------------------------------------------------------------------------- runtime
class V2Service:
    def __init__(self, svc: AgentService):
        self.svc = svc
        self.mode = os.getenv("CONNECTOR_MODE", "mock").lower()
        if svc.settings.online:
            set_default_openai_key(svc.settings.openai_api_key)
        self.agent = build_agents(svc.settings.model)
        self.db = str(svc.settings.runtime_dir / "v2_sessions.db")
        self.pending: dict[str, dict[str, Any]] = {}    # approval_id -> {state, ctx, conv, items}
        self._graph = None
        if self.mode == "live":
            self._graph = GraphConnector()   # raises GraphConfigError with a clear message if not configured

    @property
    def available(self) -> bool:
        return self.svc.settings.online

    def _ctx(self, user_id: str, upn: str | None) -> V2Context:
        return V2Context(user_id=user_id, svc=self.svc, tctx=ToolContext(user_id=user_id), upn=upn, graph=self._graph)

    def _pack(self, result, ctx: V2Context, conv: str) -> dict[str, Any]:
        calls = []
        for it in result.new_items:
            if isinstance(it, ToolCallItem):
                raw = it.raw_item
                calls.append({"tool": getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else "?"),
                              "arguments": (getattr(raw, "arguments", None) or (raw.get("arguments") if isinstance(raw, dict) else ""))[:300],
                              "agent": it.agent.name})
            elif isinstance(it, ToolCallOutputItem):
                if calls:
                    calls[-1]["output"] = str(it.output)[:400]
        out: dict[str, Any] = {"conversation_id": conv, "engine": "openai-agents-sdk", "model": self.svc.settings.model,
                               "connector_mode": self.mode, "tool_calls": calls, "trace_workflow": WORKFLOW}
        interruptions = list(getattr(result, "interruptions", []) or [])
        if interruptions:
            aid = "APR-" + uuid.uuid4().hex[:8].upper()
            self.pending[aid] = {"state": result.to_state(), "ctx": ctx, "conv": conv, "items": interruptions,
                                 "user_id": ctx.user_id}
            out.update({"status": "awaiting_approval", "approval_id": aid,
                        "approvals": [{"tool": i.tool_name or i.name, "arguments": i.arguments} for i in interruptions],
                        "reply": "I'm ready to submit this ticket. Please approve or reject it."})
        else:
            out.update({"status": "done", "reply": str(result.final_output)})
        out["tickets"] = [{k: t[k] for k in ("number", "subcategory", "priority", "assignment_group", "status")}
                          for t in self.svc.tickets.for_user(ctx.user_id)]
        out["draft"] = self.svc.tickets.pending_for(ctx.user_id)
        return out

    async def chat(self, user_id: str, message: str, conversation_id: str | None, upn: str | None = None) -> dict[str, Any]:
        conv = conversation_id or "V2-" + uuid.uuid4().hex[:10]
        ctx = self._ctx(user_id, upn)
        session = SQLiteSession(f"{user_id}:{conv}", self.db)
        try:
            result = await Runner.run(self.agent, message, context=ctx, session=session, max_turns=10,
                                      run_config=RunConfig(workflow_name=WORKFLOW, group_id=conv))
        except InputGuardrailTripwireTriggered:
            return {"conversation_id": conv, "engine": "openai-agents-sdk", "status": "refused", "tool_calls": [],
                    "reply": ctx.guard_message or "I can't help with that request.", "connector_mode": self.mode,
                    "tickets": [], "draft": None}
        return self._pack(result, ctx, conv)

    async def decide(self, approval_id: str, user_id: str, approve: bool) -> dict[str, Any]:
        p = self.pending.get(approval_id)
        if not p:
            raise KeyError("approval not found or already decided")
        if p["user_id"] != user_id:
            raise PermissionError("this approval belongs to another user")
        self.pending.pop(approval_id)
        state = p["state"]
        for item in p["items"]:
            (state.approve if approve else state.reject)(item)
        session = SQLiteSession(f"{user_id}:{p['conv']}", self.db)
        result = await Runner.run(self.agent, state, context=p["ctx"], session=session, max_turns=10,
                                  run_config=RunConfig(workflow_name=WORKFLOW, group_id=p["conv"]))
        return self._pack(result, p["ctx"], p["conv"])
