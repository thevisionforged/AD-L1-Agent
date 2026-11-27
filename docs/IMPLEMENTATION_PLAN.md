# Implementation Plan: AD Identity L1 Virtual Agent (L3-grade)

| Item | Value |
|---|---|
| Group | **hack-grp-22** (Team 22) |
| Creator / Captain | **Samarth Pandey** |
| Use case | **UC-05: L1 Virtual Agent** (Service desk & DWP), specialised for Active Directory and identity |
| Deadline | Fri 27-Nov-2026, 11:00 PM IST (target: submitted by 9:30 PM to leave buffer) |
| Zip name | `Team22_UseCase05_Final.zip` |
| Portal fields | Group Name `hack-grp-22`, Use Case Number `5`, Captain = Samarth's HCLTech email, attachments = zip + solution .docx |

## 1. Why UC-05, and what makes it L3

No blueprint is purely about Active Directory. UC-05 is the one whose core workload is AD: password and MFA resets, account lockouts, access requests, "user and device from the directory", and knowing when a fix needs a *privileged action*. The blueprint is rated "Starter". We lift it to L3 depth in four ways, and each one is justified by the use case rather than added for show (the evaluation guide penalises complexity without purpose):

1. **AD root-cause engine (deterministic, the L3 differentiator).** This is what an L3 identity engineer runs first: account enabled or expired (`accountExpires`), lockout state with auto-unlock time, **lockout-source correlation from DC events 4740/4625** (the "phone with an old password" problem), password expiry from `pwdLastSet` + `maxPwdAge`, MFA/SSPR readiness, and **effective group membership including nested groups** for access questions. The model never computes dates or account state. It cites these findings.
2. **Two-agent orchestration with a clear trust boundary.**
   - *L1 Virtual Agent* (conversation, KB-grounded answers, clarifying questions, escalation decision).
   - *Identity Diagnostics Agent* (AD specialist sub-agent: turns raw diagnostics plus events into a root-cause statement with an evidence chain and the least-privilege L2/L3 action it needs). It is called as a tool by the L1 agent and has read-only tools only.
   - *Ticket composer* is deterministic code: enumerated category, ITIL impact × urgency priority, VIP rule and routing table. Submission happens **only** on the user's Confirm click. It is not a model tool.
3. **Hybrid retrieval.** BM25 (exact AD terms such as 4740, SSPR, Kerberos) plus OpenAI embeddings (`text-embedding-3-small`, cached to disk), fused with Reciprocal Rank Fusion. Every step cites `KB-AD-00X#sN`. A post-check strips any citation that was not actually retrieved.
4. **Evidence and safety engineering.** Ten mandatory safety tests as pytest, a golden evaluation set with measured accuracy, latency, tokens and cost, JSONL tracing of every run, a GitHub Actions CI pipeline with tests and a secret scan, and an offline deterministic fallback for demo resilience.

## 2. Architecture (summary)

```
Browser UI (no key) --HTTPS--> FastAPI backend (only OpenAI caller)
   |                               |- Guardrails: length/empty, secret redaction, injection, access-control, scope, Moderation API
   |                               |- Orchestrator (tool-call budget 6, max_completion_tokens 800, backoff+jitter on 429)
   |                               |    |- L1 Virtual Agent (gpt-4.1-mini, structured JSON output)
   |                               |    |    tools: search_kb, get_user_context, check_group_access, draft_ticket,
   |                               |    |           consult_identity_diagnostics -> Identity Diagnostics Agent
   |                               |    |- Identity Diagnostics Agent (gpt-4.1-mini, read-only tools: run_ad_diagnostics, get_lockout_events)
   |                               |    |- Offline rule engine (same tools, same schema) = fallback
   |                               |- Output validation: schema, citation grounding, "ticket submitted" claim check, secret scrub
   |                               |- Mock AD (synthetic users, policy, DC events) | KB (10 articles) | Mock ITSM (drafts -> confirm)
   |-- Confirm click ---------------> POST /api/tickets/{draft}/confirm  (human approval gate, owner-checked)
```

Foundry alignment: `azure.yaml` (from the organizers' template) and `foundry_host.py`, which exposes the same agent through Microsoft Agent Framework's `ResponsesHostServer` for `azd ai agent run`. Local FastAPI is the primary demo path (local-first track, no Azure deployment needed).

## 3. Work plan and timeline (IST)

| # | Phase | Tasks | Output | Time |
|---|---|---|---|---|
| 1 | Environment | Python 3.13 venv, pinned deps, OpenAI key held only as a server-side env var (never in repo), `.gitignore` from minute one | `requirements.txt`, `.env.example` | 15:30-15:45 |
| 2 | Data | Synthetic KB (10 AD/identity articles), directory (7 users), DC events, routing/priority matrix, resource→group map with nesting | `sample_data/` | done/15:50 |
| 3 | Core build | KB + hybrid retrieval, AD diagnostics engine, tools, guardrails, ticket store, offline engine | `src/l1agent/` | 15:50-17:00 |
| 4 | Agents | L1 agent + Identity Diagnostics sub-agent, retry/backoff, structured outputs, grounding validator, tracing | `agent.py`, `llm.py` | 17:00-17:45 |
| 5 | UI + API | FastAPI endpoints, single-page UI (user picker = simulated SSO, chat, evidence panel, ticket confirm/cancel, AI disclosure) | `api.py`, `static/` | 17:45-18:30 |
| 6 | Testing | pytest: unit, 10 safety tests, fake-OpenAI integration (429/timeout), live run with gpt-4.1-mini; golden eval set → metrics table | `tests/`, `eval/`, `docs/test_results.md` | 18:30-19:15 |
| 7 | Docs | README (9 sections in the required order), `docs/architecture.md` + diagram PNG, screenshots, demo script | `README.md`, `docs/` | 19:15-19:45 |
| 8 | Packaging | `run.ps1` (creates venv, installs, starts, opens browser, no prompts), clean copy, secret scan (`sk-`, api_key, token, password), clean-room test from the unzipped folder, zip as one top-level folder | `Team22_UseCase05_Final.zip` | 19:45-20:15 |
| 9 | GitHub | Commits authored as Samarth Pandey, push to the new repo, CI run green, history secret check | repo URL | 20:15-20:30 |
| 10 | Solution doc | Fill the official template Parts A-C (all tables, 10 tests with actual results, metrics, architecture image, declaration) | `hack-grp-22_UC05_Solution_Documentation.docx` | 20:30-21:15 |
| 11 | Submission | **Done by Samarth:** open the PowerApps link, request access, fill Group Name / Use Case 5 / comments, attach zip + docx, Save. Keep the app running on the team VM for the live demo | confirmation screen | by 21:30 |

## 4. Attribution

Everything is credited to **Samarth Pandey, hack-grp-22**: README header, UI footer, solution document, git commit author and code headers. OpenAI appears only where the guidelines require it, as the model provider used by the app ("which OpenAI models you used"). It is not credited as an author.

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| API 429 or outage during judging | Capped exponential backoff with jitter, then automatic offline fallback with a visible banner |
| Key leakage | Key only in the server env; `.env` gitignored; scans before commit, zip and push; CI secret scan |
| Evaluator's Windows VM differs | `run.ps1` creates its own venv and installs pinned wheels; README clean-room steps; offline mode works with no key |
| Hallucinated steps | Steps only from retrieved sections; citation post-check; low confidence plus escalation when the KB has no answer |
| Out of time | Phases 1-8 make a complete, submittable package; 9-10 are ordered so a partial doc is still submittable |

## 6. Items only Samarth can do

1. Create an empty GitHub repository (the session's GitHub token is invalid, so it cannot create one), then share its name so it can be attached and pushed.
2. Submit on the PowerApps portal (it needs HCLTech SSO) and keep the demo running on the team VM.
3. Revoke the temporary OpenAI key after judging, as the API policy requires.
