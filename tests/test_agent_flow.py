"""Integration tests for the online orchestration (against the fake OpenAI server) and offline scenarios."""
from __future__ import annotations

import pytest

from conftest import final, rca


def test_online_tool_loop_with_identity_subagent(online_service, fake_openai):
    fake_openai.script = [
        rca("Phone MOB-ARAO-01 uses an old password",
            [{"fact": "3 lockouts", "ref": "REPEATED_LOCKOUTS"}, {"fact": "made up", "ref": "EVT-9999"}]),  # pre-flight sub-agent
        {"tool_calls": [("search_kb", {"query": "account locked out"})]},
        final(response_type="answer", message="Update your phone.", steps=["Update the phone"],
              citations=[{"source_id": "KB-AD-001#s3", "title": "x"}]),
    ]
    r = online_service.handle("u1001", "my account keeps locking")
    assert r["engine"] == "online"
    assert r["identity_analysis"]["engine"] == "llm"
    assert r["identity_analysis"]["ungrounded_evidence_dropped"] == ["EVT-9999"]
    assert [t["tool"] for t in r["tool_trace"]][:4] == ["kb_prefetch", "account_preflight", "consult_identity_diagnostics",
                                                       "draft_ticket"]
    # Deterministic escalation policy turned the answer into an escalation with a real draft.
    assert r["reply"]["response_type"] == "escalate" and r["ticket_draft"]
    assert any(t["tool"] == "policy_preflight" and "REPEATED_LOCKOUTS" in t["summary"] for t in r["tool_trace"])
    assert r["reply"]["citations"][0]["source_id"] == "KB-AD-001#s3"


def test_online_draft_ticket_grounding(online_service, fake_openai):
    fake_openai.script = [
        {"tool_calls": [("search_kb", {"query": "printer access denied"})]},
        {"tool_calls": [("draft_ticket", {"category": "Printing", "subcategory": "Printer setup",
                                          "short_description": "Printer access denied", "description": "d",
                                          "impact": "medium", "urgency": "medium", "steps_attempted": ["added printer"],
                                          "diagnostic_codes": ["INVENTED_CODE"],
                                          "kb_references": ["KB-AD-007#s2", "KB-FAKE#s1"]})]},
        final(response_type="escalate", message="Please review the draft and press Confirm."),
    ]
    r = online_service.handle("u1006", "printer says access denied for the whole team")
    d = r["ticket_draft"]
    assert d["priority"] == "P3" and d["assignment_group"] == "EUC-L2 Desktop Support"
    assert "INVENTED_CODE" not in d["diagnostic_codes"] and "KB-FAKE#s1" not in d["kb_references"]
    assert r["reply"]["ticket_draft_id"] == d["draft_id"]


@pytest.mark.parametrize("uid,msg,rtype,group", [
    ("u1001", "My account keeps getting locked out every few hours", "escalate", "IAM-L2 Identity Ops (AD)"),
    ("u1002", "VPN says authentication failed since this morning", "answer", None),
    ("u1003", "I need access to the finance share", "escalate", "IAM-L2 Identity Ops (AD)"),
    ("u1006", "I get access denied on the finance share", "answer", None),
    ("u1004", "I can't log in, it says my account has expired", "escalate", "IAM-L2 Identity Ops (AD)"),
    ("u1005", "I lost my phone and can't approve MFA. Urgent", "escalate", "IAM-L2 Identity Ops (AD)"),
    ("u1003", "The trust relationship between this workstation and the primary domain failed", "escalate", "EUC-L2 Desktop Support"),
    ("u1003", "How do I add a printer?", "answer", None),
    ("u1006", "Outlook keeps asking for my password", "answer", None),
])
def test_offline_scenarios(offline_service, uid, msg, rtype, group):
    r = offline_service.handle(uid, msg)
    assert r["reply"]["response_type"] == rtype
    if group:
        assert r["ticket_draft"]["assignment_group"] == group
    if rtype == "answer":
        assert r["reply"]["citations"] and r["reply"]["steps"]


def test_vip_priority_bump(offline_service):
    r = offline_service.handle("u1005", "I lost my phone and can't approve MFA")
    assert "VIP" in r["ticket_draft"]["priority_reason"]


def test_clarify_then_escalate(offline_service):
    r1 = offline_service.handle("u1006", "something is wrong with my screen")
    assert r1["reply"]["response_type"] == "clarify"
    r2 = offline_service.handle("u1006", "the display goes black randomly", conversation_id=r1["conversation_id"])
    assert r2["reply"]["response_type"] == "escalate" and r2["ticket_draft"]


@pytest.mark.parametrize("msg", ["What are your capabilities and limitations", "hi", "what can you do?",
                                 "Are you a bot or a real person?"])
def test_conversational_questions_answered_directly(offline_service, msg):
    # User feedback: "what are your capabilities" was hijacked by the lockout workflow and escalated.
    r = offline_service.handle("u1001", msg)
    assert r["reply"]["response_type"] == "answer"
    assert r["ticket_draft"] is None and offline_service.tickets.drafts == {}
    assert not any(t["tool"] in ("account_preflight", "consult_identity_diagnostics", "draft_ticket") for t in r["tool_trace"])
    if "hi" != msg:
        assert "only look at your own" in r["reply"]["message"] or "can't make changes" in r["reply"]["message"]
    assert "locked out" in r["reply"]["message"]          # gentle offer, not a takeover


def test_thanks_has_no_account_nag(offline_service):
    r = offline_service.handle("u1001", "thanks, that worked")
    assert r["reply"]["response_type"] == "answer" and "locked" not in r["reply"]["message"]


def test_online_conversational_turn_uses_no_tools(online_service, fake_openai):
    fake_openai.script = [final(message="I can check your own account and give cited steps. By the way, I can see your account is currently locked out. Would you like me to help with that?")]
    r = online_service.handle("u1001", "What are your capabilities and limitations")
    assert r["engine"] == "online" and r["reply"]["response_type"] == "answer"
    assert r["ticket_draft"] is None and r["tool_trace"] == []
    body = [q for q in fake_openai.requests if q["path"].endswith("/chat/completions")][0]["body"]
    assert "tools" not in body


def test_real_problem_still_escalates_after_conversation(offline_service):
    r1 = offline_service.handle("u1001", "hi")
    r2 = offline_service.handle("u1001", "My account keeps getting locked out", conversation_id=r1["conversation_id"])
    assert r2["reply"]["response_type"] == "escalate" and r2["ticket_draft"]
