# Rebar Toolkit cho người dùng chuyển từ Tekla — Phương án xây dựng

> Ngày lập: 2026-10-02 · Trạng thái: **ĐỀ XUẤT — chưa có dòng code nào**
> Panel mới: `T3Lab.extension/T3Lab_Dev.tab/Rebar & Assembly.panel/`
> Phạm vi Revit: 2022 → 2027 (gate `dev/audit_revit_compat.py`) · CPython 3 + WPF theo `.claude/rules/new-tool-standard.md`
> Mục tiêu: người quen Tekla Structures mở Revit là **làm việc được ngay với từ vựng và thao tác quen**,
> và mọi tool **hoạt động đúng cả khi phần tử nằm trong Assembly** (cast unit của Revit).

---

## 0. Vì sao cần bộ tool này

| Người Tekla quen có | Revit stock có | Khoảng trống |
|---|---|---|
| **Cast unit** = part chính + part phụ + **toàn bộ rebar tự thuộc về** | Assembly: phải tự chọn từng rebar để add; rebar thêm sau không tự vào | Tool tự gom rebar theo host |
| **Numbering series** (prefix + start number, đánh số lại, khoá số) | Rebar Number tự động theo Partition, không prefix, không start number theo ý | Numbering Manager |
| **Cast unit drawing + Clone drawing** từ bản vẽ mẫu | Assembly views tạo tay từng cái; không có "clone" | **Clone Drawing** — tool trọng tâm |
| **System component** (Beam reinforcement 63, Column 83, Pad footing 77 …) | Không có; chỉ Rebar / Rebar Set vẽ tay | Rebar Wizard |
| **Copy special → to another object** | Copy/Paste rồi rebar vẫn trỏ host cũ | Copy Rebar to Similar Hosts |
| **Report** (bending schedule, cast unit list, weight by diameter) | Schedule tự dựng; Bending Detail chỉ từ 2023 | BBS Export |
| Bật/tắt hiển thị rebar theo view bằng một nút | Unobscured / Solid chỉnh **từng** thanh, **từng** view | Rebar View |
| **Numbering / clash / unassigned check** | Không có | Rebar Check |

Nguyên tắc xuyên suốt: **không giả lập Tekla**, chỉ *đặt tên và tổ chức thao tác* theo cách người Tekla đã quen, còn phía dưới là đối tượng Revit chuẩn (Assembly, Rebar, Partition, View Template). Người dùng học Revit thật, không học một lớp vỏ.

---

## 1. Bảng ánh xạ thuật ngữ Tekla → Revit (dùng trong UI, tooltip, docs)

> UI là tiếng Anh. Nhãn dùng **thuật ngữ Revit**; thuật ngữ Tekla đặt trong tooltip dạng `Assembly (Tekla: cast unit)` để người mới tra được. Không đổi tên khái niệm Revit.

| Tekla | Revit | Ghi chú kỹ thuật |
|---|---|---|
| Cast unit (in-situ / precast) | `AssemblyInstance` + `AssemblyType` | Revit tự gộp các assembly **giống hệt hình học** thành cùng type — giống Tekla gán cùng mark cho cast unit giống nhau |
| Main part | Element đặt tên cho assembly (`NamingCategoryId`) | |
| Cast unit mark (C-1, B-12) | `AssemblyInstance.AssemblyTypeName` | Tool đặt tên theo series prefix + số |
| Rebar numbering series / partition | Rebar **Partition** (`NUMBER_PARTITION_PARAM`) + Rebar Number (`REBAR_NUMBER`) | `NumberingSchema` cho phép prefix / số bắt đầu theo partition |
| Reinforcing bar group | Rebar Set (layout Fixed Number / Maximum Spacing / Number with Spacing / Minimum Clear Spacing) | `RebarShapeDrivenAccessor` |
| Rebar shape catalog (shape code) | Rebar Shape family + `RebarShape` | Shape code BS 8666 / ACI / TCVN tuỳ family nạp |
| Pull-out picture | Rebar Bending Detail (2023+) | 2022: fallback ảnh shape trong schedule |
| Cover | `RebarCoverType` + `RebarHostData.SetCoverType` | |
| Hook | `RebarHookType` | |
| Coupler / end anchor | `RebarCoupler` | |
| Cast unit drawing | Bộ view của assembly (3D ortho, Front/Top/Side/Section, Part List, Material Takeoff, schedule) + Sheet | `AssemblyViewUtils.*` |
| GA drawing | Sheet thường | ngoài phạm vi |
| Clone drawing | **Không có** → tool #2 | |
| Drawing list | Sheet browser lọc theo assembly → tool #2 tab "Drawings" | |
| Report | Schedule + export Excel → tool #7 | |
| Phase / Organizer | Phase / Workset (đã có `ManaWorkset`) | không làm lại |

---

## 2. Luật "hoạt động tốt với Assembly" — áp cho MỌI tool trong bộ

Đây là yêu cầu khó nhất của đề bài, viết thành luật để test được:

| # | Luật | Vì sao / API |
|---|---|---|
| A1 | Mọi tool **chọn host** phải chấp nhận cả: element rời, element trong assembly, và chính AssemblyInstance (chọn assembly = chọn tất cả member hợp lệ). | `AssemblyInstance.GetMemberIds()`; `Element.AssemblyInstanceId` |
| A2 | Rebar sinh ra cho host nằm trong assembly phải **được add vào assembly đó** ngay trong cùng Transaction. | `AssemblyInstance.AddMemberIds`; nếu không, rebar "mồ côi", schedule của assembly thiếu thanh — lỗi phổ biến nhất của người Tekla khi mới sang |
| A3 | Rebar bị xoá / đổi host phải được **remove khỏi assembly cũ** trước, add vào assembly mới sau. Không được để phần tử thuộc hai assembly (Revit từ chối). | `AreElementsValidForAssembly(doc, ids, ElementId.InvalidElementId)` kiểm tra trước khi add |
| A4 | Thay đổi hình học/thành phần có thể làm Revit **tách type** (assembly không còn giống nhau) → tool phải báo số assembly bị tách type và mark bị đổi, không im lặng. | So `AssemblyTypeName` trước/sau trong cùng lần chạy |
| A5 | Không được add: phần tử đang trong Group, phần tử của link, assembly khác (không lồng). Tool lọc trước, báo rõ "skipped: in group". | ràng buộc Revit |
| A6 | Thao tác trên nhiều assembly: **một `TransactionGroup` assimilate** cho một lần bấm; mỗi assembly một `Transaction` con để lỗi 1 cái không hỏng cả lô. Ctrl+Z vẫn là một bước. | S1/S2 của new-tool-standard; `disposing(...)` theo S19 |
| A7 | View của assembly chỉ hợp lệ cho assembly đó; tool tạo view phải đọc lại `View.AssociatedAssemblyInstanceId` để không tạo trùng. | |
| A8 | Mọi tool có cột "Assembly" trong bảng kết quả, và lọc được "only in assemblies / only loose". | UI nhất quán |

**Cần xác minh trong Revit (spike GĐ0):** các instance **cùng** AssemblyType có dùng chung bộ view trong Project Browser hay không, và khi một instance tách type thì bộ view đi theo instance nào. Đây là điểm quyết định thiết kế Clone Drawing (mục 3.2) — không được giả định.

---

## 3. Bộ tool đề xuất (8 tool, 1 panel)

Thứ tự dưới đây cũng là **thứ tự ưu tiên build**. Pattern/size class theo `new-tool-standard.md` §0.

| # | Tool | Tekla tương đương | Pattern | Size | Sửa model | Ưu tiên |
|---|---|---|---|---|---|---|
| 1 | **Cast Unit Manager** (`CastUnit`) | Cast unit + numbering cast unit | P2 + P4 | L | Có | **P1** |
| 2 | **Clone Drawing** (`CloneDrawing`) | Clone drawing / Create cast unit drawings | P2 + P3 + P5 | L | Có (view/sheet) | **P1** |
| 3 | **Rebar Numbering** (`RebarNumbering`) | Numbering settings / Numbering series | P1 + P4 | M | Có | P1 |
| 4 | **Rebar View** (`RebarView`) | Rebar visibility / representation | P2 | S | Có (view-only) | P2 (quick win) |
| 5 | **BBS Export** (`BBSExport`) | Report: bending schedule, weight list | P4 | L | Không | P2 |
| 6 | **Copy Rebar** (`CopyRebar`) | Copy special → to another object | P2 + P5 | M | Có | P2 |
| 7 | **Rebar Check** (`RebarCheck`) | Numbering check / Clash check manager | P4 | L | Không (chỉ select/isolate) | P3 |
| 8 | **Rebar Wizard** (`RebarWizard`) | System components 63 / 83 / 77 / 92 | P1 + P5 | L | Có | P3 (lớn, làm sau) |

### 3.1 Cast Unit Manager — `CastUnit.pushbutton`

Một cửa sổ, hai trang (rail trái): **Create** và **Manage**.

**Create**
- Chọn host(s) trên model hoặc theo bộ lọc (Category + Type + Level + Workset).
- Checkbox mặc định bật: *Include hosted rebar* (gom `Rebar`, `RebarInSystem`, `AreaReinforcement`, `PathReinforcement`, `RebarCoupler`, `FabricSheet` có `GetHostId()` = host) → đúng hành vi cast unit Tekla.
- Tuỳ chọn: *Include joined / hosted elements* (door/window/opening, embedded parts), *Naming category*.
- Chế độ **batch**: "One assembly per host" (mỗi cột precast thành một cast unit) hoặc "All selected into one".
- Trước khi tạo: bảng preview `Host | Rebar count | Will skip (reason) | Mark (dự kiến)`; `AreElementsValidForAssembly` chạy trước, lý do skip hiện chữ (`in group`, `already in assembly`, `linked`).

**Manage**
- Bảng mọi AssemblyInstance: `Mark (type name) | Instances | Members | Rebar | Views | Sheets | Level`.
- Hành động: Rename theo series (prefix + start + step, giữ cùng type cùng mark), Add/Remove member, **Sync rebar** (quét rebar có host trong assembly nhưng chưa là member → add; báo số), Disassemble.
- Cột `Views` = số view có `AssociatedAssemblyInstanceId` trỏ về instance → người dùng thấy ngay cast unit nào chưa có drawing (bắc cầu sang tool #2).

API chính: `AssemblyInstance.Create / AddMemberIds / RemoveMemberIds / GetMemberIds / AssemblyTypeName / NamingCategoryId`, `AreElementsValidForAssembly`, `Rebar.GetHostId`, `RebarInSystem`, `FilteredElementCollector.OfClass(AssemblyInstance)`.
Helper mới: `lib/Snippets/_assembly.py` (mục 5).

### 3.2 Clone Drawing — `CloneDrawing.pushbutton` (tool trọng tâm)

Hai chế độ, cùng một cửa sổ:

**Mode A — From preset** (tương đương "Create cast unit drawings" hàng loạt)
- Chọn danh sách assembly (bảng từ 3.1, lọc "no views yet").
- Preset lưu JSON ở `%APPDATA%\T3LabAI\rebar_drawing_presets.json`: bộ view cần tạo (3D ortho, Front/Top/Right/Section…), View Template cho từng view, scale, titleblock, vị trí viewport trên sheet (toạ độ theo sheet), schedule nào kèm (Part List / Material Takeoff / Rebar schedule theo category), quy tắc tên sheet (`{mark}` / `{level}` / `{seq}`).
- Chạy theo P3: progress + log từng assembly; mỗi assembly một Transaction con (A6).

**Mode B — From master assembly** (clone drawing thật)
- Chọn **một** assembly mẫu đã có view + sheet hoàn chỉnh (đã dim, tag, text).
- Chọn các assembly đích. Tool chấm điểm "giống nhau" (cùng category host, cùng kích thước bao ±tolerance, cùng số rebar) để cảnh báo trước khi clone sang assembly khác hẳn.
- Thực hiện 3 tầng, mỗi tầng có thể bật/tắt, kết quả báo theo tầng:

| Tầng | Nội dung | Cách làm | Độ chắc |
|---|---|---|---|
| T1 | Bộ view (cùng loại, cùng orientation, template, scale, crop offset, detail level), sheet cùng titleblock, viewport cùng vị trí | `AssemblyViewUtils.*` tạo mới; copy thuộc tính view; `Viewport.Create` ở toạ độ lấy từ sheet mẫu (toạ độ tương đối với tâm assembly: `AssemblyInstance.GetCenter()` / `GetTransform()`) | Cao |
| T2 | Annotation **không có reference**: text, detail line, detail item, filled region, symbol, revision cloud | `ElementTransformUtils.CopyElements(sourceView, ids, destView, transform, options)` với transform = chuyển hệ toạ độ assembly mẫu → assembly đích | Cao (cần spike xác nhận copy giữa 2 assembly view) |
| T3 | Annotation **có reference**: rebar tag, multi-rebar annotation, dimension, spot elevation | **Tạo lại**, không copy: khớp phần tử mẫu ↔ phần tử đích bằng *fingerprint* (category + shape + vị trí tương đối với tâm assembly + chỉ số rebar trong set), rồi `IndependentTag.Create` / `MultiReferenceAnnotation.Create` / `doc.Create.NewDimension` với reference lấy từ phần tử đích tương ứng. Dimension bám mặt bê tông: khớp face theo normal + khoảng cách tới tâm | Trung bình — ghi rõ số tag/dim **không khớp được** vào log, không im lặng |

- Sau clone, bảng kết quả: `Assembly | Views created | Sheet | Annotations copied | Tags re-created | Dims re-created | Unmatched` + nút *Open sheet*.
- P5 confirm: "Create N views and M sheets for K assemblies?". Không bao giờ xoá view/sheet có sẵn; assembly đã có drawing thì skip hoặc "add missing only".

Ràng buộc đã biết: `CopyElements` chỉ copy được giữa view **cùng loại** (section→section, drafting→drafting); view 3D ortho của assembly không nhận annotation 2D. Dimension/tag copy chéo assembly bằng `CopyElements` sẽ **mất reference** → vì thế T3 phải tạo lại.

### 3.3 Rebar Numbering — `RebarNumbering.pushbutton`

- Bảng Partition: `Partition | Prefix | Start number | Next number | Rebar count | Duplicates`.
- Sửa prefix / start number theo partition (`NumberingSchema` — `NumberingSchemaTypes.StructuralNumberingSchemas.Rebar`; chữ ký method xác nhận trong spike).
- **Assign partition by rule**: partition = `{AssemblyMark}` / `{Level}` / `{HostType}` / `{Workset}` → đúng cách Tekla đánh số theo series từng cast unit; áp cho rebar trong assembly thì lấy mark assembly làm partition mặc định.
- Renumber: Revit tự đánh số lại khi đổi partition; tool chỉ báo bảng trước/sau. **Không** ghi đè `REBAR_NUMBER` bằng tay (read-only khi numbering bật).
- Schedule Mark (`REBAR_ELEM_SCHEDULE_MARK`): tuỳ chọn ghi `{prefix}{number}` để người Tekla có cột "Bar mark" quen thuộc trên schedule/tag.

### 3.4 Rebar View — `RebarView.pushbutton`

- Chọn view (hiện tại / nhiều view / tất cả view của N assembly).
- Hành động theo lô: Unobscured on/off, Solid in 3D on/off, Host transparency (override graphic), bật/tắt category Structural Rebar, ẩn rebar không thuộc assembly của view.
- API: `Rebar.SetUnobscuredInView`, `Rebar.SetSolidInView(View3D, bool)`, `View.SetCategoryHidden`, `OverrideGraphicSettings.SetSurfaceTransparency`.
- Chạy trong 1 Transaction; tác vụ > 2 s (model nhiều rebar) có progress (S5).

### 3.5 BBS Export — `BBSExport.pushbutton`

- Nguồn: toàn model / theo assembly / theo partition / theo selection.
- Cột chuẩn: `Mark | Partition | Assembly | Shape code | Bar type | Ø (BarNominalDiameter) | Qty | Length (TotalLength / Quantity) | A B C D E F | Weight/m | Total weight | Host`.
- Kích thước A–F: đọc parameter của rebar theo `RebarShape` (shape-driven) — các tham số `RebarShapeParameters`; free-form rebar báo "n/a".
- Tổng hợp: theo Ø, theo assembly, theo partition. Xuất **CSV + XLSX** (openpyxl đã có trong engine? — *xác nhận*; không thì CSV + `.xlsx` qua COM Excel như `ManaSheets` đang làm).
- 2023+: kèm cột ảnh Bending Detail nếu người dùng bật (`RebarBendingDetail` — chữ ký API xác nhận trong spike, có `_compat` guard; 2022 bỏ qua cột này và báo).
- Không sửa model.

### 3.6 Copy Rebar — `CopyRebar.pushbutton`

- Chọn host nguồn (hoặc assembly nguồn) → chọn host đích (nhiều).
- Kiểm tra tương thích: cùng category, cùng family type (hoặc cùng kích thước tiết diện ±tol), host hợp lệ (`RebarHostData.IsValidHost`).
- Thực hiện: `ElementTransformUtils.CopyElements` với transform từ `host.Location` + hướng (kể cả mirror tuỳ chọn) → `rebar.SetHostId(doc, targetHostId)` → nếu đích trong assembly thì `AddMemberIds` (A2), nếu nguồn trong assembly và đích không thì rebar đi theo đích (không giữ assembly cũ).
- P5 confirm với số lượng; kết quả bảng `Target host | Copied | Rehosted | Added to assembly | Failed (reason)`.

### 3.7 Rebar Check — `RebarCheck.pushbutton`

Bảng phát hiện, mỗi dòng có *Select* / *Isolate in view*:

| Check | Cách phát hiện |
|---|---|
| Rebar không có host hợp lệ / host đã xoá | `GetHostId()` invalid |
| Rebar có host trong assembly nhưng **không** là member | so `Element.AssemblyInstanceId` của rebar với host |
| Assembly chưa có view / chưa có sheet | A7 |
| Trùng Rebar Number trong cùng partition với hình khác (số bị khoá cũ) | group theo partition + number, so shape/dia/length |
| Rebar nằm ngoài bounding box host (vẽ nhầm) | `BoundingBoxXYZ` của rebar vs host (có tolerance) |
| Rebar không thuộc partition nào ("<unassigned>") | tham số rỗng |
| Shape-driven rebar bị "Shape not recognized" | `RebarShapeDrivenAccessor` + shape id invalid |

Không sửa model; nút *Fix* chỉ cho hai mục an toàn: *Sync rebar into assembly* và *Assign partition* (gọi lại logic 3.1/3.3).

### 3.8 Rebar Wizard — `RebarWizard.pushbutton` (giai đoạn sau)

Thay cho system component của Tekla; làm **từng host một**, form P1 với preview text số thanh:
- **Beam**: thanh trên/dưới theo nhịp (số lượng, Ø, lớp), đai theo **3 vùng** (hai đầu dày / giữa thưa), cover, hook.
- **Column**: thanh dọc theo cạnh, đai + đai phụ, vùng đai dày chân/đỉnh, lap length.
- **Pad footing**: lưới trên/dưới, hook lên.
- **Wall / Slab**: dùng `AreaReinforcement` theo Ø + spacing hai lớp.
- Sinh bằng `Rebar.CreateFromCurves` / `CreateFromRebarShape` + `RebarShapeDrivenAccessor.SetLayoutAs*`; mọi thanh add vào assembly của host nếu có (A2).
- Preset JSON để người dùng lưu "component" của mình giống lưu attribute file trong Tekla.

Rủi ro: hình học host không chữ nhật, dầm nghiêng, cột tròn — khoanh phạm vi V1 là **tiết diện chữ nhật, host thẳng**; ngoài phạm vi báo rõ.

---

## 4. Giai đoạn thực hiện

| GĐ | Nội dung | Deliverable | Ước lượng |
|---|---|---|---|
| **0 · Spike** | Xác minh 6 giả định API trong Revit 2022 + 2026 (mục 6) bằng script chạy trong RPS/pyRevit console; ghi kết quả vào mục 6 | mục 6 tick ✅/❌ + quyết định thiết kế T2/T3 của Clone Drawing | 2–3 ngày |
| **1 · Nền** | `lib/Snippets/_assembly.py`, `lib/Snippets/_rebar.py` (+ `_compat` guard cho `BarNominalDiameter`, `RebarBendingDetail`); test thuần Python `dev/test_assembly_rules.py`, `dev/test_rebar_fingerprint.py`; panel `Rebar & Assembly.panel` + icon theo chuẩn 09 | helper + test xanh + panel rỗng hiện trên ribbon | 2 ngày |
| **2 · Quick win** | Tool #1 Cast Unit Manager, tool #4 Rebar View | 2 tool QA trong Revit | 4 ngày |
| **3 · Clone Drawing** | Mode A preset → Mode B T1 → T2 → T3 | tool #2, log unmatched rõ ràng | 6–8 ngày |
| **4 · Numbering + BBS** | Tool #3, tool #5 | 2 tool, xuất file khớp số liệu schedule Revit | 4 ngày |
| **5 · Copy + Check** | Tool #6, tool #7 | 2 tool | 4 ngày |
| **6 · Wizard** | Tool #8 (V1: beam + column chữ nhật) | tool #8 | 6+ ngày, mở roadmap riêng khi tới |

Mỗi GĐ kết thúc bằng: 4 gate (`audit_t3`, `audit_tools`, `audit_wiring`, `audit_revit_compat`) xanh + `audit_cpython` 0 P0 + `check_xaml_load` / `check_xaml_wpf.ps1` 0 FAILED + checklist QA Revit ở mục 7 — chưa QA thì ghi `NEEDS VERIFICATION`, không tick.

---

## 5. Cấu trúc file dự kiến

```
T3Lab.extension/
├── T3Lab_Dev.tab/Rebar & Assembly.panel/
│   ├── bundle.yaml                    # layout: CastUnit · CloneDrawing · RebarNumbering · RebarTools(stack) · RebarWizard
│   ├── CastUnit.pushbutton/
│   ├── CloneDrawing.pushbutton/
│   ├── RebarNumbering.pushbutton/
│   ├── RebarTools.stack/              # RebarView · CopyRebar · RebarCheck · BBSExport
│   └── RebarWizard.pushbutton/
├── lib/GUI/Tools/
│   ├── CastUnit.xaml · CloneDrawing.xaml · RebarNumbering.xaml · RebarView.xaml
│   ├── CopyRebar.xaml · RebarCheck.xaml · BBSExport.xaml · RebarWizard.xaml
├── lib/GUI/
│   ├── CastUnitDialog.py · CloneDrawingDialog.py · RebarNumberingDialog.py · ...
└── lib/Snippets/
    ├── _assembly.py     # collect, validate, create, add/remove, sync rebar, views/sheets of assembly, center/transform
    ├── _rebar.py        # host→rebar map, diameter/length/weight (compat), partition/number, shape params A–F, fingerprint
    └── _drawing_clone.py# preset schema, view-set replication, annotation copy (T2), reference re-create (T3)
dev/
├── test_assembly_rules.py      # A1–A8 trên object giả (không cần Revit)
├── test_rebar_fingerprint.py   # khớp phần tử mẫu ↔ đích, tolerance, mirror
├── test_bbs_export.py          # tổng hợp theo Ø/assembly/partition, format CSV
└── plan/rebar-tekla-toolkit-roadmap.md   # file này
```

Logic Revit API nằm trọn trong `lib/Snippets/`; `script.py` chỉ nối UI ↔ helper (luật tách bạch §1 new-tool-standard). Phần "tính toán thuần" (fingerprint, tổng hợp BBS, rule A5 lọc) tách khỏi API để test được ngoài Revit.

---

## 6. Giả định API phải xác minh trước khi code (GĐ0)

| # | Giả định | Ảnh hưởng nếu sai | Kết quả |
|---|---|---|---|
| G1 | Các instance cùng `AssemblyType` dùng chung bộ view; instance tách type thì mất view | Quyết định Mode B clone theo *type* hay theo *instance* | ⬜ |
| G2 | `ElementTransformUtils.CopyElements(viewA, ids, viewB, transform, opts)` copy được text/detail line giữa hai assembly detail view cùng orientation | T2 của Clone Drawing | ⬜ |
| G3 | Dimension/tag copy bằng `CopyElements` sang assembly khác **mất** reference (như dự đoán) | Nếu copy được thì T3 đơn giản hơn nhiều | ⬜ |
| G4 | `NumberingSchema` cho đọc/ghi prefix + start number theo partition trên 2022–2027; tên method chính xác | Tool #3 | ⬜ |
| G5 | `RebarBendingDetail` tồn tại từ 2023, chữ ký `Create(...)`; 2022 không có → `_compat` guard | Tool #5 cột ảnh | ⬜ |
| G6 | `Rebar.SetHostId(doc, id)` giữ nguyên hình học và layout của rebar set sau rehost | Tool #6 | ⬜ |
| G7 | `AssemblyInstance.AddMemberIds` chấp nhận `RebarInSystem` / `AreaReinforcement` / `FabricSheet` làm member | Tool #1 "Include hosted rebar" | ⬜ |
| G8 | `RebarBarType.BarNominalDiameter` có từ 2022 (thay `BarDiameter` deprecated) — thêm rule vào `audit_revit_compat.py` nếu cần | Tool #5 | ⬜ |

Cách xác minh: script `dev/debug/spike_rebar_assembly.py` chạy trong pyRevit console trên model test có 2 cast unit cột giống nhau + 1 khác, ghi output ra `%APPDATA%\T3LabAI\spike_rebar.log`. Người dùng chạy trên Revit 2022 và 2026 rồi dán log lại.

---

## 7. Checklist QA trong Revit cho mỗi tool (bổ sung checklist §5 new-tool-standard)

```
[ ] Chọn element rời → chạy đúng
[ ] Chọn element trong assembly → rebar sinh ra / copy sang là member của assembly (kiểm tra Project Browser + schedule assembly)
[ ] Chọn chính AssemblyInstance → tool hiểu là chọn toàn bộ member hợp lệ
[ ] Element trong Group / từ Link → báo "skipped: <reason>", không stacktrace
[ ] Chạy trên 2 assembly giống hệt → sau khi chạy vẫn cùng type (hoặc tool báo rõ số type bị tách)
[ ] Ctrl+Z một lần hoàn tác toàn bộ lần bấm (TransactionGroup assimilate)
[ ] Revit 2022 (.NET 4.8) và Revit 2026 (.NET 8) mở tool không lỗi
[ ] Model 2 000+ rebar: thao tác > 2 s có progress, không treo Revit
```

---

## 8. Rủi ro & giới hạn nói trước

- **Clone Drawing T3 không bao giờ đạt 100 %**: dimension bám vào mặt phần tử; phần tử đích khác hình học thì không có mặt tương ứng. Tool phải xuất danh sách *unmatched* để người dùng dim tay phần còn lại — vẫn nhanh hơn làm từ đầu, nhưng không hứa "một nút xong".
- **Tách type assembly** là hành vi lõi của Revit, không chặn được; chỉ phát hiện và báo (A4).
- **Rebar Wizard** là tool lớn nhất và ít "tương thích Tekla" nhất vì Tekla component có hàng chục tham số; V1 chỉ dầm/cột chữ nhật, không nhận là xong việc thay thế component.
- **Hiệu năng**: `FilteredElementCollector` trên rebar + `GetHostId()` cho mọi thanh là O(n); cache map host→rebar một lần mỗi lần mở tool (`_rebar.host_rebar_map(doc)`), không gọi lại trong vòng lặp.
- **Ngoài phạm vi**: GA drawing, precast connections/embeds, export sang Tekla/IFC (đã có panel IFC-SG), tính toán kết cấu.

---

## 9. Việc cần quyết định trước khi vào GĐ1

1. Tiêu chuẩn shape code / bảng trọng lượng mặc định cho BBS: **BS 8666**, **ACI**, hay **TCVN** (đề xuất mặc định TCVN 5574 + tuỳ chọn BS 8666, bảng kg/m đặt trong JSON `lib/Snippets/data/rebar_weights.json`).
2. Chỉ in-situ hay cả precast? (precast cần thêm embeds/lifting vào cast unit — đề xuất V1 làm chung, không phân biệt.)
3. Có model test Revit nào sẵn (cột/dầm precast có rebar) để chạy spike GĐ0 không — nếu chưa thì GĐ0 bắt đầu bằng dựng model test 1 ngày.
