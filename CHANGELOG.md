# Changelog

## Unreleased — one-click beginner onboarding (RC4 preview)

- Added a double-click **Install-WinOwner.cmd** launcher and **scripts/Setup-WinOwner.ps1**, delegating to the safe local read-only bootstrap. A ZIP extraction now works without first installing Git.
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
