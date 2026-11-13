# CODEX_REPORT — WinOwner MCP v1.1.1 Nontech Onboarding Hardening

- **Branch:** `feat/nontech-onboarding-v1.1.1` (branched from `main` @ `e15d09f`)
- **Commits:**
  - `d1c5024` — `T1: default to safe read_only profile and add explicit full-control toggle`
  - `93e5ae1` — `T2: create owner_admin.key at install with ACL check, add Show-OwnerKey.cmd, localize consent page`
  - `59d111d` — `T3: add Grant-Access.cmd and Revoke-Access.cmd delegation wrappers`
  - `a5395bf` — `T4: run scheduled task hidden via pythonw.exe`
  - `1c3c932` — `T5: add safe upgrade path with backup, task re-point, and rollback`
  - `ad51a50` — `T6: pin go.ps1 release tag with SHA-256 check, TLS 1.2, and PS5.1 iex env vars`
  - `745750a` — `T7: add Connect-ChatGPT.cmd interactive tunnel and ChatGPT onboarding wizard`
  - `fedc19e` — `T8: update v1.1.1 onboarding docs, glossary, and maintainer checklist path`
  - `8b20c88` — `T9: add T1-T6 onboarding tests and run security gates`

---

## Task Summary (T1–T9)

### T1 — Safe default profile (`DONE`)
- **Files changed:** `scripts/Setup-WinOwner.ps1`, `scripts/Bootstrap-WindowsMcp.ps1`, `go.ps1`, `scripts/Switch-WinOwnerProfile.ps1`, `Enable-FullControl.cmd`, `Disable-FullControl.cmd`, `scripts/Test-WinOwnerOneClick.ps1`, `scripts/Test-WinOwnerOnline.ps1`, `scripts/Build-ReleasePackage.ps1`.
- **Verified on real Windows:**
  - `Setup-WinOwner.ps1 -DryRun -Port 18769` -> `DRY_RUN profile=read_only Python312=True port_free=True task_exists=False` (exit 0).
  - `Switch-WinOwnerProfile.ps1 -Profile owner_full_control -Port 18791 -ConfirmPhrase NOPE` -> non-zero exit without modifying any task.
- **Mocked / reasoned:** Live interactive double-click of `Enable-FullControl.cmd` / `Disable-FullControl.cmd` on port `8768` was not executed to avoid touching the live machine instance.

### T2 — Owner key made usable (`DONE`)
- **Files changed:** `scripts/Bootstrap-WindowsMcp.ps1`, `scripts/Check-WinOwner.ps1`, `scripts/Show-OwnerKey.ps1`, `Show-OwnerKey.cmd`, `mcp-gateway/http_server.py`.
- **Verified on real Windows:**
  - `Bootstrap-WindowsMcp.ps1` live install on disposable port `18781` created `runtime\secrets\owner_admin.key` and verified `icacls` ACLs (`Assert-RestrictedAcl`).
  - `Show-OwnerKey.ps1 -KeyPath <tmp> -NoWait` copied secret to clipboard without printing the secret to stdout/stderr.
  - `pytest -q tests/test_oauth_dynamic_owner.py` -> `4 passed`; `_render_consent_html` renders Vietnamese + English labels.
- **Open question / Behavior note:** `mcp-gateway/http_server.py:2620` checks `owner_token` on `POST /oauth/authorize` against `app.state.caller_auth_token` (`gateway_caller.token`), whereas `owner_admin.key` is used for `X-MCP-Owner-Admin-Token` on delegation endpoints. Per the T2 constraint ("Do not change the auth logic"), auth logic in `http_server.py` was left untouched.

### T3 — Delegation wrapper (`DONE`)
- **Files changed:** `Grant-Access.cmd`, `Revoke-Access.cmd`, `scripts/Grant-Access.ps1`, `scripts/Revoke-Access.ps1`.
- **Verified on real Windows:**
  - `Grant-Access.ps1 -Folder "D:\winowner-mcp-v1.1.1" -Capability read -Hours 1 -Yes -DryRun` -> `[THÀNH CÔNG] DRY_RUN: folder=D:\winowner-mcp-v1.1.1 caps=FILE_READ ttl=3600` (exit 0).
  - `Grant-Access.ps1 -Folder "C:\" ...` -> exit 1 (refused).
  - `Grant-Access.ps1 -Folder $env:USERPROFILE ...` -> exit 1 (refused).
  - `Revoke-Access.ps1 -GrantId "grant_test" -Yes -DryRun` -> exit 0.
- **Mocked / reasoned:** FolderBrowserDialog UI picker and live `oauth.db` provisioning via double-click were not interactively clicked.

### T4 — Hidden service window (`DONE`)
- **Files changed:** `scripts/Bootstrap-WindowsMcp.ps1`.
- **Verified on real Windows (disposable port `18781`):**
  - `Bootstrap-WindowsMcp.ps1 -Port 18781 -NoPythonInstall` -> `LOCAL_INSTALL_PASS: WinOwnerMCP-18781 running (read_only)` (exit 0).
  - `(Get-ScheduledTask -TaskName "WinOwnerMCP-18781").Actions[0].Execute` -> `D:\winowner-mcp-v1.1.1\.venv\Scripts\pythonw.exe`.
  - Log file created: `runtime\logs\adapter-18781-20261113T075605Z.stdout.log`.
  - `Bootstrap-WindowsMcp.ps1 -Port 18781 -Remove` -> `TASK_REMOVED: WinOwnerMCP-18781, verified local listener stopped.` (exit 0).

### T5 — Upgrade path (`DONE`)
- **Files changed:** `go.ps1`, `Install-WinOwner.cmd`, `scripts/Setup-WinOwner.ps1`, `scripts/Bootstrap-WindowsMcp.ps1`, `tests/test_upgrade_path.py`.
- **Verified on real Windows:**
  - `pytest -q tests/test_upgrade_path.py` -> `1 passed in 2.46s` (tests `Find-PreviousInstallRoot`, `Backup-WinOwnerState`, `Restore-WinOwnerState` across fake `v1.1.0` and `v1.1.1` folders preserving `gateway_caller.token`, `owner_admin.key`, `oauth.db`, `policy.json`, plus `Bootstrap-WindowsMcp.ps1 -DryRun -Upgrade`).
- **Mocked / reasoned:** Live Task Scheduler `Set-ScheduledTask` rollback path on a broken upgraded health check was verified by code inspection, not by injecting a broken binary into a live scheduled task.

### T6 — `go.ps1` trust + one-liner correctness (`DONE`)
- **Files changed:** `go.ps1`, `.github/workflows/ci-security.yml`.
- **Verified on real Windows PowerShell 5.1:**
  - Local HTTP server serving `go.ps1` with `powershell -NoP -C "irm http://127.0.0.1:<port>/go.ps1 | iex"` and `WINOWNER_DRYRUN=1` -> `IEX_EXIT=0`, `DRY_RUN_PASS: download=https://github.com/phuonghuster7/winowner-mcp/releases/download/v1.1.1/winowner-mcp-v1.1.1.zip checksums=https://github.com/phuonghuster7/winowner-mcp/releases/download/v1.1.1/SHA256SUMS.txt pinned=True`.
  - `WINOWNER_REF=main` without `WINOWNER_ALLOW_UNPINNED=1` -> `UNPINNED_BLOCKED_EXIT=1`.
  - `WINOWNER_REF=main` with `WINOWNER_ALLOW_UNPINNED=1` -> `UNPINNED_ALLOWED_EXIT=0`.
  - Bad SHA-256 on `-ArchivePath` -> aborted non-zero with SHA-256 error.

### T7 — Connect wizard (`DONE`)
- **Files changed:** `Connect-ChatGPT.cmd`, `scripts/Connect-ChatGPT.ps1`, `scripts/Run-WinOwnerTunnel.ps1`.
- **Verified on real Windows:**
  - `tunnel-client.exe init --help` inspected directly; flags used (`--profile`, `--profile-dir`, `--tunnel-id`, `--mcp-server-url`, `--control-plane-api-key-ref`, `--control-plane-base-url`, `--health-listen-addr`, `--force`) and `doctor` / `run` flags match the installed `tunnel-client.exe` CLI.
  - `Connect-ChatGPT.ps1 -DryRun -TunnelId tunnel_0123456789abcdef -ApiKey <SecureString>` -> exit 0, `local_tunnel_client=True`.
  - PowerShell AST parse on `Run-WinOwnerTunnel.ps1` -> 0 parse errors.
- **Unverified / live-only:** Live download of `openai/tunnel-client` from GitHub API and live `tunnel-client run` connection to OpenAI control plane were not executed in this isolated test branch so the production tunnel is undisturbed. Note that OpenAI publishes `SHA256SUMS.txt` and `*.sigstore.json` (Authenticode status is `NotSigned`), so `Connect-ChatGPT.ps1` verifies `SHA256SUMS.txt` and prints both SHA-256 and `Get-AuthenticodeSignature` status.

### T8 — Docs (`DONE`)
- **Files changed:** `README.md`, `README.vi.md`, `docs/START_HERE.vi.md`, `SECURITY.md`, `CHANGELOG.md`, `docs/maintainers/PUBLIC_RELEASE_CHECKLIST.md` (moved from `docs/PUBLIC_RELEASE_CHECKLIST.md`), `scripts/public_release_guard.py`, `scripts/Build-ReleasePackage.ps1`.
- **Verified:** `python scripts/public_release_guard.py` -> `PUBLIC RELEASE GUARD: PASS`.

### T9 — Tests and gates (`DONE`)
- **Files changed:** `tests/test_oneclick_onboarding.py`, `tests/test_nontech_onboarding_v111.py`.
- **Exact gate results (run after final code/test edits):**
  - `python scripts/public_release_guard.py` -> `PUBLIC RELEASE GUARD: PASS (baseline paths, file inventory and high-risk identifiers)` (exit 0)
  - `python -m compileall -q backend mcp-gateway desktop-agent runtime tests` -> exit 0
  - `python -m pytest -q tests/test_oneclick_onboarding.py tests/test_s10_machine_independent_installer.py tests/test_upgrade_path.py tests/test_nontech_onboarding_v111.py tests/test_oauth_dynamic_owner.py tests/test_p0_bootstrap_auth_regressions.py tests/test_production_oauth_persistence_security.py tests/test_red_team_security.py::TestRedTeamSecurity::test_20_rollback_restores_last_known_good_state` -> `44 passed, 5 warnings in 23.26s` (exit 0)
  - PowerShell AST parse across 28 `.ps1` files -> `PS_FILES=28 PARSE_ERRORS=0`
  - `PSScriptAnalyzer` (`Invoke-ScriptAnalyzer -Severity Error,Warning` across 28 `.ps1` files): `PSSA_ERRORS=1 PSSA_WARNINGS=212`. The single Error is `PSAvoidUsingConvertToSecureStringWithPlainText` in pre-existing untouched `scripts/Install-WindowsMcpAgent.ps1:307`; all files added or modified in T1–T9 have **0** `PSScriptAnalyzer` Errors.

---

## Deviations from Prompt
- `Install-WinOwner.cmd` / `go.ps1` live E2E against GitHub Release `v1.1.1` assets cannot download from `releases/download/v1.1.1/...` until tag `v1.1.1` and its release ZIP + `SHA256SUMS.txt` are published on GitHub. Locally tested with `WINOWNER_DRYRUN=1` via `irm | iex` and offline `-ArchivePath` ZIPs.
- `tests/test_oneclick_onboarding.py` was updated from `v1.1.0` / `owner_full_control` / `raw/main/go.ps1` assertions to `v1.1.1` / `read_only` / `releases/download/v1.1.1/go.ps1`.
