# Plan — Không còn "Command Failure for External Command"

> Lập: 2026-09-29 · Mục tiêu: **0 lần** dialog *Command Failure for External Command*
> trên mọi máy, mọi bản Revit 2022–2027, đo được bằng journal chứ không bằng cảm giác.

## 1 · Dialog này thật ra là gì (đã xác minh, không suy đoán)

Disassemble `CPythonEngine.Execute` của pyRevit 6.5.5: **mọi exception Python bên trong
tool đều bị pyRevit bắt** và ghi ra cửa sổ output (`ScriptIO.WriteError`), còn T3Lab thì có
`GUI/ErrorGuard.py` hiện dialog tiếng Anh. Vậy dialog trắng "Command Failure" **chỉ xuất
hiện khi chính engine CPython hỏng, trước khi dòng code T3Lab nào chạy**. Sửa code tool
không chạm được tới nó; phải xử lý tầng engine.

Đo trên toàn bộ journal còn lưu của máy dev (33 file, Revit 2023/2025/2026):

| Số lần | Lỗi | Dấu vết journal | Nguyên nhân |
|---|---|---|---|
| 1 | `Object reference not set…` | `RuntimeData.RestoreRuntimeDataImpl` | **pyRevit Reload** tắt CPython; pythonnet 3 không khởi động lại được trên .NET 8+ |
| 1 | `This property must be set before runtime is initialized` | `CPythonEngine.Start` → `Runtime.set_PythonDLL` | hệ quả của dòng trên — runtime kẹt dở dang |
| 4 | `Object reference not set…` | `TypeManager.AllocateTypeObject` ← `CPythonEngine.SetupStreams` | hệ quả của dòng trên — mọi tool bấm sau đó trong **cùng phiên** (DatumSync, FamiTransfer, CropSync, ModelAuditor, BatchOut) |

**6/6 lỗi đo được = một lần Reload lúc 09:48, trong một phiên Revit không được khởi động
lại tới 13:55.** Ngoài ra đã biết thêm 1 lớp lỗi từ máy khác: *The type initializer for
'Delegates' threw an exception* (không nạp được `python312.dll`).

## 2 · Các nguyên nhân phải triệt

| # | Nguyên nhân | Kích hoạt | Hiện trạng |
|---|---|---|---|
| N1 | Shutdown engine CPython | pyRevit **Reload**, bật/tắt extension trong **Extensions**, **Ctrl+Alt+Shift+Click** một nút (RefreshEngine) | Chưa chặn. Bản vá có sẵn nhưng chờ duyệt (`lib/pyrevit_patches.py`) |
| N2 | Không nạp được `python3XX.dll` | IT chặn DLL trong `%APPDATA%`, pyRevit cài thiếu, antivirus | Có chẩn đoán: `Install-T3Lab.ps1 -CheckOnly` + `startup.py` |
| N3 | Nhiều bản T3Lab / pyRevit chồng nhau | T3LabLite cũ, 2 clone pyRevit | Lite đã gỡ nhưng còn thư mục rỗng |

## 3 · Kế hoạch

### GĐ0 — Dập lửa (ngay hôm nay · **user làm**)
- [ ] Đóng hẳn **phiên Revit 2026 mở từ 08:16** (journal.0017, model MBSIR2…). Phiên này
      hỏng từ 09:48: mọi tool T3Lab bấm trong đó đều sẽ lỗi cho tới khi đóng.
      Mỗi process Revit có engine riêng → **phiên nào đã từng Reload thì restart phiên đó**.
- [ ] Xoá thư mục rỗng `%APPDATA%\pyRevit\Extensions\T3LabLite.extension` (N3).
- [ ] Từ giờ: sửa code / cập nhật T3Lab → **khởi động lại Revit, không bấm Reload**.

### GĐ1 — Chặn N1 tận gốc (Claude chuẩn bị · **user duyệt**)
- [x] Tìm nguyên nhân bằng journal + IL pyRevit 6.5.5 · ghi vào CLAUDE.md, INSTALL.md, memory.
- [x] Bản vá `lib/pyrevit_patches.py` — Reload **giữ** engine CPython thay vì tắt nó
      (exact-match pyRevit 6.5.5, backup `.t3lab-backup`, `restore` gỡ được) · 11 test.
- [x] **Đã áp trên máy dev 2026-09-29 14:00** theo lệnh "APPLY" của user
      (`%APPDATA%\pyRevit-Master\...\loader\sessionmgr.py`, backup `sessionmgr.py.t3lab-backup`;
      chỉ thêm code trong `_clear_running_engines`, compile OK, CRLF giữ nguyên).
      Gỡ: `python T3Lab.extension/lib/pyrevit_patches.py restore`.
- [x] **Tự quét & tự áp khi thiếu** (yêu cầu của user 2026-09-29): `startup.py` → `auto_patch_pyrevit()`
      mỗi lần pyRevit nạp — tự vá lại sau khi cài lại/cập nhật pyRevit, và trên máy đồng nghiệp.
      Chỉ clone đang chạy, chỉ khớp chính xác 6.5.5, backup, báo MỘT lần khi vá.
      Opt-out: `T3LAB_NO_PYREVIT_PATCH=1` hoặc `%APPDATA%\T3LabAI\pyrevit_patch.disabled` · 16 test.
- [ ] Xác minh nhánh IronPython 2.7 của auto-patch trong Revit thật (chưa chạy được: Revit đã đóng).
- [ ] Verify trong Revit sau khi áp: mở 1 tool → Reload → mở lại tool → **không lỗi**;
      `dev/scan_journals.py` (GĐ3) báo 0.
- [ ] `Install-T3Lab.ps1 -CheckOnly` báo trạng thái bản vá (`already / missing / unsupported`)
      — cờ `-PatchPyRevit` không cần nữa vì `startup.py` đã tự áp.
- [ ] Khi pyRevit lên bản mới: `dev/test_pyrevit_patches.py` fail ở test anchor → cập nhật
      `ORIGINAL`/`PATCHED` cho bản mới trước khi cho team cập nhật pyRevit.
- [ ] N1 phụ — Ctrl+Alt+Shift+Click: nằm trong C# của pyRevit, không vá được bằng Python.
      Ghi vào INSTALL.md như thao tác **cấm** trên Revit 2025+.

### GĐ2 — Nếu vẫn lỡ hỏng: nói tiếng người thay cho dialog trắng
- [x] `startup.py` phát hiện runtime kẹt **lúc Reload** → dialog *Restart Revit to use T3Lab tools again*
      (đã chạy thử trong phiên hỏng thật: phát hiện đúng).
- [x] `startup.py` kiểm engine CPython lúc mở Revit (N2) — lý do Windows từ chối nạp DLL.
- [ ] **Watchdog** `hooks/view-activated.py` (IronPython, không cần CPython): kiểm 2 cờ pythonnet
      mỗi lần đổi view, hiện thông báo restart **một lần mỗi phiên**. Phủ được trường hợp
      hỏng do Ctrl+Alt+Shift+Click hoặc do bản vá chưa áp. Chi phí: đọc 2 bool tĩnh.
      *Cần xác minh trong Revit rằng hook `view-activated` của extension chạy ổn trên 2023 và 2026.*

### GĐ3 — Đo để biết đã hết (Claude làm)
- [ ] `dev/scan_journals.py`: quét journal mọi bản Revit, đếm Command Failure theo nguyên nhân
      (đúng script đã dùng để lập bảng §1). Có `--since <ngày>`.
- [ ] Baseline 2026-09-29: **6** (cả 6 từ N1).
- [ ] Máy đồng nghiệp chạy script này, gửi kết quả → gom vào bảng theo dõi dưới đây.

### GĐ4 — Lỗi Python trong tool (không ra dialog trắng, nhưng vẫn làm tool chết)
- [x] Gate đang có: `audit_cpython` (C1–C10, mới thêm C10 `with Transaction` trần),
      `audit_revit_compat` (2022–2027), `audit_wiring`, `check_xaml_load` + `check_xaml_wpf`.
- [x] Đã sửa hôm nay: 25 chỗ `with Transaction(...)` chết dưới CPython (12 file) ·
      `create_project_parameter` chết trên 2025+ · wiring DatumSync/CropSync.
- [ ] **Quyết định của user:** git `pre-commit` hook chạy bộ gate — cấu hình bền vững, cần đồng ý.
- [ ] Smoke test trong Revit **sau restart**: 45 tool × Revit 2023 / 2025 / 2026, ghi kết quả vào
      `panel-*.md`. Không bịa kết quả — user test, Claude tick theo phản hồi.

### GĐ5 — Máy khác trong team
- [ ] Mỗi máy chạy `scripts/Install-T3Lab.ps1 -CheckOnly` → 0 FAIL (bắt N2, N3).
- [ ] Thống nhất một bản pyRevit (**6.5.5**) cho cả team — bản vá GĐ1 chỉ đúng cho bản này.
- [ ] Máy báo lỗi *Delegates*: chạy `-CheckOnly`, xử lý theo mã lỗi Win32 (xem INSTALL.md).

## 4 · Định nghĩa "xong"

| Tiêu chí | Cách đo |
|---|---|
| 0 Command Failure trên máy dev trong **10 ngày làm việc liên tiếp** | `dev/scan_journals.py --since` |
| 0 Command Failure trên mọi máy team trong 10 ngày | kết quả `scan_journals` từng máy |
| Reload không còn làm hỏng CPython | test tay GĐ1 trên Revit 2025 + 2026 |
| Mọi gate xanh ở mỗi commit | pre-commit hook (nếu được duyệt) |

## 5 · Theo dõi

| Ngày | Máy | Revit | Command Failure | Nguyên nhân | Ghi chú |
|---|---|---|---|---|---|
| 2026-09-29 | PC-WHVN00078 (dev) | 2026.5 | 6 | N1 ×6 | baseline; 1 Reload lúc 09:48 |

## Phát sinh
- (trống)
