"""AD Ops extension: triage decisions, approval gating (human-in-the-loop), insights, health, reports.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import csv

import pytest

from l1agent.approvals import ApprovalError
from l1agent.connectors import ControlledAdmin
from l1agent.ops import OpsService


@pytest.fixture
def ops(offline_service) -> OpsService:
    return OpsService(offline_service)


def admin_item(r):
    return next(a for a in r["proposed_actions"] if a["type"] == "admin_change")


def test_triage_lockout_proposes_unlock_with_least_privilege_role(ops):
    r = ops.triage("INC0010001")
    a = admin_item(r)
    assert a["payload"]["action"] == "unlock_account" and a["status"] == "pending"
    assert "Helpdesk Administrator" in a["required_role"]
    assert "MOB-ARAO-01" in r["deterministic_root_cause"]
    assert r["proposed_actions"][0]["type"] == "servicenow_work_note"


def test_nothing_changes_without_human_approval(ops):
    ops.triage("INC0010001")
    assert ops.core.directory.get("u1001")["locked_out"] is True
    assert ops.teams.outbox == [] and ops.approvals.admin.audit == []


def test_approved_unlock_executes_and_is_audited(ops):
    a = admin_item(ops.triage("INC0010001"))
    res = ops.approvals.approve(a["id"], "e9001")
    assert res["status"] == "executed" and ops.core.directory.get("u1001")["locked_out"] is False
    audit = ops.approvals.admin.audit[-1]
    assert audit["action"] == "unlock_account" and audit["approval"].startswith("APR-")
    assert any("approved by" in n["text"] for n in ops.snow.tickets["INC0010001"]["work_notes"])


def test_end_user_cannot_approve(ops):
    a = admin_item(ops.triage("INC0010001"))
    with pytest.raises(ApprovalError) as e:
        ops.approvals.approve(a["id"], "u1001")
    assert e.value.code == 403


def test_two_key_rule_for_group_membership(ops):
    r = ops.triage("INC0010002")
    assert "approval_stalled" in r["flags"]
    assert any(a["type"] == "servicenow_request_approval" for a in r["proposed_actions"])
    a = admin_item(r)
    with pytest.raises(ApprovalError, match="two-key"):
        ops.approvals.approve(a["id"], "e9001")
    ops.snow.record_business_approval("INC0010002", True, "group owner")
    assert ops.approvals.approve(a["id"], "e9001")["status"] == "executed"
    assert "GG-Finance-Share-RW" in ops.core.directory.get("u1003")["member_of"]


def test_mfa_reset_needs_identity_attestation(ops):
    a = admin_item(ops.triage("INC0010004"))
    assert a["payload"]["action"] == "reset_mfa_methods"
    with pytest.raises(ApprovalError, match="identity"):
        ops.approvals.approve(a["id"], "e9002")
    assert ops.approvals.approve(a["id"], "e9002", identity_verified=True)["status"] == "executed"


def test_privileged_group_request_never_becomes_admin_change(ops):
    r = ops.triage("INC0010012")
    assert "policy_violation_privileged_group" in r["flags"]
    assert not any(a["type"] == "admin_change" for a in r["proposed_actions"])
    item = ops.approvals.propose(type="admin_change", ticket="INC0010012", summary="x",
                                 payload={"action": "add_group_member", "user_id": "u1005", "group": "Domain Admins"},
                                 proposed_by="test")
    assert item["status"] == "denied_by_policy"
    with pytest.raises(ApprovalError):
        ops.approvals.approve(item["id"], "e9003")


def test_admin_connector_rejects_calls_without_token(ops):
    with pytest.raises(PermissionError):
        ops.approvals.admin.unlock_account("u1001", "APR-forged")
    assert set(ControlledAdmin.ROLE_FOR) >= {"unlock_account", "reset_mfa_methods", "add_group_member"}


def test_approval_tokens_are_single_use(ops):
    tok = ops.approvals._mint("unlock_account", "u1001")
    ops.approvals.admin.unlock_account("u1001", tok)
    with pytest.raises(PermissionError):
        ops.approvals.admin.unlock_account("u1001", tok)


def test_misrouted_ticket_rerouted_and_disabled_user_messages_manager(ops):
    r = ops.triage("INC0010008")
    assert "misrouted" in r["flags"]
    re_ = next(a for a in r["proposed_actions"] if a["type"] == "servicenow_reassign")
    ops.approvals.approve(re_["id"], "e9001")
    assert "INC0010008" not in {t["number"] for t in ops.snow.list_queue()}
    r2 = ops.triage("INC0010005")
    msg = next(a for a in r2["proposed_actions"] if a["type"] == "teams_message")
    assert msg["payload"]["user_id"] == "u1005"  # manager, because the user's account is disabled


def test_missing_info_and_wide_impact(ops):
    assert "missing_information" in ops.triage("INC0010011")["flags"]
    r = ops.triage("INC0010009")
    assert "wide_impact" in r["flags"]
    upd = next(a for a in r["proposed_actions"] if a["type"] == "servicenow_update")
    assert upd["payload"]["fields"]["priority"] == "P2"


def test_retriage_supersedes_old_proposals(ops):
    first = ops.triage("INC0010001")["proposed_actions"]
    ops.triage("INC0010001")
    assert all(ops.approvals.items[a["id"]]["status"] == "superseded" for a in first)


def test_insights_detects_every_stall_type(ops):
    m = ops.insights.metrics()
    kinds = set(m["stall_counts"])
    assert {"sla_breached", "approval_stalled", "waiting_on_user", "ping_pong", "unassigned_high_priority",
            "misrouted", "missing_information", "policy_violation"} <= kinds
    d = ops.insights.digest()
    assert d["engine"] == "deterministic" and d["digest"]["stuck_items"]
    assert {s["ticket"] for s in d["digest"]["stuck_items"]} <= {t["number"] for t in ops.snow.list_queue()}


def test_health_check_finds_planted_faults(ops):
    h = ops.health.run()
    assert h["overall"] == "RED"
    text = " ".join(f"{i['area']} {i['target']} {i['finding']}" for i in h["issues"])
    for needle in ["Replication", "Clock offset 312", "W32Time", "svc-legacy-backup", "Disabled account", "dcdiag"]:
        assert needle in text, needle


def test_daily_report_sheets_and_dl_mail_gated(ops):
    r = ops.daily_report()
    rows = list(csv.reader(open(r["daily_sheet"]["path"], encoding="utf-8-sig")))
    assert rows[0][0] == "Ticket" and len(rows) == len(ops.snow.list_queue()) + 1
    assert r["weekly_sheet"]["totals"]["opened"] == 71
    assert ops.mail.outbox == []                         # not sent until approved
    ops.approvals.approve(r["approval_item"]["id"], "e9003")
    assert ops.mail.outbox[-1]["to"].endswith("@corp.example.com")


def test_mail_only_to_internal_dl(ops):
    with pytest.raises(ValueError):
        ops.mail.send("someone@gmail.com", "x", "y", [], "e9001")


def test_eod_updates_bulk_approved_but_admin_changes_never_bulk(ops):
    a = admin_item(ops.triage("INC0010001"))
    e = ops.eod_updates()
    assert e["count"] == len(ops.snow.list_queue())
    res = ops.approvals.approve_many([i["id"] for i in e["items"]] + [a["id"]], "e9003")
    assert sum(1 for x in res if x["status"] == "executed") == e["count"]
    assert next(x for x in res if x["id"] == a["id"])["status"] == "skipped"
    assert any(n["text"].startswith("EOD status") for n in ops.snow.tickets["INC0010001"]["work_notes"])


def test_kb_write_requires_approval_then_is_searchable(ops):
    item = ops.propose_kb_from_ticket("INC0010009")
    assert item["status"] == "pending"
    assert not any(c.article_id == item["payload"]["article_id"] for c in ops.core.kb.chunks)
    ops.approvals.approve(item["id"], "e9003")
    hits = ops.core.kb.search("several finance users locked out after password change", top_k=3)
    assert hits[0][0].article_id == item["payload"]["article_id"]


def test_ops_api_rbac(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    import importlib

    from fastapi.testclient import TestClient

    import l1agent.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.get("/api/ops/queue").status_code == 403
    assert c.get("/api/ops/queue", headers={"X-Engineer-Id": "u1001"}).status_code == 403
    assert c.get("/api/ops/queue", headers={"X-Engineer-Id": "e9001"}).status_code == 200
    assert c.get("/api/ops/reports/..%2F..%2Fdirectory.json", headers={"X-Engineer-Id": "e9001"}).status_code == 404
