# Đợt C — BatchOut và trạng thái lựa chọn

Tiếp nối [đợt B](REVIEW-2026-09-14-PHASE-B.md). Đợt này sửa đường xuất trực tiếp
từ Assistant và ba vấn đề UX đã có bằng chứng. Không thay đổi design system.

## Kết quả

| Vấn đề trước sửa | Thay đổi |
|---|---|
| Module pushbutton chỉ import hàm mở dialog, trong khi Assistant cần ExportManagerWindow | Bổ sung import class trong entry point, giữ nguyên hành vi ribbon |
| Export trực tiếp chạy trên STA worker ngoài API context; configured launch gọi trực tiếp từ WPF | Cả hai đi qua ExternalEvent; strict mode từ chối chạy inline nếu event không có, Raise lỗi hoặc request bị từ chối |
| Document có thể đổi/đóng khi yêu cầu đang chờ | Kiểm tra lại document trong API callback trước khi tạo cửa sổ/export |
| Direct export bỏ qua khởi tạo của start_export | Khởi tạo titleblock cache, bộ đếm và dữ liệu trạng thái cho lần chạy |
| count=0 vẫn trả success; count dương nhưng thiếu file vẫn báo hoàn tất | Chỉ full success khi số output đủ và không có failure; partial giữ count và trả incomplete |
| Unknown format âm thầm chạy PDF; alias img/image có thể tự bỏ tick | Kiểm tra một format được hỗ trợ trước khi tạo window; chỉ cấu hình mỗi checkbox một lần |
| PDF gộp và IFC trả 1 cho nhiều sheet | Expected output là 1 cho hai chế độ này; so snapshot size/mtime của file nonempty trước/sau để không nhận file cũ/rỗng là output mới |
| Callback báo progress lỗi có thể biến export thành thất bại | Cô lập lỗi callback khỏi kết quả export |
| Hidden window không đóng; đóng thường sẽ ghi đè latest setup | Tháo save-on-close của hidden window và dispose ExternalEvent; không lưu cấu hình tạm |
| Stop/request cũ vẫn có thể xuất khi ExternalEvent chạy muộn | Kiểm tra request id/Stop trước chạy; giữa từng output với format không gộp; giữ file đã tạo |
| Generic route finally bỏ busy sớm; LLM finish và Stop watchdog chiếm quyền trả kết quả | BatchOut giữ quyền hoàn tất tới API callback. Kiểm thử cả nested route finally, LLM finish và watchdog thực tế từ source |
| ManaFami chỉ đếm lựa chọn đang hiển thị nhưng Load chạy tất cả lựa chọn | Hiển thị tổng chọn và số đang ẩn; Load vẫn bật nếu chỉ còn lựa chọn bị filter ẩn; giữ checkbox qua filter |
| ParameterSelector không cập nhật empty overlay | Theo dõi CollectionChanged của hai danh sách, cập nhật khi load/cache/add/remove/reset |
| AutoJoin bind bool HasItems vào Visibility không có converter | Đặt tên overlay hiện có và cập nhật Visible/Collapsed trong _refresh_rules |

### Hợp đồng xuất file

`direct_export` giữ tuple `(success, count, message)`. `success=False` có thể kèm
`count>0` khi partial/Stop; caller không được suy ra không có file từ boolean này.
Chỉ format bị thiếu key mới mặc định PDF; format trống, null hoặc không hỗ trợ
được từ chối. Một lần gọi xử lý một format, không tự diễn giải danh sách format.

Stop có hiệu lực **trước hoặc giữa các native export calls**, không cưỡng bức
ngắt lời gọi Revit đang chạy. PDF gộp/IFC vẫn là một call; file đã tạo được giữ.
Nếu chỉ đang queued, request có thể kết thúc qua Stop trước khi API callback
chạy; callback cũ không được xuất hoặc giải phóng busy của request mới.

Strict mode là tham số `require_api_context=True` dùng cho hai đường BatchOut.
Các caller cũ của `run_in_api_context` giữ tương thích, không được suy ra mọi
luồng khác đã bỏ fallback inline. Handler dùng chung có thể xử lý queue khi
Raise trả Pending; request Denied/TimedOut không được chạy inline. Cơ sở API:
[Autodesk — ExternalEvent.Raise](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/13bf4411-c400-dcd2-458c-7f09357d9ecb.htm).

## File thay đổi

- `T3Lab.extension/T3Lab.tab/Views & Sheets.panel/BatchOut.pushbutton/script.py`
- `T3Lab.extension/lib/Services/batchout_executor.py`
- `T3Lab.extension/lib/Services/revit_context.py`
- `T3Lab.extension/lib/GUI/T3LabAssistantDialog.py`
- `T3Lab.extension/lib/GUI/ManaFamiDialog.py`
- `T3Lab.extension/lib/GUI/ParameterSelectorDialog.py`
- `T3Lab.extension/lib/GUI/AutoJoinDialog.py`
- `T3Lab.extension/lib/GUI/Tools/AutoJoin.xaml`

## Kiểm chứng

| Lệnh | Kết quả |
|---|---|
| `python3 dev/test_batchout_executor.py` | 20 passed; gồm file tạm thật cho IFC/PDF gộp mới/cũ/rỗng, zero/partial, format, cleanup, Stop |
| `python3 dev/test_batchout_api_routing.py` | 19 passed; strict queue, document đổi/đóng, stale request, route finally, LLM handoff và Stop watchdog |
| `python3 dev/test_tool_selection_states.py` | 7 passed; selection ẩn và lifecycle empty states |
| `python3 dev/test_selection_dialogs.py` | 10 passed |
| `python3 dev/test_auto_join_results.py` | 5 passed |
| `python3 -X utf8 dev/test_assistant_routing.py` | all passed; gồm tương thích caller cũ của API runner |
| `python3 dev/test_decoupled_dialogs.py` | compile/XAML existence checks qua |
| `python3 dev/audit_t3.py --quiet` | 59 T3, 0 vi phạm |
| `python3 dev/audit_tools.py --quiet` | clean |
| `python3 dev/audit_cpython.py --quiet` | 0 P0, 444 P1 còn cần phân loại |
| `python3 dev/audit_api_context.py` | 5 dialog, 0 vi phạm trong phạm vi checker |
| `python3 dev/sync_t3_styles.py --check` | 59 file, 0 lệch |
| `python3 dev/audit_icons.py --quiet` và `python3 dev/build_icons.py --check` | 45 bundle qua, build đồng bộ |
| `python3 dev/check_xaml_load.py --out <temp-dir>` | 59 file, 0 hỏng sau sanitise |
| `powershell -NoProfile -STA -File dev/check_xaml_wpf.ps1 -Dir <temp-dir>` | 59 OK, 0 FAILED |

**46 test mới đợt C**. Test Assistant routing lần đầu bị lỗi encoding stdout
Windows khi in tiếng Việt; chạy lại bằng `-X utf8` đã qua, không sửa logic sản
phẩm để che lỗi test. Test sử dụng source thật với API/control doubles và file
tạm; không có file xuất Revit thật được tạo trong đợt này.

## Còn cần xác minh

**NEEDS VERIFICATION trong Revit:** Reload pyRevit; mở BatchOut thường/configured;
direct export 2 sheet riêng và PDF gộp; IFC; unavailable exporter; Stop trước
API callback và giữa hai file; hủy trong hộp thoại native; đóng/đổi document;
hidden-window event unsubscribe và cấu hình latest setup không đổi.

Kiểm tra ManaFami ở bộ lọc không còn dòng nhưng còn lựa chọn ẩn; ParameterSelector
load cached config/reset/remove-all; AutoJoin thêm/xóa rule cuối; DPI 100/125%.
XAML parse và mock tests không xác minh PythonNet binding/failure processing.

Snapshot file là bằng chứng output mới/nonempty, không xác minh nội dung PDF/IFC.
Các native exporters còn cần review sâu, đặc biệt transaction IFC trong dialog
và luồng xuất trực tiếp từ ribbon; wrapper sửa ở đây không chứng nhận toàn bộ
BatchOut. Legacy format key `nwd` vẫn gọi exporter NWC như trước.

Ưu tiên tiếp: runtime A/B/C; RibbonNames đổi tên lần hai; ManaSheets/ManaViews/
SheetGen preview và phục hồi lỗi; sau đó modeless/document context và hình học.
