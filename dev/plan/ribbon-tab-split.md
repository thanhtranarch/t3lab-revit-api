# Phương án tách tab ribbon — giữ panel đồng nhất

> Mở 2026-10-03 · Trạng thái: **đề xuất, chờ chốt 3 quyết định ở §7** · Chưa đụng code.
> Phạm vi: `T3Lab.extension/T3Lab_Dev.tab/` (7 panel, 53 nút) → 4 tab.

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

- Cần ≈ **2 380 px** để mọi stack hiện chữ; màn hình chỉ có ≈ 1 950 px.
- Revit tự co ribbon → **4/7 stack mất chữ** (Standards, manaData, Create, Mana) chỉ còn 3 icon nhỏ
  không nhãn. Đó là phần "rối" nhìn thấy: cùng một loại control mà panel có chữ, panel không.
- Thứ tự control không thống nhất: 4 panel đặt stack đầu, 1 panel đặt stack cuối.
- Trên laptop 1366 px hoặc 1920 px @125% (≈ 1 536 px logic) tình trạng còn tệ hơn: panel
  bên phải sẽ bị gập thành nút xổ xuống.

Kết luận: tách tab giải quyết **độ rộng**; còn **"rối"** thì phải sửa bằng một bộ luật panel
chung (§4) áp cho mọi tab.

---

## 2. Ba phương án

| | A — 4 tab theo mô hình tab gốc của Revit **(khuyến nghị)** | B — 3 tab | C — 2 tab (chỉ tách Rebar) |
|---|---|---|---|
| Tab | Model · Rebar · Docs · Manage | Model (+Rebar) · Docs · Manage | T3Lab (còn lại) · Rebar |
| Tab rộng nhất (ước lượng) | ≈ 790 px | ≈ 1 025 px | ≈ 2 030 px ❌ |
| Stack còn chữ ở 1366 px | ✅ | ✅ (sát) | ❌ vẫn tràn |
| Ẩn theo nhóm người dùng (Mana Tabs) | ✅ người không làm kết cấu ẩn tab Rebar | ❌ | ✅ |
| Chỗ cho roadmap Rebar / Point Cloud lớn thêm | ✅ dư | ⚠️ Model sẽ vượt ngân sách | ❌ |
| Số tab thêm vào thanh tab | +3 | +2 | +1 |

Lý do chọn A: nó trùng với cách Revit tự chia — **Architecture/Structure** (dựng model),
**Precast/Steel** (tab chuyên ngành riêng, có sẵn trên ribbon của bạn), **Annotate/View**
(hồ sơ), **Manage** (thiết lập & quản trị). Người dùng Revit không phải học cách chia mới.
Thứ tự tab cũng theo Revit: Model → Rebar → Docs → Manage.

---

## 3. Bố cục đích (phương án A)

```
T3Lab Model
  Model & Datum        CAD to BIM ▾ · Element Adjust ▾ · Datum Sync · [Property Line | Tile Layout]
  Families             Mana Fami · Family Transfer · FamiGen
  Data & IFC-SG        IFC-SG Suite · [Mana Sched | Mana Para | Mana Contains]

T3Lab Rebar
  Rebar & Assembly     Tekla Bridge · Cast Unit Manager · Clone Drawing · Rebar Check · BVBS Export · Rebar Wizard

T3Lab Docs
  Views & Sheets       Mana Views · Mana Sheets · SheetGen · Crop Sync · BatchOut · PDF Import
  Annotation & Select  Mana Anno · Make Pattern · [Mana DWG | Auto Dimension | Mana Select]

T3Lab Manage
  Standards & Settings Mana Loca · Mana Group · Batch Link · [Model Auditor | Mana Styles | Mana Workset]
  Support              T3Lab Assistant · [Feedback | LLMs Setting | MCP Control]
                       · [Mana Tabs | Ribbon Names | BG Theme] · [Autodesk Forma | Autodesk Health | Bluebeam Status]

X = nút lớn · X ▾ = pulldown lớn · [a | b | c] = một stack (nút nhỏ có chữ)
```

| Tab | Panel | Slot | Lớn + stack | Rộng ≈ |
|-----|-------|-----:|-------------|-------:|
| **T3Lab Model** | Model & Datum · Families · Data & IFC-SG | 9 | 7 + 2 | 680 px |
| **T3Lab Rebar** | Rebar & Assembly | 6 | 6 + 0 | 345 px |
| **T3Lab Docs** | Views & Sheets · Annotation & Select | 9 | 8 + 1 | 600 px |
| **T3Lab Manage** | Standards & Settings · Support | 8 | 4 + 4 | 790 px |

Thay đổi so với hiện tại — **4 panel giữ nguyên tên và nội dung**, chỉ đổi tab (Standards & Settings,
Data & IFC-SG, Rebar & Assembly, Annotation & Select); Views & Sheets và Support giữ tên, đổi một nút;
chỉ Modeling & Datum tách đôi:

| # | Thay đổi | Lý do (luật §4) |
|---|----------|-----------------|
| M1 | `Modeling & Datum` tách thành **Model & Datum** + **Families** (Mana Fami, Family Transfer, FamiGen) | P1 — panel 7 slot sau M2; nhóm family là một việc riêng |
| M2 | `CAD to BIM` (`Create Elements.pulldown`) ra khỏi `Create.stack` thành pulldown **lớn** | tool chủ lực đang bị giấu; stack còn Property Line + Tile Layout |
| M3 | `PDF Import` từ Support → **Views & Sheets** | P8 — Support chỉ chứa tool hỗ trợ |
| M4 | Đảo layout: nút lớn trước, stack sau — Standards & Settings, Data & IFC-SG, Support, Model & Datum | P2 |
| M5 | Support xếp: Assistant → Assistant Tools → UI → Cloud Links (link hãng khác ở cuối) | P2 |

Rebar & Assembly giữ nguyên 6 nút, đúng thứ tự ở §6 của `rebar-tekla-toolkit-roadmap.md`.

---

## 4. Luật panel đồng nhất — áp cho MỌI tab

| # | Luật | Hiện trạng → sau phương án |
|---|------|----------------------------|
| P1 | Một panel có **2–6 slot** | đã đạt; giữ khi thêm tool — panel thứ 7 slot thì tách |
| P2 | Thứ tự trong panel: **nút lớn → pulldown lớn → stack ở phải cùng** (như Architecture › Build của Revit) | 4 panel đặt stack đầu → 0 |
| P3 | Stack có **2–3 nút** và **luôn hiện chữ** | 4 stack mất chữ → 0 (nhờ P4) |
| P4 | Ngân sách tab: **≤ 12 slot, ước lượng ≤ 1 000 px** — vừa 1366 px và 1920 px @125% mà không co | 31 slot → tối đa 9 |
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

- [ ] `lib/core/extension_paths.py`: thêm `tab_dirs()` (mọi `*.tab`, theo `layout:` của extension),
      `find_bundle(name)` (tìm folder theo tên trên mọi tab, có cache), `bundle_path(name, *parts)`.
      Giữ `tab_dir()` để tương thích, không thêm chỗ gọi mới.
- [ ] Thay các chỗ ghép cứng panel:

| File | Hiện tại | Sau |
|------|----------|-----|
| `lib/Services/bg_theme_service.py:21` | `tab_path('Support.panel', 'UI.stack', 'BG Theme.pushbutton', …)` | `bundle_path('BG Theme.pushbutton', 'dqt_bg_config.json')` |
| `lib/GUI/ImageToDraftingDialog.py:1170` | `'Modeling & Datum.panel', 'Create.stack', 'Create Elements.pulldown', …` | `bundle_path('ImageToDrafting.pushbutton', 'potrace.exe')` |
| `lib/GUI/ManaLocaDialog.py:63` | `'Standards & Settings.panel', 'ManaLoca.pushbutton'` | `bundle_path('ManaLoca.pushbutton', …)` |
| `lib/FamilyGen/guidance.py:26` | `'Modeling & Datum.panel', 'FamiGen.pushbutton', 'prompts'` | `bundle_path('FamiGen.pushbutton', 'prompts')` |
| `lib/GUI/AssistantPaneControl.py:147` | `'Support.panel', 'T3LabAssistant.pushbutton'` | `bundle_path('T3LabAssistant.pushbutton', 'script.py')` |
| `lib/GUI/T3LabAssistantDialog.py:481` | `_get_tool_script_dir()` | `find_bundle(parts[-1])` — **sửa luôn lỗi có sẵn**: hàm đang lùi 3 cấp từ `lib/GUI/` nên ra `T3Lab.extension/` (thiếu thư mục tab); đường fallback nạp BatchOut ở dòng 521 đang trỏ sai |
| `lib/Services/tool_discovery.py:28` | quét một `_TAB_DIR` | quét `tab_dirs()`, thêm field `tab`; `REGISTRY_VERSION` 4 → 5 (cache ở `%APPDATA%` chứa đường dẫn cũ) |

- [ ] `dev/tabdir.py`: thêm `TABS` + `bundle_path`. Cho `audit_tools`, `audit_wiring`,
      `dev/icons/iconlib.py` (→ `build_icons`, `audit_icons`), `generate_all_icons` và các test
      `test_assistant_*`, `test_tool_registry`, `test_batchout_executor`, `test_ribbon_name_storage`
      quét `TABS`. `EXEMPT_BUNDLES` trong `iconlib.py` đổi sang khoá theo tên bundle.
- [ ] Lưới an toàn: gate in số script / icon **theo từng tab** và FAIL nếu tab nào 0 file;
      test tên bundle duy nhất (P7); `test_extension_paths` cấm `lib/` và `dev/` ghép cứng `"<X>.panel"`.
- [ ] Tiêu chí xong: mọi gate xanh với **đúng số hiện tại** — 50 `script.py`, audit_icons 51/51,
      audit_tools clean, wiring W1/W2/W3/D1 = 0, D3 = 0.

### GĐ1 — Gate ribbon `dev/audit_ribbon.py`

- [ ] Đọc `bundle.yaml` của mọi tab/panel/stack, kiểm P1–P4, P6, P7, P10; ước lượng độ rộng tab
      bằng hằng số đo ở §1.
- [ ] Chạy trên cây hiện tại phải **ĐỎ** (31 slot > 12, 4 panel stack-trước, PDF Import trong Support) —
      đó là bằng chứng luật bắt đúng.
- [ ] Thêm vào "Essential Commands" của `CLAUDE.md` và checklist §5 của `.claude/rules/new-tool-standard.md`.

### GĐ2 — Dời folder (một commit, `git mv` để giữ lịch sử)

- [ ] Tạo `T3Lab Model.tab`, `T3Lab Rebar.tab`, `T3Lab Docs.tab`, `T3Lab Manage.tab`, mỗi tab
      một `bundle.yaml` có `layout:` các panel.
- [ ] Thêm `T3Lab.extension/bundle.yaml` với `layout:` thứ tự 4 tab (kiểm chứng trong Revit
      rằng pyRevit theo thứ tự này; nếu không, pyRevit xếp theo ABC).
- [ ] M1–M5 ở §3; xoá `T3Lab_Dev.tab/`.
- [ ] Icon: `python3 dev/build_icons.py --check`, soát tier A cho CAD to BIM (P9).
- [ ] Docs nhắc `T3Lab_Dev.tab`: `.claude/CLAUDE.md` (Folder Layout), `.claude/rules/new-tool-standard.md` §1,
      `docs/cad-to-elements.md`, `rebar-tekla-toolkit-roadmap.md` §6, `rebar-tekla-implementation-spec.md`;
      thêm `docs/ui-governance/09-ribbon-icon-standard.md` (phạm vi "ribbon `T3Lab.tab`"),
      dòng "T3Lab › Rebar & Assembly" của `docs/tekla-to-revit-2027.md` (sửa ở `dev/build_tekla_docs.py`, đừng sửa file sinh).
- [ ] Mọi gate xanh (cả `audit_ribbon` = 0) với cùng số script/icon như GĐ0.

### GĐ3 — QA trong Revit (user chạy; không tick từ gate tĩnh)

- [ ] **Khởi động lại Revit, không Reload** — ribbon đổi cấu trúc thì pyRevit dựng lại lúc khởi động,
      và Reload trên Revit 2025+ gây `This property must be set before runtime is initialized` (CLAUDE.md mục 7).
- [ ] 4 tab đúng thứ tự, không còn `T3Lab_Dev`; tab `T3Lab` (bản cũ, nếu cài song song) không bị gộp panel.
- [ ] Mọi stack hiện chữ ở 1920 px @100%, @125% và khi thu cửa sổ Revit về ≈ 1366 px.
- [ ] Mở ít nhất một tool mỗi panel. Riêng các tool từng hỏng khi đổi tên tab: BG Theme còn giữ theme,
      Image to Drafting tìm thấy potrace, FamiGen có prompt theo category, Mana Loca lưu được session,
      Assistant mở được BatchOut và "open Cast Unit".
- [ ] Mana Tabs liệt kê và ẩn/hiện được cả 4 tab; Ribbon Names rút gọn được tên 4 tab.
- [ ] Phím tắt / Quick Access Toolbar đã gán cho tool T3Lab: gán lại (xem §6).

---

## 6. Rủi ro

| Rủi ro | Xử lý |
|--------|-------|
| Code tìm file theo đường dẫn panel → hỏng im lặng | GĐ0 làm trước; `find_bundle` theo tên; gate FAIL khi tab 0 file |
| ID lệnh pyRevit sinh từ đường dẫn tab/panel/nút → **phím tắt và QAT đã gán sẽ mất** | báo người dùng gán lại một lần; tách tab **trước** khi chạy spike WP1 lấy command id cho `KeyboardShortcuts_Tekla.xml`, để không phải lấy lại |
| Trùng tên tab với extension khác → pyRevit dùng lại tab đó và gộp panel | tên mới không trùng tab `T3Lab` hiện có; kiểm trong GĐ3 |
| Thanh tab dài thêm 3 tab | Mana Tabs ẩn tab không dùng; Ribbon Names rút tên (vd `T3 Model`) |
| Cache tool registry của Assistant giữ đường dẫn cũ | bump `REGISTRY_VERSION` |
| Thứ tự tab không theo `layout:` của extension | kiểm trong GĐ3; nếu pyRevit bỏ qua thì chấp nhận ABC hoặc đặt tên cho đúng thứ tự |

---

## 7. Cần chốt trước khi làm

| # | Quyết định | Mặc định đề xuất |
|---|-----------|------------------|
| D1 | Số tab | **A — 4 tab** (Model · Rebar · Docs · Manage) |
| D2 | Tên tab | `T3Lab Model` · `T3Lab Rebar` · `T3Lab Docs` · `T3Lab Manage`. Nếu bản `T3Lab` cũ vẫn chạy song song và sau này cũng tách tab → thêm hậu tố cho repo dev (`T3Lab Model Dev`…) để không bị gộp |
| D3 | M1 + M2 (tách Families, nâng CAD to BIM thành nút lớn) | **Có** |

---

## 8. Ghi nhận, ngoài phạm vi

- `Text to Element` có hai lối vào: nút riêng trong pulldown CAD to BIM và tab trong Mana Para.
- `Mana DWG` (quản lý CAD import/link) hợp với Batch Link ở Standards & Settings hơn là Annotation & Select.
- `Make Pattern` (fill pattern) gần với Mana Styles (đã quản lý Fill Patterns).

Ba mục này chỉ ghi nhận; muốn đổi thì mở việc riêng sau khi tách tab xong.
