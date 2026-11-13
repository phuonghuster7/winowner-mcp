# WinOwner MCP v1.1.1 — Hướng dẫn từng bước cho người mới

## Trước khi bắt đầu — Kiểm tra điều kiện tài khoản

1. **Gói ChatGPT:** Cần **ChatGPT Business / Enterprise / Edu** bật custom MCP để dùng đầy đủ công cụ ghi file, chạy lệnh, điều khiển màn hình. Gói **Pro** chỉ hỗ trợ tìm kiếm/đọc (read/fetch).
2. **OpenAI Platform:** Tài khoản org có quyền `Tunnels Read + Use`, đã tạo `tunnel_id` và một `runtime API key` (`sk-...`).

---

## Bước 1 — Cài đặt cục bộ trên Windows

Nhấn **Start**, gõ **cmd**, mở **Command Prompt**, dán lệnh dưới rồi nhấn **Enter**:

```cmd
powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/releases/download/v1.1.1/go.ps1|iex"
```

- **Bạn sẽ thấy gì:** Cửa sổ hiện chữ xanh `==== WinOwner MCP v1.1.1 - ONLINE SETUP ====`, xác minh `SHA-256 verified`, tải và cài Python 3.12 (nếu máy chưa có), tạo khóa chủ máy và kết thúc bằng `[THANH CONG] WinOwner MCP da cai dat tren Windows.`
- `[Ảnh minh họa 1: Cửa sổ CMD hiển thị [THANH CONG] sau khi chạy go.ps1]`

> **Cảnh báo SmartScreen / Windows Defender:** KHÔNG tắt Windows Defender hay SmartScreen. Nếu tải file ZIP thủ công và thấy hộp thoại "Windows protected your PC" do app chưa ký chứng thư thương mại, chỉ bấm **More info → Run anyway** khi bạn tải đúng từ trang Releases chính thức của dự án và đã đối chiếu mã SHA-256.

---

## Bước 2 — Kiểm tra dịch vụ cục bộ

Nhấn **Win + R**, dán đường dẫn sau rồi nhấn **Enter**:

```text
%LOCALAPPDATA%\WinOwnerMCP\v1.1.1
```

Nhấp đúp **Check-WinOwner.cmd**.
- **Bạn sẽ thấy gì:** Cửa sổ hiển thị `THÀNH CÔNG`, xác nhận dịch vụ chạy ẩn trên `127.0.0.1:8768`, chế độ an toàn `read_only`, truy cập ẩn danh `/mcp` bị từ chối (HTTP 401), và quyền file khóa (`icacls`) hợp lệ.
- `[Ảnh minh họa 2: Kết quả Check-WinOwner.cmd báo THÀNH CÔNG]`

---

## Bước 3 — Kết nối với ChatGPT

Trong thư mục `%LOCALAPPDATA%\WinOwnerMCP\v1.1.1`, nhấp đúp **Connect-ChatGPT.cmd**:

1. Đọc màn hình kiểm tra điều kiện tài khoản.
2. Nhập `tunnel_id` (dạng `tunnel_...`) và nhập `runtime API key` (ký tự sẽ được ẩn).
3. Trình hướng dẫn tự mã hóa API key bằng DPAPI (chỉ tài khoản Windows hiện tại giải mã được), kiểm tra `tunnel-client.exe` qua `SHA256SUMS.txt`, đăng ký tác vụ chạy ẩn khi đăng nhập, sao chép **Owner Key** vào clipboard (tự xóa sau 60 giây) và mở trang ChatGPT Connectors.
- **Bạn sẽ thấy gì:** Trình duyệt mở trang ChatGPT Connectors, trang xác nhận hỏi "Owner Passphrase / Token" — bạn chỉ cần bấm **Ctrl + V** để dán Owner Key vừa được chép sẵn (hoặc nhấp đúp **Show-OwnerKey.cmd** để chép lại).
- `[Ảnh minh họa 3: Trang xác nhận OAuth hiển thị ô dán Owner Key]`

4. **Kiểm tra cuối (bắt buộc):** Mở ChatGPT, chọn connector vừa tạo và gọi thử **1 công cụ thật** (ví dụ đọc trạng thái hệ thống). Chỉ khi công cụ trả về kết quả thật mới coi là đã kết nối xong.

---

## Bước 4 — Cấp quyền thư mục & nâng cấp / gỡ cài đặt

- **Cấp quyền có thời hạn:** Nhấp đúp **Grant-Access.cmd** → chọn thư mục cụ thể (không chọn cả ổ `C:\` hay thư mục gốc người dùng) → chọn mức quyền (`chỉ đọc` / `ghi file` / `chạy lệnh` / `màn hình`) → chọn thời hạn (mặc định 1 giờ, tối đa 24 giờ). Thu hồi bất cứ lúc nào bằng **Revoke-Access.cmd**.
- **Chế độ toàn quyền (tùy chọn):** Mặc định máy ở chế độ `read_only`. Nếu cần bật toàn quyền, nhấp đúp **Enable-FullControl.cmd** và gõ đúng cụm xác nhận; quay lại an toàn bằng **Disable-FullControl.cmd**.
- **Nâng cấp phiên bản mới:** Chạy `go.ps1 -Update` (sao lưu khóa, `policy.json`, `oauth.db` và tự hoàn tác nếu kiểm tra sức khỏe thất bại).
- **Gỡ cài đặt:** Nhấp đúp **Uninstall-WinOwner.cmd** để dừng dịch vụ và xóa tác vụ tự khởi động. Dữ liệu, cấu hình và khóa riêng vẫn được giữ nguyên để tránh mất dữ liệu.

---

## Từ điển thuật ngữ (1 dòng mỗi mục)

- **read_only**: Chế độ mặc định chỉ cho phép đọc, chặn ghi file và chạy lệnh hệ thống.
- **owner_full_control**: Chế độ toàn quyền cho phép AI ghi file, chạy lệnh và thao tác màn hình theo ủy quyền của chủ máy.
- **Owner Key**: Khóa chủ máy (`owner_admin.key`) dùng để xác nhận bạn là chủ sở hữu trên trang phê duyệt ChatGPT OAuth.
- **tunnel_id**: Mã định danh đường hầm bảo mật trên OpenAI Platform giúp ChatGPT gọi về máy bạn mà không mở port router.
- **DPAPI**: Cơ chế mã hóa tích hợp của Windows giúp chỉ tài khoản Windows hiện tại trên máy này mới giải mã được API key.
- **OAuth**: Quy trình cấp quyền chuẩn để bạn phê duyệt cho ChatGPT truy cập WinOwner MCP mà không lộ mật khẩu.
