# WinOwner MCP — v1.1.0

**Install a Windows MCP agent with one CMD command to connect it to ChatGPT.** The local installer defaults to Owner Full Control, effective only for an authenticated, authorized owner.

## Check ChatGPT access first

Open **ChatGPT web → Plugins → + → Add custom MCP server**. Windows write/modify tools require a workspace that supports custom **full MCP** connections. According to [OpenAI's current documentation](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt), full MCP is available to **Business and Enterprise/Edu**, while Pro custom MCP connections are limited to read/fetch. If your account lacks the custom MCP option, the Windows component can still install but ChatGPT pairing cannot be completed through this guide.

## Install locally with one CMD command

Open **Start → Command Prompt (cmd)**, paste and press Enter:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"

WinOwner downloads to %LOCALAPPDATA%\WinOwnerMCP\v1.1.0, installs Python 3.12 if missing and winget is available, and creates a local startup task. No internal MCP token entry is required.

**SUCCESS** confirms the *Windows installation*, **not ChatGPT account pairing**. For failures, read the error in CMD. Do not disable SmartScreen or Defender to work around warnings.

## Check and stop the service

Press **Win + R**, paste %LOCALAPPDATA%\WinOwnerMCP\v1.1.0 and press Enter. Double-click **Check-WinOwner.cmd** to check the local service. **Uninstall-WinOwner.cmd** stops the agent and unregisters auto-start. Program files, settings and credentials remain; this is not a complete deletion of data.

## Connect ChatGPT

The account owner must create/select a private Platform tunnel, keep tunnel-client running and authorize OAuth in ChatGPT. The local installer cannot grant these account permissions. See the [advanced guide](docs/ADVANCED_CHATGPT.vi.md).

**A real WinOwner tool call from ChatGPT is required for end-to-end acceptance.** Local service health alone is insufficient.

[Beginner guide](docs/START_HERE.vi.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Security](SECURITY.md) · [Tiếng Việt](README.vi.md)

MIT licensed; independent of OpenAI and Microsoft.