# KB-MS-011: Kerberos tickets and refreshing access after a group change (klist)
Category: Account & Access | Subcategory: Access request
Audience: engineer
Source: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/klist

## [s1] klist basics
- klist (or klist tickets) lists the cached Kerberos tickets for the signed-in user: the ticket-granting ticket (TGT) and service tickets. klist tgt shows the TGT details.
- klist purge deletes all tickets of the logon session. klist sessions lists the logon sessions on the computer.

## [s2] Using it after a group membership change
- Group memberships are captured in Kerberos tickets when they are issued, so a user may still be denied access just after being added to a group.
- The reliable user-facing fix is to sign out of Windows and sign back in (KB-AD-004#s3). An engineer can also purge cached tickets with klist purge so new tickets are requested.
- Verify with whoami /groups that the new group appears.
