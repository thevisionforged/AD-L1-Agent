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
    assert [t["tool"] for t in r["tool_trace"]][:3] == ["kb_prefetch", "account_preflight", "consult_identity_diagnostics"]
    # Deterministic escalation policy turned the answer into an escalation with a real draft.
    assert r["reply"]["response_type"] == "escalate" and r["ticket_draft"]
    assert any(f.startswith("policy_escalation_enforced:REPEATED_LOCKOUTS") for f in r["flags"])
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
