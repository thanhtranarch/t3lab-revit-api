# Đợt B — kết quả transaction và phản hồi lỗi

Tiếp nối [review tổng thể](REVIEW-2026-09-13.md), đợt này xử lý ba nhóm P1:
AutoJoin, Advanced Purge và Group Manager. Không thay đổi XAML hoặc design system.
Các sửa của đợt A và tài liệu đang có trong working tree được giữ nguyên.

## Thay đổi

| Nhóm | Lỗi xác định từ source | Hành vi sau sửa |
|---|---|---|
| AutoJoin / Unjoin | Bỏ qua Commit status; exception rollback vẫn trả số cặp đã thao tác | Chỉ trả số cặp đã xác nhận sau Committed; lỗi/Pending trả 0 confirmed cùng chi tiết lỗi |
| AutoJoin — Stop | Nhánh hủy commit phần đã chạy nhưng UI che mất thông báo lỗi nếu người dùng bấm Stop | Giữ chính sách lưu phần đã làm khi commit thành công, nói rõ partial commit; lỗi khi commit vẫn được hiển thị |
| AutoJoin — UI/Quick Join | Lỗi vẫn hiện Done/completed và số liệu có thể bị hiểu là đã lưu | Phân biệt completed, completed with errors, stopped by request và stopped with an error |
| Advanced Purge | Category rollback vẫn giữ deleted; không kiểm tra Assimilate; callback lỗi sau commit có thể khiến thao tác bị báo thất bại | Kết quả chỉ chứa mục được commit; lỗi group được truyền lên; lỗi cập nhật progress không thay đổi kết quả transaction |
| Group Manager — 4 thao tác ghi | Rename, Workset, Purge và Ungroup đều bỏ qua Commit status | Chỉ trả kết quả sau Committed; rollback chỉ khi Started; Pending không bị rollback sai trạng thái |
| Group rename | Đổi A→B thành công, B→A thất bại, phục hồi B trùng tên rồi bị nuốt exception, có thể lưu T3TMP | Nếu phục hồi tên tạm thất bại, rollback toàn bộ thao tác; đồng bộ lại tên cache sau rollback |
| Group rename — tên hợp lệ | Dùng prefix T3TMP để đoán item chưa rename xong | Theo dõi item thực sự đã ghi tên cuối; không sửa lại tên người dùng chủ ý đặt bắt đầu bằng T3TMP |
| Group Workset | Parameter.Set trả False vẫn được đếm Moved | Ghi failed và thông báo tham số từ chối giá trị |
| Group Manager — UI | Exception luôn được mô tả là đã rollback/không đổi model, kể cả Pending | Thông báo chưa hoàn tất, giữ chi tiết lỗi và hướng dẫn xử lý failure dialog, kiểm tra model trước retry |

## File triển khai

- `T3Lab.extension/lib/Services/join_service.py`
- `T3Lab.extension/lib/Services/ModelAuditor/advanced_purge/advanced_purge_executor.py`
- `T3Lab.extension/lib/Snippets/_group_ops.py`
- `T3Lab.extension/lib/GUI/AutoJoinDialog.py`
- `T3Lab.extension/lib/GUI/ManaGroupDialog.py`

API trả về được giữ tương thích: AutoJoin vẫn tuple bốn trường, Purge vẫn
deleted/failed, Group vẫn danh sách kết quả theo item. Lỗi transaction của Group
tiếp tục dùng exception như trước; caller không được dùng danh sách chưa commit.

## Kiểm thử và giới hạn

Các test thực thi source đang ship với transaction/model/control doubles, không
phải bản sao logic. Bao phủ Commit=Committed/RolledBack/Pending, exception,
Start failure, partial success, hủy, tên hoán đổi và callback lỗi.

Kết quả kiểm tra cuối ngày 2026-09-14:

| Lệnh | Kết quả |
|---|---|
| `python3 dev/test_join_transactions.py` | 11 passed |
| `python3 dev/test_advanced_purge_transactions.py` | 11 passed |
| `python3 dev/test_group_transactions.py` | 10 passed, gồm ma trận trạng thái cho cả 4 thao tác Group |
| `python3 dev/test_auto_join_results.py` | 5 passed |
| `python3 dev/test_group_manager.py` | 61 passed |
| `python3 dev/test_service_transactions.py` | 22 passed |
| `python3 dev/test_selection_dialogs.py` | 10 passed |
| `python3 dev/audit_t3.py --quiet` | 59 T3, 0 vi phạm |
| `python3 dev/audit_tools.py --quiet` | clean |
| `python3 dev/audit_cpython.py --quiet` | 0 P0, 444 P1 chưa xử lý trong phạm vi đợt B |
| `python3 dev/audit_api_context.py` | 5 dialog, 0 vi phạm |
| `python3 dev/sync_t3_styles.py --check` | 59 file, 0 lệch |
| `python3 dev/audit_icons.py --quiet` / `python3 dev/build_icons.py --check` | 45 bundle qua, build đồng bộ |

Tổng cộng **37 test mới đợt B**, cộng 93 test hồi quy có sẵn = **130 passed**.
Không sửa XAML trong đợt B; lần nạp 59/59 WPF gần nhất được ghi trong báo cáo A.
Review chéo đã kiểm tra Group Manager và thông báo UI; phần sửa thứ tự cấu hình
failure options sau `Transaction.Start()` của AutoJoin đã được kiểm tra source
và test tái hiện việc Start reset options.

**NEEDS VERIFICATION trong Revit:** failure dialog/finalizer, Undo, Partial Stop
với model thật, worksharing ownership, tên group trùng/hoán đổi và refresh sau
Pending. Đợt này không chạy thao tác ghi lên model của người dùng.

Reload pyRevit trước khi kiểm thử thay đổi trong `lib/`. Gate xanh và mock test
không chứng minh tính đúng của failure processing trong Revit.

## Ưu tiên tiếp theo

1. Smoke test trong Revit cho các bản sửa A/B trước khi đánh dấu runtime verified.
2. BatchOut: kiểm tra 0 file, partial export và cancel, đối chiếu file tạo thực tế.
3. ManaFami: làm rõ số lượng lựa chọn bị filter ẩn; ParameterSelector/AutoJoin:
   nối empty state; RibbonNames: kiểm tra đổi short name lần hai.
4. ManaSheets/ManaViews/SheetGen: preview và phục hồi lỗi theo item.
