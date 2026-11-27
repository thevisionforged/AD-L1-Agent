# KB-MS-008: Investigating AD account lockouts with events 4740 and 4625
Category: Account & Access | Subcategory: Account lockout
Audience: engineer
Source: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4740
Source: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/auditing/event-4625
Source: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/account-lockout-threshold

## [s1] Event 4740: a user account was locked out
- Logged when an account is locked out. For domain accounts, look on the domain controllers.
- The key field is Caller Computer Name: the computer the failed logon attempt came from just before the account was locked. That is the device to fix (phone, laptop, server, scheduled task).
- Note: even for computers outside the domain, 4740 shows a computer name, not an IP address.
- Microsoft recommends alerting when an account is locked out from a Caller Computer Name it should not be used from.

## [s2] Event 4625: an account failed to log on
- Shows Status and Sub Status codes, Workstation Name and Source Network Address of the failed attempt.
- Common codes: 0xC000006A bad password; 0xC0000064 unknown or misspelled user name (many in a row can indicate user enumeration); 0xC0000234 account locked out; 0xC0000072 account disabled; 0xC0000193 account expired; 0xC000006F logon outside allowed hours; 0xC0000070 logon from an unauthorised workstation; 0xC000015B logon type not granted on that machine.
- Microsoft recommends monitoring 4625 for service accounts, which should never be locked out.

## [s3] Lockout threshold policy
- Account lockout threshold can be 0 to 999 (0 means never lock out). Windows security baselines suggest 10 as an acceptable starting point.
- A threshold can be abused for denial of service (an attacker locks out many users on purpose), so balance it against password-attack risk. Related settings: Account lockout duration and Reset account lockout counter after.

## [s4] CORP procedure
1. Find the latest 4740 for the user and note the Caller Computer Name.
2. Check 4625 events from that source for the status code (0xC000006A means an old or wrong password is being retried).
3. Have the user update or remove the stored credential on that device (KB-AD-001#s3), then unlock the account.
