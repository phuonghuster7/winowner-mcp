# WinOwner MCP v1.2.0 — kết nối Windows với ChatGPT

**Cài bằng một lệnh CMD. Mặc định chỉ đọc; Full Control phải do chủ máy cấp quyền riêng.**

## 1. Cài trên Windows 10/11

Mở CMD, dán dòng sau sau khi bản v1.2.0 được phát hành:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

Cài tự động vào thư mục %LOCALAPPDATA%\WinOwnerMCP\v1.2.0. Không cần Git. Nếu máy thiếu Python 3.12, trình cài dùng winget khi sẵn có.

**LOCAL_INSTALL_PASS** chỉ xác nhận Windows MCP cục bộ, chưa phải ChatGPT đã kết nối.

## 2. Kiểm tra và kết nối ChatGPT

Nhấn Win + R, mở:

    %LOCALAPPDATA%\WinOwnerMCP\v1.2.0

Nhấp đúp Check-WinOwner.cmd, rồi Connect-ChatGPT.cmd. Nhập tunnel_id và runtime API key vào ô nhập ẩn. Trình cài tải tunnel-client chính thức, kiểm hash, tạo profile bảo vệ và task chạy nền.

Trong ChatGPT vào Plugins → + → Add custom MCP server. Chọn Connection: Tunnel, chọn tunnel của mình, rồi Authentication: None. Kiểm tra bằng một lệnh đọc thật. Nếu không thấy tùy chọn thêm MCP, kiểm tra quyền tạo MCP của tài khoản/workspace.

## 3. Nếu cần Full Control

Mặc định read_only. Nhấp Grant-OwnerAccess.cmd để xem cảnh báo và xác nhận cấp quyền cho client đã xác thực. Show-OwnerKey.cmd chỉ copy token khi chính chủ đồng ý. **Không dán owner token vào màn hình OpenAI Platform API key**: đây là hai loại credential khác nhau. Clipboard có thể bị ứng dụng khác đọc.

## 4. Dừng và gỡ

Uninstall-WinOwner.cmd dừng agent và task. Tệp cấu hình cùng key không bị xóa tự động; gỡ plugin/tunnel trên ChatGPT riêng khi muốn ngắt quyền truy cập.

[Hướng dẫn ngắn](docs/START_HERE.vi.md) · [Authentication None](docs/AUTH_NONE.vi.md) · [Bảo mật](SECURITY.md) · [English](README.md)

MIT, độc lập với OpenAI và Microsoft.