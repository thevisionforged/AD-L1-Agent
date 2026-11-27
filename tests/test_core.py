"""Unit tests: AD diagnostics, nested groups, escalation policy, retrieval, ticket rules."""
from __future__ import annotations

import pytest

from l1agent.diagnostics import check_group_access, escalation_policy, run_diagnostics
from l1agent.tickets import TicketValidationError


def codes(svc, uid):
    return {f["code"] for f in run_diagnostics(svc.directory, uid)["findings"]}


def test_lockout_with_source_device(offline_service):
    d = run_diagnostics(offline_service.directory, "u1001")
    rep = next(f for f in d["findings"] if f["code"] == "REPEATED_LOCKOUTS")
    assert rep["evidence"]["probable_source"] == "MOB-ARAO-01"
    assert rep["evidence"]["lockouts_24h"] == 3
    locked = next(f for f in d["findings"] if f["code"] == "LOCKED_OUT")
    assert 0 <= locked["evidence"]["minutes_until_unlock"] <= 30
    assert d["escalation_policy"]["escalation_required"] is True


@pytest.mark.parametrize("uid,expected", [
    ("u1002", {"PASSWORD_EXPIRED"}),
    ("u1003", {"PASSWORD_EXPIRING_SOON", "SSPR_NOT_REGISTERED"}),
    ("u1004", {"ACCOUNT_EXPIRED"}),
    ("u1007", {"ACCOUNT_DISABLED", "MFA_NOT_REGISTERED", "SSPR_NOT_REGISTERED"}),
    ("u1006", set()),
])
def test_diagnostic_findings(offline_service, uid, expected):
    assert codes(offline_service, uid) == expected


def test_escalation_policy_rules():
    assert escalation_policy({"ACCOUNT_EXPIRED"})["escalation_required"]
    assert escalation_policy({"PASSWORD_EXPIRED", "SSPR_NOT_REGISTERED"})["escalation_required"]
    assert not escalation_policy({"PASSWORD_EXPIRED"})["escalation_required"]
    assert not escalation_policy({"LOCKED_OUT"})["escalation_required"]


def test_nested_group_membership(offline_service):
    svc = offline_service
    r = check_group_access(svc.directory, svc.toolbox.resources, "u1006", "finance share")
    assert r["is_member"] and r["membership"].startswith("nested via GG-Finance-Managers")
    r2 = check_group_access(svc.directory, svc.toolbox.resources, "u1003", "\\\\fs01\\finance")
    assert not r2["is_member"] and r2["required_group"] == "GG-Finance-Share-RW"
    assert not check_group_access(svc.directory, svc.toolbox.resources, "u1003", "moon base")["ok"]


@pytest.mark.parametrize("query,article", [
    ("account locked out", "KB-AD-001"),
    ("password expired reset", "KB-AD-002"),
    ("new phone authenticator", "KB-AD-003"),
    ("access denied shared folder", "KB-AD-004"),
    ("vpn authentication failed", "KB-AD-005"),
    ("outlook profile", "KB-AD-006"),
    ("add printer", "KB-AD-007"),
    ("install software", "KB-AD-008"),
    ("trust relationship workstation primary domain failed", "KB-AD-009"),
    ("account expired contractor", "KB-AD-010"),
])
def test_bm25_top_hit(offline_service, query, article):
    hits = offline_service.kb.search(query, top_k=3)
    assert hits and hits[0][0].article_id == article


def test_priority_matrix_and_vip(offline_service):
    t = offline_service.tickets
    assert t.compute_priority("high", "high", False)[0] == "P1"
    assert t.compute_priority("low", "medium", False)[0] == "P4"
    prio, why = t.compute_priority("low", "medium", True)   # VIP bumps urgency medium -> high
    assert prio == "P3" and "VIP" in why


def test_ticket_validation(offline_service):
    with pytest.raises(TicketValidationError):
        offline_service.tickets.draft(requester_id="u1001", category="Payroll", subcategory="x", short_description="a",
                                      description="", impact="low", urgency="low", steps_attempted=[],
                                      diagnostic_codes=[], kb_references=[])
