// AD Ops Console. Built by Samarth Pandey (hack-grp-22). XSS-safe: server values only via textContent.
"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = String(text); return e; };
let selected = null;
let authMode = "demo";

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "X-Engineer-Id": $("eng").value } });
  let data = null; try { data = await r.json(); } catch { /* */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });
function toast(parent, text, bad) { const d = el("div", bad ? "warnbox" : "card", text); parent.prepend(d); setTimeout(() => d.remove(), 6000); }
function busy(btn, on) { btn.disabled = on; if (on) { btn.dataset.t = btn.textContent; btn.textContent = "Working…"; } else if (btn.dataset.t) btn.textContent = btn.dataset.t; }

// ---------- tabs ----------
document.querySelectorAll(".otab").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".otab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".opane").forEach((p) => p.classList.toggle("hidden", p.id !== "p-" + b.dataset.p));
  if (b.dataset.p === "approvals") loadApprovals();
  if (b.dataset.p === "audit") loadAudit();
  if (b.dataset.p === "tracker") loadTracker();
});

async function init() {
  try { authMode = (await (await fetch("/api/session")).json()).auth_mode || "demo"; } catch { /* */ }
  const engs = await (await fetch("/api/ops/engineers")).json();
  engs.forEach((e) => { const o = el("option", null, e.name); o.value = e.engineer_id; $("eng").appendChild(o); });
  try { const h = await (await fetch("/api/health")).json(); $("mode").textContent = h.mode === "online" ? `Online · ${h.model}` : "Offline rules"; $("mode").className = "badge " + h.mode; } catch { /* */ }
  $("eng").onchange = () => { loadQueue(); };
  await loadQueue();
}

// ---------- queue ----------
async function loadQueue() {
  const q = await api("/api/ops/queue");
  $("group").textContent = q.assignment_group;
  const tb = $("queue").querySelector("tbody"); tb.replaceChildren();
  for (const t of q.tickets) {
    const tr = el("tr"); if (t.number === selected) tr.className = "sel";
    tr.appendChild(el("td", null, t.number));
    const p = el("td"); p.appendChild(el("span", "pill " + t.priority.toLowerCase(), t.priority)); tr.appendChild(p);
    tr.appendChild(el("td", null, t.state + (t.approval && t.approval.state === "requested" ? " ⏳" : "")));
    tr.appendChild(el("td", null, t.caller));
    tr.appendChild(el("td", null, t.short_description));
    tr.appendChild(el("td", null, t.age_hours + "h"));
    tr.appendChild(el("td", t.sla_used_pct >= 100 ? "slabad" : t.sla_used_pct >= 75 ? "slawarn" : null, t.sla_used_pct + "%"));
    tr.onclick = () => { selected = t.number; loadQueue(); showTicket(t.number); };
    tb.appendChild(tr);
  }
  refreshPendingCount();
}

async function showTicket(n, triageResult) {
  const d = $("detail"); d.replaceChildren(el("p", "muted", "Loading…"));
  const info = await api(`/api/ops/tickets/${encodeURIComponent(n)}`);
  const t = info.ticket, c = info.caller || {};
  d.replaceChildren();
  const bar = el("div", "bar"); bar.appendChild(el("h2", null, `${t.number} · ${t.priority} · ${t.state}`));
  const btns = el("span");
  const tri = el("button", "primary sm", "Run Queue Triage Agent"); tri.onclick = async () => { busy(tri, true); try { const r = await post(`/api/ops/tickets/${n}/triage`); await showTicket(n, r); loadQueue(); } catch (e) { toast(d, e.message, true); } busy(tri, false); };
  btns.appendChild(tri);
  if (authMode === "demo" && t.approval && t.approval.state === "requested") {
    const ba = el("button", "secondary sm", "Simulate approver: approve"); ba.title = "Demo only: the group owner / manager approves in ServiceNow";
    ba.onclick = async () => { await post(`/api/ops/tickets/${n}/business-approval`, { approved: true }); showTicket(n); loadQueue(); };
    btns.appendChild(ba);
  }
  const kb = el("button", "secondary sm", "Draft KB article"); kb.onclick = async () => { await post(`/api/ops/tickets/${n}/kb-proposal`); toast(d, "KB article draft sent to Approvals."); refreshPendingCount(); };
  btns.appendChild(kb); bar.appendChild(btns); d.appendChild(bar);

  const card = el("div", "card");
  card.appendChild(el("h3", null, t.short_description));
  card.appendChild(el("div", null, t.description));
  card.appendChild(el("div", "muted", `Caller ${c.display_name || t.caller} · ${c.department || ""}${c.vip ? " · VIP" : ""} · enabled ${c.account_enabled} · locked ${c.locked_out} · age ${t.age_hours} h · SLA ${t.sla_used_pct}% · reassigned ${t.reassignment_count}x`));
  if (t.approval) card.appendChild(el("div", "warnbox", `Business approval: ${t.approval.state} by ${t.approval.approver_role} (${t.approval.pending_hours} h, ${t.approval.reminders_sent || 0} reminders)`));
  d.appendChild(card);

  const r = triageResult || info.last_triage;
  if (r) {
    const a = el("div", "card");
    a.appendChild(el("h3", null, `Triage (${r.engine})${r.flags.length ? " · " + r.flags.join(", ") : ""}`));
    a.appendChild(el("div", null, r.summary));
    a.appendChild(el("div", null, "Root cause: " + r.root_cause));
    if (r.evidence.length) { const ul = el("ul"); r.evidence.forEach((x) => ul.appendChild(el("li", null, x))); a.appendChild(el("div", "muted", "Evidence (Entra / AD diagnostics):")); a.appendChild(ul); }
    if (r.sign_in_failures.length) { const ul = el("ul"); r.sign_in_failures.forEach((x) => ul.appendChild(el("li", null, `${x.time} ${x.event_id} ${x.dc} from ${x.source} ${x.failure}`))); a.appendChild(el("div", "muted", "Sign-in failures (Entra, 24 h):")); a.appendChild(ul); }
    if (r.permissions_required.length) {
      a.appendChild(el("div", "muted", "Permissions and approvals required:"));
      const ul = el("ul"); r.permissions_required.forEach((p) => ul.appendChild(el("li", null, `${p.action}: role ${p.least_privilege_role}; business approval: ${p.business_approval}${p.identity_verification ? "; identity verification required" : ""}`))); a.appendChild(ul);
    }
    if (r.kb_refs.length) a.appendChild(el("div", "muted", "KB: " + r.kb_refs.join(", ")));
    d.appendChild(a);
  }
  const acts = info.actions.filter((x) => x.status !== "superseded");
  if (acts.length) { d.appendChild(el("h2", null, "Proposed actions")); acts.forEach((x) => d.appendChild(actionCard(x, () => { showTicket(n); loadQueue(); }))); }
}

// ---------- approvals ----------
function actionCard(x, after, selectable) {
  const c = el("div", "action");
  const h = el("div", "hdr");
  const left = el("div");
  if (selectable && x.status === "pending" && x.type !== "admin_change") { const cb = el("input"); cb.type = "checkbox"; cb.value = x.id; cb.className = "bulkcb"; left.appendChild(cb); left.appendChild(document.createTextNode(" ")); }
  left.appendChild(el("b", null, x.summary)); h.appendChild(left);
  h.appendChild(el("span", "status-" + x.status, x.status));
  c.appendChild(h);
  c.appendChild(el("div", "type", `${x.id} · ${x.type} · risk ${x.risk} · ${x.ticket || "no ticket"} · by ${x.proposed_by}${x.required_role ? " · role " + x.required_role : ""}`));
  if (x.policy_note) c.appendChild(el("div", "warnbox", x.policy_note));
  if (x.needs_business_approval) c.appendChild(el("div", "muted", "Two-key rule: business approval on the ticket must be granted first."));
  const p = x.payload;
  let ta = null;
  if (p.text != null) { if (x.status === "pending") { ta = el("textarea"); ta.rows = 5; ta.value = p.text; c.appendChild(ta); } else c.appendChild(el("pre", null, p.text)); }
  if (p.body) c.appendChild(el("pre", null, `To: ${p.to}\nSubject: ${p.subject}\nAttachments: ${(p.attachments || []).join(", ")}\n\n${p.body}`));
  if (p.fields) c.appendChild(el("div", "muted", "Fields: " + JSON.stringify(p.fields)));
  if (p.action) c.appendChild(el("div", "muted", `Admin action: ${p.action} on ${p.user_id}${p.group ? " → " + p.group : ""}${p.days ? " (+" + p.days + " days)" : ""}`));
  if (p.group && !p.action) c.appendChild(el("div", "muted", "Reassign to: " + p.group));
  if (p.sections) c.appendChild(el("pre", null, `${p.article_id}: ${p.title}\n\n` + p.sections.map((s) => `## ${s.heading}\n${s.text}`).join("\n\n")));
  if (x.result) c.appendChild(el("div", x.result.ok ? "muted" : "warnbox", "Result: " + (x.result.result || x.result.error)));
  if (x.status === "pending") {
    const b = el("div", "btns"); let idv = null;
    if (x.needs_identity_attestation) { const l = el("label"); idv = el("input"); idv.type = "checkbox"; l.appendChild(idv); l.appendChild(document.createTextNode(" I verified the caller's identity (manager call-back)")); b.appendChild(l); }
    const ok = el("button", "primary sm", "Approve & execute"); const no = el("button", "secondary sm", "Reject");
    // On success the card replaces itself with the server's updated item, so the result is visible wherever
    // the card is shown; `after` (optional) then refreshes the surrounding view.
    ok.onclick = async () => {
      busy(ok, true);
      try {
        const res = await post(`/api/ops/approvals/${x.id}/approve`, { identity_verified: idv ? idv.checked : false, edited_text: ta ? ta.value : null });
        c.replaceWith(actionCard(res, after, selectable)); refreshPendingCount(); if (after) after();
      } catch (e) { busy(ok, false); toast(c, e.message, true); }
    };
    no.onclick = async () => {
      try {
        const res = await post(`/api/ops/approvals/${x.id}/reject`, { reason: "rejected by engineer" });
        c.replaceWith(actionCard(res, after, selectable)); refreshPendingCount(); if (after) after();
      } catch (e) { toast(c, e.message, true); }
    };
    b.append(ok, no); c.appendChild(b);
  }
  return c;
}

async function loadApprovals() {
  const items = await api("/api/ops/approvals?status=pending");
  const box = $("approvals"); box.replaceChildren();
  if (!items.length) box.appendChild(el("p", "muted", "Nothing waiting for approval."));
  items.forEach((x) => box.appendChild(actionCard(x, null, true)));
  refreshPendingCount();
}
$("bulk").onclick = async () => {
  const ids = [...document.querySelectorAll(".bulkcb:checked")].map((c) => c.value);
  if (!ids.length) { toast($("approvals"), "Select low/medium-risk items first (admin changes are never bulk-approved).", true); return; }
  await post("/api/ops/approvals/bulk-approve", { ids }); loadApprovals(); loadQueue();
};
async function refreshPendingCount() { try { const items = await api("/api/ops/approvals?status=pending"); $("pendcount").textContent = items.length; } catch { /* */ } }

$("triageall").onclick = async () => { const b = $("triageall"); busy(b, true); try { const r = await post("/api/ops/triage-all"); toast($("detail"), `Triaged ${r.length} ticket(s); proposals are in Approvals.`); } catch (e) { toast($("detail"), e.message, true); } busy(b, false); loadQueue(); };

// ---------- insights ----------
function kpi(parent, label, value) { const k = el("div", "kpi"); k.appendChild(el("b", null, value)); k.appendChild(el("span", null, label)); parent.appendChild(k); }
function renderDigest(box, r) {
  const m = r.metrics, d = r.digest;
  const k = el("div", "kpis");
  kpi(k, "open tickets", m.open_tickets); kpi(k, "stuck", m.tickets_stuck); kpi(k, "SLA breached", m.sla_breached); kpi(k, "SLA at risk", m.sla_at_risk);
  kpi(k, "unassigned", m.unassigned); kpi(k, "avg age (h)", m.avg_age_hours);
  box.appendChild(k);
  const c = el("div", "card"); c.appendChild(el("h3", null, `${d.headline} (${r.engine})`)); c.appendChild(el("div", null, d.summary)); box.appendChild(c);
  const t = el("table", "grid"); const hr = el("tr"); ["Ticket", "Why stuck", "Next step", "Owner"].forEach((h) => hr.appendChild(el("th", null, h)));
  const th = el("thead"); th.appendChild(hr); t.appendChild(th); const tb = el("tbody");
  d.stuck_items.forEach((s) => { const tr = el("tr"); [s.ticket, s.why_stuck, s.next_step, s.owner].forEach((v) => tr.appendChild(el("td", null, v))); tb.appendChild(tr); });
  t.appendChild(tb); box.appendChild(t);
  if (d.actions_for_today.length) { box.appendChild(el("h2", null, "Actions for today")); const ul = el("ul"); d.actions_for_today.forEach((a) => ul.appendChild(el("li", null, a))); box.appendChild(ul); }
  if (d.risks.length) { box.appendChild(el("h2", null, "Risks")); const ul = el("ul"); d.risks.forEach((a) => ul.appendChild(el("li", null, a))); box.appendChild(ul); }
}
$("digest").onclick = async () => { const b = $("digest"); busy(b, true); const box = $("insights"); box.replaceChildren(); try { renderDigest(box, await api("/api/ops/insights")); } catch (e) { toast(box, e.message, true); } busy(b, false); };

async function download(name) {
  const r = await fetch(`/api/ops/reports/${encodeURIComponent(name)}`, { headers: { "X-Engineer-Id": $("eng").value } });
  const blob = await r.blob(); const a = el("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove();
}
$("daily").onclick = async () => {
  const b = $("daily"); busy(b, true); const box = $("insights"); box.replaceChildren();
  try {
    const r = await post("/api/ops/daily-report");
    renderDigest(box, r.digest);
    const c = el("div", "card"); c.appendChild(el("h3", null, "Discussion sheets (CSV, opens in Excel)"));
    [r.daily_sheet, r.weekly_sheet].forEach((s) => { const l = el("button", "link", "⬇ " + s.file); l.onclick = () => download(s.file); c.appendChild(l); c.appendChild(el("br")); });
    c.appendChild(el("div", "muted", `Weekly: net backlog ${r.weekly_sheet.net_backlog_change >= 0 ? "+" : ""}${r.weekly_sheet.net_backlog_change}, approvals completed ${r.weekly_sheet.approval_completion_pct}%`));
    box.prepend(c);
    box.prepend(actionCard(r.approval_item, null));
  } catch (e) { toast(box, e.message, true); }
  busy(b, false); refreshPendingCount();
};
$("eod").onclick = async () => {
  const b = $("eod"); busy(b, true); const box = $("insights"); box.replaceChildren();
  try {
    const r = await post("/api/ops/eod");
    const c = el("div", "card"); c.appendChild(el("h3", null, `${r.count} EOD status notes drafted for ServiceNow`));
    const all = el("button", "primary sm", "Approve all EOD notes"); all.onclick = async () => { const res = await post("/api/ops/approvals/bulk-approve", { ids: r.items.map((i) => i.id) }); toast(box, `${res.filter((x) => x.status === "executed").length} work notes written to ServiceNow.`); refreshPendingCount(); };
    c.appendChild(all); box.appendChild(c);
    r.items.forEach((x) => box.appendChild(actionCard(x, null)));
  } catch (e) { toast(box, e.message, true); }
  busy(b, false); refreshPendingCount();
};

// ---------- major ticket tracker ----------
function renderTracker(box, tr) {
  const k = el("div", "kpis"); kpi(k, "incidents", tr.counts.Incident); kpi(k, "changes", tr.counts.Change); kpi(k, "tasks", tr.counts.Task);
  box.appendChild(k);
  box.appendChild(el("div", "muted", `As of ${tr.as_of} · SharePoint: ${tr.sharepoint.list} · Teams: ${tr.teams_channel.team} > ${tr.teams_channel.channel}`));
  const cols = ["Type", "Number", "Priority/Risk", "State", "Title", "Owner", "Why tracked", "Blocker", "Next action", "Next update due"];
  const t = el("table", "grid"); const hr = el("tr"); cols.forEach((c) => hr.appendChild(el("th", null, c)));
  const th = el("thead"); th.appendChild(hr); t.appendChild(th); const tb = el("tbody");
  tr.rows.forEach((r) => { const row = el("tr"); cols.forEach((c) => row.appendChild(el("td", null, r[c] || ""))); tb.appendChild(row); });
  t.appendChild(tb); box.appendChild(t);
}
async function loadTracker() { const box = $("tracker"); box.replaceChildren(); try { renderTracker(box, await api("/api/ops/tracker")); } catch (e) { toast(box, e.message, true); } }
$("refreshtracker").onclick = loadTracker;
$("publishtracker").onclick = async () => {
  const b = $("publishtracker"); busy(b, true); const box = $("tracker"); box.replaceChildren();
  try {
    const r = await post("/api/ops/tracker/publish");
    const c = el("div", "card"); c.appendChild(el("h3", null, "Ready to publish (pending your approval)"));
    const l = el("button", "link", "⬇ " + r.csv.file); l.onclick = () => download(r.csv.file); c.appendChild(l);
    box.appendChild(c);
    r.approval_items.forEach((x) => box.appendChild(actionCard(x, null)));
    renderTracker(box, r.tracker);
  } catch (e) { toast(box, e.message, true); }
  busy(b, false); refreshPendingCount();
};

// ---------- health ----------
$("runhealth").onclick = async () => {
  const box = $("health"); box.replaceChildren();
  const h = await api("/api/ops/health");
  const top = el("div", "bar"); top.appendChild(el("span", "overall " + h.overall, "Overall " + h.overall));
  top.appendChild(el("span", "muted", `${h.as_of} · DCs ${h.dcs_checked.join(", ")} · FSMO ${Object.entries(h.fsmo).map(([k, v]) => k + ": " + v.join("/")).join("; ")}`));
  box.appendChild(top);
  const k = el("div", "kpis"); Object.entries(h.counts).forEach(([s, n]) => kpi(k, s, n)); box.appendChild(k);
  const t = el("table", "grid"); const hr = el("tr"); ["Severity", "Area", "Target", "Finding", "Recommended action"].forEach((x) => hr.appendChild(el("th", null, x)));
  const th = el("thead"); th.appendChild(hr); t.appendChild(th); const tb = el("tbody");
  h.issues.forEach((i) => { const tr = el("tr"); const s = el("td"); s.appendChild(el("span", "pill sev-" + i.severity + "-t", i.severity)); tr.appendChild(s); [i.area, i.target, i.finding, i.recommended_action].forEach((v) => tr.appendChild(el("td", null, v))); tb.appendChild(tr); });
  t.appendChild(tb); box.appendChild(t);
};

// ---------- audit ----------
async function loadAudit() {
  const a = await api("/api/ops/audit"); const box = $("audit"); box.replaceChildren();
  const sec = (title, rows, fmt) => { box.appendChild(el("h2", null, title)); if (!rows.length) box.appendChild(el("p", "muted", "None yet.")); rows.forEach((r) => box.appendChild(el("div", "pre", fmt(r)))); };
  sec("Admin changes (controlled admin role)", a.admin_changes, (r) => `${r.at} ${r.action} ${r.user_id} via ${r.role_used}\nbefore ${JSON.stringify(r.before)}\nafter  ${JSON.stringify(r.after)}\napproval ${r.approval}`);
  sec("Approval decisions", a.approvals.slice(0, 30), (r) => `${r.at} ${r.event.toUpperCase()} ${r.id} ${r.type} ${r.ticket || ""} by ${r.decided_by || "agent"}: ${r.summary}`);
  sec("Teams messages sent", a.teams_outbox, (r) => `${r.at} to ${r.to} (approved by ${r.approved_by})\n${r.text}`);
  sec("Reports mailed", a.mail_outbox, (r) => `${r.at} to ${r.to}: ${r.subject} [${r.attachments.join(", ")}] approved by ${r.approved_by}`);
  sec("Teams channel posts", a.channel_posts, (r) => `${r.at} ${r.team} > ${r.channel} (approved by ${r.approved_by})\n${r.text}`);
}
$("refreshaudit").onclick = loadAudit;

init();
