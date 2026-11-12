# WinOwner MCP 1.1.0 — cài đặt bằng một lệnh CMD

Dành cho người dùng Windows 10/11, không cần biết lập trình.

**Bước 1.** Mở **Command Prompt (CMD)** trong menu Start. Dán toàn bộ **một dòng** sau và nhấn Enter:

    powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"

Lệnh tải bộ cài **từ GitHub của dự án**, tự giải nén và cài WinOwner vào thư mục của tài khoản Windows. Không cần Git, tải ZIP hay nhập mã nội bộ. Nếu máy chưa có Python 3.12, bộ cài sẽ thử cài bằng winget.

**Bước 2.** Xem dòng kết quả:

- **THÀNH CÔNG:** WinOwner MCP đã chạy trên máy.
- **THẤT BẠI:** chưa cài xong; xem thông báo trong cửa sổ.
- Thư mục cài mặc định: %LOCALAPPDATA%\WinOwnerMCP\v1.1.0.

**Bước 3.** Mở thư mục cài, nhấp đúp **Check-WinOwner.cmd** để kiểm tra. Muốn dừng và bỏ tự khởi động, nhấp đúp **Uninstall-WinOwner.cmd**. Hai cửa sổ này đều giữ kết quả để bạn đọc.

## Quan trọng: kết nối với ChatGPT

Một lệnh trên cài **toàn bộ phần mềm WinOwner trên máy Windows**, với chế độ **Owner Full Control** dành cho chủ máy có xác thực.

**ChatGPT không tự liên kết tài khoản chỉ nhờ chạy CMD.** Lần đầu vẫn cần chọn tunnel của chính chủ và xác nhận OAuth trong ChatGPT. Đây là bước cấp quyền tài khoản; không phải token nội bộ mà bộ cài bắt nhập.

Người cần tự thiết lập tunnel lần đầu xem [Hướng dẫn kết nối ChatGPT nâng cao](ADVANCED_CHATGPT.vi.md). Sau khi đã ghép nối, không phải làm lại phần cài local mỗi lần.

## Không chạy được?

- **Windows báo không thể chạy file:** kiểm tra bạn đã copy đủ một dòng trong khung CMD và kết nối Internet. Không tắt SmartScreen hoặc Defender để sửa lỗi.
- **Thiếu Python/winget:** bộ cài sẽ báo lỗi cụ thể. Có thể cài Python 3.12 từ trang python.org, rồi dán lại cùng lệnh.
- **Cổng 8768 đang được sử dụng:** bộ cài không tắt dịch vụ lạ để giành cổng. Xem file kết quả và kiểm tra dịch vụ đã có.
- **Đã cài rồi:** chạy lại lệnh sẽ dùng lại thư mục WinOwner hiện có, không tự xóa cấu hình và khóa riêng.
- **Muốn cài thủ công:** tải [ZIP mã nguồn chính](https://github.com/phuonghuster7/winowner-mcp/archive/refs/heads/main.zip), giải nén rồi nhấp đúp **Install-WinOwner.cmd**.

**Lưu ý:** WinOwner có thể sửa file và thao tác trên Windows trong phạm vi quyền mà bạn đã cấp. Chỉ kết nối máy mình được phép quản lý.