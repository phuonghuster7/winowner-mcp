# WinOwner MCP v1.2.0 — private Windows MCP for ChatGPT

**Windows 10/11 · One CMD installer · Read-only by default.**

This guide describes v1.2.0. Use the pinned installer only after that release is published and verified.

## 1. Install

Open Windows CMD. Use the version-pinned installer from the verified release:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

The local installation is in `%LOCALAPPDATA%\WinOwnerMCP\v1.2.0`. Git is not required. Do not disable security software to install.

## 2. Connect ChatGPT

Open the installation directory. `Check-WinOwner.cmd` reports local service health. `Connect-ChatGPT.cmd` guides private tunnel configuration; the tool itself verifies the pinned tunnel-client package and checks readiness.

Use the [connection guide](docs/CONNECT_CHATGPT.md) with an account/workspace permitted to add custom MCP servers. Authentication: None is not anonymous Windows access. Use a read-only status check to verify the actual connection.

| Result | Meaning |
| --- | --- |
| `LOCAL_INSTALL_PASS` / `CHECK_PASS` | Local service healthy |
| `TUNNEL_SETUP_PASS` / `TUNNEL_READY` | Tunnel readiness verified; ChatGPT not yet verified |
| Successful read-only tool call from ChatGPT | Actual connection verified |

## 3. Owner permissions

Keep read-only as the default. Owner access requires separate verified identity, explicit consent and scoped delegation. `Grant-OwnerAccess.cmd` and `Show-OwnerKey.cmd` are sensitive administrative utilities, not required for read-only setup. Never share or paste private credentials into chat. Owner pairing and revocation require separate acceptance testing.

## 4. Stop and revoke

`Uninstall-WinOwner.cmd` checks/removes the local service and verified tunnel scheduled task, preserving configuration and secrets. Revoke remote plugin/tunnel access separately.

Rerunning the same version repairs that installation; side-by-side upgrades keep the previous folder. Remote pairing may need to be checked again. See [upgrade and repair](docs/INSTALLATION.md).

[Quick guide](docs/START_HERE.vi.md) · [Authentication None](docs/AUTH_NONE.vi.md) · [Security](SECURITY.md) · [Vietnamese](README.vi.md)

MIT licensed, independent of OpenAI and Microsoft.