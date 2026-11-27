# KB-AD-006: Outlook keeps prompting for a password or will not open
Category: Email & Collaboration | Subcategory: Outlook
Applies to: Outlook for Windows (Microsoft 365) | Last reviewed: synthetic sample

## [s1] Repeated password prompts
- Most often caused by an expired password or a locked account. Check KB-AD-001 and KB-AD-002 first.
- After a password change, close Outlook, sign out of Office (File > Office Account > Sign out) and sign back in.

## [s2] Recreate the Outlook profile
1. Close Outlook.
2. Open Control Panel > Mail (Microsoft Outlook) > Show Profiles > Add.
3. Name the new profile, sign in with your corporate email address and let it configure automatically.
4. Choose "Always use this profile", select the new profile and open Outlook.

## [s3] When to escalate
- The new profile also fails, or the mailbox shows as unavailable. Escalate to Messaging-L2 with the exact error text.
