# Installation — WinOwner MCP v1.2.0 (development)

## Beginner / one-click local setup (no Git required)

Use the version-specific installation instructions: [English](../README.md) | [Tiếng Việt](START_HERE.vi.md). Alternatively download and extract the ZIP, then double-click **Install-WinOwner.cmd**. The wrapper invokes the locally tested bootstrap with Windows PowerShell 5.1 and displays errors instead of silently exiting. Python 3.12 is installed through winget if available, otherwise install from python.org and rerun. The result is **LOCAL_INSTALL_PASS** with the authenticated local profile (read_only by default), not ChatGPT pairing. The one-line installer also runs local health/authentication checks automatically; a manual Check-WinOwner.cmd run is for troubleshooting or later rechecks.

Follow the [English quick start](../README.md) or [Vietnamese quick start](START_HERE.vi.md) for one-command local setup. For tunnel, Platform permissions and OAuth see the [advanced guide](ADVANCED_CHATGPT.vi.md). Prior RC3 release ZIPs do not contain the new launcher; use a newer source snapshot/release.

## Prerequisites

Use Windows 10/11 x64, PowerShell 5.1+ (or PowerShell 7), Python 3.12+, Git, and a dedicated user session. Review the security policy first. Do not expose the local MCP/console endpoints publicly.

## Clean checkout

Run in a fresh directory or disposable VM:

    git clone https://github.com/phuonghuster7/winowner-mcp.git winowner-mcp
    cd winowner-mcp
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

## One-line local setup

Prerequisite: Git on Windows. Paste into PowerShell:

```powershell
git clone https://github.com/phuonghuster7/winowner-mcp.git winowner-mcp; if ($LASTEXITCODE -eq 0) { powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\winowner-mcp\scripts\Bootstrap-WindowsMcp.ps1" }
```

Both the legacy bootstrap and the beginner installer default to read_only; Owner Full Control requires explicit opt-in. Both create the Python 3.12 virtual environment, install dependencies and automatically generate a protected local authentication value, registers a least-privilege Task Scheduler job that runs the standalone adapter at owner logon, then tests /health and HTTP 401 without authentication. It fails closed when an unknown process uses the port or the task name is occupied; no firewall is opened. It can install Python 3.12 with winget where supported. Git itself cannot install or OAuth-pair an agent.

Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Bootstrap-WindowsMcp.ps1 -DryRun` for a non-mutating check. Run with `-Remove` to stop the verified local adapter and unregister the auto-start task. Existing secrets and policies remain on disk for owner-controlled cleanup. Auto-start has been verified across separate command sessions, but a full Windows reboot remains a separate acceptance check.

**LOCAL_INSTALL_PASS does NOT mean the ChatGPT connector is connected.** Connect the private tunnel using Connect-ChatGPT.cmd. Authentication None is valid at ChatGPT plugin creation when backend auth is configured. The local default is read_only; privileged tools require explicit owner verification and grants.

## Verify installer before modifying your workstation

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-WindowsMcpAgent.ps1 -DryRun

Review the output and explicitly decide which local services, scheduled tasks and ports should be installed before running the non-dry-run script. Test installation in a clean VM first; the automated Windows installer and cleanup gates have passed; additional account-pairing acceptance is tracked separately.

## Configure your own identity and scope

Start with example files under config/. Never copy the developer's actual policy.json, trusted_workspaces.json or tunnel-autonomous runtime files. Every machine and ChatGPT account needs its own OAuth owner onboarding, locally bound endpoints, tunnel setup and access scope. One-click defaults to read_only; -OwnerFullControl is an explicit opt-in for authorized owners. Do not enable OWNER_GLOBAL_FILESYSTEM until identity, scope, audit, grants and revocation have been tested.

## Verify runtime

Check local service health, OAuth state and the watchdog without exposing secrets. For example, a configured adapter may listen on 127.0.0.1:8768; a healthy local port is not evidence that the cloud connector is authenticated. Validate the native agent_system_status and a harmless read-only file operation from the intended ChatGPT client.

## Tests and troubleshooting

Run collection and a focused non-mutating subset on a **disposable** checkout:

    .\.venv\Scripts\python.exe -m pytest --collect-only -q
    .\.venv\Scripts\python.exe -m pytest -q tests/test_mcp_gateway.py tests/test_owner_full_control.py tests/test_oauth_dynamic_owner.py tests/test_direct_coding_security.py

No shared OAuth secret or owner delegation is supplied in this repository. For a failed connector, check owner OAuth, tunnel runtime, port binding and logs locally; redact credentials before sharing any diagnostic output.


## Upgrade and repair checks

The version-pinned command installs side by side; it does not automatically transfer
private credentials or reauthorize ChatGPT. Keep the previous folder until both the
new local service and your actual ChatGPT connection work. Rerunning the same
version repairs that installation instead of overwriting configuration. A separately
obtained trusted archive SHA-256 can be supplied as ExpectedArchiveSha256 when running
go.ps1; a tag and HTTPS alone are not an independent integrity check. A rollback is
reported as verified only after the old local service passes health and authentication
checks. Remote pairing remains a separate check.

## Maintainer release alignment

Keep the version pinned to an explicitly reviewed release. When preparing a new release, update the installer default ref, display version, install directory, both READMEs, the beginner guide and `SECURITY.md` together. The onboarding tests check version drift and referenced documentation assets. Passing those checks is not proof that the GitHub tag exists or that a real Windows/ChatGPT connection has been accepted; verify both before publication. Do not silently download or execute unreviewed branch HEADs.
