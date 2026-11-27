# Production Deployment Guide: AD Identity L1 Virtual Agent + AD Ops Agents

| | |
|---|---|
| Owner | Samarth Pandey (hack-grp-22) |
| Version | 1.0 (27-Nov-2026) |
| Audience | IAM / AD engineering, platform engineering, security architecture, service management |
| Status | Hackathon build hardened for production. **Enterprise connectors are mocks** and must be implemented against the real APIs before go-live (section 4). |

---

## 1. What is production-ready today, and what is not

| Area | State in this release | Evidence |
|---|---|---|
| Agent logic, guardrails, escalation policy, approval gate | **Ready** | 128 automated tests; live regression suite (`eval/regression_live.py`) |
| Authentication | **Ready**: `AUTH_MODE=easyauth` (Entra ID via App Service / Container Apps auth, app roles `AD.Engineer`, `AD.Approver`) | `tests/test_production.py` |
| Observability basics | **Ready**: JSON access logs with request IDs, per-turn audit JSONL, `/healthz`, `/readyz` | `api.py` |
| Kill switch | **Ready**: `ACTIONS_ENABLED=false` freezes all execution | `test_kill_switch_freezes_execution` |
| Container | **Ready to build**: `Dockerfile` (non-root, health check). Not built in the hackathon sandbox (no Docker daemon) | `Dockerfile` |
| Enterprise connectors (Entra, AD DS admin, ServiceNow, Teams, Outlook, SharePoint) | **Not ready: mocked.** Interfaces match the real APIs | `connectors.py`, `tracker.py`, `ops_reports.py` |
| State persistence | **Not ready**: in-memory + JSON files (single replica only) | section 5 |
| Load / soak testing | **Not done** | section 9 |

Rule for go-live: every "Not ready" row must be closed and pass the gates in section 9.

---

## 2. Target architecture (Azure)

```
Users / AD engineers (browser, Entra ID SSO)
   │  HTTPS
Azure Front Door + WAF (optional, recommended)
   │
Azure Container Apps (or App Service)  ── built-in authentication: Microsoft Entra ID (Easy Auth)
   │  container: ad-l1-agent (this repo, AUTH_MODE=easyauth, ACTIONS_ENABLED=true)
   │  managed identity ─────────────► Azure Key Vault (OpenAI key / ServiceNow secret / certs)
   │                   ─────────────► Azure OpenAI (or OpenAI API) - private endpoint where available
   │                   ─────────────► Azure SQL / Cosmos DB (conversations, drafts, approvals, audit)
   │                   ─────────────► Azure Cache for Redis (rate limits, idempotency)
   │                   ─────────────► Microsoft Graph (Entra read: Global Reader; Mail.Send; Sites.Selected)
   │                   ─────────────► ServiceNow Table API (integration user)
   │                   ─────────────► Hybrid worker (Azure Arc / Automation Hybrid Runbook Worker) in the AD site
   │                                      └─ PowerShell (ActiveDirectory module) under a gMSA with delegated OU rights
Log Analytics + Application Insights (logs, metrics, alerts)
```

Design principles carried into production:

- **Only the backend calls models and APIs.** The browser never receives keys or tokens.
- **Agents propose, humans approve.** Every write goes through the Approval Center: a single-use token, then the connector, then the audit log.
- **Rules decide, the LLM drafts.** Account state, escalation, priority, routing and allowed actions are deterministic code.

---

## 3. Identity and access design

### 3.1 App registration (the web app)

1. Register `ad-l1-agent` in Microsoft Entra ID. Redirect URI: `https://<app-host>/.auth/login/aad/callback`.
2. Create **app roles**: `AD.Engineer` (open the Ops Console, triage, propose) and `AD.Approver` (approve/reject actions). Assign them to Entra **groups** (for example `SG-AD-Ops-Engineers`, `SG-AD-Ops-Approvers`), not to individuals.
3. Enable **assignment required** on the enterprise app, so only assigned users and groups can sign in.
4. Enable Container Apps / App Service **authentication** with this registration, set "Require authentication", and forward the principal header. Do not expose any other ingress: the app trusts `X-MS-CLIENT-PRINCIPAL` only because the platform strips it from client requests.
5. Set `AUTH_MODE=easyauth`. In this mode the picker endpoints return only the signed-in identity, Swagger docs are disabled, the demo-only "simulate approver" endpoint returns 404, and HSTS is added.
6. Maintain the engineer roster (UPN → engineer ID) in configuration. The app requires **both** the app role and roster membership.

### 3.2 Service identities: least privilege (Microsoft guidance, see KB-MS-007)

| Capability | Production identity | Least-privileged permission |
|---|---|---|
| Read users, groups, sign-in failures (Entra) | Managed identity → Graph | Directory read (`User.Read.All`, `GroupMember.Read.All`); sign-in logs `AuditLog.Read.All` (Reports Reader equivalent). Global Reader only if broader read is required |
| On-prem AD reads and DC events 4740/4625 | Hybrid worker gMSA | Read on user OUs; Event Log Readers on DCs (or read from Sentinel/Log Analytics) |
| Unlock account (AD DS) | Hybrid worker gMSA | Delegated "write lockoutTime" on user OUs only |
| Cloud lockout / password reset | Executor managed identity | Helpdesk or Password Administrator (non-admins), **scoped to an administrative unit** |
| MFA method reset / TAP | Executor managed identity | Authentication Administrator (non-admins only; never Privileged Authentication Administrator), AU-scoped |
| Group membership | Hybrid worker gMSA / Graph | Delegated "write member" on the share-group OU, or Groups Administrator scoped by an **administrative unit** |
| Account expiry / enable | Hybrid worker gMSA | Delegated write `accountExpires` / `userAccountControl` on user OUs |
| Revoke sessions | Service principal | Helpdesk Administrator (non-admins) |
| Privileged groups (Domain Admins etc.) | **None** | Denied by policy in code; PIM/CAB only |

Additional controls:

- Humans use **PIM** (just-in-time, approval, MFA). The executor identity holds only AU-scoped roles and is exercised only by the Approval Center.
- Separate identities for read and write paths.
- Monitoring of the workload identities' sign-ins.

Step-by-step registration, app roles, group assignment, Graph permissions, Exchange/SharePoint scoping, OU delegation and a validation checklist are in **[ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md](ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md)**.

---

## 4. Replacing the mock connectors

Each mock exposes the same methods the agents call; implement them against the real system and keep the method signatures.

| Mock (file) | Real implementation | Notes |
|---|---|---|
| `EntraReader` (`connectors.py`) | Microsoft Graph `/users`, `/users/{id}/memberOf` (transitive), `/auditLogs/signIns` | Read-only identity. Map `onPremisesSamAccountName`. Effective groups via `transitiveMemberOf` |
| `Directory` + `run_diagnostics` (`directory.py`, `diagnostics.py`) | AD DS via hybrid worker: `Get-ADUser -Properties lockedOut,badPwdCount,pwdLastSet,accountExpires,msDS-UserPasswordExpiryTimeComputed`; 4740/4625 from the PDC emulator or Sentinel | Keep diagnostics deterministic; only the data source changes |
| `ControlledAdmin` (`connectors.py`) | Hybrid runbooks: `Unlock-ADAccount`, `Add-ADGroupMember`, `Set-ADAccountExpiration`, `Enable-ADAccount`; Graph for MFA methods / TAP / revoke sessions | Must keep the `approval_token` check; log before/after state |
| `ServiceNowConnector` (`connectors.py`) | ServiceNow Table API: `incident`, `sc_task`, `change_request`, `sysapproval_approver`; work notes via `work_notes` field | Integration user with ITIL role scoped to the AD assignment group. Business approvals arrive by webhook (replaces the demo button) |
| `TeamsConnector` (1:1 chat) | Microsoft Graph chat messages (delegated) or a Teams bot (Bot Framework) | App-only chat posting is restricted; a bot is the supported pattern |
| `ChannelConnector` (`tracker.py`) | Teams **Workflows** webhook or a bot posting an Adaptive Card to the AD channel | |
| `MailConnector` (`ops_reports.py`) | Graph `sendMail` from a shared mailbox | `Mail.Send` application permission restricted to that mailbox (Exchange RBAC for Applications / application access policy) |
| `SharePointConnector` (`tracker.py`) | Graph `sites/{id}/lists/{list}/items` | `Sites.Selected` with write on the one site only |
| `KBWriter` | ServiceNow Knowledge (`kb_knowledge`) or SharePoint pages; re-index after publish | Keep the approval gate and the "AI-drafted" label |

Contract tests: for each connector, add tests that run the same scenarios as `tests/test_ops.py` against a sandbox tenant or instance.

---

## 5. Data, state and retention

| Data | Today | Production |
|---|---|---|
| Conversations, drafts, approvals, tickets | In-memory + `runtime/*.json` | Azure SQL or Cosmos DB, with optimistic concurrency on approval items (prevents double execution across replicas) |
| Rate limiting, repeat cache | In-memory | Azure Cache for Redis |
| Audit (per-turn JSONL, approvals, admin before/after) | `runtime/*.jsonl` | Log Analytics (immutable table) + storage with retention ≥ 1 year (align with the audit policy) |
| Reports (CSV sheets) | `runtime/reports` | Blob storage (private) + SharePoint library |
| Knowledge base + embeddings | Markdown + disk cache | Keep, or Azure AI Search (hybrid) for scale |

PII:

- The audit stores a redacted input preview (passwords and keys removed) and user IDs.
- Set retention and access controls accordingly, and complete a DPIA before go-live.

**Until persistence is implemented, run exactly one replica.**

---

## 6. Configuration reference

| Variable | Production value | Purpose |
|---|---|---|
| `AUTH_MODE` | `easyauth` | Entra ID via platform auth |
| `ACTIONS_ENABLED` | `true` (set `false` to freeze) | Kill switch for all executions |
| `AGENT_MODE` | `auto` | Uses the model when a key is present; offline rules otherwise |
| `OPENAI_API_KEY` | Key Vault reference | Never in code or image |
| `OPENAI_BASE_URL` | Azure OpenAI or proxy endpoint if used | Optional |
| `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL` / `OPENAI_MODERATION_MODEL` | `gpt-4.1-mini` / `text-embedding-3-small` / `omni-moderation-latest` | Pin versions; re-run regression on change |
| `MAX_TOOL_CALLS`, `MAX_OUTPUT_TOKENS`, `MAX_RETRIES`, `REQUEST_TIMEOUT_SECONDS` | 6, 800, 3, 30 | Cost and latency guardrails |
| `MAX_INPUT_CHARS`, `MAX_TURNS_PER_CONVERSATION` | 2000, 20 | Abuse limits |
| `APP_HOST`, `APP_PORT` | `0.0.0.0`, `8000` | Container ingress |
| `RUNTIME_DIR` | `/app/runtime` (until persistence lands) | Writable path |

---

## 7. Deployment procedure (Azure Container Apps)

1. **Build and scan:** `docker build -t <acr>.azurecr.io/ad-l1-agent:<git-sha> .`, then run an image vulnerability scan (Defender for Containers) and push to ACR.
2. **Infrastructure:** use Bicep or Terraform for the Container Apps environment (VNet-integrated), Key Vault, Log Analytics / App Insights, managed identity (`AcrPull`, Key Vault Secrets User), and SQL/Redis when persistence is implemented.
3. **Container App:**
   - Ingress on port 8000 (HTTPS only).
   - Min 1 / max 1 replica until section 5 is done.
   - Liveness probe `/healthz`, readiness probe `/readyz`.
   - CPU 0.5 / memory 1Gi to start.
4. **Secrets:** set Key Vault references for `OPENAI_API_KEY` and connector secrets. Plain environment variables are only for non-secret settings (section 6).
5. **Authentication:** add the Microsoft provider, require authentication, and allow unauthenticated access to nothing except the platform's own endpoints.
6. **Smoke test** (as an engineer with both roles):
   - `/readyz` returns 200.
   - The portal answers "How do I add a printer?" with citations.
   - Triaging `INC…` proposes actions.
   - Approving a work note succeeds.
   - `ACTIONS_ENABLED=false` returns 503 on approve.
7. **CI/CD:** GitHub Actions with OIDC federation to Azure (no stored cloud secrets). The pipeline is `pytest` → offline eval → live regression (gate, section 9) → build → deploy to staging → smoke → manual approval → production.

---

## 8. Operations

### 8.1 Monitoring and alerts (Log Analytics / App Insights)

| Signal | Source | Alert threshold (starting point) |
|---|---|---|
| 5xx rate | access log `status` | > 1% over 5 min |
| LLM fallback rate | audit `flags` contains `llm_unavailable_fallback_offline` | > 10% over 15 min |
| Guardrail hits | flags `prompt_injection`, `embedded_instruction_removed`, `content_safety` | Spike vs 7-day baseline (possible attack) |
| Policy-enforced escalations | flags `policy_escalation_enforced:*` | Trend; a sudden rise means the model is drifting |
| Ungrounded output corrections | flags `ungrounded_*`, `inconsistent_claim_rewritten` | > 5% of turns |
| Token spend | audit `usage` | Daily budget (for example $20/day) |
| Approval latency | approvals audit | Pending > 4 h for P1/P2-related actions |
| Admin executions | admin audit | Any execution outside business hours: notify the team lead |

Every request carries `X-Request-ID`; include it when raising support cases.

### 8.2 Runbooks

- **Model outage or rate limiting:** no action needed; turns fall back to the offline engine automatically (banner shown). For prolonged incidents set `AGENT_MODE=offline`.
- **Suspected misuse or unsafe actions:** set `ACTIONS_ENABLED=false` (freeze), review the approvals audit, rotate the affected credentials.
- **Key rotation:** rotate in Key Vault and restart the revision. Keys are never logged.
- **Model upgrade:** change `OPENAI_MODEL` in staging, run `eval/regression_live.py --runs 3`, and promote only if the gates pass.
- **KB change:** edit Markdown (or publish via the approval flow), then re-index on restart. Every article keeps its `Source:` lines and audience tag.

---

## 9. Release quality gates

| Gate | Command | Pass criteria |
|---|---|---|
| Unit, safety and ops tests | `python -m pytest -q tests` | 100% pass |
| Offline evaluation | `python eval/run_eval.py --mode offline` | 100% decision / citation / routing |
| Live regression (model) | `python eval/regression_live.py --runs 3` | Golden ≥ 95% overall, no case failing in all runs; adversarial **100%**; triage 100%; digest grounded |
| Browser end-to-end | Playwright E2E (43 checks across both consoles) | 100% |
| Security | Secret scan (CI), dependency audit (`pip-audit`), container scan | No high/critical findings |
| Load (before go-live) | e.g. Locust, 50 concurrent users, 30 min | p95 < 15 s per agent turn, error rate < 1% |

---

## 10. Responsible AI and compliance

- **Disclosure:** AI disclosure on every page, and in Teams and DL content ("AI-assisted").
- **Approvals:** human approval for every write. Admin changes need two keys (business approver plus engineer) and an identity attestation for MFA reset or re-enable.
- **Scope:** refusals for out-of-scope, harmful, other-user and privileged-access requests. Indirect prompt injection in quoted content is neutralised.
- **Grounding:** KB citations are validated. Microsoft-grounded articles carry source links and CC BY 4.0 attribution.
- **Before go-live:** DPIA, security architecture review, CAB approval for the integration, a user communication plan, and a red-team exercise each quarter.

---

## 11. Rollout plan

| Phase | Duration | Scope | Exit criteria |
|---|---|---|---|
| 0. Shadow | 2 weeks | Read-only connectors; agents triage real tickets, engineers compare with their own analysis; no writes | ≥ 90% agreement on root cause and routing |
| 1. Assisted | 4 weeks | Work notes, Teams messages, tracker, reports, EOD notes (low-risk writes) with approval | Approval acceptance ≥ 80%, no incidents |
| 2. Controlled admin | 4 weeks | Unlock, group add (two-key), account extension; MFA reset with attestation | Zero unauthorised changes, audit complete |
| 3. Scale | ongoing | More assignment groups, KB expansion, end-user portal in the company portal | Agreed KPIs (section 12) |

## 12. KPIs to track

- Ticket deflection (L1 self-service resolution rate).
- Median time to first evidence-based response.
- Misroute rate.
- Average approval wait.
- SLA breach rate.
- Engineer hours saved on stand-up, report and EOD work.
- Model cost per resolved ticket.

## 13. Ownership (RACI)

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Agent and policy changes | AD engineering | AD team lead | Security | Service desk |
| Connector permissions / PIM | IAM platform | IAM lead | Security, CAB | AD team |
| Model and prompt changes | AD engineering | AD team lead | Responsible AI board | Users |
| Incidents (freeze, rotate) | On-call AD engineer | AD team lead | Security operations | Stakeholders |
