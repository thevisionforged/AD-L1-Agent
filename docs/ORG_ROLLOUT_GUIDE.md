# Roll out the AD L1 Agent v2 to your organisation

**Entra ID sign-in, Microsoft Graph, ServiceNow and Teams, step by step**

Built by **Samarth Pandey** · **hack-grp-22**

This guide turns the demo into an app that real colleagues use:

1. People open the app and **sign in with their work account** (Microsoft Entra ID).
2. The v2 agent reads **their own** account from **Microsoft Graph**: account state, groups and recent sign-in failures.
3. When they approve, the ticket is created in **your ServiceNow**, and a card is posted to the **AD team's Teams channel**.

Each part is switched on separately. You can start with step A only.

| Part | What it gives | Settings in `.env` |
|---|---|---|
| A. Entra ID sign-in | org users sign in; app roles; only assigned people get in | `AUTH_MODE=entra`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `ENTRA_REDIRECT_URI`, `SESSION_SECRET` |
| B. Microsoft Graph (read-only) | the agent diagnoses the user's **real** account | `CONNECTOR_MODE=live` (reuses the same app registration) |
| C. ServiceNow | real incidents on approval; "status of my ticket" reads live incidents | `SNOW_INSTANCE`, `SNOW_USERNAME`/`SNOW_PASSWORD` or `SNOW_CLIENT_ID`/`SNOW_CLIENT_SECRET`, `SNOW_ASSIGNMENT_GROUP` |
| D. Teams | an Adaptive Card in the AD team channel for every new ticket | `TEAMS_WEBHOOK_URL` |

Check everything at any time, without starting the app (Windows):

```powershell
.\.venv\Scripts\python.exe -m l1agent.v2.check_connectors your.name@yourcompany.com
```

On macOS/Linux use `PYTHONPATH=src .venv/bin/python -m l1agent.v2.check_connectors your.name@yourcompany.com`. It prints **OK**, **NOT CONFIGURED** or **FAILED** with the reason for each part, and never prints a secret.

---

## Who needs to do what

| Step | Who |
|---|---|
| A1–A6 app registration, consent, assignment | Entra **Application Administrator** (admin consent for Graph application permissions needs a **Privileged Role Administrator** or **Global Administrator**) |
| C1–C3 ServiceNow integration user | ServiceNow **admin** |
| D1 Teams webhook | an **owner** of the AD team in Teams |
| `.env` and running the app | the person hosting the app |

---

## A. Entra ID sign-in (any host, including a VM or localhost)

The app does the sign-in itself (OpenID Connect, authorization code + PKCE), so it does not need Azure App Service.

**A1. Register the app**

1. Go to **https://entra.microsoft.com**, then **Identity → Applications → App registrations → New registration**.
2. Fill in:
   - **Name:** `AD L1 Agent`
   - **Supported account types:** *Accounts in this organizational directory only* (single tenant)
   - **Redirect URI:** platform **Web**, value `http://localhost:8000/auth/callback`

   Plain `http://` is allowed only for `localhost`. For a server, add `https://<your-host>/auth/callback` later under **Authentication**.
3. Press **Register**. On **Overview**, copy the **Directory (tenant) ID** and the **Application (client) ID**.

**A2. Create a client secret**

**Certificates & secrets → Client secrets → New client secret**, with an expiry of 6 months. Copy the **Value** now: it is shown only once. Store it in `.env` or Key Vault. Never put it in chat, email or git.

**A3. Create the app roles**

Under **App roles → Create app role**, create two roles. For both, set **Allowed member types** to *Users/Groups*.

| Display name | Value | Use |
|---|---|---|
| AD Engineer | `AD.Engineer` | opens the AD Ops Console |
| AD Approver | `AD.Approver` | approves admin actions in the Ops Console |

End users need no role. Being assigned to the app (A5) is enough.

**A4. API permissions (Microsoft Graph)**

Go to **API permissions → Add a permission → Microsoft Graph**.

| Type | Permission | Why |
|---|---|---|
| Delegated | `openid`, `profile`, `email` (`User.Read` is already there) | sign-in |
| Application | `User.Read.All` | read the signed-in user's account state (part B) |
| Application | `GroupMember.Read.All` | read their group memberships, nested groups included (part B) |
| Application | `AuditLog.Read.All` | read their recent sign-in failures (part B). Needs Entra ID P1 or P2. |

Then press **Grant admin consent for <your tenant>**. All three application permissions are **read-only**. The agent never writes to Entra ID.

> The agent only ever looks up **the signed-in person's own UPN**. Its tools take no user name from the model, so it cannot be talked into reading someone else's account.

**A5. Control who can use it (Enterprise application)**

1. Go to **Identity → Applications → Enterprise applications → AD L1 Agent**.
2. Under **Properties**, set **Assignment required?** to **Yes**, then **Save**. Now only assigned people can sign in. Everyone else gets AADSTS50105, which the app explains.
3. Under **Users and groups → Add user/group**:
   - Assign your pilot group, or *all employees*, with **Default Access**.
   - Assign the AD team group with **AD Engineer**, and approvers with **AD Approver**.
   - New joiners get access automatically when they join the group.
4. Optional, under **Conditional Access**: require MFA and a compliant device for this app.

**A6. Fill in `.env`**

```dotenv
AUTH_MODE=entra
ENTRA_TENANT_ID=<Directory (tenant) ID>
ENTRA_CLIENT_ID=<Application (client) ID>
ENTRA_CLIENT_SECRET=<secret value from A2>
ENTRA_REDIRECT_URI=http://localhost:8000/auth/callback
SESSION_SECRET=<random, at least 32 characters>
```

Generate `SESSION_SECRET` with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Restart with `run.ps1` and open `http://localhost:8000/v2`. You are sent to Microsoft sign-in, then back to the agent, with **Signed in: <your name> · Sign out** in the header.

**How sign-in is protected:**
- **ID token:** checked against your tenant's published signing keys (RS256), plus issuer, audience, expiry, nonce and tenant ID.
- **Redirect:** PKCE and `state` protect it.
- **Session:** an HMAC-signed, HttpOnly, SameSite=Lax cookie that expires after 8 hours. No access tokens are kept in the browser.
- **Host security:** on an `https://` host the cookies are also `Secure`, and HSTS is sent.

**Which page each person uses:**
- **Organisation users** (anyone signed in) use **`/v2`**.
- The **v1 portal (`/`)** works only for people in the synthetic demo directory.
- The **Ops Console** needs the `AD.Engineer` role, plus an entry in the engineer roster (`sample_data/engineers.json`).

## B. Microsoft Graph: real account data

This uses the same app registration and the permissions from A4. In `.env`:

```dotenv
CONNECTOR_MODE=live
```

`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID` and `GRAPH_CLIENT_SECRET` are only needed if you use a **separate** app for Graph. Otherwise the `ENTRA_*` values are reused.

To try live Graph **without** Entra sign-in, keep `AUTH_MODE=demo` and set `GRAPH_DEMO_UPN=<a real UPN>`. Every demo user then reads that account. Use this on test tenants only.

Now ask v2 *"my account keeps getting locked out"*. The **Agent activity** panel shows `consult_identity_diagnostics`, and the output starts with `source: microsoft-graph`, followed by the real `accountEnabled`, groups, and AADSTS codes with their meanings.

## C. ServiceNow: real incidents

**C1. Integration user** (in ServiceNow, **User Administration → Users → New**)

| Field | Value |
|---|---|
| User ID | `svc_ad_l1_agent` |
| Web service access only | ticked |
| Password | set a strong password |
| Roles | `itil`, which lets it create and read incidents. Nothing else. |

Free test instance: **developer.servicenow.com → Personal Developer Instance**.

**C2. Optional: OAuth instead of a password**

1. Go to **System OAuth → Application Registry → New → Create an OAuth API endpoint for external clients**.
2. Enable the client-credentials grant for it (system property `glide.oauth.inbound.client.credential.grant_type.enabled = true`).
3. Set the OAuth application user to `svc_ad_l1_agent`.
4. Use `SNOW_CLIENT_ID` and `SNOW_CLIENT_SECRET` instead of the user name and password.

**C3. `.env`**

```dotenv
SNOW_INSTANCE=yourcompany              # or the full https://yourcompany.service-now.com
SNOW_USERNAME=svc_ad_l1_agent
SNOW_PASSWORD=<password>
SNOW_ASSIGNMENT_GROUP=IAM-L2 Identity Ops (AD)   # exact group name in ServiceNow (optional)
```

**What happens:**
- **Ticket submission:** when the user approves `submit_ticket`, the agent creates the incident through the Table API (`POST /api/now/table/incident`).
  - The **caller** is the ServiceNow user whose email or user ID equals their Entra UPN.
  - Impact and urgency are mapped, and the assignment group is set.
  - The user is told the **real INC number**.
- **Status questions:** *"What's the status of my ticket?"* makes `my_tickets` read the caller's **open incidents live**.
- **If ServiceNow is down:** the ticket is kept in the local store and the error is reported. Nothing is lost.

## D. Teams: notify the AD team channel

1. In Teams, open the AD team channel, then **⋯ → Workflows → "Post to a channel when a webhook request is received"**.
2. Pick the team and channel, then copy the URL it gives you.
3. In `.env`, set `TEAMS_WEBHOOK_URL=<that URL>`. Treat the URL as a secret: anyone who has it can post to that channel.

Every approved ticket now posts an Adaptive Card with the ticket number, requester, priority, team, a summary and an **Open in ServiceNow** button. The app only accepts Microsoft/Power Automate webhook hosts.

## Network allow-list (if the host is restricted)

The host needs outbound HTTPS to:
- `login.microsoftonline.com`
- `graph.microsoft.com`
- `<instance>.service-now.com`
- your Workflows host (`*.logic.azure.com` or `*.powerplatform.com`)
- `api.openai.com`

## Go-live checklist

- [ ] App registration is single tenant, with a client secret that has an expiry and is stored in Key Vault (`.env` is fine for a pilot)
- [ ] **Assignment required = Yes**, with pilot groups assigned
- [ ] Graph application permissions are **read-only** and admin consent is granted
- [ ] `https://` redirect URI on the real host (cookies become Secure and HSTS is on)
- [ ] ServiceNow integration user has **only** `itil` and web-service-only access
- [ ] Teams webhook URL is treated as a secret
- [ ] `check_connectors` shows OK for every enabled part
- [ ] One end-to-end test with a pilot user: sign in → lockout question → ticket → approve → INC in ServiceNow → card in Teams
- [ ] Secret rotation dates are in the team calendar (client secret, ServiceNow password)

## Known limitations

- **Pending approvals:** v2 keeps pending approvals in memory, so run **one** app instance, or add a shared store before scaling out. Conversation memory is in SQLite in `runtime/`.
- **Access requests:** the "which group grants access to share X" catalogue is synthetic. For org users the agent lists their real groups and offers an access-request ticket.
- **Ops Console:** it still runs on synthetic data. Connecting it to the real ServiceNow queue is the next step.
