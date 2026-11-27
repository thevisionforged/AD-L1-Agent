# Microsoft Entra ID: App Registration, Roles, Permissions and Authorisation Guide

> **Running outside Azure App Service** (VM, laptop, container), or connecting ServiceNow and Teams: use [ORG_ROLLOUT_GUIDE.md](ORG_ROLLOUT_GUIDE.md) (`AUTH_MODE=entra`, built-in Entra sign-in).

| | |
|---|---|
| Solution | AD Identity L1 Virtual Agent + AD Ops agents |
| Owner | Samarth Pandey (hack-grp-22) |
| Version | 1.0 (27-Nov-2026) |
| Audience | Entra ID / IAM administrators, AD engineers, platform engineers, security reviewers |
| Companion | [PRODUCTION_DEPLOYMENT_GUIDE.md](PRODUCTION_DEPLOYMENT_GUIDE.md) |

Portal paths, setting names, permission names and cmdlets in this guide were checked against Microsoft Learn on 27-Nov-2026 (sources in section 10). Items marked *(general guidance)* are standard practice not covered by those pages; validate them in a test tenant first.

---

## 1. The identities involved

| # | Identity | Used for | Type |
|---|---|---|---|
| A | **Web app registration** `ad-l1-agent` | Signing users and engineers in (App Service / Container Apps authentication) and carrying the **app roles** `AD.Engineer`, `AD.Approver` | App registration + enterprise app |
| B | **Backend read identity** (`ad-l1-agent-read`) | Reading users, group memberships and sign-in logs from Microsoft Graph | User-assigned managed identity |
| C | **Backend write identity** (`ad-l1-agent-exec`) | Executing *approved* cloud actions (MFA re-registration, session revocation, cloud group membership, mail, SharePoint list) | Separate user-assigned managed identity |
| D | **On-prem executor** (gMSA, e.g. `gmsa-adl1-exec$`) | Executing *approved* AD DS actions (unlock, group add, account expiry, enable) through a hybrid worker | Group managed service account |
| E | **Human administrators** | Setup, consent, break-glass and manual admin work | Users with PIM-eligible roles |

Why read and write are separate: the write identity's permissions are only ever exercised by the Approval Center after a human approval (`src/l1agent/approvals.py`). Keeping them on a different identity makes misuse of the read path harmless and makes the write path easy to monitor and revoke.

---

## 2. Prerequisites and licensing

| Task | Minimum role for the person doing it |
|---|---|
| Register the app, define app roles, assign users/groups to app roles | **Cloud Application Administrator** (Microsoft's procedure for app-role assignment says "at least a Cloud Application Administrator") |
| Grant admin consent / application permissions to managed identities | An administrator allowed to grant application permissions for Microsoft Graph *(general guidance: Privileged Role Administrator or Global Administrator in most tenants)* |
| Assign directory roles (Helpdesk, Authentication, Groups Administrator) to the executor | Privileged Role Administrator *(general guidance)* |
| Exchange RBAC for Applications (restrict `Mail.Send`) | Exchange administrator |
| Grant `Sites.Selected` access to the SharePoint site | SharePoint administrator / site owner *(general guidance)* |
| AD DS OU delegation, gMSA, hybrid worker | Domain administrator (one-off, under change control) |

Licensing (from Microsoft Learn):

- **Administrative units:** each administrator assigned a directory role scoped to an administrative unit needs a **Microsoft Entra ID P1** licence.
- **Privileged Identity Management:** requires the appropriate Microsoft Entra ID Governance licensing.

---

## 3. Identity A: web app registration (sign-in, app roles)

### 3.1 Create the registration

1. Easiest route: in the **Container App** (Security > Authentication) or **App Service** (Settings > Authentication), select **Add identity provider** > **Microsoft**. The platform can create the registration for you.
2. Manual route (if your account cannot create registrations): in the Microsoft Entra admin center create an app registration:
   - Supported account types: **Accounts in this organizational directory only (single tenant)**.
   - Redirect URI, platform **Web**: `https://<your-app-host>/.auth/login/aad/callback`.
3. Credential:
   - **Preferred:** configure the app to use an identity instead of a client secret.
   - **Otherwise:** create a client secret and store it **only** as the platform setting `MICROSOFT_PROVIDER_AUTHENTICATION_SECRET`, sourced from Key Vault. Set it to expire and rotate it.
4. Issuer URL: if the express setup set the legacy `https://sts.windows.net` issuer, change it to the current v2 issuer for your tenant (App registrations > Endpoints > OpenID Connect metadata, minus `/.well-known/openid-configuration`).

### 3.2 Define the app roles (App registrations > `ad-l1-agent` > App roles > Create app role)

| Display name | Allowed member types | Value (must match the code exactly) | Description |
|---|---|---|---|
| AD Engineer | Users/Groups | `AD.Engineer` | Open the AD Ops Console, triage tickets, view insights, tracker, health and audit, and *propose* actions |
| AD Approver | Users/Groups | `AD.Approver` | Approve or reject proposed actions (admin changes, ServiceNow updates, Teams/DL messages, SharePoint, KB publish) |

Rules:

- The **Value** appears in the token's `roles` claim; the app checks these exact strings (`src/l1agent/auth.py`). Values cannot contain spaces.
- Enable both roles. No app role is needed for end users of the L1 portal: being assigned to the app is enough.

### 3.3 Restrict who can sign in and assign roles (Enterprise apps > `ad-l1-agent`)

1. **Properties** > **Assignment required?** = **Yes**. Only assigned users and groups can then get a token for the app.
2. **Users and groups** > **Add user/group**, then assign:

| Entra security group | App role | Purpose |
|---|---|---|
| `SG-ADL1-Users` (or all employees) | Default access | End users of the L1 Virtual Agent portal |
| `SG-ADL1-AD-Engineers` | AD Engineer | AD / IAM engineers (Ops Console) |
| `SG-ADL1-AD-Approvers` | AD Approver | Senior engineers / team lead (approvals). Members also need AD Engineer |

Notes from Microsoft Learn:

- Assigning a **service principal** to a group that holds an app role does **not** put the role in tokens issued to that service principal. Assign roles to people (via groups), not to automation through groups.
- App roles and groups are not mutually exclusive. We use app roles because they are defined by the application and survive tenant moves, unlike group object IDs.

*(general guidance)*

- Group-based assignment to enterprise apps depends on your licensing tier; confirm it in your tenant.
- Keep the approver group small, and review it quarterly with access reviews.

### 3.4 Configure platform authentication

1. **Require authentication**. For unauthenticated requests use **HTTP 302 Found redirect** (recommended for websites).
2. Token store: enable if you later need downstream tokens; this app does not.
3. Allowed token audiences: only needed if other clients call the API with access tokens.
4. App setting `AUTH_MODE=easyauth`. The platform injects the signed-in user's claims as **`X-MS-CLIENT-PRINCIPAL`** (Base64 JSON), and external requests are not allowed to set these headers. Therefore:
   - Expose the app **only** through the platform's authenticated ingress (no direct container or VNet path that bypasses it).
   - Behind Front Door, restrict the origin to Front Door.
5. Engineer roster: each engineer's **UPN** must be in the roster (`sample_data/servicenow_queue.json` → `engineers[].upn` today; a database table in production). The app requires **both** the app role and roster membership.

### 3.5 What each persona can do (enforced in `src/l1agent/api.py`)

| Capability | End user (assigned) | `AD.Engineer` | `AD.Engineer` + `AD.Approver` |
|---|---|---|---|
| Chat, own diagnostics, confirm/cancel own ticket draft | Yes (own identity only; the body `user_id` cannot override the token) | Yes | Yes |
| View queue, triage, insights, tracker, health, audit, propose actions | No (403) | Yes | Yes |
| Approve / reject / bulk-approve actions | No | No (403) | Yes |
| Simulate business approver | No | No (endpoint disabled outside demo mode) | No |
| Swagger / OpenAPI docs | No | No (disabled in `easyauth`) | No |

---

## 4. Identity B: read identity (Microsoft Graph, application permissions)

Create a **user-assigned managed identity** `ad-l1-agent-read`, attach it to the container app, and grant Microsoft Graph application permissions with Microsoft Graph PowerShell (`New-MgServicePrincipalAppRoleAssignment`, as in Microsoft's "assign a managed identity to an application role" article) or Azure CLI.

| Permission (application) | Microsoft description | Why the agent needs it |
|---|---|---|
| `User.Read.All` | Read all users' full profiles | Caller profile, account state, manager |
| `GroupMember.Read.All` | Read all group memberships | Effective/nested membership for access triage |
| `AuditLog.Read.All` | Read all audit log data | Entra sign-in failures (for example AADSTS50053 / 50126) for root cause |

Alternative for a human-style read role: Microsoft lists **Reports Reader** as the least-privileged role for audit and sign-in logs, and **Global Reader** as the read-only counterpart of Global Administrator (KB-MS-007). Prefer the narrow Graph permissions above for the workload identity.

---

## 5. Identity C: cloud write identity (only for approved actions)

### 5.1 Prefer scoped directory roles over tenant-wide Graph permissions
Graph **application** permissions apply to the whole tenant. For user-management actions, prefer assigning the executor identity a **directory role scoped to an administrative unit (AU)** that contains only the in-scope users and groups. Microsoft's example is delegating Helpdesk Administrator to regional support through an AU.

| Approved action | Least-privileged Entra role (Microsoft "least privileged roles by task") | Scope | Graph permission if a role cannot be used |
|---|---|---|---|
| Require MFA re-registration / manage a user's methods / TAP | **Authentication Administrator** (non-admin users only) | AU of in-scope users | `UserAuthenticationMethod.ReadWrite.All` |
| Revoke sessions (non-admins) | **Helpdesk Administrator** | AU of in-scope users | `User.RevokeSessions.All` |
| Reset a non-admin's cloud password (if ever enabled) | **Password Administrator** | AU | `User-PasswordProfile.ReadWrite.All` |
| Enable a disabled cloud account | **User Administrator** | AU | `User.EnableDisableAccount.All` |
| Add to a cloud security group | **Groups Administrator** or **group owner** | AU containing the share groups | `GroupMember.ReadWrite.All` |

Hard rules (also enforced in code):

- **Never** grant Privileged Authentication Administrator, Privileged Role Administrator or Global Administrator to the executor.
- Admin users and privileged groups are out of scope. The code denies privileged groups, and AU scoping keeps admins out.
- An AU-scoped User Administrator can manage the authentication methods of users only if those users are **direct members of the AU**. Put users, not just groups, in the AU.

### 5.2 Mail to the team DL: `Mail.Send` restricted to one mailbox
`Mail.Send` (application) means "Send mail as any user". Restrict it with **Exchange RBAC for Applications**:
```powershell
New-ServicePrincipal -AppId <managed-identity-app-id> -ObjectId <service-principal-object-id> -DisplayName "ad-l1-agent-exec"
New-ManagementScope -Name "ADL1 report mailbox" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'ad-ops-reports@corp.example.com'"
New-ManagementRoleAssignment -App <service-principal-object-id> -Role "Application Mail.Send" -CustomResourceScope "ADL1 report mailbox"
Test-ServicePrincipalAuthorization -Identity <service-principal-object-id> -Resource ad-ops-reports@corp.example.com
```
Do not also grant tenant-wide `Mail.Send` in Entra, or the restriction is bypassed *(general guidance: verify against your Exchange configuration)*.

### 5.3 SharePoint tracker list: `Sites.Selected`

- `Sites.Selected` (application) allows "access [to] a subset of site collections". Grant it, then give the identity **write** permission on the AD Identity Ops site only. The agent upserts items in the "AD Major Ticket Tracker" list.

### 5.4 Teams

- `ChannelMessage.Send` exists **only as a delegated permission** (no application permission), so a background service cannot post to a channel with app-only Graph.
- Use a **Teams Workflows webhook** for the AD channel tracker post, or a **Teams bot** for 1:1 user messages and channel posts.

---

## 6. Identity D: on-premises AD DS executor *(general guidance)*

1. Create a **gMSA** (for example `gmsa-adl1-exec$`) usable only by the hybrid worker servers.
2. Delegate on the **user OUs** in scope with the Delegation of Control wizard or `dsacls`. Grant only these rights:

| Approved action | Delegated permission |
|---|---|
| Unlock account | Read/Write `lockoutTime` |
| Extend contractor expiry | Read/Write `accountExpires` |
| Enable account | Read/Write `userAccountControl` |
| Add to file-share group | Write `member` on the share-group OU only |

3. **Do not** delegate on tier-0 OUs, and do not grant membership of Domain/Enterprise/Schema Admins, Account Operators, or other built-in privileged groups. Accounts protected by AdminSDHolder have their ACLs reset, and must be handled manually.
4. Event reading for 4740/4625: add the gMSA to **Event Log Readers** on the DCs, or read from Microsoft Sentinel / Log Analytics.
5. The hybrid worker runs only the runbooks behind `ControlledAdmin` (`Unlock-ADAccount`, `Add-ADGroupMember`, `Set-ADAccountExpiration`, `Enable-ADAccount`). Each runbook requires the approval ID and logs before/after state.

---

## 7. Identity E: human administrators (PIM)

- Make administrative roles **eligible** in **Privileged Identity Management**: Cloud Application Administrator, Privileged Role Administrator, Exchange Administrator and others. PIM provides just-in-time and time-bound access, approval to activate, justification, and MFA on activation.
- Use PIM for setup and break-glass, and whenever an engineer must act outside the agent.
- The agent's own Approval Center decisions are recorded separately (`runtime/approvals_audit.jsonl` → Log Analytics).

---

## 8. Validation checklist (run in a test tenant first)

| # | Check | Expected |
|---|---|---|
| 1 | Sign in as an unassigned user | Blocked by Entra (assignment required) |
| 2 | Signed-in end user opens `/.auth/me` | Claims include `preferred_username`; no `roles` |
| 3 | Engineer `/.auth/me` | `roles` contains `AD.Engineer` (and `AD.Approver` for approvers) |
| 4 | End user calls `/api/ops/queue` | 403 |
| 5 | Engineer without AD.Approver approves an action | 403 |
| 6 | Request with a forged `X-MS-CLIENT-PRINCIPAL` sent directly to the container (bypassing auth) | Not reachable (ingress only through platform auth) |
| 7 | Read identity: `GET /users/{id}`, `/users/{id}/transitiveMemberOf`, `/auditLogs/signIns` | 200 |
| 8 | Exec identity: re-register MFA for an in-AU user | Succeeds; for a user outside the AU or an admin: denied |
| 9 | `Test-ServicePrincipalAuthorization` against the report mailbox and against another mailbox | In scope: true; other: not in scope |
| 10 | gMSA tries to unlock an account in a non-delegated OU | Access denied |
| 11 | `ACTIONS_ENABLED=false`, then approve | 503; nothing executed |

The automated equivalents of checks 2 to 5 are in `tests/test_production.py`.

---

## 9. Governance

- **Access reviews:** quarterly for `SG-ADL1-AD-Approvers` and `SG-ADL1-AD-Engineers`.
- **Credentials:** no long-lived secrets. Use managed identities and federated credentials; if a client secret is unavoidable, store it in Key Vault and rotate it before expiry.
- **Monitoring:** watch sign-ins and audit logs for the executor identities. Alert on any use outside the app or outside the expected operations.
- **Change control:** changes to app roles, Graph permissions, AU membership or OU delegation go through CAB, and the release gates in the production guide are re-run.
- **Decommission:** remove app role assignments, delete the managed identities, remove OU delegations and the Exchange management role assignment.

---

## 10. Sources (Microsoft Learn, retrieved 27-Nov-2026)

- Add app roles to your application and receive them in the token: https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-app-roles-in-apps
- Restrict your app to a set of users (Assignment required): https://learn.microsoft.com/en-us/entra/identity-platform/howto-restrict-your-app-to-a-set-of-users
- Register an application: https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
- App Service authentication with Microsoft Entra: https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-provider-aad
- Work with user identities (X-MS-CLIENT-PRINCIPAL): https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-user-identities
- Container Apps authentication with Microsoft Entra ID: https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra
- Assign a managed identity to an application role: https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-assign-app-role-managed-identity
- Microsoft Graph permissions reference: https://learn.microsoft.com/en-us/graph/permissions-reference
- Least privileged roles by task: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/delegate-by-task
- Built-in roles: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference
- Administrative units: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/administrative-units
- Role-based access control for applications in Exchange Online: https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac
- Privileged Identity Management: https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-configure
