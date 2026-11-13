# WinOwner MCP — v1.1.1

> **Account & Plan Requirements (Read First):** Full MCP (including Windows file write, commands, and desktop control) requires **ChatGPT Business, Enterprise, or Edu** with custom MCP enabled ([OpenAI docs](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)). ChatGPT Pro custom MCP connections are limited to read/fetch. Connecting ChatGPT also requires an **OpenAI Platform org** with `Tunnels Read + Use`, a runtime API key, and a `tunnel_id`.

**Install a local Windows MCP agent (`read_only` by default) and pair it with ChatGPT.** Local install requires no Administrator/UAC prompt and binds strictly to `127.0.0.1:8768`.

## 1. Install locally with one CMD command

Open **Start → Command Prompt (cmd)**, paste and press Enter:

```cmd
powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/releases/download/v1.1.1/go.ps1|iex"
```

*(To test an unpinned branch such as `main`, set `WINOWNER_REF=main` and `WINOWNER_ALLOW_UNPINNED=1` first.)*

WinOwner installs to `%LOCALAPPDATA%\WinOwnerMCP\v1.1.1`, downloads Python 3.12 if missing (via `winget`), generates restricted owner credentials (`gateway_caller.token` and `owner_admin.key`), and registers a hidden per-user logon task (`pythonw.exe`).

**SUCCESS** confirms the *local Windows installation*, **not ChatGPT pairing**. Do not disable SmartScreen or Windows Defender.

## 2. Connect ChatGPT & manage access (double-click launchers)

Press **Win + R**, open `%LOCALAPPDATA%\WinOwnerMCP\v1.1.1`:

- **Connect-ChatGPT.cmd**: Interactive wizard to store your runtime API key with CurrentUser DPAPI, verify `tunnel-client.exe`, register a hidden tunnel logon task, copy your Owner Key via **Show-OwnerKey.cmd** (clears clipboard after 60s), and open ChatGPT Connectors.
- **Show-OwnerKey.cmd**: Copies `runtime\secrets\owner_admin.key` to clipboard without printing it.
- **Grant-Access.cmd / Revoke-Access.cmd**: Grant or revoke time-bounded folder permissions (`read`, `write`, `commands`, `desktop`).
- **Enable-FullControl.cmd / Disable-FullControl.cmd**: Explicitly switch between safe `read_only` and `owner_full_control`.
- **Check-WinOwner.cmd / Uninstall-WinOwner.cmd**: Verify health/auth/ACLs or stop and unregister the local service (preserves user settings and secrets).

**A real WinOwner tool call from ChatGPT is required for end-to-end acceptance.**

[Vietnamese guide](docs/START_HERE.vi.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Security](SECURITY.md) · [Tiếng Việt](README.vi.md)

MIT licensed; independent of OpenAI and Microsoft.
