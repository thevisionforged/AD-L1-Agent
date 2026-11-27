# KB-MS-009: Repairing a broken domain trust (secure channel) on a workstation
Category: Hardware/Endpoint | Subcategory: Domain join
Audience: engineer
Source: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/test-computersecurechannel?view=powershell-5.1
Source: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/reset-computermachinepassword?view=powershell-5.1

## [s1] Test the secure channel
- Test-ComputerSecureChannel checks the channel between the local computer and its domain and returns True or False. Use -Server to test against a specific domain controller, for example Test-ComputerSecureChannel -Server "DC01.corp.example.com".
- It only works on domain member computers. On domain controllers it gives false errors; use netdom.exe or nltest.exe there.

## [s2] Repair
- Test-ComputerSecureChannel -Repair tries to restore the channel. Add -Credential with an account allowed to reset the computer account.
- Reset-ComputerMachinePassword changes the computer account password used to authenticate to the domain controllers, optionally against a chosen DC: Reset-ComputerMachinePassword -Server "DC01" -Credential CORP\admin-account.
- Run from an elevated PowerShell session on the affected computer (sign in with a cached or local admin account).

## [s3] CORP routing
- This is an EUC-L2 Desktop Support task (privileged). The user only provides the computer name and restarts on the wired network (KB-AD-009).
