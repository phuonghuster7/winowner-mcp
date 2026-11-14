# WinOwner MCP v1.2.0 — kết nối Windows với ChatGPT

**Windows 10/11 · Cài bằng một lệnh CMD · Mặc định chỉ đọc.**

Hướng dẫn dành cho v1.2.0. Chỉ dùng lệnh cài sau khi phiên bản này được phát hành và xác minh.

## 1. Cài trên Windows 10/11

Mở CMD và dùng lệnh cài đã ghim phiên bản từ bản phát hành được xác minh:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

Thư mục cài: `%LOCALAPPDATA%\WinOwnerMCP\v1.2.0`. Không cần Git. Không tắt phần mềm bảo mật để cài đặt.



## 2. Kiểm tra và kết nối ChatGPT

Nhấn Win + R, mở:

    %LOCALAPPDATA%\WinOwnerMCP\v1.2.0

`Check-WinOwner.cmd` kiểm tra dịch vụ cục bộ. `Connect-ChatGPT.cmd` hướng dẫn cấu hình tunnel riêng, kiểm hash bộ tunnel-client và kiểm tra trạng thái sẵn sàng.

Xem [hướng dẫn kết nối](docs/START_HERE.vi.md). Tài khoản/workspace cần có quyền thêm MCP. Authentication: None không có nghĩa là bỏ xác thực Windows.

| Kết quả | Ý nghĩa |
| --- | --- |
| `LOCAL_INSTALL_PASS` / `CHECK_PASS` | Dịch vụ trên máy hoạt động |
| `TUNNEL_SETUP_PASS` / `TUNNEL_READY` | Tunnel sẵn sàng, chưa xác nhận ChatGPT |
| ChatGPT gọi thành công tool chỉ đọc | Kết nối thực tế đã được xác minh |

## 3. Nếu cần Full Control

Giữ chế độ chỉ đọc làm mặc định. Quyền owner cần xác minh danh tính, sự đồng ý rõ ràng và phân quyền riêng. `Grant-OwnerAccess.cmd` và `Show-OwnerKey.cmd` là tiện ích quản trị nhạy cảm, không cần cho chế độ chỉ đọc. Không chia sẻ khóa riêng hoặc dán khóa vào chat. Cần nghiệm thu riêng luồng cấp và thu hồi quyền.

## 4. Dừng và gỡ

`Uninstall-WinOwner.cmd` kiểm tra và gỡ task dịch vụ và tunnel đã xác minh; giữ lại cấu hình và khóa riêng. Thu hồi quyền plugin/tunnel từ tài khoản riêng.

Chạy lại cùng phiên bản để sửa cài đặt; nâng cấp giữ thư mục cũ. Có thể cần kiểm tra lại kết nối ChatGPT. Xem [nâng cấp và sửa lỗi](docs/INSTALLATION.md).

[Hướng dẫn ngắn](docs/START_HERE.vi.md) · [Authentication None](docs/AUTH_NONE.vi.md) · [Bảo mật](SECURITY.md) · [English](README.md)

MIT, độc lập với OpenAI và Microsoft.