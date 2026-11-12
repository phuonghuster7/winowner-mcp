# WinOwner MCP — v1.1.0

**Control your Windows computer from ChatGPT, with verified owner access.** An authenticated MCP tunnel connects ChatGPT to your private Windows machine.

[![CI](https://github.com/phuonghuster7/winowner-mcp/actions/workflows/ci-security.yml/badge.svg)](https://github.com/phuonghuster7/winowner-mcp/actions/workflows/ci-security.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Development version](https://img.shields.io/badge/development-v1.1.0-blue)

[Tiếng Việt](README.vi.md) · [Beginner's A–Z guide](docs/START_HERE.vi.md) · [Connect ChatGPT](docs/CONNECT_CHATGPT.md) · [Security](SECURITY.md) · [Releases](https://github.com/phuonghuster7/winowner-mcp/releases)

> **v1.1.0 is a development version, not an announced stable release.** The agent can modify files and interact with Windows using your account's permissions. It is not a sandbox or an independently certified security product.

## Double-click installation for beginners

1. On GitHub choose **Code → Download ZIP**.
2. Right-click the ZIP and select **Extract All**.
3. Double-click **Install-WinOwner.cmd** inside the extracted folder.

The installer runs in one command window, creates a private Python 3.12 environment and schedules the local MCP adapter to run when you log into Windows. If Python is missing, it tries Windows Package Manager (winget). No Git, command-line knowledge or manual internal token entry is needed. No Notepad launch and no final keypress. The result is saved as **WinOwner-SETUP-RESULT.txt** beside the installer, so beginners can review it after the window closes.

**Default: Owner Full Control mode.** The local agent is configured for full control **only for an authenticated, authorized owner with a valid delegation**. It offers file and code editing, command execution, Git operations and desktop controls within the Windows account's rights. Unauthenticated users and non-owners cannot use those privileges. Optional restricted mode: run the setup script with -ReadOnly.

## Connect ChatGPT the first time

Windows installation and account pairing are separate:
1. Obtain your own tunnel ID at [OpenAI Platform Tunnels](https://platform.openai.com/settings/organization/tunnels) and create a runtime key at [Platform API Keys](https://platform.openai.com/settings/organization/api-keys). The Platform organization must grant Tunnels Read + Use; creating a tunnel requires Read + Manage.
2. Configure the official tunnel-client to point to the local address 127.0.0.1:8768/mcp, following [A–Z instructions](docs/START_HERE.vi.md).
3. In ChatGPT Plugins choose **Add custom MCP server → Tunnel**, complete OAuth and test an actual tool call.

**Required account authentication and ChatGPT permission confirmations cannot be silently bypassed.** The local installer generates its own secret without asking you to type it; a new ChatGPT connection still needs the account owner's authorization.

## Capabilities and current status

- Owner-authenticated filesystem operations with hash checks, backup and audit
- Git, coding tasks, shell commands, tests, Windows mouse and keyboard tools
- Revocable owner grants, local-only service and Emergency Stop

The previous release remains historical. Before publishing v1.1.0 as stable, the project requires an independent clean-Windows install, reboot, rollback and real first-time ChatGPT OAuth end-to-end acceptance.

[Installation](docs/INSTALLATION.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

MIT licensed; not endorsed by OpenAI or Microsoft.