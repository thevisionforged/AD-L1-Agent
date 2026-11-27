"""OpsService: wires the resolver-side connectors, approval center, agents and team routines.

Shares the Directory and KnowledgeBase with the end-user AgentService, so an approved admin
change (e.g. unlock) or KB publish is immediately visible to the L1 Virtual Agent too.
Built by Samarth Pandey (hack-grp-22).
"""
from __future__ import annotations

from typing import Any

from .agent import AgentService
from .approvals import ApprovalCenter
from .connectors import EntraReader, KBWriter, ServiceNowConnector, TeamsConnector
from .ops_agents import QueueInsightsAgent, QueueTriageAgent
from .ops_reports import HealthCheck, MailConnector, SheetBuilder, eod_note, report_body
from .tracker import ChannelConnector, MajorTracker, SharePointConnector


class OpsService:
    def __init__(self, core: AgentService):
        s = core.settings
        self.core = core
        self.snow = ServiceNowConnector(s.data_dir, s.runtime_dir)
        self.entra = EntraReader(core.directory)
        self.teams = TeamsConnector(s.runtime_dir)
        self.mail = MailConnector(s.runtime_dir)
        self.kbw = KBWriter(s.runtime_dir / "kb_overlay", core.kb)
        self.tracker = MajorTracker(s.data_dir, self.snow, s.runtime_dir / "reports")
        self.sharepoint = SharePointConnector(s.runtime_dir, self.tracker.sp_cfg["site"], self.tracker.sp_cfg["list"])
        self.channel = ChannelConnector(s.runtime_dir, self.tracker.channel_cfg["team"], self.tracker.channel_cfg["channel"])
        self.approvals = ApprovalCenter(self.snow, self.teams, self.kbw, core.directory, s.runtime_dir, mail=self.mail,
                                        sharepoint=self.sharepoint, channel=self.channel)
        self.triage_agent = QueueTriageAgent(self.snow, self.entra, core.directory, core.kb, self.approvals,
                                             core.toolbox.resources, core.llm)
        self.insights = QueueInsightsAgent(self.snow, core.llm)
        self.health = HealthCheck(s.data_dir, core.directory)
        self.sheets = SheetBuilder(s.data_dir, self.snow, s.runtime_dir / "reports")
        self.team_dl = self.health.data["team_dl"]
        self.last_triage: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- RBAC
    def require_engineer(self, engineer_id: str) -> dict[str, Any]:
        eng = self.snow.engineers.get(engineer_id)
        if not eng or "ad_engineer" not in eng["roles"]:
            raise PermissionError("AD Ops Console is restricted to AD engineers")
        return eng

    # ---------------------------------------------------------------- agents
    def triage(self, number: str) -> dict[str, Any]:
        r = self.triage_agent.triage(number)
        self.last_triage[number] = r
        return r

    def triage_all_new(self) -> list[dict[str, Any]]:
        out = []
        for t in self.snow.list_queue():
            if t["state"] == "New" or t["number"] not in self.last_triage:
                r = self.triage(t["number"])
                out.append({"ticket": t["number"], "summary": r["summary"], "actions": len(r["proposed_actions"]),
                            "flags": r["flags"]})
        return out

    def propose_kb_from_ticket(self, number: str) -> dict[str, Any]:
        """KB write: turn a triaged ticket's root cause and fix into a draft article (published only after approval)."""
        r = self.last_triage.get(number) or self.triage(number)
        existing = {c.article_id for c in self.core.kb.chunks}
        n = 11
        while f"KB-AD-{n:03d}" in existing:
            n += 1
        art = f"KB-AD-{n:03d}"
        t = r["ticket"]
        sections = [
            {"heading": "Symptoms", "text": f"- {t['short_description']}\n- {t['description'][:300]}"},
            {"heading": "Root cause", "text": r["deterministic_root_cause"]},
            {"heading": "Resolution", "text": "\n".join(f"{i}. {s}" for i, s in enumerate(r["user_steps"] or
                       [p["summary"] for p in r["proposed_actions"] if p["type"] != "servicenow_work_note"], 1))},
            {"heading": "Permissions and approvals", "text": "\n".join(
                f"- {p['action']}: {p['least_privilege_role']} (business approval: {p['business_approval']})"
                for p in r["permissions_required"]) or "- No privileged action needed."},
        ]
        return self.approvals.propose(type="kb_publish", ticket=number, summary=f"Publish {art} from {number}",
                                      payload={"article_id": art, "title": f"{t['short_description'][:70]} (from {number})",
                                               "sections": sections},
                                      proposed_by="KB Curator", rationale="Knowledge gap found while triaging.")

    # ---------------------------------------------------------------- routines
    def daily_report(self, include_health: bool = True) -> dict[str, Any]:
        digest = self.insights.digest()
        health = self.health.run() if include_health else None
        daily = self.sheets.daily(digest["metrics"])
        weekly = self.sheets.weekly()
        body = report_body(digest, health)
        item = self.approvals.propose(
            type="email_report", ticket=None, summary=f"Mail daily AD report to {self.team_dl}",
            payload={"to": self.team_dl, "subject": f"AD Identity Ops daily report - {digest['metrics']['as_of']}",
                     "body": body, "attachments": [daily["file"], weekly["file"]]},
            proposed_by="Queue Insights Agent", rationale="Daily stand-up report for the AD team DL.")
        return {"digest": digest, "health": health, "daily_sheet": daily, "weekly_sheet": weekly,
                "mail_preview": body, "approval_item": item}

    def eod_updates(self) -> dict[str, Any]:
        m = self.insights.metrics()
        items = []
        for t in self.snow.list_queue():
            stalls = [s for s in m["stalls"] if s["ticket"] == t["number"]]
            acts = [a for a in self.approvals.pending(t["number"]) if a["type"] != "servicenow_work_note"
                    or not a["summary"].startswith("EOD")]
            note = eod_note(t, stalls, acts)
            items.append(self.approvals.propose(type="servicenow_work_note", ticket=t["number"],
                                                summary=f"EOD status note for {t['number']}", payload={"text": note},
                                                proposed_by="EOD Status Agent"))
        return {"count": len(items), "items": items}

    # ---------------------------------------------------------------- major ticket tracker
    def major_tracker(self) -> dict[str, Any]:
        vips = {uid for uid, u in self.core.directory.users.items() if u.get("vip")}
        return self.tracker.build(self.insights.metrics()["stalls"], vips)

    def publish_tracker(self) -> dict[str, Any]:
        """Builds the tracker, exports CSV, and PROPOSES the SharePoint upsert and Teams channel post for approval."""
        tr = self.major_tracker()
        csv_file = self.tracker.export_csv(tr)
        sp = self.approvals.propose(
            type="sharepoint_update", ticket=None,
            summary=f"Update SharePoint list '{tr['sharepoint']['list']}' ({len(tr['rows'])} rows)",
            payload={"site": tr["sharepoint"]["site"], "list": tr["sharepoint"]["list"], "rows": tr["rows"]},
            proposed_by="Major Tracker Agent", rationale="Keep the AD major ticket tracker current.")
        post = self.approvals.propose(
            type="teams_channel_post", ticket=None,
            summary=f"Post tracker to Teams: {tr['teams_channel']['team']} > {tr['teams_channel']['channel']}",
            payload={"text": self.tracker.channel_text(tr)},
            proposed_by="Major Tracker Agent", rationale="Share major incidents, changes and tasks with the AD team.")
        return {"tracker": tr, "csv": csv_file, "approval_items": [sp, post]}
