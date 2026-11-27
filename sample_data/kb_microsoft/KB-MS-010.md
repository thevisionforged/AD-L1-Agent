# KB-MS-010: AD replication error 1722 and DC time synchronisation (Kerberos)
Category: Hardware/Endpoint | Subcategory: Other
Audience: engineer
Source: https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/replication-error-1722-rpc-server-unavailable
Source: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/maximum-tolerance-for-computer-clock-synchronization
Source: https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings

## [s1] Replication error 1722 (0x6ba) "The RPC server is unavailable"
- Symptoms: dcdiag reports the Replications test failed with error 1722; repadmin /showreps or repadmin /syncall reports status 1722.
- Meaning: a lower network layer reported a connectivity failure, usually the TCP connect failed. Common causes: DNS failure (the most frequent), routing or firewall blocking ports, link/DHCP/WINS failures, IPsec or network authentication failures, resource limits, or the higher-layer service not running.

## [s2] Diagnose
1. DNS: run DCDIAG /TEST:DNS /V /E /F:<logfile> and check that the partner DC's name resolves correctly (also ping -a <IP of the problem server>).
2. Ports: the RPC endpoint mapper listens on TCP 135 and hands out dynamic ports. Test with portqry -n <problem_server> -e 135 and check that the firewall allows the RPC dynamic port range.
3. Check the RPC ClientProtocols registry key under HKLM\Software\Microsoft\Rpc on the DCs. Also consider NIC drivers, UDP fragmentation and SMB signing mismatches.
4. After fixing, force replication with repadmin /syncall and re-run dcdiag.

## [s3] Time sync and Kerberos
- Kerberos uses timestamps to prevent replay attacks. The maximum tolerated clock difference between a client and the DC is set by "Maximum tolerance for computer clock synchronization"; Microsoft advises 5 minutes. Beyond it, Kerberos sign-ins fail.
- Domain members sync time from the domain hierarchy (NT5DS); the PDC emulator of the forest root domain syncs with an external source.
- Useful commands: w32tm /query /status /verbose (current source and offset), w32tm /resync /rediscover (rediscover sources and resync), w32tm /stripchart /computer:<target> (compare offset with a server), w32tm /config /syncfromflags:domhier /update (sync from the domain hierarchy).
