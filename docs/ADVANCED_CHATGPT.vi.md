# WinOwner MCP 1.1.0 — kết nối ChatGPT nâng cao

Dành cho người quản lý ChatGPT/OpenAI Platform. Trước tiên phải cài local theo [START_HERE](START_HERE.vi.md).

Các từ chính: **tunnel** là đường kết nối riêng, **OAuth** là bước đăng nhập/cấp quyền tài khoản, **RBAC** là quyền quản trị tổ chức, **DCR** là cơ chế đăng ký OAuth, **loopback** là máy tính hiện tại (127.0.0.1); **ACL** là phân quyền file, **SHA-256** dùng kiểm tra tính toàn vẹn file và **delegation** là quyền chủ máy ủy nhiệm.

Bộ cài local chọn owner_full_control, nhưng không thể tự cấp quyền OAuth hoặc tunnel của tài khoản ChatGPT.

## D. Những quyền cần có để thêm ChatGPT

Nếu chỉ muốn dùng local, dừng tại đây. Để dùng ChatGPT, cần **cả** quyền trên OpenAI Platform lẫn quyền **Add custom MCP server** của ChatGPT/workspace.

1. Tại https://platform.openai.com/settings/organization/tunnels, chọn tổ chức đúng, tạo hoặc chọn **tunnel riêng**. Lấy **tunnel_id** dạng tunnel_...
2. Tại https://platform.openai.com/settings/organization/people/roles, kiểm tra quyền **Tunnels Read + Use** cho người chạy tunnel/đăng ký plugin. Người tạo, chỉnh sửa hoặc xóa tunnel cần **Read + Manage**. Nếu thiếu, hỏi chủ tổ chức/RBAC admin.
3. Tại https://platform.openai.com/settings/organization/api-keys, tạo **runtime API key** riêng cho tunnel. Không dùng admin API key để chạy daemon.
4. Trong https://chatgpt.com/#settings/Connectors (hoặc giao diện Plugins mới), kiểm tra quyền thêm **custom MCP plugin**. Nếu bị khóa, hỏi admin workspace. Không có cách cài phần mềm trên Windows để vượt quyền tài khoản.
5. Tunnel phải được liên kết **đúng Platform organization và ChatGPT workspace** định dùng. Có tunnel chưa có nghĩa nó tự hiện trong mọi tài khoản.

Phân biệt các giá trị:

| Tên | Mục đích |
|---|---|
| tunnel_id | ID của tunnel do bạn tạo |
| CONTROL_PLANE_API_KEY | Runtime API key dùng cho tunnel-client doctor / run |
| OPENAI_ADMIN_KEY | Chỉ dùng tác vụ quản trị tunnel; **không đưa vào daemon** |
| OPENAI_API_KEY | Chỉ cần với luồng gọi API/LLM riêng; **không bắt buộc** cho ChatGPT gọi trực tiếp tool MCP |

**Không** đưa bất kỳ key, secret, token, ảnh màn hình lộ credential nào lên GitHub, issue hoặc đoạn chat.

## E. Lấy tunnel-client chính thức

Tải binary từ trang Platform Tunnels phía trên hoặc từ bản phát hành https://github.com/openai/tunnel-client/releases/latest. Không tải bản đóng gói lại không rõ nguồn. WinOwner không bao gồm tunnel-client.exe.

Mở PowerShell tại thư mục chứa tunnel-client.exe và thử:

~~~powershell
.\tunnel-client.exe help quickstart
~~~

Nếu binary đã nằm trong PATH, có thể gọi bằng tên tunnel-client mà không cần tiền tố .\.

## F. Ghép tunnel đến MCP

Trong **PowerShell dành riêng cho tunnel**, nhập runtime API key bằng lời nhắc ẩn (không ghi vào lịch sử terminal):

~~~powershell
$secureKey = Read-Host "CONTROL_PLANE_API_KEY" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
Remove-Variable secureKey -ErrorAction SilentlyContinue
$tunnelId = Read-Host "Your tunnel_id"
~~~

Sau đó, khi đang ở thư mục có tunnel-client.exe:

~~~powershell
.\tunnel-client.exe init --sample sample_mcp_with_dcr --profile winowner-local --tunnel-id $tunnelId --mcp-server-url http://127.0.0.1:8768/mcp
.\tunnel-client.exe doctor --profile winowner-local --explain
.\tunnel-client.exe run --profile winowner-local
~~~

- Mẫu **sample_mcp_with_dcr** chỉ phù hợp khi MCP server cung cấp OAuth/DCR metadata; nếu doctor báo thiếu tính năng, cần xác nhận cấu hình adapter, không tắt xác thực để cho chạy.
- Để cửa sổ **run** hoạt động. Kiểm tra **/readyz** và **/ui** từ địa chỉ health mà tunnel-client hiển thị. Tunnel healthy chưa đủ để xác nhận OAuth thành công.
- Trả về HTTP 401 từ adapter có thể là thử thách OAuth đúng mong đợi. Không sửa endpoint để chấp nhận truy cập ẩn danh.
- Bộ cài local tự chạy khi Windows đăng nhập; **tunnel-client foreground không tự khởi động cùng Windows**. Tự động chạy tunnel là bước nâng cao riêng sau khi kiểm thử.

Tài liệu tham chiếu chính thức: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

## G. Tạo plugin trong ChatGPT

1. Vào **ChatGPT → Plugins → dấu + → Add custom MCP server**. Tên menu có thể khác theo tài khoản.
2. Ở **Connection**, chọn **Tunnel** rồi chọn tunnel_id đúng với mục F.
3. Cấu hình xác thực tương thích với server, hoàn thành OAuth theo hướng dẫn trên màn hình. Không dùng tài khoản hay OAuth credential của chủ máy khác.
4. Đọc cảnh báo quyền truy cập máy và xác nhận, tạo plugin rồi bật nó trong đoạn chat thử nghiệm.
5. Thử lệnh đọc an toàn: **“Gọi agent_workspace_list và cho biết các workspace đã được duyệt.”** Chỉ kết luận **CONNECTED** sau khi tool trả kết quả từ đúng tài khoản.

**WRITE / EXECUTE / DESKTOP** cần capability grant tách biệt và kiểm tra giới hạn. Không bật toàn bộ ổ đĩa làm cách sửa lỗi OAuth.

## H. Khắc phục lỗi thường gặp

| Lỗi | Hướng xử lý |
|---|---|
| Python 3.12 không có, winget lỗi | Cài Python 3.12 chính thức, mở lại rồi chạy .cmd |
| Port 8768 occupied | Không kill process lạ; kiểm tra và chọn cổng khác nếu cần |
| Task name collision | Không ghi đè Scheduled Task không rõ nguồn |
| Local /health không lên | Kiểm tra task và runtime/logs; đừng bật tunnel |
| Không thấy tunnel trong ChatGPT | Kiểm tra quyền Read + Use và association với workspace |
| Tunnel báo 429 / unavailable | Xem doctor, readyz, mạng outbound và giới hạn dịch vụ |
| OAuth/DCR lỗi | Kiểm tra redirect URI, metadata, phạm vi và token; không vô hiệu hóa xác thực |
| Tool trả permission denied | Kiểm tra owner, allowed workspace, capability grant và thời hạn |

Tham khảo docs/TROUBLESHOOTING.md và docs/CONNECT_CHATGPT.md.

## I. Kiểm tra thành công và an toàn

- Tool **READ** hoạt động trên thư mục test đã phê duyệt.
- Tool **ngoài phạm vi** bị từ chối; không mở public port hoặc gửi credential ra ngoài.
- Với **WRITE**, chỉ thử sau khi đã cấp quyền cụ thể cho thư mục thử, kiểm tra SHA-256/backup/audit và thu hồi grant.
- Thử đăng nhập lại/reboot trên Windows sạch khi phát hành bản ổn định; đừng coi Unit Test là E2E.
- Chỉ báo hoàn tất khi cả 4 bước **LOCAL**, **TUNNEL READY**, **OAUTH VERIFIED**, **CHATGPT TOOL CALLED** đều có bằng chứng.

## J. Kiểm tra lại và gỡ cài

**Kiểm tra không sửa hệ thống** khi ở thư mục repo:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-WinOwner.ps1 -DryRun
~~~

**Gỡ Scheduled Task local** (chỉ khi muốn dừng chính bản cài này):

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Bootstrap-WindowsMcp.ps1 -Remove
~~~

Lệnh Remove chỉ dừng listener được xác minh, gỡ task do WinOwner tạo và **giữ nguyên credential** cho chủ máy xử lý. Gỡ kết nối ChatGPT: xóa plugin hoặc revoke token, tunnel, runtime key trong tài khoản của chính bạn. Để cập nhật phiên bản, sao lưu riêng config và runtime/secrets trước, xem CHANGELOG; không sao chép secret từ người khác.

## K. Trạng thái phát triển v1.1.0

v1.1.0 đang kiểm thử, chưa phát hành stable. Cần nghiệm thu Windows sạch, khởi động lại, OAuth ChatGPT thật và rollback trước khi phát hành.
