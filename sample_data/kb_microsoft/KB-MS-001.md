# KB-MS-001: Microsoft Entra sign-in error codes (AADSTS) for identity support
Category: Account & Access | Subcategory: Account lockout
Audience: end user
Source: https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes

## [s1] Account and password errors
- AADSTS50053: the account is locked because of too many sign-in attempts with a wrong user ID or password, OR the sign-in was blocked because it came from an IP address with malicious activity. The Failure reason in the Entra sign-in logs shows which of the two it was. For a lockout, follow KB-AD-001 and KB-MS-004.
- AADSTS50126: the user name or password is wrong. Retype the password carefully and check the Caps Lock key. A small number of these is normal.
- AADSTS50055: the password has expired. Reset it with self-service password reset (KB-MS-002 / KB-AD-002) or ask the service desk.
- AADSTS50144: the on-premises Active Directory password has expired. Reset it with self-service password reset or ask the service desk.
- AADSTS50057: the account is disabled. Only an administrator can re-enable it (KB-AD-010).
- AADSTS50034: the account was not found in the directory. Usually the user name was mistyped or the wrong tenant was used.

## [s2] MFA and policy errors
- AADSTS50076: multifactor authentication is required, for example because of a Conditional Access policy or a new location. Complete the MFA prompt and try again.
- AADSTS50079 / AADSTS50072: the user must register for MFA. Register at the Security info page (KB-MS-012).
- AADSTS50074: strong authentication is required and the MFA challenge was not passed. Try again and approve the prompt.
- AADSTS53003: access was blocked by a Conditional Access policy. This is a policy decision; raise a ticket with the exact error and the app you were using.

## [s3] Session errors after a password change
- AADSTS50133: the session is no longer valid because the password expired or was recently changed. Sign in again with the new password.
- AADSTS50173: the sign-in grant was revoked, usually after a password change or reset. Sign in again to get a fresh token.

## [s4] What to report to the service desk
- The full error code (AADSTS followed by digits), the time it happened, the app you were signing in to, and the device. Never send your password.
