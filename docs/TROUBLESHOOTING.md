# Troubleshooting

## Check the basics

```powershell
python --version
Test-NetConnection 127.0.0.1 -Port 8768
python .\runtime\watchdog.py --status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-WindowsMcpStack.ps1
```

The port check measures **local listening only**. A working ChatGPT connector also requires a healthy authenticated tunnel, correct owner identity, valid OAuth token and a compatible MCP endpoint.

## Connection issues

- **ChatGPT shows `Not Verified` in Setup Center (`LOCAL_READY`):** `/health` and tunnel `/readyz` alone never verify ChatGPT connectivity. Run a harmless read-only prompt in ChatGPT (e.g. `agent_system_status` or `agent_workspace_list`) and click **Verify Connection** in WinOwner Setup Center (`http://127.0.0.1:8767/setup`).
- **`UNVERIFIABLE_CLIENT_IDENTITY` when requesting owner pairing:** `Authentication: None` over a shared tunnel does not provide a verifiable per-client identity and is strictly capped at `Read`. Reconfigure the tunnel with OAuth 2.1 enabled (`Connect-ChatGPT.ps1 -Reconfigure -AuthMode oauth`), connect ChatGPT with OAuth 2.1, and approve the one-time pairing challenge (`XXXX-XXXX`) in WinOwner Setup Center → **Review Owner Access** or `Grant-OwnerAccess.cmd`. Never paste raw owner tokens into ChatGPT.
- **429 on SSE probe:** Check the local `tunnel-client`, restart backoff and any request quota; retry with normal operational procedures, not with unsafe anonymous fallback.
- **Tunnel unseen or reconfiguration failure:** Run `Check-WinOwner.cmd` or use **Reconfigure Tunnel** in WinOwner Setup Center (`Connect-ChatGPT.ps1 -Reconfigure` validates the new configuration before stopping the live tunnel and automatically rolls back on failure).
- **Owner false / unauthorized:** Check **WinOwner Setup Center** (`Permissions` and `Owner Identity` rows) or `scripts/manage_owner_delegation.py status --json` to inspect active client-bound delegation, grant TTL/expiry, revocation status, and server profile (`read_only` vs `owner_full_control`).
- **Outside workspace or Git worktree rejected:** Review the approved workspace list and capability grant scope. Linked Git worktrees must have their common Git directory inside an approved workspace boundary and must not contain symlinks, NTFS junctions, hooks, filters, external drivers, or config includes.

## File, test and CI issues

- **SHA-256 mismatch:** Reread the file and recompute its hash; do not force-overwrite concurrent changes.
- **GitHub Actions fails:** Read the job logs, reproduce on a clean Windows checkout and fix the root cause. Headless runners cannot stand in for a physical desktop or a live cloud connector.
- **Installer fails:** Run the installer with `-DryRun` and check available ports, Python dependencies and local permissions; do not terminate other services blindly.
- **Emergency stop active:** Diagnose why it was set and review the audit before manually clearing it.

## Safe recovery

Use the local emergency stop if unauthorized actions are suspected, disconnect the tunnel, revoke affected grants and rotate keys before restoring from a verified backup. Do not post raw diagnostic logs or OAuth information on public issues.
