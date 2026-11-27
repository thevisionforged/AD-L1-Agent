# KB-AD-002: Password expired or needs to be changed
Category: Password & MFA | Subcategory: Password reset / change
Applies to: All CORP domain accounts | Last reviewed: synthetic sample

## [s1] Password policy
- CORP passwords expire every 90 days. You get reminders from 14 days before expiry.
- Minimum length is 14 characters. The last 24 passwords cannot be reused.
- Never share your password with anyone, including the service desk. IT will never ask for it.

## [s2] Change a password that has not expired yet
1. On a domain-joined Windows PC, press Ctrl+Alt+Del and choose "Change a password".
2. Remote workers: connect to the corporate VPN first, then change the password, then lock and unlock the PC so the cached credential updates.
3. Update the new password on phones and other devices to avoid lockouts (see KB-AD-001).

## [s3] Reset an expired or forgotten password (self-service)
1. Go to the Self-Service Password Reset portal (https://passwordreset.corp.example.com).
2. Enter your user ID and complete the verification with your registered MFA method.
3. Choose a new password that meets the policy in section s1.
4. If you are remote, connect to VPN with the new password, then lock and unlock the PC.

## [s4] When to escalate
- You are not registered for self-service password reset, or verification fails.
- Escalate to SD-L1 Service Desk for identity verification. An agent verifies your identity with your manager before any reset. A password reset is a privileged action and is done only after verification.
