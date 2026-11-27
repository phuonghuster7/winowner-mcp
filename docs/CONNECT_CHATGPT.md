# Connect an authorized ChatGPT client / Kết nối ChatGPT

This is an **operator setup guide**, not an automatic activation script. It assumes you are the owner of the Windows host and have access to a supported ChatGPT MCP connector interface.

## Start here for first-time users

For guided setup, open **WinOwner Setup Center** (`http://127.0.0.1:8767/setup` via `Check-WinOwner.cmd -OpenSetupCenter`) or double-click **Connect-ChatGPT.cmd** after local installation. See [advanced ChatGPT setup](ADVANCED_CHATGPT.vi.md) for obtaining **tunnel_id** and **CONTROL_PLANE_API_KEY** from OpenAI Platform, installing the official `tunnel-client`, configuring OAuth 2.1 vs. `Authentication: None`, and verifying the first real tool call.

To reconfigure an existing tunnel safely, use **Reconfigure Tunnel** in WinOwner Setup Center or run `scripts/Connect-ChatGPT.ps1 -Reconfigure`: it verifies scheduled task ownership, backs up existing keys and configuration, validates the candidate configuration and `/readyz` probe before stopping the active tunnel, cuts over atomically, and automatically rolls back with SHA-256 verification on any failure. To disconnect only the verified local tunnel task, use `Disconnect-ChatGPT.cmd`.

Official Secure MCP Tunnel reference: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

**Do not confuse ports:** the standalone authenticated owner-profile one-click adapter listens on **127.0.0.1:8768/mcp** by default, and the local **WinOwner Setup Center** runs on **127.0.0.1:8767/setup**. Do not expose loopback MCP or Setup Center ports directly to the public Internet. Local bootstrap (`LOCAL_READY`) does **not** install `tunnel-client` or authorize the ChatGPT plugin.

## 1. Local safety checks

- Install the pinned Python requirements and run `scripts/Install-WindowsMcpAgent.ps1 -DryRun` first.
- Keep every local service bound to `127.0.0.1`. Do not expose 3080, 8765, 8766, 8767 or 8768 directly to the public Internet.
- The beginner installer defaults to read_only. A separately authorized owner may use the supported owner profile; a tunnel connection alone never establishes owner identity. Back up existing configs before changing privileges.
- Set up a unique OAuth owner identity and the operator's own private tunnel profile; no keys or tunnel IDs are distributed in this repository.

## 2. Check the local adapter

From PowerShell, after configuring the service:

```powershell
Test-NetConnection 127.0.0.1 -Port 8768
python .\runtime\watchdog.py --status
```

A listening port is **not** proof that ChatGPT is authenticated. Verify OAuth identity and connector scopes separately.

## 3. Bind a private tunnel

Use the tunnel-client provided for your supported ChatGPT MCP connection. Point the profile only to the correct loopback MCP endpoint. Review a simulated migration/binding before applying it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Connect-ChatGPTAccount.ps1 `
  -Simulated -Scenario ScenarioB -NewProfileName windows-mcp-isolated `
  -NewTunnelId "<YOUR_OWN_TUNNEL_ID>" -McpTargetUrl "http://127.0.0.1:8768/mcp"
```

At ChatGPT plugin creation, `Authentication: None` is an option for **read-only (`Read`)** access when WinOwner is authenticated by its private tunnel; choosing `None` by itself never bypasses local HTTP `401` checks and never grants owner permissions because a shared `Authentication: None` tunnel carries no verifiable per-client identity. See [Authentication None for beginners](AUTH_NONE.vi.md).

For **`Scoped` or `Owner` control** (`OWNER_VERIFIED -> OWNER_CONTROL_READY`), connect using **OAuth 2.1 (DCR + PKCE S256)** (`Connect-ChatGPT.ps1 -AuthMode oauth`). When the verified OAuth 2.1 client requests owner pairing (`agent_request_owner_pairing` or `POST /oauth/pairing/request`), WinOwner generates a cryptographic single-use pairing challenge (`XXXX-XXXX`) with TTL and anti-replay protection. Review and click **Approve** or **Deny** locally in **WinOwner Setup Center → Review Owner Access** (or `Grant-OwnerAccess.cmd`). WinOwner binds the resulting delegation and capability grant strictly to that verified OAuth `client_id` — **never enter an OAuth `client_id` manually, never paste an owner token into ChatGPT, and never share tunnel credentials across untrusted clients**.

## 4. Verify from ChatGPT

Once the owner has connected and authorized the plugin in ChatGPT, verify in this order:

1. Read-only system status; inspect `is_owner` and the running release identity.
2. Read a harmless test file in an explicitly approved workspace.
3. Test a single non-destructive command only after capability grants are configured.
4. If needed, create, read, patch and delete a temporary file in a non-sensitive workspace using SHA-256 preconditions, and verify the file is gone.
5. Confirm durable audit and revocation work. Do not enable OWNER_GLOBAL_FILESYSTEM until owner-only access and denial for a non-owner are demonstrated.

## 5. Common errors

- `MCP SSE probe 429`: investigate tunnel connectivity and rate limiting; do not claim that the local service is attached solely from port health.
- `Tunnel-client has not been seen for 300 seconds`: check the tunnel-client process and watchdog.
- `Workspace/tool has no owner-approved trusted workspace policy`: configure the intended scope; do not bypass it by opening anonymous remote access.
- OAuth owner denied: inspect token audience, scope, grant expiry and revocation without printing credentials.

See [Troubleshooting](TROUBLESHOOTING.md) and [Security](../SECURITY.md).

**Tiếng Việt:** cần xác thực tài khoản chủ máy bằng OAuth, dùng tunnel riêng, kiểm tra quyền từng bước và giữ chế độ đọc là mặc định. Không nhập token bí mật vào GitHub hoặc issue công khai.
