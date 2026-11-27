# KB-AD-001: Account locked out (Active Directory)
Category: Account & Access | Subcategory: Account lockout
Applies to: All CORP domain accounts | Last reviewed: synthetic sample

## [s1] Symptoms
- Windows sign-in shows "The referenced account is currently locked out and may not be logged on to".
- Outlook, Teams or VPN repeatedly prompt for credentials and then fail.
- The lockout happens again shortly after the account is unlocked.

## [s2] Why it happens
- The CORP domain policy locks an account after 5 failed sign-in attempts within 30 minutes.
- A locked account unlocks automatically after 30 minutes.
- Repeated lockouts are usually caused by an OLD password saved somewhere: a phone mail app, a mapped drive with stored credentials, Windows Credential Manager, a scheduled task or a disconnected Remote Desktop session.

## [s3] Self-help steps
1. Wait 30 minutes for the automatic unlock, or unlock it yourself at the Self-Service Password Reset portal (https://passwordreset.corp.example.com) if you are registered.
2. On every phone and tablet, update or remove the CORP mail account so it stops using the old password.
3. Open Control Panel > Credential Manager > Windows Credentials and remove entries that reference CORP or corp.example.com.
4. Sign out of any Remote Desktop sessions you left open on other computers.
5. Sign in again with your current password.

## [s4] When to escalate
- The account locks out 3 or more times in 24 hours, or locks again immediately after an unlock.
- You are not registered for self-service unlock and cannot wait 30 minutes.
- Escalate to IAM-L2 Identity Ops (AD). They review domain controller lockout events (event ID 4740) to find the source computer. The service desk never asks for your password.
