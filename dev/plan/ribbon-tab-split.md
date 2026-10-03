# Phương án tách tab ribbon — giữ panel đồng nhất

> Mở 2026-10-03 · Trạng thái: **đề xuất, chờ chốt quyết định ở §7** · Chưa đụng code.
> Phạm vi: `T3Lab.extension/T3Lab_Dev.tab/` (7 panel, 53 nút) → **2 tab** (sửa 2026-10-03: user thấy 4 tab là nhiều).

---

## 1. Hiện trạng — vì sao "chật và rối"

Một tab duy nhất `T3Lab_Dev`, 7 panel, **31 slot** trên ribbon (slot = 1 nút lớn,
1 pulldown lớn hoặc 1 stack): 24 nút lớn + 7 stack.

| Panel | Slot | Cấu trúc | Ghi chú |
|-------|-----:|----------|---------|
| Support | 5 | 3 stack + 2 nút lớn | stack đặt đầu; PDF Import là tool sản xuất nằm nhầm panel |
| Standards & Settings | 4 | 1 stack + 3 lớn | stack đặt đầu |
| Data & IFC-SG | 2 | 1 stack + 1 lớn | stack đặt đầu |
| Modeling & Datum | 6 | 1 stack + 5 lớn | stack đặt đầu; **CAD to BIM** (CAD to Elements, Point Cloud…) bị giấu thành pulldown nhỏ trong stack |
| Rebar & Assembly | 6 | 6 lớn | — |
| Annotation & Select | 3 | 2 lớn + 1 stack | stack đặt **cuối** — panel duy nhất làm vậy |
| Views & Sheets | 5 | 5 lớn | — |

Đo trên ảnh chụp ribbon (rộng 2000 px): nút lớn ≈ 57 px, stack có chữ ≈ 140 px.

- Cần ≈ **2 400 px** để mọi stack hiện chữ; màn hình chỉ có ≈ 1 950 px.
- Revit tự co ribbon → **4/7 stack mất chữ** (Standards, manaData, Create, Mana) chỉ còn 3 icon nhỏ
  không nhãn. Đó là phần "rối" nhìn thấy: cùng một loại control mà panel có chữ, panel không.
- Thứ tự control không thống nhất: 4 panel đặt stack đầu, 1 panel đặt stack cuối.
- Trên laptop 1366 px hoặc 1920 px @125% (≈ 1 536 px logic) tình trạng còn tệ hơn: panel
  bên phải sẽ bị gập thành nút xổ xuống.

Kết luận: tách tab giải quyết **độ rộng**; còn **"rối"** thì phải sửa bằng một bộ luật panel
chung (§4) áp cho mọi tab.

---

## 2. Các phương án

Panel giống hệt nhau ở mọi phương án; chỉ khác **gom panel nào vào tab nào**. Độ rộng ước lượng
theo hằng số ở §1, cộng 8 px giữa hai panel, đã tính các thay đổi M1–M6 ở §3.

| | **C — 2 tab cân bằng (đề xuất)** | B — 3 tab | A — 4 tab |
|---|---|---|---|
| Tab | Model · Docs | Model (+Rebar) · Docs · Manage | Model · Rebar · Docs · Manage |
| Tab rộng nhất | ≈ 1 210 px | ≈ 1 045 px | ≈ 800 px |
| Stack còn chữ ở 1920 px @100% và @125% | ✅ | ✅ | ✅ |
| Stack còn chữ ở laptop 1366 px | ✅ sát (dư ≈ 130 px, cần M6) | ✅ | ✅ |
| Chỗ trống cho tool mới | tab Model ≈ 1 nút lớn, tab Docs ≈ 0 | dư | dư nhiều |
| Ẩn riêng tab Rebar bằng Mana Tabs | ❌ | ❌ | ✅ |
| Thêm vào thanh tab | +1 | +2 | +3 |

Không được chia 2 tab kiểu **chỉ đưa Rebar ra riêng**: tab còn lại vẫn ≈ 2 110 px, vẫn tràn.
Phải chia đôi cho cân: mỗi tab khoảng một nửa.

Lối đi khi tab đầy: GĐ0 làm cho việc dời panel giữa các tab chỉ còn là dời thư mục. Khi gate
`audit_ribbon` báo một tab vượt ngân sách P4, đưa **Rebar & Assembly** ra tab riêng → thành B (3 tab),
không cần thiết kế lại.

---

## 3. Bố cục đích (phương án C — 2 tab)

```
T3Lab Model                       ≈ 1 160 px · 17 slot
  Standards & Settings Mana Loca · Mana Group · Batch Link · [Model Auditor | Mana Styles | Mana Workset]
  Model & Datum        CAD to BIM ▾ · Element Adjust ▾ · Datum Sync · [Property Line | Tile Layout]
  Families             Mana Fami · Family Transfer · FamiGen
  Rebar & Assembly     Tekla Bridge · Cast Unit Manager · Clone Drawing · Rebar Check · BVBS Export · Rebar Wizard

T3Lab Docs                        ≈ 1 210 px · 15 slot
  Views & Sheets       Mana Views · Mana Sheets · SheetGen · Crop Sync · BatchOut · PDF Import
  Annotation & Select  Mana Anno · Make Pattern · [Mana DWG | Auto Dimension | Mana Select]
  Data & IFC-SG        IFC-SG Suite · [Mana Sched | Mana Para | Mana Contains]
  Support              T3Lab Assistant · Cloud Links ▾ · [Feedback | LLMs Setting | MCP Control]
                       · [Mana Tabs | Ribbon Names | BG Theme]

X = nút lớn · X ▾ = pulldown lớn · [a | b | c] = một stack (nút nhỏ có chữ)
```

Cách chia: **Model** là dựng model, từ trái sang phải theo thứ tự làm việc (thiết lập chuẩn → dựng
model → family → cốt thép). **Docs** là lấy thông tin ra khỏi model (view/sheet → ghi chú → bảng
thống kê, IFC-SG), Support ở cuối như Manage/Add-Ins của Revit.

Cách chia ngược lại (Data & IFC-SG sang Model, Standards & Settings sang Docs) lệch hơn: 1 045 / 1 410 px,
tab Docs không vừa laptop 1366.

Thay đổi so với hiện tại — **4 panel giữ nguyên tên và nội dung**, chỉ đổi tab (Standards & Settings,
Data & IFC-SG, Rebar & Assembly, Annotation & Select); Views & Sheets và Support giữ tên, đổi nút;
chỉ Modeling & Datum tách đôi:

| # | Thay đổi | Lý do (luật §4) |
|---|----------|-----------------|
| M1 | `Modeling & Datum` tách thành **Model & Datum** + **Families** (Mana Fami, Family Transfer, FamiGen) | P1 — panel 7 slot sau M2; nhóm family là một việc riêng |
| M2 | `CAD to BIM` (`Create Elements.pulldown`) ra khỏi `Create.stack` thành pulldown **lớn** | tool chủ lực đang bị giấu; stack còn Property Line + Tile Layout |
| M3 | `PDF Import` từ Support → **Views & Sheets** | P8 — Support chỉ chứa tool hỗ trợ |
| M4 | Đảo layout: nút lớn trước, stack sau — Standards & Settings, Data & IFC-SG, Support, Model & Datum | P2 |
| M5 | Support xếp: Assistant → Cloud Links → Assistant Tools → UI | P2 |
| M6 | `CloudLinks.stack` → `CloudLinks.pulldown` (3 link Forma / Health / Bluebeam vào một nút xổ xuống) | P4 — stack chữ dài nhất (≈ 140 px) thành 1 nút lớn (≈ 57 px); thiếu nó tab Docs ≈ 1 290 px, vượt ngân sách |

Rebar & Assembly giữ nguyên 6 nút, đúng thứ tự ở §6 của `rebar-tekla-toolkit-roadmap.md`.

---

## 4. Luật panel đồng nhất — áp cho MỌI tab

| # | Luật | Hiện trạng → sau phương án |
|---|------|----------------------------|
| P1 | Một panel có **2–6 slot** | đã đạt; giữ khi thêm tool — panel thứ 7 slot thì tách |
| P2 | Thứ tự trong panel: **nút lớn và pulldown lớn trước, stack ở phải cùng** (như Architecture › Build của Revit) | 4 panel đặt stack đầu → 0 |
| P3 | Stack có **2–3 nút** và **luôn hiện chữ** | 4 stack mất chữ → 0 (nhờ P4) |
| P4 | Ngân sách tab: **ước lượng ≤ 1 250 px** — vừa laptop 1366 px và 1920 px @125% mà không co | ≈ 2 400 px → tối đa ≈ 1 210 px |
| P5 | Mọi panel ở mọi tab cùng màu tiêu đề `background: title: "#46E07B00"` (đang dùng), không panel nào màu riêng | 7/7 đã đúng — giữ |
| P6 | Tên panel tiếng Anh, Title Case, một danh từ hoặc "A & B"; không trùng tên giữa các tab | đã đúng |
| P7 | Tên folder bundle (`X.pushbutton`, `X.stack`…) **duy nhất trong toàn extension** — code tìm tool theo tên, không theo đường dẫn panel | 0 trùng — khoá bằng test |
| P8 | Panel Support chỉ chứa tool hỗ trợ (Assistant, cài đặt, giao diện, link); tool làm việc trên model nằm ở panel nghiệp vụ | PDF Import → M3 |
| P9 | Nút đổi tầng (stack ↔ nút lớn) phải soát lại tier icon (A 32 px / B 24 px, `09-ribbon-icon-standard.md`) | CAD to BIM lên tier A |
| P10 | `layout:` của panel/stack liệt kê đủ và chỉ đúng các folder có thật | gate mới kiểm |

P1–P4, P6, P7, P10 kiểm tĩnh được → gate mới `dev/audit_ribbon.py` (GĐ1).

---

## 5. Triển khai

### GĐ0 — Hạ tầng nhiều tab (ribbon chưa đổi gì) — làm TRƯỚC, ship riêng

Bài học 2026-09-26 (commit `5c108ad`): đổi tên `T3Lab.tab` → `T3Lab_Dev.tab` làm 5 tool hỏng
**im lặng**, và audit_tools / audit_wiring / audit_icons báo xanh khi quét **0 file**.
Hiện `tab_dir()` chỉ trả **một** tab, và 6 chỗ còn ghép cứng tên panel sau nó — tách tab
mà chưa sửa thì lặp lại đúng lỗi đó.

- [x] `lib/core/extension_paths.py`: thêm `tab_dirs()` (mọi `*.tab`, theo `layout:` của extension),
      `find_bundle(name)` (tìm folder theo tên trên mọi tab, có cache), `bundle_path(name, *parts)`.
      Giữ `tab_dir()` để tương thích, không thêm chỗ gọi mới.
- [x] Thay các chỗ ghép cứng panel:

| File | Hiện tại | Sau |
|------|----------|-----|
| `lib/Services/bg_theme_service.py:21` | `tab_path('Support.panel', 'UI.stack', 'BG Theme.pushbutton', …)` | `bundle_path('BG Theme.pushbutton', 'dqt_bg_config.json')` |
| `lib/GUI/ImageToDraftingDialog.py:1170` | `'Modeling & Datum.panel', 'Create.stack', 'Create Elements.pulldown', …` | `bundle_path('ImageToDrafting.pushbutton', 'potrace.exe')` |
| `lib/GUI/ManaLocaDialog.py:63` | `'Standards & Settings.panel', 'ManaLoca.pushbutton'` | `bundle_path('ManaLoca.pushbutton', …)` |
| `lib/FamilyGen/guidance.py:26` | `'Modeling & Datum.panel', 'FamiGen.pushbutton', 'prompts'` | `bundle_path('FamiGen.pushbutton', 'prompts')` |
| `lib/GUI/AssistantPaneControl.py:147` | `'Support.panel', 'T3LabAssistant.pushbutton'` | `bundle_path('T3LabAssistant.pushbutton', 'script.py')` |
| `lib/GUI/T3LabAssistantDialog.py:481` | `_get_tool_script_dir()` | `find_bundle(parts[-1])` — **sửa luôn lỗi có sẵn**: hàm đang lùi 3 cấp từ `lib/GUI/` nên ra `T3Lab.extension/` (thiếu thư mục tab); đường fallback nạp BatchOut ở dòng 521 đang trỏ sai |
| `lib/Services/tool_discovery.py:28` | quét một `_TAB_DIR` | quét `tab_dirs()`, thêm field `tab`; `REGISTRY_VERSION` 4 → 5 (cache ở `%APPDATA%` chứa đường dẫn cũ) |

- [x] `dev/tabdir.py`: thêm `TABS` + `bundle_path`. Cho `audit_tools`, `audit_wiring`,
      `dev/icons/iconlib.py` (→ `build_icons`, `audit_icons`), `generate_all_icons` và các test
      `test_assistant_*`, `test_tool_registry`, `test_batchout_executor`, `test_ribbon_name_storage`
      quét `TABS`. `EXEMPT_BUNDLES` trong `iconlib.py` đổi sang khoá theo tên bundle.
- [x] Lưới an toàn: gate in số script / icon **theo từng tab** và FAIL nếu tab nào 0 file;
      test tên bundle duy nhất (P7); `test_extension_paths` cấm `lib/` và `dev/` ghép cứng `"<X>.panel"`.
- [x] Tiêu chí xong: mọi gate xanh với **đúng số hiện tại** — 50 `script.py`, audit_icons 51/51,
      audit_tools clean, wiring W1/W2/W3/D1 = 0, D3 = 0.
      **Kết quả 2026-10-03:** đúng các số trên (D2 giữ 36 sau khi xoá `tab_path()` — API cũ, không còn ai gọi,
      và chính nó dẫn tới lối ghép đường dẫn panel). 112 file test: kết quả y hệt `main`;
      `test_extension_paths` 4 → 11 test. `test_checkbox_bridge` đỏ sẵn trên `main` (2 lỗi XAML, ngoài phạm vi).

### GĐ1 — Gate ribbon `dev/audit_ribbon.py`

- [x] Đọc `bundle.yaml` của mọi tab/panel/stack, kiểm P1–P4, P6, P7, P10; ước lượng độ rộng tab
      bằng hằng số đo ở §1.
- [x] Chạy trên cây hiện tại phải **ĐỎ** (tab ≈ 2 400 px > 1 250, 4 panel stack-trước, PDF Import trong Support) —
      đó là bằng chứng luật bắt đúng. **Kết quả 2026-10-03:** ĐỎ 5 vi phạm — P4 `T3Lab_Dev.tab` ≈ 2 396 px,
      P2 ở Data & IFC-SG, Modeling & Datum, Standards & Settings, Support. `dev/test_audit_ribbon.py` (12 test)
      kiểm từng luật trên một cây ribbon giả.
- [x] Thêm vào "Essential Commands" của `CLAUDE.md` và checklist §5 của `.claude/rules/new-tool-standard.md`.

### GĐ2 — Dời folder (một commit, `git mv` để giữ lịch sử)

- [ ] Tạo `T3Lab Model.tab` và `T3Lab Docs.tab`, mỗi tab một `bundle.yaml` có `layout:` các panel.
- [ ] Thêm `T3Lab.extension/bundle.yaml` với `layout:` Model trước Docs (kiểm chứng trong Revit
      rằng pyRevit theo thứ tự này; nếu bị bỏ qua, pyRevit xếp theo ABC thành Docs → Model — chấp nhận, hoặc đổi tên).
- [ ] M1–M6 ở §3; xoá `T3Lab_Dev.tab/`.
- [ ] Icon: `python3 dev/build_icons.py --check`, soát tier A cho CAD to BIM và Cloud Links (P9;
      Cloud Links dùng lại `icon.svg` sẵn có của stack, 3 logo hãng bên trong vẫn miễn trừ).
- [ ] Docs nhắc `T3Lab_Dev.tab`: `.claude/CLAUDE.md` (Folder Layout), `.claude/rules/new-tool-standard.md` §1,
      `docs/cad-to-elements.md`, `rebar-tekla-toolkit-roadmap.md` §6, `rebar-tekla-implementation-spec.md`;
      thêm `docs/ui-governance/09-ribbon-icon-standard.md` (phạm vi "ribbon `T3Lab.tab`"),
      dòng "T3Lab › Rebar & Assembly" của `docs/tekla-to-revit-2027.md` (sửa ở `dev/build_tekla_docs.py`, đừng sửa file sinh).
- [ ] Mọi gate xanh (cả `audit_ribbon` = 0) với cùng số script/icon như GĐ0.

### GĐ3 — QA trong Revit (user chạy; không tick từ gate tĩnh)

- [ ] **Khởi động lại Revit, không Reload** — ribbon đổi cấu trúc thì pyRevit dựng lại lúc khởi động,
      và Reload trên Revit 2025+ gây `This property must be set before runtime is initialized` (CLAUDE.md mục 7).
- [ ] 2 tab hiện đủ, không còn `T3Lab_Dev`; tab `T3Lab` (bản cũ, nếu cài song song) không bị gộp panel.
- [ ] Mọi stack hiện chữ ở 1920 px @100%, @125% và khi thu cửa sổ Revit về ≈ 1366 px.
- [ ] Mở ít nhất một tool mỗi panel. Riêng các tool từng hỏng khi đổi tên tab: BG Theme còn giữ theme,
      Image to Drafting tìm thấy potrace, FamiGen có prompt theo category, Mana Loca lưu được session,
      Assistant mở được BatchOut và "open Cast Unit".
- [ ] Cloud Links ▾ mở đủ 3 trang web.
- [ ] Mana Tabs liệt kê và ẩn/hiện được cả 2 tab; Ribbon Names rút gọn được tên 2 tab.
- [ ] Phím tắt / Quick Access Toolbar đã gán cho tool T3Lab: gán lại (xem §6).

---

## 6. Rủi ro

| Rủi ro | Xử lý |
|--------|-------|
| Code tìm file theo đường dẫn panel → hỏng im lặng | GĐ0 làm trước; `find_bundle` theo tên; gate FAIL khi tab 0 file |
| ID lệnh pyRevit sinh từ đường dẫn tab/panel/nút → **phím tắt và QAT đã gán sẽ mất** | báo người dùng gán lại một lần; tách tab **trước** khi chạy spike WP1 lấy command id cho `KeyboardShortcuts_Tekla.xml`, để không phải lấy lại |
| Trùng tên tab với extension khác → pyRevit dùng lại tab đó và gộp panel | tên mới không trùng tab `T3Lab` hiện có; kiểm trong GĐ3 |
| Tab đầy, thêm tool là tràn lại | gate `audit_ribbon` báo đỏ ngay khi vượt P4; lúc đó đưa Rebar & Assembly ra tab riêng (§2) |
| Thanh tab dài thêm 1 tab | Ribbon Names rút tên (vd `T3 Model`) |
| Cache tool registry của Assistant giữ đường dẫn cũ | bump `REGISTRY_VERSION` |
| Thứ tự tab không theo `layout:` của extension | kiểm trong GĐ3; nếu pyRevit bỏ qua thì chấp nhận ABC hoặc đặt tên cho đúng thứ tự |

---

## 7. Cần chốt trước khi làm

| # | Quyết định | Mặc định đề xuất |
|---|-----------|------------------|
| D1 | Số tab | **C — 2 tab** (Model · Docs). Có thể lên 3 tab sau, khi gate báo đầy |
| D2 | Tên tab | `T3Lab Model` · `T3Lab Docs`. Nếu bản `T3Lab` cũ vẫn chạy song song và sau này cũng tách tab → thêm hậu tố cho repo dev (`T3Lab Model Dev`…) để không bị gộp |
| D3 | M1 + M2 (tách Families, nâng CAD to BIM thành nút lớn) | **Có** |
| D4 | M6 (Cloud Links thành nút xổ xuống) | **Có** nếu có người dùng laptop 1366 px. Màn ≈ 2 000 px như ảnh chụp thì bỏ được, khi đó nâng ngân sách P4 lên 1 300 px |

---

## 8. Ghi nhận, ngoài phạm vi

- `Text to Element` có hai lối vào: nút riêng trong pulldown CAD to BIM và tab trong Mana Para.
- `Mana DWG` (quản lý CAD import/link) hợp với Batch Link ở Standards & Settings hơn là Annotation & Select.
- `Make Pattern` (fill pattern) gần với Mana Styles (đã quản lý Fill Patterns).

Ba mục này chỉ ghi nhận; muốn đổi thì mở việc riêng sau khi tách tab xong.
