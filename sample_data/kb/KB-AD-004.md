# KB-AD-004: Access to a shared folder, application or security group
Category: Account & Access | Subcategory: Access request
Applies to: File shares and applications that use AD security groups | Last reviewed: synthetic sample

## [s1] How access works
- Access to shared folders and many applications is granted through Active Directory security groups (for example GG-Finance-Share-RW).
- Each group has an owner who approves requests. The service desk cannot add you without approval.

## [s2] Request access
1. Raise an access request naming the resource and the reason you need it.
2. The group owner, or your manager for standard groups, approves the request.
3. IAM-L2 Identity Ops (AD) adds you to the group after approval.

## [s3] After you have been added
1. Sign out of Windows and sign back in so your Kerberos ticket includes the new group. Remote users should stay on VPN during sign-in.
2. To check, open Command Prompt and run: whoami /groups . The new group should be listed.
3. If you still get "Access is denied" after signing in again, escalate with the exact path and error message.
