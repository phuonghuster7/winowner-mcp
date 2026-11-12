# WinOwner MCP — v1.1.0

**Dùng ChatGPT điều khiển máy Windows của chính bạn.** WinOwner MCP kết nối ChatGPT đến máy riêng qua MCP và tunnel có xác thực.

[English](README.md) · [Hướng dẫn A–Z cho người mới](docs/START_HERE.vi.md) · [Kết nối ChatGPT](docs/CONNECT_CHATGPT.md) · [Bảo mật](SECURITY.md)

> **v1.1.0 đang kiểm thử, chưa phải bản stable đã phát hành.** AI có thể chỉnh sửa file và điều khiển desktop bằng quyền của tài khoản Windows chạy agent. Chỉ cài trên máy mà bạn có quyền quản lý.

## Cài bằng một lần nhấp đúp

1. Vào trang GitHub này, chọn **Code → Download ZIP**.
2. Nhấp phải file ZIP → **Extract All / Giải nén tất cả**.
3. Mở thư mục đã giải nén, nhấp đúp **Install-WinOwner.cmd**.

Bộ cài tự tạo môi trường Python riêng, cài phụ thuộc, tạo Scheduled Task để MCP khởi động lúc đăng nhập. Nếu thiếu Python 3.12, công cụ thử cài qua winget.

**Không hỏi nhập token nội bộ trong bước cài local:** khóa nội bộ được tạo và lưu tự động. Chỉ có cửa sổ cài đặt chính; không tự mở Notepad, không bắt bấm phím để kết thúc. Thành công sẽ hiện **LOCAL_INSTALL_PASS**. Sau khi cửa sổ đóng, mở **WinOwner-SETUP-RESULT.txt** ngay trong thư mục cài để xem kết quả; không cần mở PowerShell.

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

**Tình trạng:** đang thử nghiệm v1.1.0. Trước bản stable còn cần kiểm tra trên máy Windows mới, khởi động lại, OAuth ChatGPT và rollback.

[Hướng dẫn cài đặt](docs/INSTALLATION.md) · [Xử lý lỗi](docs/TROUBLESHOOTING.md) · [Changelog](CHANGELOG.md)

Dự án độc lập theo giấy phép MIT, không được OpenAI hay Microsoft bảo trợ.