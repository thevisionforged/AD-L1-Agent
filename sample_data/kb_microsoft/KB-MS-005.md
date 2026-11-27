# KB-MS-005: Temporary Access Pass (TAP) for users who lost their MFA phone
Category: Password & MFA | Subcategory: MFA
Audience: engineer
Source: https://learn.microsoft.com/en-us/entra/identity/authentication/howto-authentication-temporary-access-pass

## [s1] What a TAP is
- A time-limited passcode that lets a user sign in and register new authentication methods, for example a new Authenticator app after losing a phone. It can be single-use or allow several sign-ins during its lifetime.

## [s2] Policy defaults
- Default lifetime 1 hour, minimum 1 hour, maximum 8 hours by default. Allowed range 10 minutes to 43,200 minutes (30 days).
- If the policy enforces one-time use, every TAP in the tenant is one-time use.

## [s3] Who can issue a TAP
- Authentication Administrators can create, view and delete a TAP for members (non-admins), except themselves.
- Privileged Authentication Administrators can do the same for admins and members, except themselves.
- In the Microsoft Entra admin center, sign in as at least an Authentication Administrator to issue one.

## [s4] Recommended lost-phone process (CORP)
1. Verify the caller's identity (manager call-back). Never accept a password or code over chat.
2. Remove the lost device's methods (KB-MS-006) and, if the phone may be stolen, revoke sessions.
3. Issue a short-lived, one-time TAP and give it to the user over a verified channel.
4. The user signs in with the TAP and registers Microsoft Authenticator at Security info (KB-MS-012).
