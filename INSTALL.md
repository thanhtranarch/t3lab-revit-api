# Cài T3Lab trên một máy bất kỳ

Tài liệu này là quy trình cài **duy nhất** được hỗ trợ. Làm đúng 4 bước dưới đây
thì tool chạy được trên mọi máy; lệch bước nào thì phần "Khi vẫn lỗi" nói rõ
triệu chứng tương ứng.

> Mọi thông báo của script và của tool đều bằng tiếng Anh — tài liệu này tiếng
> Việt cho người cài.

---

## Yêu cầu

| Thành phần | Bắt buộc | Ghi chú |
|---|---|---|
| Windows 10/11 | ✅ | |
| Revit 2022 – 2027 | ✅ | Dải hỗ trợ, gác bằng `dev/audit_revit_compat.py`. Đã chạy thật trên 2023.1 và 2026.5; 2022 / 2024 / 2025 / 2027 chưa QA trong Revit |
| pyRevit **có CPython engine** | ✅ | Thư mục `bin\cengines\CPY3*`. Khuyến nghị bản ship `CPY3123` (Python 3.12) |
| Microsoft Excel | ⛔ tuỳ chọn | Chỉ cần cho import/export Excel của IFC-SG, Parameter Manager, Sheet Manager |

Không cần cài Python riêng, không cần `pip install` gì cả — extension chỉ dùng
thư viện chuẩn của engine pyRevit và .NET.

---

## 4 bước cài

### 1 · Lấy source

```bash
git clone <repo-url> C:\T3Lab
```

Nếu phải copy tay thay vì clone: copy **cả thư mục**, rồi xoá mọi `__pycache__`
và `T3Lab.extension\lib\config\tool_registry.json` (file này chứa đường dẫn
tuyệt đối của máy cũ).

> **Đặt ở đâu:** ổ cục bộ, đường dẫn ngắn (`C:\T3Lab` là tốt nhất).
> Tránh `C:\Program Files` (không ghi được) và tránh thư mục OneDrive đang bật
> Files On-Demand (file cloud-only đọc hỏng khi offline).

### 2 · Chạy script cài

```powershell
powershell -ExecutionPolicy Bypass -File C:\T3Lab\scripts\Install-T3Lab.ps1
```

Script sẽ: dò pyRevit clone + CPython engine (không phụ thuộc tên clone), kiểm
tra quyền ghi / độ dài path / file bị Windows chặn / file OneDrive cloud-only,
phát hiện bản T3Lab trùng, đăng ký thư mục extension với pyRevit, rồi xoá cache cũ.

Chỉ muốn kiểm tra, chưa đăng ký gì:

```powershell
powershell -ExecutionPolicy Bypass -File C:\T3Lab\scripts\Install-T3Lab.ps1 -CheckOnly
```

Phải đạt **0 FAIL** mới mở Revit. WARN thì chỉ mất tính năng phụ (ví dụ không có Excel).

### 3 · Reload pyRevit một lần

Mở Revit → ribbon **pyRevit → Reload**.

Bắt buộc, không bỏ qua: engine CPython của pyRevit là **persistent**, và
`__persistentengine__` của các cửa sổ modeless (Assistant, BatchOut…) chỉ có
hiệu lực sau một lần Reload tường minh.

### 4 · Kiểm tra

Mở một tool bất kỳ trên tab **T3Lab**. Nếu mở được và không có dialog lỗi thì xong.

---

## Khi vẫn lỗi

Dialog `Command Failure for External Command` là **wrapper chung của Revit**,
không phải lỗi thật. Lấy nguyên nhân thật theo thứ tự:

1. **Bấm `Show details`** ngay trong dialog đó.
2. **Dialog `T3Lab tools cannot start on this machine` lúc mở Revit** — do
   `T3Lab.extension/startup.py` hiện. File này chạy bằng IronPython nên vẫn chạy
   được khi engine CPython hỏng; nó nói rõ engine nào, DLL nào và lý do Windows
   từ chối nạp. Bản ghi lại ở `%APPDATA%\T3LabAI\engine_check.log`.
3. **`%APPDATA%\T3LabAI\bootstrap.log`** — chỉ được ghi khi bootstrap không nạp
   được stdlib. Nội dung cho biết tìm thấy clone/engine nào, engine nào được nạp,
   module nào còn thiếu.
4. **Revit journal**:
   `%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit <Year>\Journals\journal.XXXX.txt`
   — có stack trace thật. Đây là nguồn chính thức theo rule của repo, tuyệt đối
   không đoán mò trước khi đọc nó.

### Bảng triệu chứng → nguyên nhân

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `No module named configparser` / `csv` / `_sqlite3` | pyRevit không có CPython engine, hoặc engine khác phiên bản Python đang chạy | Chạy `Install-T3Lab.ps1 -CheckOnly`; cài bản pyRevit có `CPY3*` |
| `bad magic number in 'json'` | Đang trộn stdlib của minor version khác | Đã được chặn bằng version gate trong `lib/_cpython_bootstrap.py`; nếu vẫn thấy, kiểm tra biến môi trường `PYTHONPATH` toàn máy |
| `The type initializer for 'Delegates' threw an exception` — mọi tool `#! python3` đều chết, tool IronPython của pyRevit vẫn chạy | pythonnet không nạp được `python3XX.dll` của engine, **trước khi** chạy tới dòng code T3Lab nào. Hay gặp nhất: IT chặn DLL trong `%APPDATA%` (AppLocker / WDAC / antivirus), hoặc pyRevit cài thiếu | Chạy `Install-T3Lab.ps1 -CheckOnly` — dòng `python312.dll cannot be loaded - Win32 error N` nói rõ lý do. Bị chặn → nhờ IT whitelist thư mục engine, hoặc cài pyRevit bản all-users (`C:\Program Files`). Sửa xong **phải khởi động lại Revit**: lỗi này dính đến khi đóng Revit |
| `This property must be set before runtime is initialized`, hoặc `Object reference not set to an instance of an object` ở MỌI tool (Revit 2025+) | **pyRevit Reload** (kể cả bật/tắt extension trong Extensions manager) tắt engine CPython, mà pythonnet 3 không khởi động lại được trên .NET 8+. Runtime kẹt dở dang tới khi đóng Revit. Journal ghi `RuntimeData.RestoreRuntimeDataImpl` → `CPythonEngine.Start` → `set_PythonDLL` | Khởi động lại Revit (lúc Reload, `startup.py` hiện dialog *Restart Revit to use T3Lab tools again*). Tránh tái phát: T3Lab **tự vá pyRevit** lúc Revit khởi động (`startup.py` → `lib/pyrevit_patches.py`) để Reload giữ engine CPython — có hiệu lực từ lần restart sau khi vá. Chỉ cho pyRevit 6.5.5; tắt bằng `T3LAB_NO_PYREVIT_PATCH=1` hoặc file `%APPDATA%\T3LabAI\pyrevit_patch.disabled`; gỡ bằng `python T3Lab.extension/lib/pyrevit_patches.py restore`. Không bấm Ctrl+Alt+Shift+Click lên nút pyRevit (vẫn tắt engine) |
| Cửa sổ modeless chết ở click đầu tiên | Chưa Reload pyRevit sau khi cài | pyRevit → Reload |
| Tab T3Lab xuất hiện 2 lần, lỗi trỏ về code không khớp file trên đĩa | Có bản T3Lab thứ hai đang được load | Script báo ở mục "Duplicate T3Lab copies" — giữ đúng một bản |
| Lỗi chỉ ở tool có import/export Excel | Máy không có Microsoft Excel | Cài Excel hoặc dùng CSV |
| Tool đọc file XAML lỗi, chỉ trên một máy | File OneDrive cloud-only, hoặc path quá dài | "Always keep on this device", hoặc chuyển về `C:\T3Lab` |

---

## Ghi chú kỹ thuật cho người bảo trì

Việc dò engine CPython nằm **duy nhất** ở
[`T3Lab.extension/lib/_cpython_bootstrap.py`](T3Lab.extension/lib/_cpython_bootstrap.py).
Thứ tự dò: (1) từ chính interpreter đang chạy qua `sys.path`/`sys.prefix`,
(2) biến môi trường của pyRevit, (3) quét mọi thư mục tên bắt đầu bằng `pyrevit`
trong các install root thông dụng. Engine chỉ được nạp khi **phiên bản Python của
engine trùng interpreter đang chạy** — trộn minor version gây `bad magic number`.

Trước 2026-09-16, khối dò engine này bị **copy-paste vào 47 file** với tên clone
cứng `pyRevit-Master`/`pyRevit` và engine cứng `CPY3123` — tức đúng layout của
một máy dev duy nhất. Mọi bản sao đó đã bị xoá; đừng thêm lại. Tool mới chỉ cần
đúng khung chuẩn trong `.claude/rules/new-tool-standard.md`:

```python
import _cpython_bootstrap
_cpython_bootstrap.init_cpython_paths()
```

Debug môi trường từ trong Revit:

```python
import _cpython_bootstrap
print(_cpython_bootstrap.describe_environment())
```
