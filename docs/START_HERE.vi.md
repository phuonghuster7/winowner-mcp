# WinOwner MCP 1.1.0 — bắt đầu cho người mới

## Bước 1 — Kiểm tra quyền ChatGPT

Mở **ChatGPT web → Plugins → + → Add custom MCP server**. Muốn AI ghi/sửa file Windows, workspace phải hỗ trợ **full MCP**; OpenAI hiện ghi nhận Business, Enterprise/Edu. Không thấy mục thêm server thì phải kiểm tra quyền tài khoản trước khi ghép nối.

## Bước 2 — Copy một dòng vào CMD

Nhấn **Start**, tìm **cmd**, mở **Command Prompt**, dán rồi Enter:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"

Bộ cài tải và cài Windows. **THÀNH CÔNG** = phần cục bộ đã chạy, **chưa phải ChatGPT kết nối**. **THẤT BẠI** = đọc lý do trên màn hình, không tắt bảo vệ Windows.

## Bước 3 — Nhấp đúp để kiểm tra

Nhấn **Win + R**, dán dòng dưới rồi Enter:

    %LOCALAPPDATA%\WinOwnerMCP\v1.1.0

Nhấp đúp **Check-WinOwner.cmd**. Dòng THÀNH CÔNG xác nhận dịch vụ cục bộ chạy và từ chối truy cập chưa xác thực. **Uninstall-WinOwner.cmd** dừng dịch vụ và bỏ tự khởi động, giữ lại file và khóa.

## Bước 4 — Ghép nối ChatGPT

Lần đầu cần quyền tunnel trên OpenAI Platform và xác nhận OAuth trong ChatGPT; không thể thay thế bằng một lệnh CMD. [Hướng dẫn dành cho người có quyền quản trị](ADVANCED_CHATGPT.vi.md).

**Kiểm tra cuối:** chọn plugin WinOwner trong ChatGPT và gọi công cụ đọc trạng thái hệ thống. Chỉ có phản hồi thật từ công cụ mới chứng minh đã kết nối.

Nếu thiếu Python 3.12, có thể cài từ python.org; nếu cổng 8768 bận, không tự dừng dịch vụ lạ. Không nên tắt SmartScreen hoặc Defender để vượt cảnh báo.