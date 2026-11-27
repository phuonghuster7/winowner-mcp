# Authentication None trong ChatGPT: chỉ đọc đơn giản, tách biệt hoàn toàn với quyền Chủ máy

Khi bạn thêm WinOwner MCP vào ChatGPT bằng tunnel riêng, mục **Authentication** có thể chọn **None** cho nhu cầu **chỉ đọc (`Read`)** khi kết nối tunnel riêng đã được xác thực với WinOwner.

**Ranh giới nền tảng (Platform Truth):**
- Chọn **Authentication: None** (hoặc `Authentication None`) tại màn hình ChatGPT chỉ bỏ đăng nhập OAuth ở màn hình tạo plugin. Nó KHÔNG vô hiệu hóa chứng thực HTTP `401` phía máy, KHÔNG chứng minh người dùng là chủ máy và KHÔNG bao giờ cấp quyền `Scoped` hay `Owner` (Full Control).
- Trên đường truyền tunnel dùng chung với `Authentication: None`, ChatGPT không gửi danh tính từng client có thể xác minh mật mã xuống máy Windows. Vì vậy, mọi phiên `Authentication: None` đều **bị khóa cứng ở mức quyền tối thiểu `read_only` (`Read`)** — kể cả khi người dùng chọn `Allow all tools` trong giao diện ChatGPT.

## Hành trình 4 bước chuẩn trên v1.2.0

1. **Cài đặt (`LOCAL_READY`):** Cài WinOwner bằng một dòng CMD và kiểm tra qua `Check-WinOwner.cmd` hoặc mở **WinOwner Setup Center** (`http://127.0.0.1:8767/setup`).
2. **Kết nối (`CHATGPT_CONNECTED`):** Ghép tunnel riêng của tài khoản (`Connect-ChatGPT.cmd`), thêm plugin trong **ChatGPT → Plugins → + → Add custom MCP server → Connection: Tunnel**, rồi gọi thử một công cụ chỉ đọc thật (ví dụ `agent_system_status` hoặc `agent_workspace_list`). WinOwner chỉ chuyển sang `CHATGPT_CONNECTED` (`ChatGPT: Verified`) sau khi có lệnh gọi tool đọc thật thành công — không bao giờ báo xanh giả chỉ từ `/health` hay `/readyz`.
3. **Xác nhận một lần (`OWNER_VERIFIED`):** Nếu chỉ cần đọc workspace, bạn có thể dừng ở Bước 2 với `Authentication: None`. Khi cần quyền `Scoped` hoặc `Owner`, kết nối phải dùng danh tính client **OAuth 2.1 (DCR + PKCE)** (`Connect-ChatGPT.ps1 -AuthMode oauth`). Khi client gửi yêu cầu ghép nối (`agent_request_owner_pairing`), một mã thử thách dùng một lần (`XXXX-XXXX`, có thời hạn TTL và chống phát lại) sẽ xuất hiện trong mục **Duyệt quyền Chủ máy (Review Owner Access)** của WinOwner Setup Center (hoặc `Grant-OwnerAccess.cmd`) để chủ máy bấm **Phê duyệt (Approve)** hoặc **Từ chối (Deny)**.
4. **Sử dụng đầy đủ tính năng (`OWNER_CONTROL_READY`):** Sau khi chủ máy phê duyệt, backend gắn trực tiếp owner delegation và capability grant với đúng `client_id` OAuth 2.1 đã xác minh đó — **không bao giờ yêu cầu người dùng tự tìm/nhập OAuth `client_id` thủ công và không bao giờ dán owner token vào khung chat ChatGPT**. Các client khác trên cùng tunnel không thể kế thừa quyền này.

## Nếu báo 401

Bộ cài WinOwner v1.2.0 mặc định chặn mọi request không có credential bằng HTTP 401. Chọn Authentication None một mình không tự tạo credential phía máy; lỗi 401 chứng tỏ tunnel chưa được cấu hình đúng khóa xác thực nội bộ hoặc token đã bị thu hồi. Hãy kiểm tra trong WinOwner Setup Center hoặc chạy `Connect-ChatGPT.cmd` để cấu hình lại an toàn. Không bao giờ tắt xác thực backend.

Tài liệu: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels