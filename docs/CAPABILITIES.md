# Capabilities & Permissions / Chức năng và quyền truy cập

**Cloud MCP catalog: 14 baseline tools + 20 owner-only catalog entries = up to 34.**  
**Danh mục cloud MCP: 14 tool cơ bản + 20 tool thuộc danh mục owner = tối đa 34.**

This is an inventory of the cloud catalog implemented in source, not a promise that every tool can execute on every machine or account. The local/legacy catalog is different. / Đây là danh mục cloud trong mã nguồn, không phải cam kết mọi tool đều thực thi được trên mọi máy hoặc tài khoản. Danh mục local/legacy khác danh mục cloud.

## Tool inventory / Danh sách tool

| Capability / Nhóm | Tool identifiers / Tên tool | Catalog / Danh mục | Purpose and boundary / Công dụng và giới hạn |
|---|---|---|---|
| Workspace & Git / Thư mục và Git | `agent_workspace_list`, `agent_read_file`, `agent_git_status`, `agent_git_diff` | Baseline | Read approved data / Đọc dữ liệu được phép |
| Editing / Chỉnh sửa | `agent_write_file`, `agent_apply_patch` | Baseline | Scoped modifications; requires write authorization / Sửa trong phạm vi được cấp quyền ghi |
| Coding execution / Kiểm thử | `agent_run_command`, `agent_run_tests` | Baseline | Privileged execution; not an OS sandbox / Thực thi cần cấp quyền riêng, không phải sandbox hệ điều hành |
| Task monitoring / Theo dõi tác vụ | `agent_task_status`, `agent_task_logs`, `agent_task_diff`, `agent_task_artifacts`, `agent_list_tasks` | Baseline | Read authorized task records / Đọc thông tin tác vụ được phép |
| Task submission / Gửi tác vụ | `agent_submit_task` | Baseline | Submission does not imply approval to execute / Gửi tác vụ không đồng nghĩa được phép thực thi |
| Filesystem management / Quản lý tệp | `agent_create_file`, `agent_delete_file`, `agent_create_directory`, `agent_delete_directory`, `agent_move_path`, `agent_copy_path` | Owner | Owner-only catalog; changes and deletes are sensitive / Danh mục owner, thay đổi và xóa là thao tác nhạy cảm |
| Task control / Điều phối tác vụ | `agent_task_pause`, `agent_task_resume`, `agent_task_cancel`, `agent_emergency_stop` | Owner | Owner authorization; task lifecycle control / Cần xác thực owner, quản lý vòng đời tác vụ |
| Desktop observation / Quan sát màn hình | `agent_desktop_screenshot`, `agent_get_active_window`, `agent_visual_verify`, `agent_list_windows`, `agent_system_status` | Owner | Owner authorization; may expose private information / Cần xác thực owner, có thể hiển thị dữ liệu riêng tư |
| Desktop interaction / Tương tác màn hình | `agent_focus_window`, `agent_keyboard_type`, `agent_mouse_click`, `agent_launch_app` | Owner | Privileged interactive actions / Tương tác cần cấp quyền riêng |
| System execution / Thực thi hệ thống | `agent_powershell_execute` | Owner | Sensitive execution, separate authorization / Thực thi nhạy cảm, cần cấp quyền riêng |

## Permission model / Mô hình quyền

| Tier | Meaning / Ý nghĩa |
|---|---|
| READ | Inspect approved data; not anonymous access. / Đọc dữ liệu được phép; không phải truy cập ẩn danh. |
| WRITE | Change data only within an authorized scope. / Thay đổi dữ liệu trong phạm vi đã cấp phép. |
| EXECUTE | Separate authorization for code and process execution. / Cần quyền riêng để chạy mã và tiến trình. |
| DESKTOP | Separate authorization for interactive desktop access; session requirements apply. / Cần quyền riêng để tương tác màn hình và đáp ứng điều kiện phiên Windows. |
| SYSTEM_ADMIN | High-impact operations, blocked by default; no automatic Windows elevation. / Tác vụ tác động lớn, mặc định bị chặn; không tự nâng quyền Windows. |

- **Default / Mặc định:** `read_only`. A tool appearing in the catalog is not an authorization decision. / Tool xuất hiện trong danh mục không có nghĩa đã được phép gọi.
- **ChatGPT approvals / Phê duyệt ChatGPT:** `Allow all tools` only changes confirmation prompts for otherwise available tools; it does **not** turn a read-only account into a verified owner or bypass server/Windows policy. It is **not** required for basic setup. Prefer confirmation for sensitive operations. / `Allow all tools` chỉ thay đổi việc hỏi xác nhận với công cụ vốn đã được phép; **không** cấp danh tính owner, không vượt qua chính sách server/Windows và **không bắt buộc** khi cài đặt.
- **Identity / Danh tính:** Shared tunnel credentials and ChatGPT approval settings do not establish owner identity. / Khóa tunnel chung và thiết lập phê duyệt ChatGPT không xác minh danh tính chủ máy.
- **Scope / Phạm vi:** Effective permissions depend on server policy, authenticated identity, workspace and valid authorization; Windows access controls still apply. / Quyền thực tế phụ thuộc chính sách server, danh tính, workspace và cấp phép hợp lệ; vẫn chịu giới hạn Windows.
- **Revocation / Thu hồi:** Removing a plugin, disconnecting a tunnel and revoking an owner grant are distinct operations. Verify revocation separately. / Gỡ plugin, ngắt tunnel và thu hồi quyền owner là các thao tác khác nhau. Kiểm tra thu hồi riêng.
- **Verification / Kiểm chứng:** Do not treat discovery, a successful local health check, or CI as proof of end-to-end authorization. / Không coi việc thấy tool, kiểm tra local hoặc CI thành công là bằng chứng cấp quyền đầu-cuối.

## Source of truth / Nguồn đối chiếu

- [Cloud catalog assembly](../mcp-gateway/gateway_server.py)
- [Task definitions](../runtime/agent_mcp_tools.py)
- [Coding and filesystem definitions](../runtime/coding_tools.py)
- [Example policy](../config/policy.example.json)
- [Security](../SECURITY.md)

Source inventory reviewed at commit `c29d665`. This is a static documentation review, not a live authorization or security audit. / Đối chiếu danh mục tại commit `c29d665`; chưa phải kiểm thử quyền thực tế hoặc kiểm toán bảo mật.
