# WinOwner MCP v1.2.0 — hướng dẫn người mới

## Bước 1 — Cài trong CMD

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

Chỉ sử dụng khi tag v1.2.0 đã phát hành và được xác minh. **THÀNH CÔNG** = dịch vụ read_only chạy; **THẤT BẠI** = đọc thông báo CMD. Không tắt SmartScreen/Defender.

Nhấn **Win + R**, mở:

    %LOCALAPPDATA%\WinOwnerMCP\v1.2.0

## Bước 2 — Nhấp để kết nối

`Check-WinOwner.cmd` kiểm tra dịch vụ trên máy. `Connect-ChatGPT.cmd` là trình cấu hình tunnel; chỉ sử dụng tunnel của tài khoản/workspace được phép truy cập.

## Bước 3 — Tạo plugin

Xem [hướng dẫn kết nối](CONNECT_CHATGPT.md). Authentication None vẫn cần xác thực phía WinOwner; HTTP 401 từ request chưa xác thực là đúng. Không bỏ lớp xác thực để khắc phục lỗi.

Phân biệt `CHECK_PASS` (dịch vụ trên máy), `TUNNEL_READY` (tunnel sẵn sàng) và kết nối thực tế (ChatGPT gọi thành công tool chỉ đọc). Nếu không thấy mục custom MCP, kiểm tra quyền tài khoản/workspace.

## Bước 4 — Quyền owner (tùy chọn)

Read-only là mặc định. Quyền owner cần xác minh OAuth, cấp quyền riêng và nghiệm thu thu hồi quyền. Không chia sẻ credential; không dán khóa vào chat. Các tiện ích quản trị không cần thiết cho cài đặt chỉ đọc.

`Uninstall-WinOwner.cmd` kiểm tra và gỡ task dịch vụ/tunnel đã xác minh, giữ nguyên file và khóa. Thu hồi quyền plugin/tunnel riêng. Khi nâng cấp, giữ bản cũ đến khi kiểm tra xong kết nối mới.

Xem [Authentication None](AUTH_NONE.vi.md) hoặc [hướng dẫn kỹ thuật](ADVANCED_CHATGPT.vi.md).