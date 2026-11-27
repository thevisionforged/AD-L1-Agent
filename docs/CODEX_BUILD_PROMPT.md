# Codex build prompt: production AD L1 agent on the OpenAI Agents SDK with real connectors

*Prepared by Samarth Pandey (hack-grp-22). Paste everything below the line into Codex (or another coding agent) with this repository open. It turns the hackathon build into a real, working AD L1 agent and adds a guided setup that configures and validates every connector before any change can be made.*

> The prompt was written against `openai-agents` 0.26.0. It uses these SDK features, all verified to exist in that version:
> - `Agent`, `Runner`, handoffs
> - `function_tool(needs_approval=...)`, with approvals via `result.interruptions` → `result.to_state()` → `RunState.approve/reject` → `Runner.run(agent, state)`, and `RunState.to_string/from_string` for persistence
> - `input_guardrail` / `output_guardrail`
> - `SQLiteSession`, `trace`, and MCP servers (`agents.mcp`)
>
> Pin the version and re-check the SDK documentation if you use a newer release.

---

## ROLE
You are a senior Python engineer and Microsoft identity (Entra ID / Active Directory) engineer. You are upgrading an existing, tested hackathon project into a **production-ready AD L1 support agent** that works against **real** systems. Work in small, reviewable steps. Never invent API endpoints, permission names or SDK functions: if unsure, read the official docs or the installed package source, then implement. Never print, log or commit secrets.

## STARTING POINT (read these first)
This repository (`AD-L1-Agent`) already contains:
- `src/l1agent/`:
  - `agent.py` (end-user orchestrator); `ops.py`, `ops_agents.py` (queue triage, insights); `tracker.py`, `ops_reports.py`
  - `approvals.py` (human approval gate, two-key rule, single-use tokens, audit)
  - `guardrails.py` (redaction, injection, scope, access control), `diagnostics.py` (deterministic AD checks), `kb.py` (hybrid retrieval)
  - `auth.py` (Entra Easy Auth with app roles `AD.Engineer`, `AD.Approver`), `api.py` (FastAPI), static UI
- `src/l1agent/connectors.py`: **mock** connectors (EntraReader, ControlledAdmin, ServiceNowConnector, TeamsConnector, KBWriter), plus MailConnector and SharePoint/Channel connectors in `ops_reports.py` and `tracker.py`. **Your main job is to add real implementations behind the same interfaces.**
- `tests/` (128 passing tests), `eval/` (golden set, live regression, browser E2E), and these docs:
  - `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` (target architecture, connector map, release gates)
  - `docs/ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md` (identities, permissions, validation checklist)

Read both docs fully before writing code. They are the specification for permissions and identities.

## NON-NEGOTIABLE RULES
1. **Human approval for every write.** Agents only propose. Every tool that changes anything is a `function_tool(needs_approval=True)`. Execution happens only after an AD engineer with the `AD.Approver` app role approves, via the existing Approval Center UI. Keep these rules unchanged:
   - **Two-key** (business approval recorded in ServiceNow plus engineer approval) for group membership, account extension and re-enable.
   - **Identity attestation** for MFA reset and re-enable.
   - **Privileged groups are denied** by policy.
   - **No bulk approval** of admin changes.
2. **Rules decide, the model drafts.** Keep `diagnostics.py`, the escalation policy (`turn_policy`), priority/routing and claim-consistency checks deterministic. The model explains and drafts text; it does not decide privileged outcomes.
3. **Least privilege.** Use exactly the identities and permissions in `ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md`:
   - Separate read and write managed identities.
   - AU-scoped directory roles.
   - `Mail.Send` restricted by Exchange RBAC for Applications; `Sites.Selected`.
   - A gMSA with OU delegation for AD DS.
   - Never Global Admin, Privileged Role Admin or Privileged Authentication Admin.
4. **Secrets:** read only from environment / Azure Key Vault (managed identity). Never in code, config files, logs, prompts, traces or test fixtures. Keep `.env` gitignored.
5. **Keep the mocks.** Add `CONNECTOR_MODE=mock|live` (default `mock`) so the demo, CI and all existing tests keep working without a tenant.
6. **Keep all existing tests green**, and add tests for everything you build.

## TARGET DESIGN (OpenAI Agents SDK)

### Agents
| Agent | Purpose | Tools |
|---|---|---|
| `l1_agent` | Talks to end users | `search_kb`, `get_my_account` (read), `check_group_access` (read), `draft_ticket` (creates a draft only), handoff to `identity_diagnostics_agent` |
| `identity_diagnostics_agent` | AD L3 root cause from real data | `get_ad_account`, `get_lockout_events` (4740/4625), `get_entra_signins`; read-only |
| `queue_triage_agent` | Works ServiceNow tickets in the AD assignment group | Read tools; proposal tools for work notes, Teams message, approval request, reroute, admin changes, KB update |
| `insights_agent` | Daily digest, stuck-work detection, tracker | ServiceNow read; metrics computed in Python |

### Implementation details
- Implement agents with `agents.Agent`, run with `agents.Runner.run(..., max_turns=8)`, and use `model_settings` with a token cap.
- Port the existing system prompt and guardrails into `@input_guardrail` / `@output_guardrail` functions. Reuse `guardrails.py`; do not duplicate the logic.
- **Approvals:**
  1. When `Runner.run` returns `result.interruptions`, persist `result.to_state().to_string()` together with the interruption details in the database, as an Approval Center item.
  2. When an engineer approves in the UI, load it with `RunState.from_string(...)`, enforce the two-key and attestation checks in code, call `state.approve(item)` or `state.reject(item)`, then resume with `Runner.run(agent, state)`.
  3. Record before/after state in the audit log.
- Sessions: `SQLiteSession` for development; production uses a database-backed session (Azure SQL / Cosmos DB) with the same interface.
- Tracing: wrap each turn in `agents.trace(...)` with the request ID. Disable or redact sensitive trace data (no user secrets, no tokens).
- Structured outputs: keep the `AgentReply` Pydantic schema as `output_type`.
- Keep the conversational behaviour: greetings and "what can you do" are answered directly, with no workflow and no ticket.

## REAL CONNECTORS (implement behind the existing interfaces, `CONNECTOR_MODE=live`)
Use `azure-identity` (`ManagedIdentityCredential` / `DefaultAzureCredential`) and `httpx` with timeouts, retries (exponential backoff with jitter, max 3) and pagination.

| Interface | Live implementation | Permissions (see the access guide) |
|---|---|---|
| `EntraReader` | Microsoft Graph: `GET /users/{id}`, `GET /users/{id}/transitiveMemberOf`, `GET /auditLogs/signIns?$filter=userId eq '{id}'` | Read identity: `User.Read.All`, `GroupMember.Read.All`, `AuditLog.Read.All` |
| AD DS reads (`Directory` / `run_diagnostics` data source) | Azure Automation **Hybrid Runbook Worker** in the AD site: runbook `Get-ADUser -Properties lockedOut,badPwdCount,pwdLastSet,accountExpires,msDS-UserPasswordExpiryTimeComputed,memberOf`; events 4740/4625 from the PDC emulator (or Microsoft Sentinel / Log Analytics query) | gMSA with read + Event Log Readers |
| `ControlledAdmin` (AD DS) | Runbooks `Unlock-ADAccount`, `Add-ADGroupMember`, `Set-ADAccountExpiration`, `Enable-ADAccount`, started via the Automation jobs API **only** after approval. Each runbook requires the approval ID, re-checks the target is in an allowed OU and not privileged, and returns before/after state | gMSA with OU delegation only |
| `ControlledAdmin` (cloud) | Graph authentication methods / Temporary Access Pass / revoke sessions | Write identity with AU-scoped Authentication Administrator / Helpdesk Administrator |
| `ServiceNowConnector` | ServiceNow Table API: `incident`, `sc_task`, `change_request`, `sysapproval_approver`; work notes via the `work_notes` field; filter by `assignment_group`. OAuth client credentials from Key Vault. **Business approvals arrive by webhook** (replace the demo "simulate approver" endpoint) | Integration user, ITIL role scoped to the AD group |
| `TeamsConnector` / `ChannelConnector` | Teams **bot** (Bot Framework) for 1:1 messages; **Workflows webhook** or bot for the AD channel tracker post. App-only `ChannelMessage.Send` is not available | Bot registration |
| `MailConnector` | Graph `POST /users/{mailbox}/sendMail` | `Mail.Send` restricted by Exchange RBAC for Applications to the report mailbox |
| `SharePointConnector` | Graph `sites/{site-id}/lists/{list-id}/items` (upsert by ticket number) | `Sites.Selected` with write on the one site |
| `KBWriter` / KB source | ServiceNow `kb_knowledge` (read for retrieval; publish after approval) or SharePoint pages; re-index after publish | ServiceNow KB role |

Every live connector must have:
- a `health_check()` that verifies reachability **and** the specific permission, using a harmless read;
- typed errors;
- no secret in exceptions.

## GUIDED SETUP ("asks to configure the necessary connectors")
Build a setup flow that an admin runs once, plus a status page.

1. **CLI** `python -m l1agent.setup` (interactive by default; `--non-interactive --config setup.yaml` for automation). It asks for each value, explaining where to find it:
   - Tenant ID; web app client ID; read and write managed identity client IDs.
   - Key Vault URI (secrets referenced by name only, never typed into the CLI).
   - Automation account / hybrid worker group and runbook names.
   - Allowed user OUs and the share-group OU.
   - ServiceNow instance URL, AD assignment group `sys_id`, Key Vault secret names for OAuth.
   - Teams bot or Workflows webhook (secret in Key Vault); report mailbox; SharePoint site and list IDs; team DL address.
2. It writes **non-secret** settings to `config/connectors.yaml` (gitignored template `config/connectors.example.yaml`). Secrets stay in Key Vault.
3. It then runs the **validation checklist** from section 8 of `ENTRA_APP_REGISTRATION_AND_ACCESS_GUIDE.md`, connector by connector, and prints **PASS / FAIL / FIX** with the exact remediation. Example: "write identity cannot reach users outside the AU: expected; add test user to AU 'ADL1-Users'".
4. **Safe enablement:** the app starts in **shadow mode** (`CONNECTOR_MODE=live`, `ACTIONS_ENABLED=false`): read-only, proposals visible, nothing executes. Writes can only be enabled when every write-path check has passed and an admin runs `python -m l1agent.setup --enable-actions`. That command records who enabled them and when.
5. **Status page** `/ops/setup` (requires `AD.Approver`): live health of each connector, its last check and its configuration (no secrets), with a "re-run checks" button.

## PHASED PLAN (one PR per phase; tests must pass at each)
1. **Agents SDK migration (mock mode):** port the orchestration to `openai-agents` with the same behaviour. Make all 128 existing tests and `eval/run_eval.py --mode offline` pass, then run `eval/regression_live.py --runs 3`, which must meet the gates below.
2. **Approval interruptions:** `needs_approval` tools plus `RunState` persistence wired into the Approval Center. Tests for approve, reject, two-key, attestation, kill switch and restart-safety (state survives a process restart).
3. **Read connectors (live):** Graph read and AD DS read via the hybrid worker, behind `CONNECTOR_MODE=live`. Add contract tests that run against a sandbox tenant when `LIVE_TEST=1`.
4. **Setup wizard and status page,** in shadow mode.
5. **Write connectors (live):** ServiceNow, Teams/Workflows, Mail, SharePoint, cloud admin, AD DS runbooks, each executed only after approval. Add contract tests in the sandbox.
6. **Persistence and scale:** database for sessions, approvals and audit; Redis for rate limits; optimistic concurrency on approval items; then allow more than one replica.
7. **Hardening:** `pip-audit`, container scan, load test (50 users, p95 < 15 s), threat model update, runbooks.

## RELEASE GATES (must pass before enabling writes in production)
- `pytest` 100% pass; `eval/run_eval.py --mode offline` 100/100/100.
- `eval/regression_live.py --runs 3`:
  - golden ≥ 95% overall, with no case failing in every run;
  - adversarial 100%;
  - triage 100%;
  - digest grounded.
- Browser E2E (`eval/e2e_browser_test.js`) 100%.
- Setup validation checklist: all PASS in the target tenant.
- Two weeks of shadow mode with ≥ 90% engineer agreement on root cause and routing.

## DEFINITION OF DONE
- With `CONNECTOR_MODE=mock` everything behaves exactly as today (demo, tests, CI).
- With `CONNECTOR_MODE=live` and a completed setup:
  - an end user signs in with Entra ID, asks about a lockout, and sees the **real** 4740 source device and a cited fix;
  - an engineer triages a **real** ServiceNow ticket and approves an unlock;
  - the hybrid worker unlocks the account, and ServiceNow gets the work note with before/after state.
- No secret appears anywhere in the repo, logs or traces. The README documents setup, shadow mode, enabling actions and rollback (`ACTIONS_ENABLED=false`).

## WORKING STYLE
- Before each phase, write a short plan in the PR description; after it, list what was tested and how.
- If a required value (tenant ID, OU, `sys_id`, webhook) is missing, **stop and ask**. Never guess or hard-code it.
- Prefer official SDKs and documented endpoints; link the Microsoft / ServiceNow / OpenAI doc page you used in code comments for each external call.
- Keep credit to the original author in file headers: "Built by Samarth Pandey (hack-grp-22)".
