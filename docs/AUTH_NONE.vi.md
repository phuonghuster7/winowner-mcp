# Authentication None trong ChatGPT: giao diện đơn giản, quyền Windows vẫn an toàn

Khi bạn thêm WinOwner MCP vào ChatGPT bằng tunnel riêng, mục **Authentication** có thể chọn **None** nếu kết nối phía WinOwner đã được xác thực bằng một cơ chế riêng. Đây là lựa chọn đã có trong ChatGPT; không cần bắt người dùng chọn OAuth trong giao diện nếu tầng backend đã có bảo vệ phù hợp.

**Phân biệt hai bước:** Authentication None tại màn hình ChatGPT chỉ bỏ đăng nhập OAuth ở màn hình tạo plugin. Nó KHÔNG vô hiệu hóa chứng thực phía máy, KHÔNG chứng minh người dùng là chủ máy và KHÔNG tự cấp Full Control.

## Hướng dẫn người mới

1. Cài WinOwner bằng một dòng CMD và nhấp đúp Check-WinOwner.cmd để kiểm tra cục bộ.
2. Ghép tunnel riêng của tài khoản; tunnel phải liên kết đúng tổ chức/workspace được phép truy cập. Không mở cổng MCP của Windows ra Internet.
3. Vào **ChatGPT → Plugins → + → Add custom MCP server**, chọn **Connection: Tunnel**, rồi chọn tunnel của mình. Nếu backend đã có chứng thực và quyền hợp lệ, có thể chọn **Authentication: None**; đọc và chấp thuận cảnh báo quyền.
4. Thử công cụ đọc trạng thái. Chỉ xác nhận CONNECTED sau khi ChatGPT gọi công cụ thật thành công.

## Nếu báo 401

Bộ cài WinOwner v1.2.0 mặc định chặn request không có credential. Chọn Authentication None một mình không tự tạo credential phía máy; lỗi 401 chứng tỏ kết nối chưa được cấp quyền phù hợp. Chủ máy cần hoàn tất ghép nối được xác thực hoặc chọn luồng OAuth. Không đổi cấu hình chỉ để cho request chưa xác thực đi qua.

## Nếu cần Full Control

Các thao tác chủ máy vẫn cần chứng thực danh tính owner và capability grant/delegation hiện có. Không tự biến token chung của tunnel thành chứng thực owner. Không chia sẻ tunnel hoặc quyền điều khiển máy với người không được phép.

**Đích v1.2.0:** WinOwner Setup Center tự kiểm tra trạng thái tunnel, hỗ trợ thiết lập có xác nhận của chủ máy và xác minh công cụ thật. Giữ Authentication None ở giao diện ChatGPT nếu có chứng thực backend hợp lệ, thay vì yêu cầu đăng nhập hai lần.

Tài liệu: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels