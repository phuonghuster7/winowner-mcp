# WinOwner MCP v1.2.0 — hướng dẫn người mới

## Bước 1 — Cài trong CMD

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"

Chỉ sử dụng khi tag v1.2.0 đã phát hành. **THÀNH CÔNG** = dịch vụ read_only chạy, **THẤT BẠI** = đọc thông báo CMD. Không tắt SmartScreen/Defender.

Nhấn **Win + R**, mở:

    %LOCALAPPDATA%\WinOwnerMCP\v1.2.0

## Bước 2 — Nhấp để kết nối

Nhấp Check-WinOwner.cmd rồi Connect-ChatGPT.cmd. Nhập tunnel_id và runtime API key của tài khoản; API key được nhập ẩn. Bộ cài kiểm hash, tạo task chạy nền.

## Bước 3 — Tạo plugin

Trên ChatGPT mở Plugins → + → Add custom MCP server. Chọn Connection: Tunnel và Authentication: None. Authentication None vẫn cần xác thực phía WinOwner; HTTP 401 từ request không có credential là chính xác.

Chỉ xác nhận đã kết nối khi ChatGPT gọi được công cụ thật. Nếu không thấy mục custom MCP thì kiểm tra quyền của tài khoản/workspace.

## Bước 4 — Quyền owner (tùy chọn)

Read-only là mặc định. Grant-OwnerAccess.cmd yêu cầu hộp thoại xác nhận quyền. Show-OwnerKey.cmd copy token vào clipboard sau khi chủ máy xác nhận; không nhập token này vào trang API key của OpenAI Platform. Một trang OAuth có thể dùng redirect thay vì nhập key thủ công.

Uninstall-WinOwner.cmd dừng tác vụ; giữ nguyên file và khóa. Ngắt plugin/tunnel trong tài khoản riêng nếu muốn thu hồi kết nối.

Xem [Authentication None](AUTH_NONE.vi.md) hoặc [hướng dẫn kỹ thuật](ADVANCED_CHATGPT.vi.md).