# Changelog

## v1.1.1 — Nontech onboarding hardening

- **Safe default (`read_only`)**: Changed installer default profile back to `read_only` and added `Enable-FullControl.cmd` (explicit confirmation phrase) / `Disable-FullControl.cmd` via `scripts/Switch-WinOwnerProfile.ps1`.
- **Usable Owner Key**: `Setup`/`Bootstrap` now provisions `runtime\secrets\owner_admin.key` at install with verified `icacls` ACLs; added `Show-OwnerKey.cmd` (clipboard copy without printing, 60s auto-clear) and bilingual Vietnamese/English OAuth consent labels.
- **Delegation wrappers**: Added `Grant-Access.cmd` (folder dialog, unsafe root rejection, capability picker, 1h–24h TTL) and `Revoke-Access.cmd`.
- **Hidden service**: Scheduled task now runs via `.venv\Scripts\pythonw.exe` without showing a console window at logon while preserving `runtime\logs` and `-Remove` cleanup.
- **Safe upgrade path**: Added `go.ps1 -Update` and `Bootstrap-WindowsMcp.ps1 -Upgrade` with secrets/`policy.json`/`oauth*.db` backup, verified task re-point, and automatic rollback on health check failure.
- **Pinned `go.ps1` + PS 5.1 `iex`**: Pinned default `$Ref` to release tag `v1.1.1` with `SHA256SUMS.txt` verification, explicit TLS 1.2, `-AllowUnpinned` gate for `main`, and `WINOWNER_*` env-var overrides for `irm | iex`.
- **Connect wizard**: Added `Connect-ChatGPT.cmd` + `scripts/Connect-ChatGPT.ps1` + `scripts/Run-WinOwnerTunnel.ps1` (Vietnamese prerequisites, DPAPI secret storage, `SHA256SUMS.txt` verification for `tunnel-client.exe`, hidden logon task, `Show-OwnerKey` + ChatGPT Connectors launch).
- **Docs & release checklist**: Updated READMEs, `docs/START_HERE.vi.md` (with screenshot placeholders, SmartScreen guidance, uninstall, and glossary), `SECURITY.md`, and moved `PUBLIC_RELEASE_CHECKLIST.md` to `docs/maintainers/`.

## v1.1.0 — ChatGPT owner-first onboarding

- Added a double-click **Install-WinOwner.cmd** launcher and **scripts/Setup-WinOwner.ps1**, selecting authenticated owner_full_control while preserving OAuth and owner delegation. A ZIP extraction now works without first installing Git.
- Added Vietnamese [A–Z setup guide](docs/START_HERE.vi.md) documenting Platform tunnel roles, runtime key vs admin key, supported tunnel-client, OAuth and an actual ChatGPT tool call.
- Updated both READMEs, install/pairing docs, release inventory, source guard and Windows CI checks.
- Fixed Repair -DryRun on a fresh checkout to validate the example policy without writing a live policy or report; rollback without a valid baseline remains fail-closed.
- Verified the local installer dry-run, selected regression tests and package inventory. Fresh-machine cloud OAuth/tunnel E2E and reboot recovery still require separate acceptance before stable production release.


## Unreleased — P0 Windows bootstrap and OAuth hardening

- Upgraded vulnerable runtime dependencies (MCP SDK 2.3.0, Pillow 13.0.0, cryptography 50.0.2, AnyIO 4.16.0, pydantic-settings 2.14.2), with pip-audit reporting 0 known advisories on the locked top-level requirements as tested; the audit now fails CI on new findings.
- One-line local read-only bootstrap using only pinned runtime dependencies, with a least-privileged per-user Task Scheduler action that survives the installer process and automatically starts at interactive owner logon.
- Verifies local health, HTTP 401 for anonymous MCP calls, idempotent installation and controlled removal; remote owner pairing remains a separate explicit step.
- OAuth redirect URI strict host validation, legacy pass-the-hash rejection and removal of loopback-only authentication fallback.
- Adds Windows CI integration for install/start/idempotency/removal and dedicated OAuth regression tests.
- Production RC3 remains unchanged; this unreleased source is not a stable public deployment certification.

## Unreleased — WinOwner MCP branding

- Renamed the public repository to `phuonghuster7/winowner-mcp` and adopted **WinOwner MCP** as the public product name.
- Added English and Vietnamese README files, accurate compatibility/permission descriptions and architecture overview.
- Updated publication URLs and future source-package naming while retaining existing runtime entry points for backward compatibility.
- The existing v1.0.0-rc3 tag and release assets remain unchanged; PR #9 handles the separate P0 bootstrap/security work.

## v1.0.0-rc3 — public-source candidate (2026-11-04)

- OAuth-based verified owner authorization, durable delegation and rolling capability grants.
- Owner coding and filesystem operations, including opt-in OWNER_GLOBAL_FILESYSTEM with audit and SHA-256 verification.
- Local gateway, Windows desktop tooling, watchdog and authenticated cloud adapter.
- Separate private production configuration from portable example configurations.
- **Status:** public-source prerelease; not certified production-ready.
