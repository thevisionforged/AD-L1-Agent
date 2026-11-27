// AD L1 Agent v2 UI (OpenAI Agents SDK). Built by Samarth Pandey (hack-grp-22). Server values rendered with textContent only.
"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = String(text); return e; };
let conversationId = null;
let demoMode = true;
const SAMPLES = [
  ["u1001", "What can you do?"],
  ["u1001", "My account keeps getting locked out every few hours"],
  ["u1001", "Yes, create the ticket"],
  ["u1001", "Yes, submit it"],
  ["u1001", "What's the status of my ticket?"],
  ["u1002", "VPN says authentication failed since this morning"],
  ["u1003", "I need access to the finance share"],
  ["u1005", "I lost my phone and can't approve MFA"],
];

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let d = null; try { d = await r.json(); } catch { /* */ }
  if (!r.ok) throw new Error((d && d.detail) || `HTTP ${r.status}`);
  return d;
}
const userId = () => $("user").value;

async function init() {
  const info = await api("/api/v2/info");
  const m = $("mode");
  m.textContent = info.available ? `Agents SDK · ${info.model}` : "v2 unavailable";
  m.className = "badge " + (info.available ? "online" : "offline");
  $("model").textContent = info.model;
  const c = info.connectors || {};
  $("datasrc").textContent = `directory: ${c.directory || "synthetic directory"} · tickets: ${c.tickets || "built-in"} · Teams: ${c.teams || "not connected"}`;
  const sess = await api("/api/session");
  demoMode = sess.auth_mode === "demo";
  if (sess.signed_in) {
    const who = $("who"); who.replaceChildren(el("span", null, `Signed in: ${sess.name || sess.upn}`));
    if (sess.auth_mode === "entra") { const out = el("a", "link", "Sign out"); out.href = "/auth/logout"; who.append(" · ", out); }
    who.classList.remove("hidden");
  }
  if (!info.available) addBot({ reply: "v2 needs OPENAI_API_KEY in .env: it is a real OpenAI Agents SDK agent. " + (info.error || ""), status: "refused", tool_calls: [] });
  const users = await api("/api/users");
  users.forEach((u) => { const o = el("option", null, `${u.display_name} · ${u.department}`); o.value = u.user_id; $("user").appendChild(o); });
  const seen = new Set();
  SAMPLES.filter(([, t]) => demoMode || !seen.has(t) && seen.add(t)).forEach(([uid, t]) => { const b = el("button"); b.type = "button"; b.textContent = t; b.onclick = () => { if (demoMode && userId() !== uid) { $("user").value = uid; reset(); } $("input").value = t; $("input").focus(); }; $("samples").appendChild(b); });
  $("user").onchange = reset;
}
function reset() { conversationId = null; $("log").replaceChildren(); addBot({ reply: "Hi! Ask me anything about your sign-in, password, MFA, access or tickets.", tool_calls: [] }); $("activity").replaceChildren(); $("tickets").replaceChildren(el("p", "muted", "No tickets yet.")); }
function scroll() { const l = $("log"); l.scrollTop = l.scrollHeight; }
function addUser(t) { const m = el("div", "msg user"); m.appendChild(el("div", "bubble", t)); $("log").appendChild(m); scroll(); }
function addBot(res) {
  const m = el("div", "msg bot"); const b = el("div", "bubble");
  if (res.status === "refused") b.appendChild(el("span", "tag refuse", "refused by guardrail"));
  b.appendChild(el("div", null, res.reply));
  if (res.status === "awaiting_approval") b.appendChild(approvalCard(res));
  m.appendChild(b); $("log").appendChild(m); scroll();
}
function approvalCard(res) {
  const c = el("div", "approval");
  c.appendChild(el("b", null, "Approval needed (SDK human-in-the-loop)"));
  res.approvals.forEach((a) => c.appendChild(el("div", "muted", `${a.tool}(${a.arguments})`)));
  const bt = el("div", "btns"); const ok = el("button", "primary", "Approve"); const no = el("button", "secondary", "Reject");
  const decide = async (approve) => {
    ok.disabled = no.disabled = true;
    try { const r = await api(`/api/v2/approvals/${encodeURIComponent(res.approval_id)}`, { method: "POST", body: JSON.stringify({ user_id: userId(), approve }) }); c.appendChild(el("div", null, approve ? "Approved: the agent resumed." : "Rejected: the agent resumed.")); render(r, approve ? "approval granted" : "approval rejected"); }
    catch (e) { c.appendChild(el("div", "warnbox", e.message)); }
  };
  ok.onclick = () => decide(true); no.onclick = () => decide(false);
  bt.append(ok, no); c.appendChild(bt); return c;
}
function render(res, label) {
  addBot(res);
  const a = $("activity");
  a.appendChild(el("div", "turn-sep", label || "turn"));
  if (!res.tool_calls.length) a.appendChild(el("div", "muted", res.status === "refused" ? "Input guardrail tripped: no model or tool call." : "Answered directly (no tool needed)."));
  res.tool_calls.forEach((t) => {
    const c = el("div", "call"); c.appendChild(el("div", "who", t.agent));
    c.appendChild(el("div", "name", `→ ${t.tool}`));
    if (t.arguments && t.arguments !== "{}") c.appendChild(el("pre", null, t.arguments));
    if (t.output) c.appendChild(el("pre", null, "← " + t.output));
    a.appendChild(c);
  });
  if (res.status === "awaiting_approval") a.appendChild(el("div", "warnbox", "Run paused: waiting for your approval"));
  a.scrollTop = a.scrollHeight;
  const tk = $("tickets"); tk.replaceChildren();
  (res.tickets || []).forEach((t) => tk.appendChild(el("div", "t", `${t.number} · ${t.priority} · ${t.subcategory} → ${t.assignment_group} (${t.status})`)));
  if (res.draft) tk.appendChild(el("div", "t muted", `Draft waiting: ${res.draft.subcategory} · ${res.draft.priority}`));
  if (!tk.children.length) tk.appendChild(el("p", "muted", "No tickets yet."));
}
$("form").onsubmit = async (ev) => {
  ev.preventDefault();
  const t = $("input").value; if (!t.trim()) return;
  addUser(t); $("input").value = ""; $("send").disabled = true; $("send").textContent = "…";
  const t0 = performance.now();
  try {
    const r = await api("/api/v2/chat", { method: "POST", body: JSON.stringify({ user_id: userId(), message: t, conversation_id: conversationId }) });
    conversationId = r.conversation_id; render(r, `turn: "${t.slice(0, 40)}"`);
    $("meta").textContent = `${r.engine} · ${r.model} · ${Math.round(performance.now() - t0)} ms · ${r.tool_calls.length} tool call(s) · trace: ${r.trace_workflow}`;
  } catch (e) { addBot({ reply: "Error: " + e.message, tool_calls: [] }); }
  finally { $("send").disabled = false; $("send").textContent = "Send"; }
};
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); } });
$("newconv").onclick = reset;
init();
