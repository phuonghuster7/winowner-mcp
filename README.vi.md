# WinOwner MCP — v1.1.0

**Dùng ChatGPT điều khiển máy Windows của chính bạn.** WinOwner MCP kết nối ChatGPT đến máy riêng qua MCP và tunnel có xác thực.

[English](README.md) · [Hướng dẫn A–Z cho người mới](docs/START_HERE.vi.md) · [Kết nối ChatGPT](docs/CONNECT_CHATGPT.md) · [Bảo mật](SECURITY.md)

> **v1.1.0 đang kiểm thử, chưa phải bản stable đã phát hành.** AI có thể chỉnh sửa file và điều khiển desktop bằng quyền của tài khoản Windows chạy agent. Chỉ cài trên máy mà bạn có quyền quản lý.

## Cài bằng một dòng CMD

Mở CMD, dán một dòng rồi nhấn Enter:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"

Có thể tải ZIP từ nhánh main, giải nén rồi nhấp đúp Install-WinOwner.cmd nếu muốn cài ngoại tuyến.

Bộ cài tự tạo môi trường Python riêng, cài phụ thuộc, tạo Scheduled Task để MCP khởi động lúc đăng nhập. Nếu thiếu Python 3.12, công cụ thử cài qua winget.

**Không hỏi nhập token nội bộ trong bước cài local:** khóa nội bộ được tạo và lưu tự động. Chỉ có cửa sổ cài đặt chính; không tự mở Notepad, giữ cửa sổ để người dùng đọc kết quả trước khi nhấn phím đóng. Thành công sẽ hiện **LOCAL_INSTALL_PASS**. Sau khi cửa sổ đóng, mở **WinOwner-SETUP-RESULT.txt** ngay trong thư mục cài để xem kết quả; không cần mở PowerShell.

**Mặc định: Owner Full Control.** Chế độ này chỉ cho phép chủ máy **đã xác thực OAuth và có delegation hợp lệ** sử dụng công cụ đọc/ghi file, Git, chạy lệnh và điều khiển desktop. Không tự cấp quyền Administrator, không cho người chưa xác thực hoặc người khác dùng quyền của chủ máy.

## Kết nối ChatGPT lần đầu

Cài Windows xong **chưa phải** kết nối ChatGPT. Mở [hướng dẫn A–Z](docs/START_HERE.vi.md):

1. Tạo tunnel riêng tại [OpenAI Platform Tunnels](https://platform.openai.com/settings/organization/tunnels).
2. Tạo runtime key tại [Platform API Keys](https://platform.openai.com/settings/organization/api-keys), cần quyền Tunnels Read + Use.
3. Cấu hình tunnel-client theo hướng dẫn để trỏ về MCP trên Windows.
4. Trong **ChatGPT → Plugins → Add custom MCP server → Tunnel**, kết nối tunnel, hoàn tất OAuth và thử gọi tool.

**Các bước đăng nhập, đồng ý quyền tài khoản và cảnh báo an toàn của ChatGPT/Windows là bắt buộc khi áp dụng.** Không thể âm thầm bỏ qua bằng phần mềm cài đặt. Sau khi kết nối thành công, công cụ có thể được dùng theo quyền đã cấp; hành động nhạy cảm vẫn có thể cần xác nhận.

## Chức năng

- Thao tác file, lập trình, sửa mã nguồn, Git và chạy kiểm thử.
- Điều khiển cửa sổ, chuột, bàn phím trong phiên Windows phù hợp.
- OAuth chủ máy, cấp/thu hồi quyền, backup SHA-256, audit và dừng khẩn cấp.

Hiện dự án chỉ giới thiệu và nghiệm thu **ChatGPT**. Không mở trực tiếp cổng nội bộ ra Internet.

**Tình trạng:** đang thử nghiệm v1.1.0. CI đã qua kiểm thử cài Windows và rollback; liên kết ChatGPT lần đầu cần chủ tài khoản thực hiện.

[Hướng dẫn cài đặt](docs/INSTALLATION.md) · [Xử lý lỗi](docs/TROUBLESHOOTING.md) · [Changelog](CHANGELOG.md)

Dự án độc lập theo giấy phép MIT, không được OpenAI hay Microsoft bảo trợ.