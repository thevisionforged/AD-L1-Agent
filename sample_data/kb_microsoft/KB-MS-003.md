# KB-MS-003: Microsoft Entra password rules and expiration
Category: Password & MFA | Subcategory: Password reset / change
Audience: end user
Source: https://learn.microsoft.com/en-us/entra/identity/authentication/concept-sspr-policy

## [s1] Password rules for cloud accounts
- Between 8 and 256 characters.
- Must contain three of these four: lowercase letters, uppercase letters, numbers, symbols.
- Your organisation's on-premises AD policy can be stricter for synchronised accounts (the CORP policy in KB-AD-002 requires 14 characters and expiry every 90 days).

## [s2] Expiration
- For cloud-only accounts, current tenants default to no password expiration. Tenants created before 2021 have a 90-day expiration by default.
- Accounts synchronised from on-premises AD follow the AD password age. That is why CORP passwords still expire every 90 days.

## [s3] For engineers: check never-expires settings
- The never-expires flag for a cloud user is shown by the DisablePasswordExpiration value in the user's PasswordPolicies, read with Get-MgUser (Microsoft Graph PowerShell). User Administrators can change it; by default only accounts that are not synchronised by Microsoft Entra Connect can be set never to expire.
