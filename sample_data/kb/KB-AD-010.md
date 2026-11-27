# KB-AD-010: Account disabled or expired (contractors and leavers)
Category: Account & Access | Subcategory: Account disabled / expired
Applies to: CORP domain accounts with an end date | Last reviewed: synthetic sample

## [s1] Symptoms
- Sign-in shows "Your account has expired" or "Your account has been disabled".

## [s2] Why it happens
- Contractor accounts have an end date in Active Directory (accountExpires) and stop working automatically on that date.
- Accounts are disabled when a leaver request is processed or on a security team instruction.

## [s3] What to do
1. Ask your manager to submit an account extension request with the new end date. A user cannot extend their own account.
2. IAM-L2 Identity Ops (AD) extends or re-enables the account only after manager approval.
3. If you think the account was disabled by mistake, say so in the ticket. Identity Ops checks with the security team before re-enabling.
