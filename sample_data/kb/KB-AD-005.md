# KB-AD-005: Corporate VPN - setup and sign-in errors
Category: Network & VPN | Subcategory: VPN
Applies to: Corporate VPN client on Windows and macOS | Last reviewed: synthetic sample

## [s1] Set up the VPN client
1. Install "Corporate VPN" from Company Portal (Windows) or Self Service (macOS).
2. Portal address: vpn.corp.example.com
3. Sign in with your user principal name (for example user@corp.example.com) and approve the MFA prompt.

## [s2] "Authentication failed" errors
- Check that your password has not expired and that your account is not locked (see KB-AD-001 and KB-AD-002). These are the most common causes.
- Your account must be a member of GG-VPN-Users. If it is not, raise an access request (see KB-AD-004).

## [s3] Connected but cannot reach internal sites
1. Disconnect and reconnect the VPN.
2. Restart the computer if it has been running for several days.
3. If the problem continues, or several colleagues are affected, escalate to NET-L2 Remote Access with the time, location and error.
