# KB-AD-003: MFA - new phone, lost phone or re-registration
Category: Password & MFA | Subcategory: MFA
Applies to: All CORP users with Microsoft Authenticator | Last reviewed: synthetic sample

## [s1] Symptoms
- No MFA prompt arrives after a phone change or reinstall of the Authenticator app.
- "Approve sign-in request" is shown but the phone does not receive it.

## [s2] Self-help steps - you still have the old phone or another method
1. Sign in at My Sign-ins security info (https://mysignins.corp.example.com/security-info) using a method that still works.
2. Choose "Add sign-in method" > Authenticator app and scan the QR code with the new phone.
3. Set the new method as default, then delete the old phone entry.

## [s3] Lost phone or no working method
- You cannot re-register yourself. Removing MFA methods is a privileged action.
- Escalate to IAM-L2 Identity Ops (AD). They verify your identity through your manager and then reset your MFA methods.
- If you believe the phone was stolen, say so in the ticket. Identity Ops will also revoke active sessions.
