# KB-MS-006: Admin - manage a user's authentication methods (re-register MFA, revoke sessions)
Category: Password & MFA | Subcategory: MFA
Audience: engineer
Source: https://learn.microsoft.com/en-us/entra/identity/authentication/howto-mfa-userdevicesettings

## [s1] Where
- Microsoft Entra admin center > Entra ID > Users > select the user > Authentication methods. Sign in as at least an Authentication Administrator.

## [s2] Actions available
- Require re-register MFA: deletes the user's phone numbers, Microsoft Authenticator apps and software OATH tokens, and deactivates hardware OATH tokens. The user is asked to set up MFA again at next sign-in.
- Revoke sessions: invalidates the user's refresh tokens, forcing sign-in again on all apps. Use it when a device may be lost or stolen.
- Reset password: sets a temporary password that must be changed at next sign-in.
- Delete existing app passwords (Manage user settings).

## [s3] Who can do it
- Authentication Administrators can require non-admin users to reset their password, re-register MFA, or have sessions revoked.
- Users cannot change their own user object, but they can manage their own methods at Security info.
- For users who hold admin roles, a Privileged Authentication Administrator is required (KB-MS-007).
