"""Microsoft-grounded KB (KB-MS-*): attribution, audience separation, error-code handling, role accuracy.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

from pathlib import Path

import pytest

from l1agent.connectors import ControlledAdmin
from l1agent.ops import OpsService

MS_DIR = Path(__file__).resolve().parents[1] / "sample_data" / "kb_microsoft"


def test_every_ms_article_cites_public_microsoft_source(offline_service):
    arts = {c.article_id: c for c in offline_service.kb.chunks if c.article_id.startswith("KB-MS-")}
    assert len(arts) == 12
    for aid, c in arts.items():
        assert c.source_urls, aid
        assert all(u.startswith(("https://learn.microsoft.com/", "https://support.microsoft.com/")) for u in c.source_urls), aid
        assert c.audience in ("end user", "engineer"), aid
    assert "Creative Commons Attribution 4.0" in (MS_DIR / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("query", ["repadmin 1722 RPC server unavailable", "event 4740 caller computer name",
                                   "issue a temporary access pass", "Test-ComputerSecureChannel repair"])
def test_engineer_articles_never_reach_end_users(offline_service, query):
    user_hits = offline_service.kb.search(query, top_k=5, audience="end user")
    assert all(c.audience != "engineer" for c, _ in user_hits)
    eng_hits = offline_service.kb.search(query, top_k=5)
    assert any(c.audience == "engineer" for c, _ in eng_hits)


def test_search_tool_is_end_user_scoped(offline_service):
    from l1agent.tools import ToolContext
    ctx = ToolContext(user_id="u1006")
    r = offline_service.toolbox.call(ctx, "search_kb", {"query": "repadmin syncall dcdiag dns"})
    assert all(not x["source_id"].startswith(("KB-MS-008", "KB-MS-009", "KB-MS-010")) for x in r["results"])


@pytest.mark.parametrize("code,article", [("AADSTS50055", "KB-MS-001"), ("AADSTS50057", "KB-MS-001"),
                                          ("AADSTS50079", "KB-MS-001")])
def test_entra_error_codes_retrieved(offline_service, code, article):
    hits = offline_service.kb.search(f"I get error {code} when signing in", top_k=3, audience="end user")
    assert any(c.article_id == article for c, _ in hits)


def test_aadsts_lockout_code_routes_to_lockout_flow(offline_service):
    r = offline_service.handle("u1001", "Teams sign-in fails with AADSTS50053")
    assert r["reply"]["response_type"] == "escalate"
    assert r["ticket_draft"]["subcategory"] == "Account lockout"


def test_triage_work_note_cites_microsoft_runbook(offline_service):
    ops = OpsService(offline_service)
    r = ops.triage("INC0010001")
    assert "KB-MS-008#s4" in r["engineer_refs"]
    note = r["proposed_actions"][0]["payload"]["text"]
    assert "KB-MS-008#s4" in note
    teams = next(a for a in r["proposed_actions"] if a["type"] == "teams_message")["payload"]["text"]
    assert "KB-MS-008" not in teams and "4740" not in teams   # engineer procedure stays internal


def test_roles_match_microsoft_least_privilege_guidance():
    assert ControlledAdmin.ROLE_FOR["revoke_sessions"].startswith("Helpdesk Administrator")
    assert ControlledAdmin.ROLE_FOR["reset_mfa_methods"].startswith("Authentication Administrator")
    assert "Privileged Authentication Administrator" in ControlledAdmin.ROLE_FOR["reset_mfa_methods"]
    assert ControlledAdmin.ROLE_FOR["add_group_member"].startswith("Groups Administrator")
