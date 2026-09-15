# Đợt D — RibbonNames và kết quả export native

Tiếp nối [đợt C](REVIEW-2026-09-14-PHASE-C.md). Tập trung vào khả năng
khôi phục tên ribbon và thông báo đúng kết quả export; không đổi XAML/design system.

## Thay đổi

| Vấn đề | Cách xử lý |
|---|---|
| Đổi alias lần hai làm mất liên hệ với tên gốc | Chụp danh tính tab trước thao tác, lưu theo tab ID; giữ tương thích cấu hình cũ |
| Alias trùng khiến restore đoán sai tab | Từ chối alias mới bị trùng; bỏ qua danh tính cũ không phân giải được và báo rõ |
| Tab thiếu ID hoặc title setter thất bại | Báo giới hạn trong phiên, đếm từng tab và lưu trạng thái mixed khi chỉ thành công một phần |
| Lưu JSON thất bại nhưng UI vẫn báo đã lưu | Callback trả kết quả thật; ghi UTF-8 qua file tạm cùng thư mục rồi atomic replace; giữ file cũ khi lỗi |
| File cũ/rỗng vẫn được nhận sau timeout | Chỉ nhận file thường, nonempty và mới; fallback không nhận lại file expected đã fail, không dùng một file cho hai item |
| PDF gộp đánh dấu cả sheet bị bỏ qua là thành công | Chỉ đưa item hợp lệ vào tập export và cập nhật các item đó sau xác minh output |
| PDF gộp/từng sheet bỏ qua boolean export, adapter luôn trả True | Adapter trả kết quả native; cả hai nhánh yêu cầu API thành công trước khi xác minh file; giữ artifact khi API thất bại để kiểm tra |
| IFC chưa kiểm tra đủ kết quả native/transaction/file | Kiểm tra boolean export, commit và output; rollback transaction Started; dừng batch khi Pending |
| Image alias không trỏ đúng checkbox thật | Dùng export_img; test đối chiếu với tên control trong XAML |
| Coordinator luôn báo Export complete | Đối chiếu count với số output kỳ vọng từng format, kể cả exporter trả 0 mà không ghi item failure; phân biệt zero/partial/full và giữ count trước lỗi |

## Kiểm chứng và giới hạn

Các test lấy method/class thật từ source, thay thế Revit/WPF bằng doubles và dùng
file tạm thật cho storage/output. Chúng không chứng minh native Revit đã tạo file.

- RibbonNames: 12 test; JSON storage: 6; file verification: 9.
- Direct executor: 21 test, gồm kiểm tra control image thực trong XAML.
- Native PDF/IFC: 23 test, gồm boolean False dù file có tồn tại, file cũ/rỗng,
  item bị bỏ qua, IFC rollback/Pending và PDF từng sheet tiếp tục sau một item lỗi.
- Coordinator summary: 6 test, gồm lỗi chặn format sau, giữ count trước,
  format unavailable sau format thành công và số output PDF gộp.
- API routing: 19 test; selection states: 7 test.
- Gate T3: 59 file, 0 vi phạm; static gate: clean.
- API context: 5 dialog, 0 vi phạm; CPython: 0 P0, còn 444 P1.
- Shared T3 styles: 59 file, 0 lệch. Không thay đổi XAML trong đợt này.

Tổng cộng **103 test qua** trong lượt kiểm tra cuối: BatchOut 69, RibbonNames/storage
18, file verification 9, selection states 7. Đây là tổng test hồi quy đã chạy,
không phải 103 test mới.

Test API routing phải chạy bằng `python3 dev/test_batchout_api_routing.py` vì
import helper cùng thư mục; gọi dưới dạng module từ root không tìm được helper.
Không thay đổi code sản phẩm để che lỗi cách gọi test.

## Kế hoạch tiếp theo

1. Reload pyRevit rồi kiểm tra RibbonNames: đổi hai lần, Save, mở lại, Restore,
   tab readonly/không có ID, lỗi quyền ghi. Kiểm tra thông báo dài và tooltip ở DPI 100/125%.
2. BatchOut trên model kiểm thử: PDF gộp có item bị bỏ qua, file đích đã tồn tại,
   IFC thành công/thất bại/Cancel, Pending trong failure dialog và nhiều format.
   Đối chiếu file trên đĩa với bảng kết quả; output chưa xác nhận có thể vẫn tồn tại.
3. Tiếp tục backlog ManaSheets/ManaViews/SheetGen: preview, validation và recovery;
   chưa gán điểm UX từ static gate hoặc coi runtime đã đạt.

Không tạo commit trong đợt này. Các sửa ở thư viện cần Reload pyRevit trước khi thử.
