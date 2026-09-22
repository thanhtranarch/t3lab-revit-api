# T3Lab pyRevit Extension

pyRevit extension for Revit automation.
Framework: CPython 3 (Python 3.12+ via pyRevit) + WPF + Revit API

## Rules
- **UI standard — one source, no exceptions:** `pyRevit UI Design System/T3LAB_UI_STANDARD.md` + `pyRevit UI Design System/T3Lab.Styles.xaml`. Every colour, size, margin and control style comes from `{StaticResource T3.*}`; a tool XAML never defines its own brush, style or hex.
- **Every new tool/script follows `.claude/rules/new-tool-standard.md`** — file layout, the 14 XAML rules, the `script.py` frame, and the pre-commit checklist.
- **The old standards are dead** (2026-08-28): Lumina, Revit-native, Terra v2, Kinetix. Their rule files, the showcase and the Lumina audit/sync scripts were deleted — see git history if you need the old wording.
- **Gates check is mandatory:** `python3 dev/audit_t3.py --quiet` (UI gate) + `python3 dev/audit_tools.py --quiet` (static gate). Both must be GREEN before committing any fix or feature.
- **Debugging & Error Prevention Protocol:**
  1. **Revit Journal First:** Khi gặp "Command Failure for External Command" hoặc crash không có dialog chi tiết, **tuyệt đối không đoán mò**. Đọc ngay file Revit Journal mới nhất (`%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit <Year>\Journals\journal.XXXX.txt`) để lấy stack trace thực tế.
  2. **CPython Shebang:** Mọi pushbutton `script.py` bắt buộc có `#! python3` ở dòng đầu tiên.
  3. **Path Bootstrap:** Mọi pushbutton `script.py` phải đảm bảo `lib_dir` được thêm vào `sys.path`: `if lib_dir not in sys.path: sys.path.insert(0, lib_dir)`.
  4. **WPF dưới CPython:** pyRevit `forms.WPFWindow` gốc ném `PyRevitCPythonNotSupported`. Mọi UI window phải dùng `T3WPFWindow` (từ `GUI.WPF_Base` hoặc `from GUI.forms import WPFWindow`), tự động xử lý tách event handlers cho `XamlReader`.
  5. **.NET Interface Namespaces:** Mọi class triển khai .NET interface (`ISelectionFilter`, `IExternalEventHandler`, `IFailuresPreprocessor`) bắt buộc khai báo `__namespace__`. Trong PythonNet, thiếu `__namespace__` sẽ khiến đối tượng không implement interface (`object does not implement <Interface>`). Nếu class nằm trong `lib/`, dùng `__namespace__ = "T3Lab.<UniqueName>"`. Nếu class nằm trực tiếp trong pushbutton `script.py` (chạy lại mỗi lần click), bắt buộc dùng namespace động: `__namespace__ = "T3Lab.<Name>_" + uuid.uuid4().hex[:8]` để tránh `Duplicate type name within an assembly`.
  6. **Cấm cú pháp Python 2:** Không tái phạm cú pháp Python 2 cũ (`xrange`, `__builtin__`, `execfile`, `unicode`, `open(..., 'wb')` cho CSV, `urllib2`).
  7. **Engine Hot-Reload Awareness:** Đổi engine hoặc sửa runtime assembly trong khi Revit đang mở có thể gây race condition nạp nóng PythonNet (`set_PythonDLL: This property must be set before runtime is initialized`) ở cú click đầu tiên. Khi đổi cấu hình engine, cần reload pyRevit sạch hoặc khởi động lại Revit.
  8. **CPython Standard Library Isolation (`configparser`, v.v.):** pyRevit CPython chạy nhúng trong Revit.exe nên không tự động nhận `python312.zip`. Mọi pushbutton `script.py` bắt buộc có khối bootstrap (`_cpython_bootstrap` từ `lib`) ở đầu file trước khi import `pyrevit` để đảm bảo nạp thư viện chuẩn Python 3 (`configparser`, `json`, `email`, v.v.) vào `sys.path`. **Chỉ gọi `_cpython_bootstrap.init_cpython_paths()`** — tuyệt đối không copy lại khối dò engine vào script: tên clone và tên engine (`pyRevit-Master`, `CPY3123`) là của riêng máy dev, hardcode là tool chết trên máy khác. Xem `INSTALL.md`.
  9. **Cấm Multiple Inheritance với CLR Class (`Non .NET type used as super class for meta type`):** Trong PythonNet (CPython 3), một class kế thừa từ CLR type (như `System.Windows.Window` thông qua `T3WPFWindow`) **không được phép** kế thừa thêm bất kỳ pure-Python class nào khác (như mixin: `class MyWindow(T3WPFWindow, MyMixin)` gây crash fatal lúc nạp module). Toàn bộ tính năng progress/pause/cooperative stop đã được tích hợp trực tiếp vào `T3WPFWindow`; các subclass chỉ kế thừa duy nhất `T3WPFWindow`: `class MyWindow(T3WPFWindow):`.
  10. **.NET Core / .NET 8 (Revit 2025/2026) Assembly & XAML Loading Resilience:** Trên .NET 8, `System.Xml.dll` là facade assembly và namespace `System.Windows.Markup` bị phân mảnh giữa các assembly (`PresentationFramework.dll`, `System.Xaml.dll`, `WindowsBase.dll`). Luôn khai báo đầy đủ tham chiếu assembly `System.Xml.ReaderWriter` và `System.Private.Xml`. Mọi thao tác nạp XAML phải đi qua cơ chế 4-tier fallback trong `T3WPFWindow` (`GUI.WPF_Base`), tuyệt đối không gọi `XamlReader.Load()` trực tiếp dạng đơn lẻ trong script.
  11. **Engine Hot-Reload & Persistent Engine Lifecycle:** Khi đổi engine CPython hoặc sửa runtime assembly trong khi Revit đang mở, cú click đầu tiên có thể ném `set_PythonDLL: This property must be set before runtime is initialized`. Cần reload pyRevit sạch (`pyRevit` -> `Reload`) để reset runtime. Mọi tool modeless có ExternalEvent phải khai báo `__persistentengine__ = True` và sử dụng `detect_persistent_engine()` để tự động chuyển về chế độ modal an toàn khi engine chưa được reload.
- XAML files go in `T3Lab.extension/lib/GUI/Tools/`
- Python dialog classes stay in `T3Lab.extension/lib/GUI/`
- Keep Revit API logic separate from WPF/UI code
- **Path Portability Rule**: All file paths in agent definitions and documentation must be relative to the repository workspace (e.g. `T3Lab.extension/...`) to ensure portability.

## Agents

Spawn the appropriate agent based on the task:

| Task | Agent |
|------|-------|
| Create or modify WPF windows / XAML | `@ui-agent` |
| Revit API logic, transactions, collectors | `@revit-api-agent` |
| Build a new pushbutton end-to-end | `@tool-builder-agent` |
| Review or test completed code | `@qa-agent` |
| Standardize script.py structure to BatchOut frame | `@script-frame-agent` |
| Daily UI/UX governance cycle (audit · score · standardize · track) | `@ui-governance-agent` |

Agent definitions: `.Codex/agents/`

## Skills

| Skill | Purpose |
|-------|---------|
| `.claude/skills/wpf-pattern.md` | Python WPF window class boilerplate |
| `.claude/skills/xaml-templates.md` | XAML snippets (T3) for all UI components |

## Quick Reference

| Resource | Path |
|----------|------|
| Bilingual VI/EN analysis | `T3Lab.extension/lib/Intelligence/language/` |
| Graph agent layer | `T3Lab.extension/lib/Intelligence/graph/` |
| Assistant architecture doc | `docs/assistant-graph-architecture.md` |
| **UI standard (the only one)** | `pyRevit UI Design System/T3LAB_UI_STANDARD.md` |
| **UI stylesheet — 82 `T3.*` keys** | `pyRevit UI Design System/T3Lab.Styles.xaml` |
| **Rule for every new tool** | `.claude/rules/new-tool-standard.md` |
| **UI governance routine** | `docs/ui-governance/` (start at `README.md`) |
| All XAML files | `T3Lab.extension/lib/GUI/Tools/` |
| Logo asset | `T3Lab.extension/lib/GUI/T3Lab_logo.png` |
| Example XAML (simple) | `T3Lab.extension/lib/GUI/Tools/ExportManager.xaml` |
| Example XAML (wizard nav) | `T3Lab.extension/lib/GUI/Tools/ExportManagerTest.xaml` |
| Python dialogs | `T3Lab.extension/lib/GUI/` (FamilyLoaderDialog.py, etc.) |
| Snippets | `T3Lab.extension/lib/Snippets/` |

## Folder Layout

```
T3Lab.extension/
├── T3Lab.tab/          ← ribbon panels and pushbutton scripts
├── lib/
│   ├── GUI/
│   │   ├── Tools/      ← ALL .xaml files live here
│   │   ├── Resources/  ← shared WPF styles (WPF_styles.xaml)
│   │   ├── forms.py    ← WPF helpers
│   │   ├── WPF_Base.py
│   │   ├── *Dialog.py  ← Python WPF dialog classes
│   │   └── T3Lab_logo.png
│   ├── Snippets/       ← reusable Revit API helpers
│   ├── Renaming/       ← renaming tool library
│   └── ...
├── checks/             ← model checker scripts
└── commands/           ← command scripts
```

## Example Workflow: New Tool

```
You: "Build a new WallType manager tool"
         ↓
Codex reads AGENTS.md → spawns @tool-builder-agent
    ├── @ui-agent    → creates lib/GUI/Tools/WallTypeManager.xaml
    └── @revit-api-agent → implements WallType logic in script.py
         ↓
@qa-agent reviews output
         ↓
Files placed in correct folders ✅
```
