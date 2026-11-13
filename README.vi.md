# WinOwner MCP — v1.1.0

**Cài phần mềm điều khiển Windows qua ChatGPT bằng một dòng CMD.** Chế độ mặc định là Owner Full Control, chỉ có hiệu lực với chủ máy đã xác thực và có quyền hợp lệ.

## Trước khi cài: kiểm tra quyền ChatGPT

Trên ChatGPT web, mở **Plugins → + → Add custom MCP server**. Để ChatGPT ghi/sửa file hay điều khiển Windows, workspace cần hỗ trợ **full MCP** và cho phép tạo kết nối tùy chỉnh. Theo tài liệu OpenAI hiện tại, full MCP dành cho **Business, Enterprise/Edu**; Pro chỉ dùng custom MCP với quyền read/fetch. Không thấy mục này thì phần cài Windows vẫn làm được, nhưng chưa thể hoàn tất kết nối ChatGPT bằng quy trình repo. Xem [tài liệu OpenAI](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## 1. Một lệnh CMD trên Windows 10/11

Nhấn **Start**, tìm **cmd**, mở **Command Prompt**, dán rồi nhấn Enter:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"

Bộ cài tự tải về %LOCALAPPDATA%\WinOwnerMCP\v1.1.0, cài Python 3.12 nếu cần và có winget, tạo dịch vụ MCP tự chạy khi đăng nhập. **Không nhập token nội bộ**.

**THÀNH CÔNG** nghĩa là cài Windows xong, **không đồng nghĩa ChatGPT đã kết nối**. Nếu **THẤT BẠI**, đọc thông báo trong CMD; không tắt SmartScreen hoặc Defender để né cảnh báo.

## 2. Kiểm tra hoặc gỡ dịch vụ

Nhấn **Win + R**, dán đường dẫn này rồi Enter:

    %LOCALAPPDATA%\WinOwnerMCP\v1.1.0

Nhấp đúp **Check-WinOwner.cmd** để kiểm tra dịch vụ và truy cập chưa xác thực. Nhấp đúp **Uninstall-WinOwner.cmd** để dừng dịch vụ, gỡ tác vụ tự khởi động. **Tệp chương trình, cấu hình và khóa riêng vẫn được giữ lại**, chưa phải xóa sạch dữ liệu khỏi máy.

## 3. Liên kết ChatGPT

Người có quyền lần đầu tạo/chọn tunnel trên OpenAI Platform, chạy tunnel-client rồi xác nhận OAuth trong ChatGPT. Không thể cấp quyền tài khoản thay người dùng bằng CMD. [Hướng dẫn dành cho người quản trị](docs/ADVANCED_CHATGPT.vi.md).

**Chỉ coi là đã kết nối khi ChatGPT gọi được một công cụ WinOwner thật.** Đừng dựa vào thông báo cài Windows hoặc cổng đang mở.

[Hướng dẫn người mới](docs/START_HERE.vi.md) · [Khắc phục lỗi](docs/TROUBLESHOOTING.md) · [Bảo mật](SECURITY.md) · [English](README.md)

Dự án độc lập theo giấy phép MIT, không được OpenAI hay Microsoft bảo trợ.