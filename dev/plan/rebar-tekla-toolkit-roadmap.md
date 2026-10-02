# Rebar Toolkit cho người dùng chuyển từ Tekla — Phương án xây dựng

> Ngày lập: 2026-10-02 · Sửa lần 3 cùng ngày theo ba ràng buộc của chủ extension:
> **(1) không tool nào trùng / lặp chức năng Revit đã có sẵn; (2) Revit đang dùng là 2027;**
> **(3) người Tekla sang không bị bỡ ngỡ và có đủ chức năng họ cần — hoặc một cách làm tối ưu hơn nhưng gần gũi.**
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

**N3 — Đủ cho người Tekla, bằng ba lớp.** "Không trùng Revit" không có nghĩa là bỏ mặc người dùng tự tìm lệnh.
Mọi bước trong quy trình rebar của Tekla (mục 2) phải rơi vào đúng một trong ba lớp:
- **Lớp 1 — Revit đã có**: tool *không* làm lại, nhưng **Tekla Bridge** (mục 4.0) đưa người dùng tới đúng lệnh
  Revit bằng tên gọi Tekla, kèm một dòng giải thích khác biệt; tài liệu `docs/tekla-to-revit-2027.md` nói rõ
  cách Revit làm việc đó và vì sao đôi khi tốt hơn.
- **Lớp 2 — Revit thiếu**: tool T3Lab lấp đúng khoảng trống (Cast Unit Manager, Clone Drawing, Rebar Check,
  BVBS Export, Rebar Wizard).
- **Lớp 3 — Revit làm khác nhưng tốt hơn**: không bắt chước Tekla; tool/tài liệu chỉ *đặt tên quen* lên cách
  làm của Revit (ví dụ: Tekla đánh số lại cả model, Revit đánh số tự động theo partition — người dùng chỉ cần
  gán partition đúng, việc đó Cast Unit Manager làm theo quy tắc).
Thước đo: bảng mục 2 **không còn ô nào trống** ở cột "Làm ở đâu trong Revit 2027".

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
| *(mới)* Tekla Bridge: bảng lệnh gọi theo tên Tekla → mở lệnh Revit tương ứng | **Không có.** Revit có Keyboard Shortcuts và search lệnh, nhưng không có lớp "tên Tekla → lệnh Revit" | **THÊM** — chỉ điều hướng (`PostCommand`) + tip, không làm lại lệnh nào |
| *(mới)* BVBS Export: file `.abs` (BF2D / BF3D) cho máy uốn | **Không có** trong Revit; Tekla có sẵn, xưởng uốn ở VN / EU nhận BVBS **(xác minh 2027)** | **THÊM** — khoảng trống thật của người Tekla làm precast / thầu rebar |

Kết quả: **8 → 6 tool** (4 làm ngay, BVBS sau khi Clone Drawing xong, Wizard sau cùng), cộng một trang tài liệu và một bộ phím tắt.

---

## 2. Bản đồ quy trình Tekla → Revit 2027 (thước đo "đủ")

> UI tiếng Anh, nhãn dùng **thuật ngữ Revit**; thuật ngữ Tekla đặt trong tooltip dạng `Assembly (Tekla: cast unit)`.
> Bảng này là xương sống của `docs/tekla-to-revit-2027.md` (song ngữ EN/VI) và của danh sách lệnh trong Tekla Bridge.
> Quy tắc: mỗi dòng phải có câu trả lời ở cột "Làm ở đâu"; ô ghi **Lớp 1** = lệnh Revit, **Lớp 2** = tool T3Lab, **Lớp 3** = Revit làm khác / tốt hơn.

### 2.1 Mô hình & cast unit

| Bước Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú cho người Tekla |
|---|---|---|---|
| Tạo part (beam, column, slab, footing) | Structural Framing / Column / Floor / Foundation | 1 | Part ↔ Family instance; profile ↔ Family Type |
| Cast unit (in-situ / precast), main part | Assembly → Create Assembly (Naming Category = main part) | 1 (+2) | **Cast Unit Manager** khi làm hàng loạt hoặc cần tự gom rebar |
| Add / remove part to cast unit | Edit Assembly | 1 | Rebar thêm sau phải add tay → **Sync rebar** (2) |
| Cast unit numbering (prefix + number, cùng hình cùng số) | Revit tự gộp type giống hệt; mark = Assembly Type Name | 3 (+2) | **Rename series** của Cast Unit Manager đặt mark theo prefix/start/step |
| Pour unit / pour break | Parts + Phases | 1 | ngoài phạm vi bộ tool |
| Organizer (lọc theo thuộc tính) | Project Browser Organization + View Filters + Schedules | 3 | mạnh hơn Organizer cho lọc theo view |

### 2.2 Rebar

| Bước Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú |
|---|---|---|---|
| Rebar đơn (Create rebar, polygon) | Structure → Rebar (sketch / place by shape / free form) | 1 | Shape-driven = catalog shape; Free Form = polygon tay |
| Rebar group (spacing, exact number) | Rebar Set + layout rule (Fixed Number / Maximum Spacing / Number with Spacing / Minimum Clear Spacing) | 1 | tên khác, logic giống |
| Rebar mesh | Fabric Sheet / Fabric Area | 1 | |
| Area reinforcement (slab / wall) | Area Reinforcement · Path Reinforcement | 1 | |
| Cover | Rebar Cover Settings + Cover per face (`RebarCoverType`) | 1 | Revit cover là **thuộc tính host**, không phải của thanh |
| Hook / bend radius | Rebar Hook Type · Rebar Bar Type bend diameter | 1 | |
| Splice / coupler / end anchor | Rebar Coupler (coupler family) · lap by overlapping bars | 1 | |
| System component (Beam 63, Column 83, Pad footing 77) | **không có** | 2 | **Rebar Wizard** (sau cùng, G6) |
| Copy special → to another object / Mirror | Copy / Paste Aligned / Mirror; rebar tự nhận host mới | 1 (+2) | rebar dán vào host trong assembly → **Sync rebar** (G5) |
| Rebar visibility / representation | Rebar properties → View Visibility States; View Filters; Rebar Set presentation | 1 | mạnh hơn Tekla: theo từng view |
| Clash check (rebar–rebar, rebar–part) | Collaborate → Interference Check | 1 | |

### 2.3 Đánh số

| Bước Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú |
|---|---|---|---|
| Numbering series per part / assembly | Rebar Partition | 3 (+2) | Revit đánh số **tự động, liên tục** theo partition; chỉ cần gán partition đúng → **Partition by rule** |
| Numbering settings / renumber / remove gaps | Reinforcement Numbering | 1 | |
| Kiểm tra số trùng / chưa đánh số | **không có** | 2 | **Rebar Check** |

### 2.4 Bản vẽ

| Bước Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú |
|---|---|---|---|
| Cast unit drawing (view set + sheet) | Assembly → Create Views (+ View Template, titleblock) | 1 | |
| Clone drawing | **không có** | 2 | **Clone Drawing** |
| GA drawing | Sheet + views thường | 1 | ngoài phạm vi |
| Rebar marks / pull-out picture / dimension | Rebar Tag · Multi-Rebar Annotation · Rebar Bending Detail · Dimension | 1 | |
| Drawing list | Sheet List schedule · cột *Views/Sheets* của Cast Unit Manager | 1 (+2) | |
| Drawing not up-to-date flag | Revit view luôn live với model | 3 | khái niệm "update drawing" không tồn tại |

### 2.5 Báo cáo & xuất

| Bước Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú |
|---|---|---|---|
| Bending schedule / rebar list | Rebar Schedule (Shape, A–F, Total Bar Length, Quantity) + Bending Detail | 1 | |
| Weight report theo Ø | Schedule + shared parameter `T3_WeightPerMetre` trên Rebar Bar Type + calculated field — **template kèm docs** | 1 | không cần tool |
| Cast unit list | Assembly schedule / Part List / Material Takeoff | 1 | |
| BVBS (`.abs`) cho máy uốn | **không có** | 2 | **BVBS Export** (G9) |
| IFC | Export IFC (panel IFC-SG đã có) | 1 | |
| Unitechnik / PXML (precast machine) | không có | — | **ngoài phạm vi** V1, ghi nhận nhu cầu |

### 2.6 Thao tác & môi trường

| Thói quen Tekla | Làm ở đâu trong Revit 2027 | Lớp | Ghi chú |
|---|---|---|---|
| Phím tắt Tekla | Keyboard Shortcuts (Import XML) | 1 (+content) | bộ `KeyboardShortcuts_Tekla.xml` do T3Lab cung cấp, import bằng dialog Revit |
| Tìm lệnh theo tên Tekla | **không có** | 2 | **Tekla Bridge** |
| Attribute file (lưu thiết lập component) | Preset JSON của Rebar Wizard · View Template · Type | 3 | |
| Phase manager | Phases / Worksets (`ManaWorkset`) | 1 | |

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

## 4. Bộ tool sau rà soát (6 tool)

| # | Tool | Khoảng trống Revit 2027 mà nó lấp | Pattern | Size | Sửa model | Ưu tiên |
|---|---|---|---|---|---|---|
| 0 | **Tekla Bridge** (`TeklaBridge`) | Gọi lệnh Revit theo tên Tekla + tip khác biệt; lối vào các tool 1–5 | P2 | S | Không | P1 (rẻ, làm cùng GĐ1) |
| 1 | **Cast Unit Manager** (`CastUnit`) | Tạo assembly hàng loạt có tự gom rebar · Sync rebar · rename series · gán partition theo quy tắc · trạng thái bản vẽ | P2 + P4 | L | Có | P1 |
| 2 | **Clone Drawing** (`CloneDrawing`) | Nhân bản bản vẽ assembly mẫu sang assembly khác | P2 + P3 + P5 | L | Có (view / sheet) | **P1 — trọng tâm** |
| 3 | **Rebar Check** (`RebarCheck`) | Kiểm tra rebar / assembly ở mức dữ liệu | P4 | L | Không (select / isolate; Fix chỉ gọi 1 và 2) | P2 |
| 4 | **BVBS Export** (`BVBSExport`) | File `.abs` BF2D / BF3D cho máy uốn | P2 + P4 | M | Không | P2 |
| 5 | **Rebar Wizard** (`RebarWizard`) | System component dầm / cột / móng | P1 + P5 | L | Có | P3, sau cùng |

### 4.0 Tekla Bridge — `TeklaBridge.pushbutton`

Lớp "không bỡ ngỡ". Một cửa sổ nhỏ (S, NoResize), ô tìm kiếm + danh sách lệnh **đặt tên theo Tekla**,
nhóm theo mục 2.1–2.6. Mỗi dòng: tên Tekla · tên Revit · một dòng "What is different" · nút *Open*.
- *Open* với lệnh Revit: `UIApplication.PostCommand(RevitCommandId.LookupPostableCommandId(PostableCommand.X))`
  — cửa sổ đóng rồi mới post (PostCommand chạy sau khi lệnh hiện tại kết thúc). Lệnh không có trong
  `PostableCommand` thì hiện đường dẫn ribbon + tip, không giả lập.
- *Open* với tool T3Lab: chạy pushbutton tương ứng.
- Nguồn dữ liệu: `lib/data/tekla_bridge.json` sinh từ bảng mục 2 (một nguồn, docs và tool không lệch nhau).
- Tuỳ chọn *Show tip once*: lần đầu mở một lệnh Revit từ Bridge thì hiện tip, lần sau mở thẳng.
- Không sửa model; không phụ thuộc document (mở được khi chưa có model).


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

### 4.4 BVBS Export — `BVBSExport.pushbutton`

Khoảng trống thật của người Tekla làm precast hoặc thầu rebar: máy uốn nhận BVBS (`.abs`), Revit không xuất.
- Phạm vi: toàn model / theo assembly / theo partition / selection; lọc shape-driven; free-form 2D phẳng xuất
  BF2D nếu tách được đoạn thẳng + cung, còn lại báo *skipped: free-form 3D*.
- Mỗi thanh (hoặc mỗi vị trí trong set khi set biến thiên): block `BF2D` với header `H` (project, mark =
  partition + number, Ø, số lượng `n`, tổng dài `l`, trọng lượng `w` từ `T3_WeightPerMetre` hoặc bảng mặc định),
  geometry `G` (đoạn `l`, góc `w`, bán kính uốn `r` từ bar type) và checksum `C` theo đặc tả BVBS 2.0.
- Dữ liệu hình: `Rebar.GetCenterlineCurves(adjustForSelfIntersection, suppressHooks, suppressBendRadius, multiplanarOption, barPositionIndex)`; đoạn thẳng → `Line`, bend → `Arc`; hook tách thành đoạn cuối + góc.
- Bảng preview trước khi ghi: `Mark | Ø | n | l | Shape | Status`; xuất một file cho mỗi assembly hoặc một file gộp.
- Kiểm chứng: đọc lại file bằng viewer BVBS miễn phí (hoặc `dev/test_bvbs_writer.py` so với mẫu chuẩn BF2D trong đặc tả) — không "xuất xong" khi chưa đọc lại được.

### 4.5 Rebar Wizard — `RebarWizard.pushbutton` (sau cùng, xác minh 2027 trước)

- Beam: thanh trên / dưới, đai 3 vùng, cover, hook · Column: thanh dọc, đai + đai phụ, vùng đai dày · Pad footing: lưới hai lớp · Wall / Slab: `AreaReinforcement`.
- `Rebar.CreateFromCurves` / `CreateFromRebarShape` + `RebarShapeDrivenAccessor.SetLayoutAs*`; mọi thanh add vào assembly của host (A2).
- Preset JSON `%APPDATA%\T3LabAI\rebar_wizard_presets.json` — tương đương lưu attribute file trong Tekla.
- V1: tiết diện chữ nhật, host thẳng; ngoài phạm vi báo rõ.

---

## 5. Giai đoạn thực hiện

| GĐ | Nội dung | Deliverable | Ước lượng |
|---|---|---|---|
| **0 · Spike** | Trên Revit 2027: xác minh G1–G8 (mục 7) bằng `dev/debug/spike_rebar_assembly.py`; đồng thời rà "What's New 2027" cho hai mục **(xác minh)** ở mục 1 | mục 7 tick ✅ / ❌; danh sách tool chốt | 2 ngày |
| **1 · Nền + Bridge** | `lib/Snippets/_assembly.py`, `_rebar.py`, `_drawing_clone.py` (+ `_compat` guard); test thuần Python; panel + icon theo chuẩn 09; `lib/data/tekla_bridge.json` + `docs/tekla-to-revit-2027.md` (EN/VI) sinh từ bảng mục 2; **Tekla Bridge** (tool 0); bộ `KeyboardShortcuts_Tekla.xml` | helper + test xanh + Bridge chạy được + docs | 3 ngày |
| **2 · Cast Unit Manager** | Tool 1 | QA Revit 2027 | 3 ngày |
| **3 · Clone Drawing** | T1 → T2 → T3 | Tool 2, log unmatched | 6–8 ngày |
| **4 · Rebar Check + BVBS** | Tool 3, tool 4 (`dev/test_bvbs_writer.py` so với mẫu chuẩn) | QA Revit 2027 + file đọc lại được bằng viewer BVBS | 4 ngày |
| **5 · Wizard** | Tool 5 (V1 dầm + cột chữ nhật) nếu GĐ0 xác nhận Revit 2027 chưa có | mở roadmap riêng khi tới | 6+ ngày |

Mỗi GĐ kết thúc bằng: 4 gate (`audit_t3`, `audit_tools`, `audit_wiring`, `audit_revit_compat`) xanh + `audit_cpython` 0 P0 + `check_xaml_load` / `check_xaml_wpf.ps1` 0 FAILED + checklist mục 8 trên Revit 2027. Chưa QA thì ghi `NEEDS VERIFICATION`, không tick.

---

## 6. Cấu trúc file dự kiến

```
T3Lab.extension/
├── T3Lab_Dev.tab/Rebar & Assembly.panel/
│   ├── bundle.yaml                 # layout: TeklaBridge · CastUnit · CloneDrawing · RebarCheck · BVBSExport · RebarWizard
│   ├── TeklaBridge.pushbutton/
│   ├── CastUnit.pushbutton/
│   ├── CloneDrawing.pushbutton/
│   ├── RebarCheck.pushbutton/
│   ├── BVBSExport.pushbutton/
│   └── RebarWizard.pushbutton/     # GĐ5
├── lib/GUI/Tools/   TeklaBridge.xaml · CastUnit.xaml · CloneDrawing.xaml · RebarCheck.xaml · BVBSExport.xaml · RebarWizard.xaml
├── lib/GUI/         TeklaBridgeDialog.py · CastUnitDialog.py · CloneDrawingDialog.py · RebarCheckDialog.py · BVBSExportDialog.py · RebarWizardDialog.py
├── lib/data/
│   ├── tekla_bridge.json           # bảng mục 2: tên Tekla → PostableCommand / tool T3Lab / tip (một nguồn cho tool + docs)
│   ├── rebar_weights.json          # kg/m mặc định theo Ø (TCVN / BS) khi bar type chưa có T3_WeightPerMetre
│   └── KeyboardShortcuts_Tekla.xml # import qua Keyboard Shortcuts của Revit
└── lib/Snippets/
    ├── _assembly.py        # collect, validate, batch create, sync rebar, rename series, views/sheets, center/transform
    ├── _rebar.py           # host→rebar map (cache 1 lần / lần mở tool), partition by rule, fingerprint, centerline → segments
    ├── _drawing_clone.py   # view-set replication (T1), annotation copy (T2), reference re-create (T3)
    └── _bvbs.py            # BF2D/BF3D writer thuần Python (không import Revit) — test được ngoài Revit
dev/
├── debug/spike_rebar_assembly.py   # GĐ0, chạy trong pyRevit console
├── build_tekla_docs.py             # sinh docs/tekla-to-revit-2027.md từ lib/data/tekla_bridge.json (--check)
├── test_assembly_rules.py          # A1–A8 trên object giả, không cần Revit
├── test_rebar_fingerprint.py       # khớp mẫu ↔ đích, tolerance, mirror
├── test_bvbs_writer.py             # block BF2D + checksum so với mẫu trong đặc tả BVBS 2.0
└── plan/rebar-tekla-toolkit-roadmap.md
docs/tekla-to-revit-2027.md         # sinh tự động: mỗi bước Tekla → lệnh Revit / tool T3Lab / vì sao Revit làm khác
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
| G9 | Revit 2027 **không** xuất BVBS (`.abs`) native | Giữ hay cắt BVBS Export | ⬜ |
| G10 | `PostCommand` + `PostableCommand` có đủ các lệnh rebar / assembly cần cho Tekla Bridge (Create Assembly, Create Views, Reinforcement Numbering, Rebar, Rebar Set, Area/Path Reinforcement, Interference Check); lệnh thiếu thì Bridge chỉ hiện đường dẫn ribbon | Phạm vi nút *Open* của Bridge | ⬜ |

Cách xác minh: model test có 2 cast unit cột giống nhau + 1 khác, mỗi cột có rebar; script ghi log ra `%APPDATA%\T3LabAI\spike_rebar.log`; G6 / G7 / G9 rà thêm trong Revit 2027 What's New; G10 liệt kê `PostableCommand` bằng reflection trong cùng script.

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
[ ] Người chưa biết Revit làm xong một quy trình Tekla (mục 2) chỉ bằng Tekla Bridge + docs, không phải hỏi
[ ] Model 2 000+ rebar: thao tác > 2 s có progress, không treo Revit
```

---

## 9. Rủi ro & giới hạn nói trước

- **Clone Drawing T3 không bao giờ 100 %**: dim bám mặt phần tử; đích khác hình thì không có mặt tương ứng → xuất *unmatched* để dim tay phần còn lại. Nhanh hơn làm từ đầu, không hứa "một nút xong".
- **Tách type assembly** là hành vi lõi Revit, chỉ phát hiện và báo (A4).
- **Rebar Wizard** là tool lớn nhất và dễ bị Revit bắt kịp nhất — vì thế để cuối và gác bằng G6.
- **Hiệu năng**: `GetHostId()` cho mọi thanh là O(n); cache map host→rebar một lần mỗi lần mở tool.
- **Tekla Bridge dễ thành "Revit bọc lại"** nếu thêm nút làm việc thay lệnh Revit: Bridge chỉ được *mở lệnh và giải thích*; mọi nút khác là vi phạm N1.
- **BVBS**: cung hook / bend radius sai một chút là máy uốn ra sai thanh — V1 chỉ shape-driven + kiểm chứng file bằng viewer, free-form 3D để sau.
- **Ngoài phạm vi**: GA drawing, precast connections / embeds, export sang Tekla / IFC (đã có panel IFC-SG), tính toán kết cấu, mọi thứ ở cột "Revit 2027 có sẵn" của mục 1.

---

## 10. Việc cần quyết định trước khi vào GĐ1

1. Chỉ in-situ hay cả precast? (precast cần thêm embeds / lifting vào cast unit — đề xuất V1 làm chung.)
2. Có model test Revit 2027 sẵn (cột / dầm precast có rebar, 2 cast unit giống nhau) để chạy spike GĐ0 không — chưa thì GĐ0 thêm 1 ngày dựng model test.
3. Trang `docs/tekla-to-revit-2027.md`: đề xuất **song ngữ EN/VI** (docs không bị luật "UI tiếng Anh"); Tekla Bridge trong Revit chỉ EN.
4. BVBS: xưởng uốn của anh / khách hàng nhận chuẩn nào (BVBS 2.0 BF2D là phổ biến nhất; BF3D cho thanh 3D; có nơi đòi thêm `BFMA` cho lưới)? Quyết định phạm vi V1 = BF2D.
5. Bộ phím tắt Tekla: anh gửi danh sách phím anh hay dùng nhất (10–20 phím) để lập `KeyboardShortcuts_Tekla.xml`; không tự bịa keymap.
