# WinOwner MCP v1.2.0 — private Windows MCP for ChatGPT

**One CMD installer. Read-only by default; owner Full Control requires explicit approval.**

## 1. Install

Open Windows CMD and paste this pinned release command only after v1.2.0 is published:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

The installer prepares Windows in %LOCALAPPDATA%\WinOwnerMCP\v1.2.0. Git is not required. **LOCAL_INSTALL_PASS** means local service health, not that the ChatGPT app has connected.

## 2. Connect ChatGPT

Open the installation directory. Double-click Check-WinOwner.cmd, then Connect-ChatGPT.cmd. Enter your tunnel ID and hidden runtime API key. This verifies the official tunnel-client ZIP checksum, creates a private profile and registers a background scheduled task.

On ChatGPT select Plugins → Add custom MCP server → Connection: Tunnel → Authentication: None. Select your private tunnel. Confirm functionality by calling a read-only tool. Your account/workspace still needs permission to add custom MCP servers.

## 3. Owner permissions

Authentication None does not mean anonymous access to Windows. The backend requires authentication; owner commands require verified owner delegation. Run Grant-OwnerAccess.cmd to review and confirm elevated scope, and Show-OwnerKey.cmd only when a trusted owner-authorization field specifically needs the token. Never paste owner tokens into the OpenAI Platform runtime API key form; clipboard content is sensitive.

## 4. Stop and revoke

Uninstall-WinOwner.cmd unregisters the local service but preserves configuration and secrets. Revoke the corresponding remote ChatGPT plugin and tunnel access separately.

[Quick guide](docs/START_HERE.vi.md) · [Authentication None](docs/AUTH_NONE.vi.md) · [Security](SECURITY.md) · [Vietnamese](README.vi.md)

MIT licensed, independent of OpenAI and Microsoft.