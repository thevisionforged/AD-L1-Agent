# KB-MS-004: Microsoft Entra smart lockout and how it works with on-premises AD lockout
Category: Account & Access | Subcategory: Account lockout
Audience: end user
Source: https://learn.microsoft.com/en-us/entra/identity/authentication/howto-password-smart-lockout

## [s1] What the user sees
- After too many wrong passwords, the cloud sign-in shows a message that the account is temporarily locked and to try again later.
- Smart lockout ignores repeats of the same wrong password: it remembers the last three bad password hashes, so retyping the same wrong password does not keep adding to the count.
- Locations you normally sign in from and unfamiliar locations have separate counters, which helps stop an attacker elsewhere from locking you out.

## [s2] What the user can do
1. Wait for the lockout period to end, then sign in with the correct password.
2. Or reset the password at https://aka.ms/sspr and choose "I forgot my password". That resets the lockout timer to zero.
3. If the lockout keeps coming back, a device is probably retrying an old password. Follow KB-AD-001#s3.

## [s3] For engineers: defaults and hybrid alignment
- Defaults: lockout threshold 10 failed attempts (Azure Public tenants), lockout duration 60 seconds. Repeated lockouts make the duration longer.
- With pass-through authentication, the Entra threshold must be lower than the AD DS threshold (AD at least two to three times higher), and the Entra lockout duration must be longer than the AD duration. Note that Entra uses seconds and AD uses minutes. This lets Entra stop password-spray attacks before they lock the on-premises account.
- An administrator can unlock a cloud account locked by smart lockout without waiting, by resetting the user's password.
