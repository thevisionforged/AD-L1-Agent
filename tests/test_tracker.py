"""Major ticket tracker (incidents, changes, tasks) with approval-gated SharePoint / Teams channel publishing.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import pytest

from l1agent.ops import OpsService


@pytest.fixture
def ops(offline_service) -> OpsService:
    return OpsService(offline_service)


def test_tracker_selects_major_items(ops):
    tr = ops.major_tracker()
    nums = {r["Number"]: r for r in tr["rows"]}
    assert tr["counts"] == {"Incident": 8, "Change": 4, "Task": 3}
    assert "VIP caller" in nums["INC0010004"]["Why tracked"]
    assert "wide impact" in nums["INC0010009"]["Why tracked"]
    assert "approval stalled" in nums["INC0010002"]["Why tracked"]
    assert "missing test evidence" in nums["CHG0030101"]["Blocker"]
    assert "post-implementation review" in nums["CHG0030099"]["Blocker"]
    assert nums["SCTASK0040202"]["Blocker"].startswith("Waiting for HR")
    assert nums["INC0010012"]["Next action"].startswith("Reject")
    assert "INC0010010" not in nums                      # routine P4 within SLA is not "major"
    assert tr["rows"][0]["Priority/Risk"] in ("P1", "P2")  # most severe first


def test_publish_is_gated_and_idempotent(ops):
    r = ops.publish_tracker()
    sp, post = r["approval_items"]
    assert sp["type"] == "sharepoint_update" and post["type"] == "teams_channel_post"
    assert ops.sharepoint.items == {} and ops.channel.posts == []      # nothing published yet
    ops.approvals.approve(sp["id"], "e9003")
    ops.approvals.approve(post["id"], "e9003")
    assert len(ops.sharepoint.items) == 15 and "Major Ticket Tracker" in ops.channel.posts[-1]["channel"]
    r2 = ops.publish_tracker()
    res = ops.approvals.approve(r2["approval_items"][0]["id"], "e9001")
    assert "0 created, 15 updated" in res["result"]["result"]          # upsert by ticket number


def test_tracker_api_rbac(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    import importlib

    from fastapi.testclient import TestClient

    import l1agent.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.get("/api/ops/tracker", headers={"X-Engineer-Id": "u1001"}).status_code == 403
    r = c.post("/api/ops/tracker/publish", headers={"X-Engineer-Id": "e9003"}).json()
    assert r["csv"]["rows"] == 15 and all(i["status"] == "pending" for i in r["approval_items"])


def test_long_channel_post_can_be_approved_with_edits(tmp_path, monkeypatch):
    # E2E finding: the 15-row channel post (~3,000 chars) was rejected with 422 when sent back as edited_text.
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    import importlib

    from fastapi.testclient import TestClient

    import l1agent.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    h = {"X-Engineer-Id": "e9003"}
    r = c.post("/api/ops/tracker/publish", headers=h).json()
    post = r["approval_items"][1]
    assert len(post["payload"]["text"]) > 2000
    res = c.post(f"/api/ops/approvals/{post['id']}/approve", json={"edited_text": post["payload"]["text"]}, headers=h)
    assert res.status_code == 200 and res.json()["status"] == "executed"
