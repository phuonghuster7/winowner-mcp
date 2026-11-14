# Connect an authorized ChatGPT client / Kết nối ChatGPT

This is an **operator setup guide**, not an automatic activation script. It assumes you are the owner of the Windows host and have access to a supported ChatGPT MCP connector interface.

## Start here for first-time users

For guided setup, double-click **Connect-ChatGPT.cmd** after local installation. See [advanced ChatGPT setup](ADVANCED_CHATGPT.vi.md) for obtaining **tunnel_id** and **CONTROL_PLANE_API_KEY** from OpenAI Platform, installing the official tunnel-client, configuring OAuth and verifying the first real tool call.

To disconnect only the verified local tunnel task, use `Disconnect-ChatGPT.cmd`. The local MCP service and local credentials are unchanged. Remote access revocation is separate. Automatic reconfiguration rollback is not implemented.

Official Secure MCP Tunnel reference: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

**Do not confuse ports:** the standalone authenticated owner-profile one-click adapter listens on **127.0.0.1:8768/mcp** by default. Ports 8765/8766 refer to optional components of the older full stack; do not point a new user's tunnel at those ports unless that stack is actually installed and authenticated. Local bootstrap does **not** install tunnel-client or authorize the ChatGPT plugin.

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

At ChatGPT plugin creation, Authentication None is an option when WinOwner is already authenticated by its private tunnel; choosing None by itself does not bypass local HTTP 401 or establish owner permissions. See [Authentication None for beginners](AUTH_NONE.vi.md). The exact connector registration UI and OAuth onboarding steps depend on the ChatGPT account and supported connector type. Owner-scoped OAuth consent requires the independent owner administration credential; the shared tunnel/caller credential cannot approve owner scopes. Dynamic client registration and authorization are covered by isolated tests. Automatic owner pairing through Authentication None has not been verified or implemented. Never reuse a tunnel ID or token published in somebody else's repository.

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
