# KB-MS-002: Microsoft Entra self-service password reset (SSPR) - how it works
Category: Password & MFA | Subcategory: Password reset / change
Audience: end user
Source: https://learn.microsoft.com/en-us/entra/identity/authentication/concept-sspr-howitworks

## [s1] Register first
- Before you can reset your own password you must register authentication methods. Register at https://aka.ms/ssprsetup, or at the combined security info page https://aka.ms/setupsecurityinfo. Your organisation may also ask you to register when you sign in.
- Register more than one method if you can, so you still have a way in if you lose one (for example your phone).
- Methods the organisation can allow for SSPR include: Microsoft Authenticator notifications, software or hardware OATH codes, SMS, voice call and email one-time passcode. You can only use methods your administrator has enabled.

## [s2] Reset or unlock your password yourself
1. Go to https://aka.ms/sspr.
2. Enter your work account and complete the verification. Your organisation requires one or two methods.
3. Choose a new password that meets the policy (KB-MS-003).
4. If you are locked out, choose "I forgot my password": this resets the cloud lockout timer to zero so you do not have to wait.

## [s3] Hybrid accounts (password managed in on-premises AD)
- If password writeback is configured, a reset in the cloud is written back to Active Directory, so the new password works on domain-joined PCs too.
- If writeback is not configured and your password is managed on-premises, SSPR tells you to contact your administrator. Raise a ticket (KB-AD-002#s4).

## [s4] When SSPR will not work
- You have not registered the required number of methods: you see an error page and an administrator must reset your password after verifying your identity.
- Administrator accounts always use a stricter "two-gate" policy (two methods, no security questions) that cannot be changed.
