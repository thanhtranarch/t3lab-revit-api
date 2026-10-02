# Rebar Toolkit cho người dùng chuyển từ Tekla — Phương án xây dựng

> Ngày lập: 2026-10-02 · Sửa lần 2 cùng ngày theo hai ràng buộc của chủ extension:
> **(1) không tool nào trùng / lặp chức năng Revit đã có sẵn; (2) Revit đang dùng là 2027.**
> Trạng thái: **ĐỀ XUẤT — chưa có dòng code nào**
> Panel mới: `T3Lab.extension/T3Lab_Dev.tab/Rebar & Assembly.panel/`
> Mục tiêu: người quen Tekla Structures mở Revit là làm việc được ngay với từ vựng và thao tác quen,
> và mọi tool hoạt động đúng cả khi phần tử nằm trong Assembly (cast unit của Revit).

---

## 0. Hai nguyên tắc chốt

**N1 — Chỉ lấp khoảng trống, không làm lại Revit.** Một chức năng chỉ được đưa vào tool khi Revit 2027
*không làm được*, hoặc chỉ làm được *từng phần tử một* trong khi giá trị của tool nằm ở việc chạy hàng loạt
theo quy tắc. Chức năng Revit đã có thì tool **gọi thẳng lệnh Revit** hoặc ghi vào hướng dẫn "dùng lệnh X của
Revit", không bọc lại bằng UI riêng. Mục 1 là bảng rà soát từng đề xuất.

**N2 — Thiết kế và QA trên Revit 2027.** Code dùng API mới nhất khi có lợi (Bending Detail 2023+, ElementId
`Value`, …). Gate `dev/audit_revit_compat.py` vẫn bắt dải 2022→2027 nên API thêm sau 2022 phải đi qua
`lib/Snippets/_compat.py` để không nổ import trên máy khác, nhưng **không QA và không hứa hành vi** trên
2022–2026 cho bộ tool này. Revit 2027 chạy .NET 8: mọi window modeless/ExternalEvent theo luật
`__persistentengine__` + `detect_persistent_engine()`; sửa `lib/` thì restart Revit, không Reload.

---

## 1. Rà soát trùng chức năng với Revit 2027 (quyết định giữ / cắt)

> Cột "Revit 2027 có sẵn" ghi theo hiểu biết đến bản 2026 cộng những gì chắc chắn còn nguyên ở 2027.
> Dòng đánh dấu **(xác minh)** là chức năng tôi *không chắc* 2027 đã có hay chưa — kiểm tra trong GĐ0 trước
> khi code, nếu Revit đã có thì cắt nốt. Không được giữ một tool chỉ vì "tiện hơn một chút".

| Đề xuất ban đầu | Revit 2027 có sẵn | Quyết định |
|---|---|---|
| Rebar View: bật Unobscured / Solid hàng loạt theo view | **Có.** Properties của rebar → *View Visibility States* đặt Unobscured + Solid cho mọi view trong một bảng; View Filter + Visibility/Graphics; Rebar Set presentation (show all / first-last / middle) | **CẮT** |
| Rebar Numbering: prefix + start number theo partition | **Có.** *Reinforcement Numbering* (Structure → Reinforcement) quản lý partition, đánh số lại, xoá khoảng trống; Schedule Mark là tham số có sẵn | **CẮT** phần đánh số. Giữ duy nhất **gán Partition theo quy tắc** (partition = mark assembly / level / host type) — Revit chỉ gán partition tay từng thanh → gộp vào Cast Unit Manager |
| BBS Export: bảng uốn + trọng lượng ra Excel | **Có phần lớn.** Rebar Schedule có Shape, tham số hình A–F, Total Bar Length, Quantity; *Rebar Bending Detail* (2023+) chèn hình uốn vào schedule và vào view; schedule export ra txt/csv | **CẮT.** Khoảng trống còn lại (kg/m theo tiêu chuẩn, tổng theo Ø) giải quyết bằng **một schedule template + shared parameter `T3_WeightPerMetre` trên RebarBarType + calculated field** — là nội dung hướng dẫn, không phải tool |
| Copy Rebar sang host tương tự | **Có phần lớn.** Copy / Paste Aligned → rebar dán vào host hợp lệ sẽ tự nhận host mới; Mirror có sẵn | **CẮT** tool riêng. Phần Revit *không* làm: rebar dán vào host đang trong assembly không tự vào assembly → đây là việc của *Sync rebar* trong Cast Unit Manager |
| Cast Unit Manager: tạo / sửa / tách assembly | **Có một phần.** Create Assembly, Edit Assembly, Disassemble, Naming Category — nhưng **từng assembly một**, phải chọn tay từng rebar để add, không có "đồng bộ rebar", không đổi tên hàng loạt theo series | **GIỮ, cắt gọn**: bỏ create/edit/disassemble đơn lẻ (dùng lệnh Revit); giữ batch create một-assembly-mỗi-host có tự gom rebar, Sync rebar, Rename theo series, trạng thái drawing, gán partition theo quy tắc |
| Clone Drawing — Mode A tạo bộ view theo preset | **Có.** Assembly → *Create Views* tạo 3D, plan, elevation, section, Part List, Material Takeoff, sheet với view template + titleblock | **CẮT Mode A.** Bản vẽ mẫu phải tạo bằng lệnh Create Views của Revit |
| Clone Drawing — Mode B nhân bản bản vẽ mẫu sang assembly khác | **Không có.** Revit không có cách nào mang view + sheet + dim + tag từ một assembly sang assembly khác; instance tách type là mất bản vẽ, làm lại từ đầu | **GIỮ — tool trọng tâm** |
| Rebar Check | **Không có.** Interference Check chỉ va chạm hình học; không có kiểm tra rebar mồ côi, rebar chưa vào assembly, assembly chưa có bản vẽ, trùng số khác hình | **GIỮ** |
| Rebar Wizard (system component dầm / cột / móng) | **Chưa thấy có** trong sản phẩm; Autodesk Rebar Extensions đã ngừng từ lâu **(xác minh ở 2027)** | **GIỮ, giai đoạn cuối**; nếu 2027 có auto-reinforcement thì cắt |

Kết quả: **8 → 4 tool**, trong đó 3 tool làm ngay, 1 tool làm sau.

---

## 2. Bảng ánh xạ thuật ngữ Tekla → Revit (dùng trong tooltip, docs onboarding)

> UI tiếng Anh, nhãn dùng **thuật ngữ Revit**; thuật ngữ Tekla đặt trong tooltip dạng `Assembly (Tekla: cast unit)`.
> Bảng này cũng là xương sống của một trang hướng dẫn "Tekla → Revit 2027" trong `docs/` — vì phần lớn
> việc "quen tay" được giải quyết bằng *biết lệnh Revit nào tương ứng*, không phải bằng tool.

| Tekla | Revit 2027 | Tool T3Lab liên quan |
|---|---|---|
| Cast unit | `AssemblyInstance` + `AssemblyType` (instance giống hệt → cùng type, giống Tekla cùng mark) | Cast Unit Manager |
| Main part | Element đặt tên (`NamingCategoryId`) | Cast Unit Manager |
| Cast unit mark (C-1, B-12) | `AssemblyTypeName` | Cast Unit Manager (rename series) |
| Numbering series | Rebar Partition + Reinforcement Numbering (**lệnh Revit**) | Cast Unit Manager (gán partition theo quy tắc) |
| Reinforcing bar group | Rebar Set (Fixed Number / Maximum Spacing / Number with Spacing / Minimum Clear Spacing) | — |
| Shape catalog | Rebar Shape family | — |
| Pull-out picture | Rebar Bending Detail (**lệnh Revit**) | — |
| Cover / hook / coupler | `RebarCoverType` / `RebarHookType` / `RebarCoupler` | — |
| Cast unit drawing | Assembly → Create Views (**lệnh Revit**) | — |
| Clone drawing | **không có** | Clone Drawing |
| Report (bending schedule, weight) | Rebar Schedule + Bending Detail + template T3 (hướng dẫn) | — |
| Numbering / clash check | Interference Check (hình học) · còn lại không có | Rebar Check |
| System component | **không có** (xác minh 2027) | Rebar Wizard |
| Phase / Organizer | Phase / Workset (`ManaWorkset` đã có) | — |

---

## 3. Luật "hoạt động tốt với Assembly" — áp cho MỌI tool trong bộ

| # | Luật | API / lý do |
|---|---|---|
| A1 | Tool **chọn host** chấp nhận cả: element rời, element trong assembly, và chính AssemblyInstance (= toàn bộ member hợp lệ) | `AssemblyInstance.GetMemberIds()`; `Element.AssemblyInstanceId` |
| A2 | Rebar sinh ra cho host nằm trong assembly phải **được add vào assembly đó** trong cùng Transaction | `AddMemberIds`; nếu không, rebar mồ côi, schedule assembly thiếu thanh — lỗi phổ biến nhất của người Tekla mới sang |
| A3 | Đổi host / xoá: remove khỏi assembly cũ trước, add vào assembly mới sau; một phần tử không thuộc hai assembly | `AreElementsValidForAssembly(doc, ids, ElementId.InvalidElementId)` kiểm tra trước |
| A4 | Thay đổi có thể làm Revit **tách type** → tool báo số assembly bị tách và mark bị đổi, không im lặng | so `AssemblyTypeName` trước / sau |
| A5 | Không add: phần tử trong Group, phần tử của link, assembly khác (không lồng). Lọc trước, báo `skipped: in group` | ràng buộc Revit |
| A6 | Thao tác nhiều assembly: một `TransactionGroup` assimilate cho một lần bấm; mỗi assembly một `Transaction` con | S1/S2; `disposing(...)` theo S19 |
| A7 | View của assembly chỉ hợp lệ cho assembly đó; đọc `View.AssociatedAssemblyInstanceId` để không tạo trùng | |
| A8 | Mọi bảng kết quả có cột "Assembly", lọc được *only in assemblies / only loose* | UI nhất quán |

---

## 4. Bộ tool sau rà soát (4 tool)

| # | Tool | Khoảng trống Revit 2027 mà nó lấp | Pattern | Size | Sửa model | Ưu tiên |
|---|---|---|---|---|---|---|
| 1 | **Cast Unit Manager** (`CastUnit`) | Tạo assembly hàng loạt có tự gom rebar · Sync rebar · rename series · gán partition theo quy tắc · trạng thái bản vẽ | P2 + P4 | L | Có | P1 |
| 2 | **Clone Drawing** (`CloneDrawing`) | Nhân bản bản vẽ assembly mẫu sang assembly khác | P2 + P3 + P5 | L | Có (view / sheet) | **P1 — trọng tâm** |
| 3 | **Rebar Check** (`RebarCheck`) | Kiểm tra rebar / assembly ở mức dữ liệu | P4 | L | Không (select / isolate; Fix chỉ gọi 1 và 2) | P2 |
| 4 | **Rebar Wizard** (`RebarWizard`) | System component dầm / cột / móng | P1 + P5 | L | Có | P3, sau cùng |

### 4.1 Cast Unit Manager — `CastUnit.pushbutton`

Một cửa sổ, rail trái 3 trang. Không có nút Create / Edit / Disassemble đơn lẻ — việc đó là lệnh Revit.

**Batch create** (Revit: từng assembly một, chọn tay rebar)
- Chọn host trên model hoặc theo bộ lọc (Category + Type + Level + Workset).
- *Include hosted rebar* mặc định bật: gom `Rebar`, `RebarInSystem`, `AreaReinforcement`, `PathReinforcement`, `RebarCoupler`, `FabricSheet` có `GetHostId()` = host → đúng hành vi cast unit Tekla.
- *One assembly per host* (mỗi cột precast một cast unit) hoặc *All selected into one*.
- Preview `Host | Rebar | Will skip (reason) | Mark (dự kiến)`; `AreElementsValidForAssembly` chạy trước, lý do skip bằng chữ.

**Manage** (Revit: không có bảng tổng hợp)
- Bảng mọi AssemblyInstance: `Mark | Instances | Members | Rebar | Views | Sheets | Level`.
- **Sync rebar**: quét rebar có host trong assembly nhưng chưa là member → add; báo số. Đây cũng là bước sau khi người dùng Copy / Paste Aligned rebar bằng lệnh Revit.
- **Rename series**: prefix + start + step, cùng type giữ cùng mark (A4 báo nếu có type tách).
- Cột `Views` / `Sheets` đếm theo `AssociatedAssemblyInstanceId` → thấy ngay cast unit nào chưa có bản vẽ, bắc cầu sang tool 2.

**Partition by rule** (Revit: gán partition tay từng thanh)
- partition = `{AssemblyMark}` / `{Level}` / `{HostType}` / `{Workset}` / chuỗi cố định, phạm vi: selection / assembly / toàn model.
- Chỉ ghi `NUMBER_PARTITION_PARAM`; **không** đụng Rebar Number — Revit tự đánh số lại; sau đó người dùng mở Reinforcement Numbering của Revit nếu muốn chỉnh tay.

### 4.2 Clone Drawing — `CloneDrawing.pushbutton` (tool trọng tâm)

Tiền đề: bản vẽ mẫu đã làm **bằng lệnh Revit** (Assembly → Create Views, dim, tag, text, sheet). Tool chỉ nhân bản.

- Chọn một assembly mẫu có view + sheet; chọn các assembly đích. Tool chấm "độ giống" (cùng category host, kích thước bao ± tolerance, cùng số rebar) và cảnh báo trước khi clone sang assembly khác hẳn.
- Ba tầng, bật / tắt được, báo kết quả theo tầng:

| Tầng | Nội dung | Cách làm | Độ chắc |
|---|---|---|---|
| T1 | Bộ view cùng loại / orientation / template / scale / crop / detail level; sheet cùng titleblock; viewport cùng vị trí | `AssemblyViewUtils.Create3DOrthographic / CreateDetailSection / CreatePartList / CreateMaterialTakeoff / CreateSingleCategorySchedule`; copy thuộc tính view; `Viewport.Create` theo toạ độ sheet mẫu | Cao |
| T2 | Annotation **không reference**: text, detail line, detail item, filled region, symbol | `ElementTransformUtils.CopyElements(sourceView, ids, destView, transform, options)`, transform = hệ assembly mẫu → đích (`GetTransform()` / `GetCenter()`) | Cao (spike G2) |
| T3 | Annotation **có reference**: rebar tag, multi-rebar annotation, dimension, spot elevation | **Tạo lại**: khớp phần tử mẫu ↔ đích bằng fingerprint (category + shape + vị trí tương đối tâm assembly + chỉ số trong set) → `IndependentTag.Create` / `MultiReferenceAnnotation.Create` / `doc.Create.NewDimension`; dim bám mặt bê tông khớp face theo normal + khoảng cách tới tâm | Trung bình — log rõ số **unmatched** |

- Kết quả: `Assembly | Views | Sheet | Copied | Tags re-created | Dims re-created | Unmatched` + *Open sheet*.
- P5 confirm với số lượng. Không xoá view / sheet có sẵn; assembly đã có bản vẽ → skip hoặc *add missing only*.
- Ràng buộc đã biết: `CopyElements` chỉ giữa view cùng loại; dim / tag copy chéo assembly mất reference → T3 phải tạo lại.

### 4.3 Rebar Check — `RebarCheck.pushbutton`

| Check | Cách phát hiện |
|---|---|
| Rebar không có host hợp lệ / host đã xoá | `GetHostId()` invalid |
| Rebar có host trong assembly nhưng **không** là member | so `AssemblyInstanceId` của rebar với host |
| Assembly chưa có view / chưa có sheet | A7 |
| Trùng Rebar Number trong partition nhưng khác hình / Ø / dài | group theo partition + number |
| Rebar nằm ngoài bounding box host | `BoundingBoxXYZ` ± tolerance |
| Rebar chưa có partition | tham số rỗng |
| Shape-driven rebar "shape not recognized" | `RebarShapeDrivenAccessor` + shape id invalid |

Mỗi dòng: *Select* / *Isolate in view*. Nút *Fix* chỉ cho hai mục an toàn, gọi lại logic tool 1: *Sync rebar into assembly* và *Assign partition*. Không có check va chạm — dùng Interference Check của Revit.

### 4.4 Rebar Wizard — `RebarWizard.pushbutton` (sau cùng, xác minh 2027 trước)

- Beam: thanh trên / dưới, đai 3 vùng, cover, hook · Column: thanh dọc, đai + đai phụ, vùng đai dày · Pad footing: lưới hai lớp · Wall / Slab: `AreaReinforcement`.
- `Rebar.CreateFromCurves` / `CreateFromRebarShape` + `RebarShapeDrivenAccessor.SetLayoutAs*`; mọi thanh add vào assembly của host (A2).
- Preset JSON `%APPDATA%\T3LabAI\rebar_wizard_presets.json` — tương đương lưu attribute file trong Tekla.
- V1: tiết diện chữ nhật, host thẳng; ngoài phạm vi báo rõ.

---

## 5. Giai đoạn thực hiện

| GĐ | Nội dung | Deliverable | Ước lượng |
|---|---|---|---|
| **0 · Spike** | Trên Revit 2027: xác minh G1–G8 (mục 7) bằng `dev/debug/spike_rebar_assembly.py`; đồng thời rà "What's New 2027" cho hai mục **(xác minh)** ở mục 1 | mục 7 tick ✅ / ❌; danh sách tool chốt | 2 ngày |
| **1 · Nền** | `lib/Snippets/_assembly.py`, `_rebar.py`, `_drawing_clone.py` (+ `_compat` guard); test thuần Python; panel + icon theo chuẩn 09; trang `docs/tekla-to-revit-2027.md` từ bảng mục 2 | helper + test xanh + panel rỗng + docs | 2 ngày |
| **2 · Cast Unit Manager** | Tool 1 | QA Revit 2027 | 3 ngày |
| **3 · Clone Drawing** | T1 → T2 → T3 | Tool 2, log unmatched | 6–8 ngày |
| **4 · Rebar Check** | Tool 3 | QA Revit 2027 | 2 ngày |
| **5 · Wizard** | Tool 4 (V1 dầm + cột chữ nhật) nếu GĐ0 xác nhận Revit 2027 chưa có | mở roadmap riêng khi tới | 6+ ngày |

Mỗi GĐ kết thúc bằng: 4 gate (`audit_t3`, `audit_tools`, `audit_wiring`, `audit_revit_compat`) xanh + `audit_cpython` 0 P0 + `check_xaml_load` / `check_xaml_wpf.ps1` 0 FAILED + checklist mục 8 trên Revit 2027. Chưa QA thì ghi `NEEDS VERIFICATION`, không tick.

---

## 6. Cấu trúc file dự kiến

```
T3Lab.extension/
├── T3Lab_Dev.tab/Rebar & Assembly.panel/
│   ├── bundle.yaml                 # layout: CastUnit · CloneDrawing · RebarCheck · RebarWizard
│   ├── CastUnit.pushbutton/
│   ├── CloneDrawing.pushbutton/
│   ├── RebarCheck.pushbutton/
│   └── RebarWizard.pushbutton/     # GĐ5
├── lib/GUI/Tools/   CastUnit.xaml · CloneDrawing.xaml · RebarCheck.xaml · RebarWizard.xaml
├── lib/GUI/         CastUnitDialog.py · CloneDrawingDialog.py · RebarCheckDialog.py · RebarWizardDialog.py
└── lib/Snippets/
    ├── _assembly.py        # collect, validate, batch create, sync rebar, rename series, views/sheets, center/transform
    ├── _rebar.py           # host→rebar map (cache 1 lần / lần mở tool), partition by rule, fingerprint
    └── _drawing_clone.py   # view-set replication (T1), annotation copy (T2), reference re-create (T3)
dev/
├── debug/spike_rebar_assembly.py   # GĐ0, chạy trong pyRevit console
├── test_assembly_rules.py          # A1–A8 trên object giả, không cần Revit
├── test_rebar_fingerprint.py       # khớp mẫu ↔ đích, tolerance, mirror
└── plan/rebar-tekla-toolkit-roadmap.md
docs/tekla-to-revit-2027.md         # bảng mục 2 + lệnh Revit tương ứng cho từng thao tác Tekla
```

Logic Revit API nằm trọn trong `lib/Snippets/`; `script.py` chỉ nối UI ↔ helper. Phần tính toán thuần (fingerprint, rule A5, partition rule) tách khỏi API để test ngoài Revit.

---

## 7. Giả định phải xác minh trên Revit 2027 trước khi code (GĐ0)

| # | Giả định | Ảnh hưởng nếu sai | Kết quả |
|---|---|---|---|
| G1 | Instance cùng `AssemblyType` dùng chung bộ view; instance tách type thì mất view | Clone theo type hay theo instance | ⬜ |
| G2 | `CopyElements(viewA, ids, viewB, transform, opts)` copy được text / detail line giữa hai assembly detail view cùng orientation | T2 | ⬜ |
| G3 | Dim / tag copy bằng `CopyElements` sang assembly khác **mất** reference | Nếu còn reference thì T3 đơn giản hơn | ⬜ |
| G4 | `AddMemberIds` chấp nhận `RebarInSystem` / `AreaReinforcement` / `FabricSheet` làm member | Batch create "Include hosted rebar" | ⬜ |
| G5 | Rebar dán bằng Paste Aligned vào host trong assembly **không** tự vào assembly (lý do giữ Sync rebar) | Nếu 2027 tự add thì cắt Sync rebar | ⬜ |
| G6 | Revit 2027 **không** có auto-reinforcement cho dầm / cột / móng | Giữ hay cắt Rebar Wizard | ⬜ |
| G7 | Revit 2027 **không** có clone / propagate assembly views sang assembly khác | Giữ hay cắt Clone Drawing (nếu cắt thì bộ tool chỉ còn 1 + 3) | ⬜ |
| G8 | `AssemblyViewUtils.*` và `Viewport.Create` chạy ổn trên .NET 8 qua pythonnet (không cần overload đặc biệt) | T1 | ⬜ |

Cách xác minh: model test có 2 cast unit cột giống nhau + 1 khác, mỗi cột có rebar; script ghi log ra `%APPDATA%\T3LabAI\spike_rebar.log`; G6 / G7 rà thêm trong Revit 2027 What's New.

---

## 8. Checklist QA trên Revit 2027 cho mỗi tool (bổ sung §5 new-tool-standard)

```
[ ] Chọn element rời → chạy đúng
[ ] Chọn element trong assembly → rebar sinh ra / đồng bộ là member của assembly (Project Browser + schedule assembly)
[ ] Chọn chính AssemblyInstance → tool hiểu là toàn bộ member hợp lệ
[ ] Element trong Group / từ Link → "skipped: <reason>", không stacktrace
[ ] 2 assembly giống hệt → sau khi chạy vẫn cùng type (hoặc báo rõ số type bị tách)
[ ] Ctrl+Z một lần hoàn tác toàn bộ lần bấm (TransactionGroup assimilate)
[ ] Không có nút nào làm việc Revit đã có (so lại bảng mục 1 trước khi tick)
[ ] Model 2 000+ rebar: thao tác > 2 s có progress, không treo Revit
```

---

## 9. Rủi ro & giới hạn nói trước

- **Clone Drawing T3 không bao giờ 100 %**: dim bám mặt phần tử; đích khác hình thì không có mặt tương ứng → xuất *unmatched* để dim tay phần còn lại. Nhanh hơn làm từ đầu, không hứa "một nút xong".
- **Tách type assembly** là hành vi lõi Revit, chỉ phát hiện và báo (A4).
- **Rebar Wizard** là tool lớn nhất và dễ bị Revit bắt kịp nhất — vì thế để cuối và gác bằng G6.
- **Hiệu năng**: `GetHostId()` cho mọi thanh là O(n); cache map host→rebar một lần mỗi lần mở tool.
- **Ngoài phạm vi**: GA drawing, precast connections / embeds, export sang Tekla / IFC (đã có panel IFC-SG), tính toán kết cấu, mọi thứ ở cột "Revit 2027 có sẵn" của mục 1.

---

## 10. Việc cần quyết định trước khi vào GĐ1

1. Chỉ in-situ hay cả precast? (precast cần thêm embeds / lifting vào cast unit — đề xuất V1 làm chung.)
2. Có model test Revit 2027 sẵn (cột / dầm precast có rebar, 2 cast unit giống nhau) để chạy spike GĐ0 không — chưa thì GĐ0 thêm 1 ngày dựng model test.
3. Trang `docs/tekla-to-revit-2027.md` có viết song ngữ (EN + VI) không, hay chỉ EN như UI?
