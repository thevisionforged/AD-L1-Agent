# KB-AD-009: "The trust relationship between this workstation and the primary domain failed"
Category: Hardware/Endpoint | Subcategory: Domain join
Applies to: Domain-joined Windows PCs | Last reviewed: synthetic sample

## [s1] What it means
- The computer's secure channel (machine account password) with the CORP domain is broken. This often happens after restoring the PC from an old image or after it was offline for a long time.
- Your user account is usually fine. The problem is the computer account.

## [s2] What you can try
1. Connect the PC to the office wired network or VPN-capable network and restart it once.
2. Note the computer name from the label on the device.
3. Do not try to remove the PC from the domain yourself.

## [s3] When to escalate
- The error persists after a restart. Rejoining the domain or resetting the computer account is a privileged action. Escalate to EUC-L2 Desktop Support with the computer name.
