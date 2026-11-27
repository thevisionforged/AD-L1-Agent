"""Deterministic rule-based engine.

Used when no OpenAI key is configured (AGENT_MODE=offline), and automatically as
the demo fallback when the OpenAI API is unavailable (timeouts, 429 after capped
retries). It calls the SAME tools as the LLM engine and returns the SAME
structured reply, so the UI, grounding checks and tests behave identically.
"""
from __future__ import annotations

import re
from typing import Any

from .schemas import AgentReply, Citation
from .tools import ToolBox, ToolContext

# intent -> (keywords, KB article, category, subcategory)
INTENTS: dict[str, tuple[list[str], str, str, str]] = {
    "trust":    (["trust relationship", "primary domain failed", "domain trust"], "KB-AD-009", "Hardware/Endpoint", "Domain join"),
    "disabled": (["account has expired", "account expired", "account is disabled", "account has been disabled",
                  "account disabled", "contract", "contractor"], "KB-AD-010", "Account & Access", "Account disabled / expired"),
    "mfa":      (["mfa", "authenticator", "new phone", "lost phone", "lost my phone", "2fa", "verification code",
                  "multi-factor", "approve sign-in", "sign-in request"], "KB-AD-003", "Password & MFA", "MFA"),
    "lockout":  (["locked out", "lockout", "locked", "lock out", "keeps locking"], "KB-AD-001", "Account & Access", "Account lockout"),
    "password": (["password expired", "expired password", "change password", "change my password", "reset password",
                  "reset my password", "forgot", "password"], "KB-AD-002", "Password & MFA", "Password reset / change"),
    "access":   (["access to", "access denied", "access is denied", "permission", "shared folder", "share", "security group",
                  "\\\\", "need access", "folder", "add me to", "gg-", "group membership"], "KB-AD-004",
                 "Account & Access", "Access request"),
    "vpn":      (["vpn", "remote access"], "KB-AD-005", "Network & VPN", "VPN"),
    "outlook":  (["outlook", "outlook keeps asking", "mailbox", "email keeps", "mail profile"], "KB-AD-006", "Email & Collaboration", "Outlook"),
    "printer":  (["printer", "print "], "KB-AD-007", "Printing", "Printer setup"),
    "software": (["install", "software", "licence", "license", "application request"], "KB-AD-008", "Software", "Software request"),
    "login":    (["can't log in", "cannot log in", "can't login", "cannot login", "can't sign in", "cannot sign in",
                  "unable to log", "unable to sign", "log in", "login", "sign in", "logon", "log on"], "", "Account & Access", "Account lockout"),
}
FINDING_INTENT = {"ACCOUNT_DISABLED": "disabled", "ACCOUNT_EXPIRED": "disabled", "LOCKED_OUT": "lockout",
                  "REPEATED_LOCKOUTS": "lockout", "PASSWORD_EXPIRED": "password"}
IDENTITY_INTENTS = {"lockout", "password", "disabled", "login", "vpn", "outlook", "mfa", None}
URGENT = re.compile(r"\b(urgent|asap|immediately|right now|deadline|board meeting|client call|critical|cannot work|can't work)\b", re.I)
WIDE = re.compile(r"\b((whole|entire) (\w+ )?(team|office|floor|department)|everyone|all users|"
                  r"several (\w+ )?(users|colleagues|people)|many users|nobody can|no one can|\d{1,4} users)\b", re.I)
TRIED = re.compile(r"\b(already tried|tried (that|this|everything|the steps)|did ?n[o']t work|does ?n[o']t work|still (not|failing|locked))\b", re.I)
LOST = re.compile(r"\b(lost|stolen|broke|broken|no longer have|don'?t have (my|the) (old )?phone|factory reset)\b", re.I)
SELF_HELP_HEADINGS = ("self-help", "what to do", "request", "set up", "add", "change", "reset", "recreate",
                      "what you can try", "install", "after you", "authentication failed", "connected but", "repeated password")


def detect_intent(text: str) -> str | None:
    t = text.lower()
    best, best_score = None, 0
    for name, (kws, *_rest) in INTENTS.items():
        score = sum(len(k) for k in kws if k in t)
        if score > best_score:
            best, best_score = name, score
    return best


def _steps_from(text: str) -> list[str]:
    steps = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:\d+\.|-)\s+(.*)$", line)
        if m:
            steps.append(m.group(1).strip())
    return steps


def run_offline(toolbox: ToolBox, ctx: ToolContext, text: str, history: list[dict[str, str]]) -> AgentReply:
    intent = detect_intent(text)
    previous_clarify = bool(history) and history[-1].get("response_type") == "clarify"
    if intent is None and previous_clarify:
        # Combine with the earlier message so a one-line answer to our question still classifies.
        intent = detect_intent(" ".join(h["text"] for h in history if h["role"] == "user") + " " + text)

    findings: list[dict[str, Any]] = []
    if intent in IDENTITY_INTENTS:
        diag = toolbox.call(ctx, "consult_identity_diagnostics", {"problem": text})
        findings = diag.get("findings", []) if diag.get("ok") else []
    blocking = [f for f in findings if f["severity"] in ("critical", "high")]

    # Root cause from the directory beats the user's wording (e.g. "VPN fails" but password expired).
    root_cause_note = None
    if blocking and intent in {"login", "vpn", "outlook", "password", "lockout", "disabled", None}:
        derived = FINDING_INTENT.get(blocking[0]["code"])
        if derived and derived != intent:
            if intent in {"vpn", "outlook"}:
                root_cause_note = (f"Your {intent.upper() if intent == 'vpn' else 'Outlook'} problem is most likely caused by "
                                   f"your account: {blocking[0]['summary']}")
            intent = derived
    if intent == "login":
        intent = None  # sign-in problem with a healthy account: need more information

    facts = [f["summary"] for f in findings]

    # ---- clarify / unknown --------------------------------------------------------------
    if intent is None:
        if previous_clarify:
            return _escalate(toolbox, ctx, text, history, "Hardware/Endpoint", "Other", None, facts,
                             reason="I could not match the problem to a knowledge base article after a clarifying question.",
                             urgent=bool(URGENT.search(text)), wide=bool(WIDE.search(text)))
        return AgentReply(
            response_type="clarify",
            message=("I checked your account and found no lockout, expiry or password problem." if findings is not None and not blocking
                     and intent is None and ctx.diagnostics else "I want to make sure I point you to the right fix.")
                    + " Could you tell me a bit more?",
            clarifying_question="What exact error message do you see, and on which device (laptop or phone) and app?",
            facts_from_directory=facts, confidence="low")

    kws, article, category, subcategory = INTENTS[intent]
    search = toolbox.call(ctx, "search_kb", {"query": f"{text} {' '.join(kws[:3])}", "top_k": 5})
    if article and not any(r["source_id"].startswith(article) for r in search.get("results", [])):
        toolbox.call(ctx, "search_kb", {"query": toolbox.kb.article_sections(article)[0].title, "top_k": 5})

    sections = [c for c in toolbox.kb.article_sections(article) if c.source_id in ctx.retrieved_ids]
    help_secs = [c for c in sections if c.section.lower().startswith(SELF_HELP_HEADINGS)]

    # ---- access requests: check the group ---------------------------------------------------
    access = None
    if intent == "access":
        access = toolbox.call(ctx, "check_group_access", {"resource": text})
        if not access.get("ok"):
            return AgentReply(
                response_type="clarify",
                message="I can help you get access. I need to know exactly which resource you mean.",
                clarifying_question="Which shared folder or application do you need, for example \\\\fs01\\finance or 'sales share'?",
                citations=[Citation(source_id=c.source_id, title=ctx.retrieved_titles[c.source_id]) for c in sections[:1]],
                confidence="low")
        facts.append(f"Access to {access['resource_id']} is granted by AD group {access['required_group']}; "
                     f"you are {'already' if access['is_member'] else 'not'} a member. Approver: {access['approver']}.")
        if access["is_member"]:
            help_secs = [c for c in sections if c.source_id.endswith("#s3")]
        else:
            help_secs = [c for c in sections if c.source_id.endswith("#s2")]

    if intent == "mfa":
        help_secs = [c for c in sections if c.source_id.endswith("#s3" if LOST.search(text) else "#s2")]
    if intent == "password":
        want = "#s2" if any(f["code"] == "PASSWORD_EXPIRING_SOON" for f in findings) else "#s3"
        help_secs = [c for c in sections if c.source_id.endswith(want)] or help_secs

    steps: list[str] = []
    for c in help_secs:
        steps.extend(_steps_from(c.text))
    citations = [Citation(source_id=c.source_id, title=ctx.retrieved_titles[c.source_id]) for c in help_secs]

    # ---- decide whether to escalate ------------------------------------------------------------
    relevant_codes = {code for code, i in FINDING_INTENT.items() if i == intent} | {"MFA_NOT_REGISTERED", "SSPR_NOT_REGISTERED"}
    privileged = [f for f in findings if f["fix_requires_privileged_action"] and f["code"] in relevant_codes]
    sspr_missing = any(f["code"] == "SSPR_NOT_REGISTERED" for f in findings)
    reason = None
    rca = ctx.identity_analysis or {}
    if privileged:
        reason = (f"{rca.get('root_cause') or privileged[0]['summary']} Fixing this needs a privileged action by "
                  f"{rca.get('resolver_group') or toolbox.tickets.route(subcategory)}.")
    elif intent == "mfa" and LOST.search(text):
        reason = "Your MFA methods need to be reset after identity verification. Only IAM-L2 Identity Ops can do this."
    elif intent == "access" and access and not access["is_member"]:
        reason = f"Adding you to {access['required_group']} needs approval from {access['approver']}."
    elif intent == "trust":
        reason = "Rejoining the computer to the domain is a privileged action for EUC-L2 Desktop Support."
    elif intent == "password" and sspr_missing and any(f["code"] == "PASSWORD_EXPIRED" for f in findings):
        reason = "Your password has expired and you are not registered for self-service reset, so the service desk must verify you."
    elif TRIED.search(text) or (previous_clarify and TRIED.search(" ".join(h["text"] for h in history))):
        reason = "You have already tried the self-help steps."
    elif WIDE.search(text):
        reason = "Several users seem to be affected, which needs investigation by the resolver team."
    elif URGENT.search(text):
        reason = "You said this is urgent."

    if reason:
        return _escalate(toolbox, ctx, text, history, category, subcategory, article, facts, reason=reason,
                         urgent=bool(URGENT.search(text)) or bool(blocking),
                         wide=bool(WIDE.search(text)), steps=steps, citations=citations, root_cause_note=root_cause_note)

    lead = {
        "lockout": "Your account is locked out.",
        "password": "Here is how to sort out your password.",
        "mfa": "Here is how to move MFA to your new phone.",
        "access": "You already have the right group membership.",
        "vpn": "Here is how to set up and troubleshoot the VPN.",
        "outlook": "Here is how to fix Outlook.",
        "printer": "Here is how to add the printer.",
        "software": "Here is how to get the software.",
        "disabled": "Your account has an end date or has been disabled.",
        "trust": "This is a computer-account problem.",
    }[intent]
    detail = "" if root_cause_note else next((f["summary"] for f in blocking + findings if f["code"] in relevant_codes), "")
    msg = " ".join(x for x in [root_cause_note, lead, detail,
                               "Follow the steps below. If they do not work, tell me and I will prepare a ticket for you."] if x)
    return AgentReply(response_type="answer", message=msg, steps=steps, citations=citations,
                      facts_from_directory=facts, confidence="high" if blocking or intent not in IDENTITY_INTENTS else "medium",
                      assumptions=[] if findings or intent not in IDENTITY_INTENTS else ["Assumed the issue is on a CORP-managed device."])


def _escalate(toolbox: ToolBox, ctx: ToolContext, text: str, history: list[dict[str, str]], category: str,
              subcategory: str, article: str | None, facts: list[str], *, reason: str, urgent: bool, wide: bool,
              steps: list[str] | None = None, citations: list[Citation] | None = None,
              root_cause_note: str | None = None) -> AgentReply:
    user_texts = [h["text"] for h in history if h["role"] == "user"] + [text]
    tried = [t for t in user_texts if TRIED.search(t)]
    codes = [f["code"] for f in (ctx.diagnostics or {}).get("findings", [])]
    draft = toolbox.call(ctx, "draft_ticket", {
        "category": category, "subcategory": subcategory,
        "short_description": f"{subcategory}: {text[:90]}",
        "description": "User report: " + " / ".join(user_texts)[:600] + "\nDirectory findings: " + ("; ".join(facts) or "none")
                       + f"\nEscalation reason: {reason}",
        "impact": "high" if wide else "low",
        "urgency": "high" if urgent else "medium",
        "steps_attempted": tried or ["Self-help guidance provided by the L1 Virtual Agent"],
        "diagnostic_codes": codes,
        "kb_references": [c.source_id for c in (citations or [])]})
    draft_id = draft["draft"]["draft_id"] if draft.get("ok") else None
    msg = " ".join(x for x in [root_cause_note, reason,
                               "I have prepared a pre-filled ticket. Please review it and press Confirm to submit it, "
                               "or Cancel if you no longer need it."] if x)
    return AgentReply(response_type="escalate", message=msg, steps=steps or [], citations=citations or [],
                      facts_from_directory=facts, escalation_reason=reason, ticket_draft_id=draft_id,
                      confidence="high" if facts else "medium")
