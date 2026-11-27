# KB-MS-007: Least-privileged Microsoft Entra roles for identity support tasks
Category: Account & Access | Subcategory: Access request
Audience: engineer
Source: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/delegate-by-task
Source: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference

## [s1] Least-privileged role per task (Microsoft guidance)
- Reset password of non-admins: Password Administrator (User Administrator can also do it).
- Reset password of privileged admins: Privileged Authentication Administrator.
- View, provision and manage an individual user's authentication methods (for example re-register MFA): Authentication Administrator.
- Invalidate refresh tokens (revoke sessions) of non-admins: Helpdesk Administrator.
- Create and manage groups and group settings: Groups Administrator.
- Create or delete users, reset passwords for limited admins: User Administrator.
- Read audit and sign-in logs: Reports Reader.
- Configure the Temporary Access Pass policy and manage individual TAPs: see KB-MS-005.

## [s2] Role summaries
- Global Reader: a privileged role that can read settings and administrative information across Microsoft 365 services but cannot take management actions. It is the read-only counterpart of Global Administrator, suitable for audits and investigations.
- Helpdesk Administrator: can change passwords, invalidate refresh tokens and manage support requests. Which users it can reset depends on their roles. It cannot change credentials or reset MFA for members and owners of role-assignable groups.
- Password Administrator: limited password management only; which users it can reset depends on their roles.
- Authentication Administrator: can set or reset any authentication method (including passwords) for non-administrators and some roles, require re-registration of MFA, and revoke remembered MFA on devices.
- Privileged Authentication Administrator: can set or reset any authentication method for any user, including Global Administrators.
- Groups Administrator: can create and manage all groups and group settings in the organisation, across Teams, SharePoint and Outlook.

## [s3] How CORP applies this
- The AD Ops agents read the directory with read-only access (Global Reader; Reports Reader plus Directory Readers is an even narrower alternative for sign-in logs and directory reads).
- Each admin action uses the narrowest role above, activated just-in-time, and only after human approval in the Approval Center.
