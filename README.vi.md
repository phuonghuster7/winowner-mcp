# WinOwner MCP — v1.1.1

> **Yêu cầu tài khoản & gói cước (Đọc trước khi cài):** Để ChatGPT dùng đầy đủ công cụ MCP (ghi file, chạy lệnh, điều khiển màn hình), cần gói **ChatGPT Business, Enterprise hoặc Edu** có bật custom MCP ([tài liệu OpenAI](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)). Gói ChatGPT Pro chỉ hỗ trợ tìm kiếm/đọc (read/fetch). Khi kết nối cần thêm tài khoản **OpenAI Platform org** có quyền `Tunnels Read + Use`, `tunnel_id` và runtime API key.

**Cài dịch vụ Windows MCP cục bộ (mặc định an toàn `read_only`) và kết nối với ChatGPT.** Không cần quyền Admin/UAC; mọi cổng chỉ mở nội bộ tại `127.0.0.1:8768`.

## 1. Cài đặt bằng một lệnh CMD trên Windows 10/11

Nhấn **Start**, gõ **cmd**, mở **Command Prompt**, dán lệnh sau rồi nhấn Enter:

```cmd
powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/releases/download/v1.1.1/go.ps1|iex"
```

Bộ cài tự xác minh mã SHA-256 của gói phát hành `v1.1.1`, cài vào `%LOCALAPPDATA%\WinOwnerMCP\v1.1.1`, cài Python 3.12 (qua `winget` nếu thiếu), tạo khóa chủ máy `owner_admin.key` và đăng ký tác vụ chạy ẩn (`pythonw.exe`) khi đăng nhập.

**THÀNH CÔNG** nghĩa là phần mềm cục bộ trên Windows đã chạy, **không đồng nghĩa ChatGPT đã kết nối**. Không tắt SmartScreen hay Windows Defender.

## 2. Kết nối ChatGPT & quản lý quyền (nhấp đúp)

Nhấn **Win + R**, dán `%LOCALAPPDATA%\WinOwnerMCP\v1.1.1` rồi Enter:

- **Connect-ChatGPT.cmd**: Trình hướng dẫn tiếng Việt nhập `tunnel_id` và API key (mã hóa DPAPI, ẩn khi nhập), kiểm tra `tunnel-client.exe`, tạo tác vụ tunnel chạy ẩn, sao chép Owner Key qua **Show-OwnerKey.cmd** (tự xóa clipboard sau 60 giây) và mở trang ChatGPT Connectors.
- **Show-OwnerKey.cmd**: Chép `owner_admin.key` vào clipboard (không in ra màn hình) để dán vào trang xác nhận ChatGPT.
- **Grant-Access.cmd / Revoke-Access.cmd**: Cấp hoặc thu hồi quyền có thời hạn (tối đa 24 giờ) cho từng thư mục cụ thể.
- **Enable-FullControl.cmd / Disable-FullControl.cmd**: Bật/tắt chế độ toàn quyền (`owner_full_control` / `read_only`).
- **Check-WinOwner.cmd / Uninstall-WinOwner.cmd**: Kiểm tra sức khỏe dịch vụ hoặc dừng và gỡ tác vụ tự khởi động (giữ nguyên dữ liệu và khóa riêng).

**Chỉ coi là đã kết nối khi bạn gọi thử thành công một công cụ WinOwner thật từ ChatGPT.**

[Hướng dẫn người mới](docs/START_HERE.vi.md) · [Khắc phục lỗi](docs/TROUBLESHOOTING.md) · [Bảo mật](SECURITY.md) · [English](README.md)

Dự án độc lập theo giấy phép MIT, không được OpenAI hay Microsoft bảo trợ.
