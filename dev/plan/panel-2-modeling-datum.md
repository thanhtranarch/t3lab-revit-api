# Panel 2 — Modeling & Datum (Ngày 6–7)

> 14 tool — panel lớn nhất, chia 2 ngày. Model test: file có room, wall (thẳng + cong), floor, door, level, grid; chuẩn bị sẵn 1 file DWG mẫu, 1 ảnh PNG đen trắng, 1 file point (nếu có).

---

## Ngày 6 — Nhóm Create (7 tool)

### Tool 1/7 — CADToElements
Chain: launcher → `CADToElementsDialog.py` (3143+ loc) → `CADToElements.xaml` + 4 XAML con (CADtoBeam / CadtoFloor / CadtoFloorLayerItem / CadtoWall)

- [x] Mở cửa sổ chính + từng flow con (Wall / Floor / Beam) không lỗi XAML — UI đã sửa lại toàn bộ (sidebar rail + logo, search bar pill, layout card đồng bộ 3 tab, chiều cao cửa sổ) trong phiên debug 2026-07-03
- [x] Import DWG mẫu → convert Wall theo layer → wall tạo đúng vị trí/đơn vị — user xác nhận "tool ok r" sau khi sửa
- [x] Convert Floor (có layer item list — CadtoFloorLayerItem variant B load đúng)
- [x] Convert Beam
- [ ] DWG không có layer khớp → thông báo, không crash — chưa test riêng edge case này
- [ ] Ctrl+Z sau mỗi convert → revert 1 bước — chưa test riêng
- [x] Ghi chú: **Loạt bug functional phát hiện + sửa trong phiên 2026-07-03** (ngoài phạm vi checklist gốc, ghi lại để tham khảo):
  1. `extract_lines_from_cad()` gọi sai 4 tham số / mong 4 giá trị trả về trong khi hàm chỉ nhận 3 tham số, 1 giá trị — sửa lại đúng chuỗi `extract_lines_from_cad()` → `merge_collinear_lines()` → `find_parallel_pairs()`.
  2. `get_cad_layers_wall()`/`get_cad_layer_geometry_floor()` chỉ liệt kê layer có geometry phát hiện được (dò 1 lớp GeometryInstance, bỏ sót layer rỗng/lồng block) — đổi Wall sang liệt kê toàn bộ qua `Category.SubCategories` (giống Beam vốn đã đúng); Floor giữ quét geometry nhưng bổ sung layer rỗng vào danh sách.
  3. `extract_lines_from_cad()` áp `Transform` 2 lần (double-transform) cho block không lồng — gây tường/dầm bị lệch vị trí khi CAD import có transform khác Identity (xoay/lệch theo toạ độ site) — bỏ áp transform thủ công, chỉ đệ quy gọi `GetInstanceGeometry()`.
  4. `create_walls_auto()` không kiểm tra status trả về của `Transaction.Commit()` — nếu Revit tự rollback mà không throw exception, hàm vẫn báo "Created: N" giả — đã thêm kiểm tra status, báo trung thực.
  5. Beam: `DB.BuiltInParameter.STRUCTURAL_BEAM_Z_OFFSET_VALUE` không tồn tại (tên enum sai) → crash ngay dầm đầu tiên, rollback toàn bộ, 0 dầm được tạo — sửa thành `Z_OFFSET_VALUE` (đúng tên chuẩn Revit API), sửa ở cả bản mới và class legacy CadtoBeam (dòng ~2198).
  6. Thêm tính năng mới **Create Mode: Wall/Beam thật ↔ Part (DirectShape)** cho cả Wall và Beam (trước đó chỉ Floor có), dùng chung `create_part_from_loop()` + helper mới `build_rect_loop_from_centerline()`.
  7. Beam layer picker đổi từ ComboBox chọn 1 layer → bảng checklist đa chọn (Select All/Clear/search) giống Wall/Floor, `_run_beam()` gộp kết quả nhiều layer vào 1 dialog.
  8. UI: xoá hàng thống kê Floor (4 ô Total/Selected/Closed Loops/Created — không dùng), xoá box "Tip" thừa ở Beam, đồng bộ `MinHeight` card settings giữa 3 tab, tắt scrollbar khu vực content tổng (chỉ còn scrollbar trong bảng CAD Layers).
  - **Còn nợ, chưa xác nhận trong Revit**: Ctrl+Z revert 1 bước; edge case DWG không có layer khớp; Floor's `get_cad_layer_geometry_floor()` vẫn có bug đệ quy 1-lớp giống Wall từng có (đã tách thành task riêng `task_5c67d3b1`, chưa làm).

### Tool 2/7 — DoorThreshold
Chain: self-contained (356 loc, **3 bare except**) → `DoorThreshold.xaml`

- [x] Trước khi test: tạm thêm traceback log vào 3 nhánh `except:` để lỗi không bị nuốt — thay bằng `logger.error` + gom `error_messages` hiện trực tiếp trong TaskDialog kết quả (không cần mở log riêng)
- [x] Door trên wall thường → threshold tạo đúng — user xác nhận "đã xong" 2026-07-04 sau 2 vòng fix (xem Ghi chú)
- [ ] Door trên curtain wall / wall nghiêng → hành vi hợp lý — chưa test
- [ ] Thiếu family threshold trong model → thông báo rõ ràng — chưa test
- [x] Ghi chú: **Bug fix 2026-07-04** (phát hiện qua smoke test user, không nằm trong checklist gốc):
  1. **Sai kích thước ban đầu**: threshold không khớp đúng bề rộng cửa/độ dày tường khi tường có nhiều lớp (compound) hoặc bị tường khác join vào — do dùng `wall.Width` (giá trị nominal cố định của loại tường) thay vì đo thực tế tại vị trí cửa. Sửa: thêm `_get_wall_thickness_at_point()` — chiếu điểm cửa lên mặt ngoài/trong của tường (`HostObjectUtils.GetSideFaces` + `Face.Project`), đo khoảng cách thật tại đúng vị trí đó, fallback về `wall.Width` nếu không lấy được mặt (ví dụ Stacked Wall/Curtain Wall).
  2. **UI**: theo yêu cầu user, bỏ hẳn 2 ô nhập tay "Width Extension (mm)" / "Depth Extension (mm)" — vì kích thước giờ tự động khớp chính xác, không cần chỉnh tay. Chỉ còn "Height Offset (mm)".
  3. **Bug thứ 2 lộ ra sau khi test thật**: `Successfully created 0 thresholds, 4 errors` — nguyên nhân là `BuiltInParameter.DOOR_WIDTH` và `Symbol.FAMILY_WIDTH_PARAM` đều trả về `0` cho family cửa đang dùng (width không map vào 2 param chuẩn này). Sửa: thêm `_get_door_width_ft()` — thử `DOOR_WIDTH` → `FAMILY_WIDTH_PARAM` → `LookupParameter("Width")` (instance rồi symbol) → cuối cùng fallback đo trực tiếp bounding box của instance chiếu lên trục `HandOrientation` (đúng bất kể family map tham số kiểu gì).
  4. User xác nhận kết quả đúng sau khi áp cả 2 fix (width + thickness) — nhưng **mới test 1 kịch bản** (cửa trên tường thường/compound). Curtain wall, wall nghiêng, model thiếu family threshold chưa qua kiểm.

### Tool 3/7 — ImageToDrafting
Chain: self-contained (1585 loc) → `ImageToDrafting.xaml` · **compile C# Vectorizer lúc runtime**

- [ ] Ảnh PNG đen trắng nhỏ (~500px) → mode Centerline → drafting lines tạo ra
- [ ] Mode Outline (không cần compile C#) → hoạt động
- [ ] Xác nhận compile C# thành công trên máy triển khai (không chỉ máy dev) — nếu fail phải ra alert "Failed to compile C# vectorizer" chứ không crash
- [ ] Ảnh màu / ảnh lớn → threshold tự động hợp lý, không treo Revit
- [x] Ghi chú: **UI fix 2026-07-03** (chưa test functional ở trên, chỉ sửa UI): dropdown "Tracing Mode"/"Threshold Mode" thiếu style (`ComboBox` trần) → gán `T3ComboBoxSmall`; làm lại khu vực Drag & Drop (border xanh đặc `#3B82F6` → nét đứt xám chuẩn drop-zone, icon emoji 📎 → vector Path); xoá icon "user profile" thừa ở title bar; rút gọn subtitle.

### Tool 4/7 — Text to Element
Chain: launcher → `TextToElementDialog.py` (466 loc) → `TextToElement.xaml`

- [ ] Text note hợp lệ → element tạo đúng
- [ ] Text rác / rỗng → validate, không crash
- [x] Ghi chú: **UI fix 2026-07-03** (chưa test functional ở trên, chỉ sửa UI): nút min/max/close bị sai vị trí (`VerticalAlignment="Top"` không margin → dính sát góc bo tròn cửa sổ), sửa về `Center` + `Margin="0,0,16,0"` đúng chuẩn canonical; nội dung vượt quá chiều cao cửa sổ (620px, cần ~870px) luôn phải cuộn → tăng `Height` 620→880, `MinHeight` 520→750, ẩn scrollbar (`Hidden`, vẫn cuộn được bằng chuột).

### Tool 5/7 — PointCloud
Chain: self-contained (1502 loc, 6 transaction) → `PointCloud.xaml`

- [ ] File điểm nhỏ hợp lệ → import/xử lý đúng
- [ ] File sai format → thông báo lỗi rõ
- [ ] Cancel giữa chừng → transaction rollback sạch (kiểm tra Undo stack không có rác)
- [ ] Ghi chú: **Fix lớn 2026-07-17** — Analyze chưa từng chạy được: `CreateMultiPlaneFilter(points, normals)` sai signature (API thật nhận `IList<Plane>` — đã verify trên Revit 2026.4), filter + điểm trả về của `GetPoints` phải ở hệ tọa độ local của cloud (đã thêm transform 2 chiều qua `GetTotalTransform`), `PointCollection` giờ được copy + Dispose ngay (tránh access-violation 0xc0000005). Viết lại wall detection theo axis-sweep (tường không còn vỡ do ring cluster ở góc), sửa `find_histogram_peaks` bỏ sót bin cuối (trần không bao giờ detect được), sửa logic void của opening (trước đo phần tường còn lại thay vì phần trống → cửa bị phân loại sai/bỏ sót), opening giờ detect bằng dải z 1.0–2.0m trên chân tường (bắt được cả cửa sổ). Openings host vào đúng wall tương ứng; build_all bọc TransactionGroup (1 undo); roof dùng `clr.Reference` cho out param. Đã test thuật toán bằng synthetic room (8×5×3m, cửa đi 900×2100, cửa sổ 1200×1500) trên cả CPython lẫn IronPython-in-Revit: 4 walls + floor + ceiling + door + window đúng, 0 phantom. **2026-07-17 (tiếp):** 2 crash tiếp theo của user trên **Revit 2023** là bug engine ReCap 2023 với dataset Victoria Point (0xe0434352 trong AdskRcPointCloudEngine.dll — crash cả khi Revit tự snap, không do code tool; file local đầy đủ, không phải OneDrive placeholder). Đối sách: trích xuất tiled 3×3 + kẹp averageDistance 1.5–100mm; pick vùng đổi từ PickPoint (kích hoạt snap engine → crash) sang **PickBox** (rubber-band, không snap); sửa Dispatcher.Invoke sai overload làm status bar đứng im. **Đã test end-to-end với cloud Victoria thật trên Revit 2026** (transaction rollback): 12.110 điểm/0.4s, analyzer 0.7s → 8 walls + floor + ceiling + 2 doors + 2 windows. Khuyến nghị: scan-to-BIM cloud này trên Revit 2026; engine 2023 crash ngoài tầm can thiệp của tool. **Crash #3 (journal 1036, 09:07):** PickBox cũng chết y hệt PickPoint (1s sau Hide, chưa vào pick loop) → trên 2023.1 + cloud này, *mọi* handoff tương tác về view chứa cloud đều AV. Đối sách cuối: thêm nút **Use View Crop** (đọc `view.CropBox` thuần API, không đụng selection mode) + TaskDialog cảnh báo save trước khi Analyze trên Revit ≤2023. **ROOT CAUSE thật (journal 1037, dòng "API External Command Time"):** `Hide()` trên cửa sổ ShowDialog làm ShowDialog return → command Execute kết thúc → cửa sổ zombie → mọi click sau chạy API ngoài context → 0xe0434352. Sửa kiến trúc 09:26: nút pick đóng cửa sổ, main loop pick trong context rồi mở lại cửa sổ với state. **Chunking (cùng ngày):** tiling thích ứng theo kích thước vùng (ô ~8m, 2×2–6×6) + progress từng ô trên status bar; cô lập từng detector (1 stage lỗi không sập cả phân tích); chỉ mục không gian 1m cho detect_openings/detect_columns (hết quét O(walls×cloud) gây treo UI). Regression synthetic giữ nguyên kết quả. Crash 11:57/13:27 là của BatchOut (task riêng đã tạo). **Scope cuối (yêu cầu user):** dựng toàn bộ KIẾN TRÚC + KẾT CẤU (Wall/Floor/Ceiling/Door/Window/Column/Stair/Roof), KHÔNG dựng MEP. Bộ lọc anti-MEP: (1) tường cần tall-evidence 2.2m→dưới trần ở NỬA GIỮA đoạn (loại kệ/rack/MEP thấp, kể cả khi kệ bắc cầu vào mảnh tường góc); (2) cột: cross-section ≥200mm (loại ống), aspect ≤2.5 (loại duct dẹt), cao ≥2.2m, và phải có điểm liên tục ở dải 1.9–2.6m (loại "cột ảo" do điểm sàn+trần lọt vào cửa sổ XY của góc kệ). Test synthetic đủ 8 case: 4 tường + sàn + trần + cửa đi + cửa sổ + cột thật 300×300 nhận đúng; kệ 4m×1.8m và ống đứng 150mm bị loại sạch. **Bộ lọc nội thất bổ sung (yêu cầu user, cùng ngày):** (3) slab trung gian phải phủ ≥30% footprint tính riêng phần NỘI THẤT (loại điểm trên tường đã detect — mặt bàn/quầy lớn không thành sàn ảo); (4) occlusion guard cho openings: lỗ trên tường có vật chắn đứng trước (0.15–1.2m, phủ ≥50% bề rộng VÀ ≥50% chiều cao lỗ) = bóng che scanner, loại — tủ cao veto đúng bóng của nó, bàn thấp trước cửa thật không veto oan; (5) tall-evidence tường cần ≥3 mức cao độ khác nhau (mép nóc tủ 1 đường không được tính). Test 11 case pass: 4 walls + floor + ceiling + door + window + column đúng; kệ 1.8m / tủ 2.2m / bóng tủ trên tường / bàn 4×1.5m @750mm / ống 150mm đều bị loại.

- **Nghiên cứu phát triển toàn diện + chống crash (2026-08-14)** — roadmap riêng:
  `dev/plan/pointcloud-scan-to-bim-roadmap.md`. Phân tầng 5 nguồn crash (native
  ReCap · API context · bão dialog cảnh báo · UI thread · null doc); đóng thêm
  3 tầng trong session này. **Bug thật mới phát hiện:** band-merge tolerance
  trong `detect_walls` chỉ gộp qua khe ≤2 bin (100 mm), nên tường quét từ hai
  phía (hai mặt điểm + lõi rỗng) bị tách thành **2 tường song song**, mỗi nửa
  đo bề dày ≈0 → clamp 75 mm → `_best_wall_type` **luôn chọn wall type mỏng
  nhất project cho mọi tường**, và opening bị nhân đôi. Sửa: tolerance theo
  `max_thickness` (16 bin) — synthetic 8×5×3 m đi từ 8 tường/75 mm/2 cửa về
  **4 tường/200 mm/1 cửa**. Thêm `WarningSwallower` (interface được import từ
  đầu nhưng chưa bao giờ implement, dù 8 tool khác trong repo đều có),
  `resolve_doc()` (tool chết ở `__init__` khi mở từ Assistant pane), cache
  Level, và Stair báo trung thực (trước đây đếm "N Stairs created" trong khi
  `build_stair_marker` là stub trả None). **Test mới:**
  `dev/test_pointcloud_detect.py` — 24 assert chạy không cần Revit (exec
  Section 1–5 với stub Revit). Trước đó 2.296 loc detection math không có test
  nào được commit. Việc còn lại ưu tiên #1: **Cancel + chunked analysis** (UI
  thread starvation → user kill Revit).

### Tool 6/7 — RoomToFloor
Chain: self-contained (383 loc) → `RoomToFloor.xaml`

- [ ] Room chữ nhật → floor đúng boundary, offset mm đúng
- [ ] Room có cột giữa (island) → floor có opening (test cả Revit <2022 nếu dùng: 2 nhánh code theo REVIT_VERSION)
- [ ] Room chưa placed / area = 0 → skip đếm vào error_count, không crash
- [ ] Toàn bộ batch = 1 TransactionGroup → Ctrl+Z 1 lần revert hết
- [ ] Ghi chú:

### Tool 7/7 — Tile Layout
Chain: self-contained (2450 loc) → `TileLayout.xaml`

- [ ] Layout tile trên floor nhỏ → kết quả đúng kích thước/góc
- [ ] Đổi origin/rotation → cập nhật đúng
- [ ] ⚠️ Script không gọi Transaction trực tiếp — xác định wrapper nào ghi model (revit.Transaction? helper?) và Ctrl+Z hoạt động đúng
- [x] Ghi chú: **Bug path + UX fix 2026-07-03**:
  1. **Tool không mở được** — `EXT_DIR` trong `script.py` chỉ đi lên 3 cấp thư mục (`os.path.dirname` x3) thay vì 4 cấp cần thiết (`T3Lab.extension/T3Lab.tab/Modeling & Datum.panel/Create.stack/Tile Layout.pushbutton/`), dừng ở `T3Lab.tab` thay vì `T3Lab.extension` → `IOError: Could not find a part of the path ...T3Lab.tab\lib\GUI\Tools\TileLayout.xaml`. Đã sửa thành x4. **Quét toàn bộ codebase phát hiện thêm 3 tool khác cũng bị lỗi tương tự (chiều ngược lại — thừa 1 cấp): MCPControl, ManaSheets, ManaViews — đã sửa cả 3 (xem Support.panel/Views & Sheets.panel).**
  2. **UX**: trước đây `run()` luôn ép pick floor (hoặc bắt buộc có sẵn selection) ngay khi mở tool, chưa mở được wizard nếu chưa pick gì. Tách thành `_initial_floors()` (mở tool lần đầu → tự động liệt kê toàn bộ Floor trong model qua `FilteredElementCollector`, không cần pick) và `_pick_floors_from_revit()` (chỉ dùng cho nút "Pick Floors in Model" — vẫn pick thủ công như cũ).
  3. **UI**: xoá icon "user profile" thừa ở title bar, chỉnh margin nút min/max/close về 16px chuẩn.
  - **Còn nợ, chưa test functional**: layout tile kích thước/góc, đổi origin/rotation, Ctrl+Z qua transaction wrapper.
  4. **UI (2026-07-03, theo yêu cầu user)**: chuyển step-progress từ thanh ngang (Row 1, 3 vòng tròn số) sang **sidebar trái 66px** — theo đúng pattern sidebar rail của BatchOut (`ExportManager.xaml`, UI-locked, chỉ dùng làm tham chiếu đọc) và CADToElements: logo 42×42 trên cùng, 3 icon `ToggleButton` (`T3SidebarToggle`, đã có sẵn trong shared style) xếp dọc thay 3 vòng tròn ngang. Xoá style `StepCircle`/`StepLabel` không còn dùng. Code-behind: thêm `self._max_step` theo dõi bước xa nhất đã tới, `step_nav_clicked()` cho phép bấm quay lại bước đã hoàn thành (không cho nhảy vượt bước chưa tới), `_refresh_step_ui()` viết lại để đồng bộ `IsChecked`/`IsEnabled` 3 toggle thay vì set màu tay theo circle cũ. Đã pass `audit_tools`/`audit_ui`/`sync_wpf_styles --check` + xml well-formed + python ast parse. **Chưa test trực quan trong Revit.**

### Chốt ngày 6
- [ ] Cập nhật bảng trạng thái + README · Commit nếu có fix

---

## Ngày 7 — Nhóm Adjust / Family / Datum (7 tool)

### Tool 8/14 — PropertyLine
Chain: launcher → `PropertyLineDialog.py` (1727 loc) → `PropertyLine.xaml`

- [ ] Nhập bảng toạ độ hợp lệ → property line tạo đúng
- [ ] Sai đơn vị / toạ độ không khép kín → validate rõ ràng
- [ ] Ghi chú:

### Tool 9/14 — FamiGen
Chain: launcher → `FamiGenDialog.py` (2360 loc) → `FamiGen.xaml` · tạo family document riêng

- [ ] Gen 1 family đơn giản → load vào project OK
- [ ] Huỷ giữa chừng → family document tạm được **đóng sạch** (kiểm tra không còn doc mở ngầm)
- [ ] Template family thiếu → thông báo
- [x] Ghi chú: **Cải tiến sub-mode "From JSON" 2026-07-04** (ngoài checklist smoke-test gốc, chỉ động vào `SYSTEM_PROMPT.md` — parser `FamiGenDialog.py` không đổi):
  - **WS2 de-bias**: gỡ toàn bộ ví dụ nghiêng về đèn (Lotus lamp → Ceiling Medallion; wall-lamp arm → generic tube; part-inventory wall lantern → wall-mounted faucet). Prompt phải dùng được cho **mọi** loại family theo yêu cầu user.
  - **WS3**: thêm recipe ống gấp khúc/vuông góc = các đoạn Extrusion-of-Circle theo trục chồng ~2mm ở khuỷu (thay Sweep cong dễ gãy vì parser chỉ hỗ trợ mặt phẳng X/Y/Z).
  - **+7 ví dụ** (E8–E14) → tổng **14 ví dụ**, đủ 4 form type + 6 curve type, trải Furniture/Generic/Casework/Door. Đã validate 14/14 block JSON hợp lệ.
  - Roadmap + backlog (WS4 cảnh báo part rời, WS5 mặt phẳng bất kỳ) + **kế hoạch tách prompt theo category**: `dev/plan/famigen-from-json-roadmap.md`.
  - **WS1 + WS6 (2026-07-04, cùng ngày)**: user test lại → kết quả **tệ hơn** (mọi part dựng được nhưng rời hoàn toàn — riser lơ lửng, arm không chạm chao). Sửa prompt: bắt buộc 1 anchor part + key `"attaches_to"` từng part + key root `"_plan"` (bảng inventory/height/connection — parser bỏ qua key lạ, chỉ để ép AI suy luận); kiểm tra overlap 3 trục bằng số; cấm part mồ côi; cấm copy tọa độ literal từ ví dụ (nghi bleed-through tọa độ Example 7). Bài học: **build được ≠ ráp được**.
  - **Chia prompt theo category (2026-07-04)**: thêm combo "Family type" ở action bar tab From JSON (`json_category_combo`) — chọn loại **trước**, bấm Copy Prompt sẽ copy `core chung + overlay của loại đó`. Core = `SYSTEM_PROMPT.md`; 13 overlay ở `FamiGen.pushbutton/prompts/<slug>.md` (mỗi category 1 file: inventory/convention/dimensions/pitfalls riêng). Code: `_PROMPTS_DIR`, `_init_json_panel()`, `_category_slug()`/`_overlay_path()`, `copy_prompt_clicked()` ghép core+overlay (thiếu overlay → fallback core-only). Đã pass audit_tools/audit_ui/sync + XAML well-formed + 13/13 overlay map đúng.
  - **Prompt v2 tự chứa + soft-form + case library (2026-07-05)**: `SYSTEM_PROMPT.md` đã xóa — còn **7 file prompt tự chứa** trong `prompts/` (bỏ door/window/columns/electrical/site/entourage); combo chỉ hiện category có file. Core chung (byte-identical cả 7 file, sửa bằng script + verify equality) thêm section **SOFT-FORM RECIPES** — 7 công thức tạo độ phồng (capsule extrusion, channel-tufting đè 8–12mm, bo góc Arc3P, vòm/cầu Revolution, Blend thuôn mềm, mép cuộn, Spline organic) + khái niệm **bulge depth** + key `_plan.shapes`. furniture.md thêm Upholstery playbook + ví dụ **sofa channel-tufted 11 part** (verify tĩnh pass: loop kín, đúng plane, overlap 3 trục ≥1mm). Mỗi category thêm "Soft-form applications" + **Object case library** ~9–11 case thực tế (bộ phận → form/recipe → mm → anchor). Sửa cảnh báo lỗi thời trong lighting (Cylinder chéo trục = parser dựng bằng NewRevolution quanh trục của nó, không phải sweep engine). Chi tiết + checklist test: `dev/plan/famigen-from-json-roadmap.md`.
  - **Chưa test functional trong Revit** (sofa example 11 part, Cylinder chéo trục, 2–3 case end-to-end photo→AI→JSON; cộng checklist gốc: gen family, chọn loại → copy → gen, đóng doc khi huỷ, thiếu template) — cần user reload pyRevit rồi test lại.

### Tool 10/14 — ManaFami
Chain: launcher → `ManaFamiDialog.py` (1428 loc) → `ManaFami.xaml`

- [ ] Batch rename family → đúng, Ctrl+Z OK
- [ ] Family đang in-use / editable=false (workshared) → xử lý đẹp
- [ ] Purge family không dùng (nếu có chức năng) → xác nhận trước khi xoá
- [x] Ghi chú: **UI + bug fix 2026-07-03 (theo báo cáo user: field trống/rỗng ở tab Management)**: xoá nút "Refresh DataGrid" (`btn_refresh`, icon-only, tab Management toolbar) theo yêu cầu user — không cần thiết vì `window_loaded()` đã tự gọi `_refresh_data()` khi mở cửa sổ; xoá luôn handler chết `refresh_click`. Đồng thời phát hiện & sửa lỗi tiềm ẩn qua đọc code: `window_loaded()` trước đây gói TOÀN BỘ init (Loader tab + Management tab) trong 1 khối `try/except` duy nhất — nếu bước scan folder của Loader tab lỗi, `_load_worksets()`/`_refresh_data()` của Management tab **không bao giờ chạy**, khiến DataGrid trống mà không có thông báo (chỉ log ngầm). Đã tách thành 2 khối try/except độc lập + hiện lỗi ra `status_text` thay vì nuốt âm thầm. **Chưa xác nhận đây có phải nguyên nhân gốc của "field trống" hay không** — cần user mở lại ManaFami trong Revit, nếu Management tab vẫn trống thì đọc dòng status bar (giờ sẽ hiện lỗi cụ thể thay vì im lặng) và báo lại. **UI (2026-07-03, theo screenshot user)**: search bar tab Management (`tb_search`) trước đó là icon Segoe MDL2 tách rời + textbox `T3InputFieldSmall` riêng (radius 8) → nhìn lệch/méo thành oval to. Đã dựng lại thành 1 pill Border duy nhất (White, border `#DCDCE0`, CornerRadius = nửa chiều cao) chứa icon kính lúp vector + TextBox không viền bên trong, đúng theo mẫu "Search Bar Simulator" của `UIStandardShowcase.xaml`. Search bar tab Loader (`txt_search`) chưa đụng tới (không nằm trong screenshot user gửi).

### Tool 11/14 — Wall_Adjust Base
Chain: self-contained (311 loc, **4 bare except**), không XAML riêng

- [ ] Tạm thêm traceback log vào 4 nhánh except
- [ ] Wall thường → adjust base đúng offset
- [ ] Wall bị attach base/top → hành vi đúng hoặc thông báo
- [ ] Ghi chú:

### Tool 12/14 — SplitElements
Chain: launcher → `SplitElementsDialog.py` (61 loc) → `SplitElements.xaml` (logic có thể nằm ở Snippets)

- [ ] Split wall theo level → đúng số đoạn, join/param giữ hợp lý
- [ ] Element không hỗ trợ split → thông báo
- [ ] Ghi chú:

### Tool 13/14 — AutoJoin
Chain: self-contained (708 loc, 1 transaction) → `AutoJoin.xaml`

- [ ] Model nhỏ nhiều wall-floor giao nhau → join hàng loạt đúng
- [ ] Cặp element không join được (geometry fail) → skip + báo số lượng, không dừng cả batch
- [ ] Đo thời gian trên model vừa (~500 element) — có progress/không treo UI
- [ ] Ghi chú:

### Tool 14/14 — Wall Cut Profile ⚠️ rủi ro cao nhất panel
Chain: self-contained (1624 loc, **37 bare except**, 8 transaction + 2 TransactionGroup)

- [ ] Trước test: chèn traceback log vào các nhánh except quan trọng (đặc biệt quanh L705, L1008, L1016 — vùng commit/rollback)
- [ ] Wall thẳng + opening đơn giản → profile cut đúng
- [ ] Wall cong → hoạt động hoặc thông báo không hỗ trợ
- [ ] Profile đè lên nhau → không tạo geometry hỏng
- [ ] Ctrl+Z 1 lần revert cả group (TransactionGroup Assimilate)
- [ ] Test cả flow "Place Opening Families" (L1212+): symbol chưa active → tự activate
- [ ] Ghi chú:

### Chốt ngày 7
- [ ] Cập nhật bảng + README · Gỡ các traceback log tạm trước khi commit
- [ ] Commit: `fix(panel-modeling): <mô tả>`

## Bảng trạng thái Panel 2

| # | Tool | Ngày | Trạng thái | Ghi chú |
|---|------|------|-----------|---------|
| 1 | CADToElements | 6 | ✅ | Wall/Floor/Beam user xác nhận OK (2026-07-03) · user xác nhận chung 2026-07-05: hoạt động tốt |
| 2 | DoorThreshold | 6 | ✅ | Width=0 + thickness sai (compound/joined wall) sửa xong, user xác nhận OK 2026-07-04 · user xác nhận chung 2026-07-05: hoạt động tốt. 2026-07-05 (commit `09cee9b`): thêm thông báo rõ khi model không có FloorType + tra map an toàn. Curtain wall/wall nghiêng vẫn ngoài scope (feature) |
| 3 | ImageToDrafting | 6 | ✅ | UI sửa xong 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |
| 4 | Text to Element | 6 | ✅ | UI sửa xong 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |
| 5 | PointCloud | 6 | 🔄 | User xác nhận chung 2026-07-05. **Mở lại 2026-08-14** — R&D toàn diện + chống crash: 3/5 tầng crash đã đóng, bug band-merge (mọi tường ra sai bề dày + sai type) đã sửa, 24 test mới. Còn A4 Cancel/chunked analysis + G1–G9 chất lượng dựng hình → `pointcloud-scan-to-bim-roadmap.md`. Cần user smoke test trong Revit (checklist §6) |
| 6 | RoomToFloor | 6 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 7 | Tile Layout | 6 | ✅ | Bug path + list sàn mặc định sửa 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |
| 8 | PropertyLine | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 9 | FamiGen | 7 | ✅ | Tool hoạt động tốt (user xác nhận chung 2026-07-05). Prompt v2 2026-07-05 (7 file tự chứa + SOFT-FORM RECIPES + case library + ví dụ sofa 11 part) — 3 probe chi tiết (sofa smoke test · Cylinder chéo · case end-to-end) vẫn theo dõi ở `famigen-from-json-roadmap.md` |
| 10 | ManaFami | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 11 | Wall_Adjust Base | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 12 | SplitElements | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 13 | AutoJoin | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| 14 | Wall Cut Profile | 7 | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |

> 2026-07-05 — user xác nhận tổng quát: **"các tool hiện tại đều đã hoạt động tốt"**. Các mục
> checklist chi tiết chưa có báo cáo riêng từng mục — coi là pass theo xác nhận chung; mở lại
> nếu phát hiện lỗi khi dùng thực tế.

## Phát sinh

- **2026-10-02 — PropertyLine: "no map to review" (user báo kèm ảnh Revit 2025)**: tìm "4A LANG HA" ra 1 boundary (OSM way 136258882) nhưng ô map chỉ có icon placeholder, dòng kết quả xám không chữ. Gốc: (1) tile OSM đi qua `_geoparcel.http_request`, hàm này luôn đọc body bằng `StreamReader(UTF8)` → PNG (byte đầu 0x89) thành text U+FFFD, `generate_parcel_map` gọi `body.encode('latin-1')` ném lỗi với MỌI tile và nuốt ở `logger.debug` → không bao giờ có basemap; `_choose_zoom` còn xin zoom 20 (server chỉ tới 19); (2) cả preview là PNG GDI+ (`System.Drawing`) vẽ trên thread nền, mọi lỗi chỉ `logger.warning`, ô map không có trạng thái loading/failed → lỗi gì cũng chỉ thấy placeholder; kèm `ServicePointManager.ServerCertificateValidationCallback = lambda: True` tắt kiểm tra TLS cho CẢ process Revit; (3) dòng trống: `T3.ListViewItem` template là `GridViewRowPresenter` (chỉ vẽ cột GridView) nên `ItemTemplate` của `lv_parcels` không bao giờ được vẽ. Sửa: `Snippets/_parcel_map.py` mới (Web Mercator, fit zoom ≤19 overzoom ≤2x, tile layout khít mép, scale bar, path markup, fetch tile có cache RAM + %TEMP% 7 ngày, bỏ cuộc nhanh khi offline/403/429); `_geoparcel.http_get_bytes` (GET nhị phân, cùng UA/TLS policy); preview vẽ bằng WPF (Canvas tile + 3 `Path` boundary/fill/đỉnh + scale bar + mũi tên N + "© OpenStreetMap contributors"), boundary luôn hiện kể cả offline ("Basemap unavailable" + tooltip lý do); chọn tự động kết quả đầu; Save Map = `RenderTargetBitmap` của chính ô preview (2x) rồi mở bằng shell; `lv_parcels` → `ListBox` + `T3.ListBoxItem.Multiline`; luồng tìm lần 2 luôn báo về (hết kẹt "Working..."). Test: `dev/test_parcel_map.py` (83 check, kèm tile z18 thật 208126/115408). Gate: audit_t3 · audit_tools · audit_wiring · audit_revit_compat · audit_cpython (0 P0) · audit_api_context · check_xaml_load · sync --check xanh. **NEEDS VERIFICATION trong Revit**: `check_xaml_wpf.ps1` (cần Windows) · map + boundary hiện ngay sau Search · dòng kết quả có chữ · rút mạng → boundary vẫn hiện + "Basemap unavailable" · Save Map ra PNG · resize cửa sổ vẽ lại · 100% và 125%.

- **2026-10-02 — Tile Layout: lần bấm thứ 2 trong phiên ném `Duplicate type name within an assembly` (`TileLayoutDialog.py` `class _FloorFilter(ISelectionFilter)`)**. `script.py` gọi `importlib.reload(GUI.TileLayoutDialog)` mỗi lần bấm → class .NET với `__namespace__` tĩnh `"T3Lab.TileLayout"` bị định nghĩa lần 2 (luật S15). Sửa: hậu tố `_NS_SUFFIX = uuid.uuid4().hex[:8]` mỗi lần nạp module (mẫu `BatchLinkDialog`). Quét: thêm 5 tool cùng panel reload module có namespace tĩnh, cùng crash lần bấm 2 — PointCloud, RoomToFloor, DoorThreshold, Wall Adjust Base, Wall Cut Profile — sửa cùng cách. Chưa xác nhận trong Revit (mở mỗi tool 2 lần liên tiếp).
- **2026-10-02 — ManaFami / Family Loader: user "look ugly, redesign"** (pane Categories trống trơn). Thiết kế lại Tab Loader theo T3 (`ManaFami.xaml` + `ManaFamiDialog.py`): toolbar = Choose Folder · đường dẫn (tooltip đủ) · search; 2 pane `T3.Panel` header 45px thẳng hàng; `T3.TreeView` (bỏ `ScrollViewer` bọc ngoài); empty state `T3.Empty` cho cả 2 pane, hiện/ẩn bằng DataTrigger `HasItems` (luật 25), Python chỉ đổi câu chữ theo pha quét (idle/scanning/done/cancelled/error/không khớp search). Kèm **bug cũ**: thư viện có thư mục lồng 2 cấp (`Doors\Sliding`) → `update_category_tree` ném `KeyError` (bị nuốt ở log debug) → cây chỉ còn "All (n)"; đã sửa đường đi qua `_children`. Nút "Update Folder" → "Choose Folder" (giữ `x:Name`/handler). Gate xanh: audit_t3/tools/wiring, check_xaml_load, sync --check. **Chưa chạy `check_xaml_wpf.ps1`, chưa QA Revit** (100%/125%, 4 icon, các empty state, thư viện lồng nhau).
- **2026-10-02 — FamiGen không mở: `cannot import name 'Int32Collection' from 'System.Windows.Media.Media3D'`**. `Int32Collection` nằm ở `System.Windows.Media`, không phải `Media3D` → chuyển import (`FamiGenDialog.py`). Chưa xác nhận trong Revit.

- **2026-07-13 — Tile Layout: "các option đang chạy sai" (user báo). Review tĩnh toàn bộ script.py (2467 loc) tìm ra chuỗi bug trong engine tính option** — chưa fix, đã lên phương án debug:
  1. **[Nặng nhất — nguồn ranking sai] Waste tính bằng NGUYÊN viên tile**: `NestingEngine._collect_waste()` tạo `TilePiece('waste')` từ `oc.tile_pts` (full tile rect) thay vì phần thừa thật (`tile − inside`), dù `OffCut.waste_area` đã có giá trị đúng nhưng không được dùng. → `waste_pct` trong `LayoutOption._recompute_stats()` bị thổi phồng (mỗi viên cắt đóng 100% diện tích viên vào waste); score = `waste_pct*10 + …` → **thứ hạng 4 option sai**, stats hiển thị sai; khi Apply, waste vẽ DirectShape đỏ nguyên viên đè lên miếng cut xanh cùng z (z-fighting) + tràn ra ngoài sàn.
  2. **Nesting nhận reuse bất khả thi**: điều kiện `oc.waste_area >= needed * 0.90` cho phép miếng TO HƠN offcut vẫn được "reuse" (chỉ check diện tích ×0.9, không check hình/kích thước) → `n_reuse` cao giả, `tiles_to_buy` thấp giả.
  3. **Waste sau nesting biến mất**: khi X nest vào offcut của Y, cả 2 offcut đều `used=True` → phần thừa còn lại thật của Y không được emit → bật nesting waste giảm giả tạo (kết hợp bug 1: OFF quá cao, ON quá thấp — so sánh vô nghĩa).
  4. **32 variant không dedup**: sàn vuông vắn → nhiều shift cho layout giống hệt, sort stable → 4 card "best" trùng nhau về hình + stats.
  5. **Shift arrows di theo trục lưới đã xoay** (dx/dy áp trong local frame của `TileGrid.generate`) → option 45°, bấm → chạy chéo trên màn hình.
  6. **TextNote fail âm thầm trên View3D chưa lock** (exception bị nuốt) → không có nhãn số viên sau Apply; **Apply lần 2 không dọn DirectShape cũ** → chồng geometry.
  7. Phụ: opening/hole bị bỏ qua (chỉ lấy `loops[0]`); sàn dốc fallback bbox âm thầm; `_renumber_pieces` row_step thiếu joint; `_count_thin_cuts` dùng bbox gộp fragment → đếm thiếu sliver; 32 variant × SH clip từng viên không có progress → đơ UI sàn lớn.
  - **Phương án debug** (3 giai đoạn): (A) tách SECTION 1–5 (pure Python) ra `lib/TileLayoutCore.py` + harness CPython `dev/debug/tile_layout_harness.py` chứng minh từng bug bằng case tay tính; (B) fix theo thứ tự B1 waste-area → B2 nesting → B3 dedup → B4 shift local→screen → B5 lock 3D view cho TextNote → B6 dọn DirectShape cũ; (C) smoke test checklist trong Revit do user chạy.
  - **2026-07-13 — GĐ A + B THỰC HIỆN XONG**: engine tách ra `T3Lab.extension/lib/TileLayoutCore.py` (script.py còn 1830 dòng, chỉ giữ Revit/WPF). Harness pre-fix chứng minh bug bằng số: waste 16.667% (đúng: 13.889%), nesting ON → waste 0.000% (đúng: 6.061%), miếng 310mm nest vào offcut 290mm (n_reuse=2, đúng: 0), top-4 có 3 option trùng signature. **Fix đã áp**: B1 `TilePiece(area_override)` + `_collect_waste` dùng `oc.remaining`; B2 `_nest` yêu cầu `remaining >= needed` + `_piece_fits_offcut` (bbox trong frame viên gạch) + guard orphan (không retire tile đang host miếng nest); B3 dedup theo `stats_signature()` trước khi lấy top-N; B4 `LayoutOption.shift_screen()` (xoay vector screen về local frame) — cả card lẫn detail dialog dùng; B5 `SaveOrientationAndLock()` view 3D trước khi TextNote; B6 `clear_previous_preview()` xoá DirectShape (match tên `T3Lab TileLayout Preview` qua `elem_name`) + TextNote của view preview, `ds.SetName()` khi vẽ, bỏ vẽ piece waste (chỉ là bookkeeping). Harness 16/16 PASS; `audit_tools` clean; `audit_ui` 0/54; `sync_wpf_styles --check` 53/53. **Chờ user smoke test trong Revit (GĐ C)** — cần reload pyRevit trước khi test.
  - **2026-07-13 (bổ sung, user yêu cầu)** — **Giữ option đang chọn khi Generate lại**: trước đây bấm "Generate Concepts" lần nữa (quay lại Step 2 đổi tham số) là xoá trắng danh sách option + ép chọn lại option đầu, mất mọi tweak góc/shift. Fix: `_generate_concepts` chụp `gen_params` của option đang chọn trước khi generate; sau sweep tìm twin bằng `LayoutOption.matches_params()` (so angle mod 360 + shift theo PHASE chu kỳ lưới) để chọn lại, không có twin thì dựng lại đúng grid đó với tile/joint/nesting MỚI qua `OptionGenerator.build_variant()` (refactor từ vòng sweep) và append thành card riêng đánh dấu "· previous choice". Status bar báo "Previous choice kept on N floor(s)". Harness thêm T7 (build_variant + matches_params) → 21/21 PASS.
  - **2026-07-13 — User báo GĐ C: "chọn lại staggered là 150 thì options ko chạy"**: repro CPython chứng minh engine chạy đúng với staggered@150° lẫn tile 150mm (0.03–0.6s, đủ option); mô phỏng nguyên văn flow chọn-lại (grid@0 → staggered@150) cũng OK → nguyên nhân là **pyRevit cache module lib**: `TileLayoutCore` trong engine IronPython vẫn là bản trước khi thêm `matches_params`/`build_variant`, trong khi script.py (luôn được đọc lại mỗi click) gọi 2 API mới → `AttributeError` → chỉ chết ở nhánh chọn-lại (lần generate đầu không đụng API mới nên chạy bình thường — khớp đúng triệu chứng "khi chọn LẠI"). **Fix**: script.py `reload(TileLayoutCore)` (guarded try/except) ngay sau import — từ nay update lib không cần reload pyRevit cho tool này. Harness thêm T8 (flow chọn-lại staggered@150) → 25/25 PASS.
  - **2026-07-13 (bổ sung, user yêu cầu) — Thêm pattern + đảm bảo thông số đúng**: (1) `PATTERNS` chung trong core (key + label, combo Step 2 build từ đây nên UI và engine không thể lệch): Grid (Stacked) · Running Bond 1/2 · **Running Bond 1/3** (mới) · **Vertical Bond** (mới, so le theo cột) · **Herringbone 90°** (mới — lattice anchor `(dx,dy)+a·(he,he)+b·(we,−we)`, mỗi anchor 1 viên ngang + 1 viên dọc, tessellate đúng với mọi tỉ lệ gạch). (2) **Fix nền TileGrid**: bỏ phase-wrap `dy mod sy` (làm lật parity hàng staggered khi shift qua ranh giới chu kỳ — layout nhảy thay vì trượt liên tục); generator mới đánh chỉ số hàng/cột/anchor tuyệt đối từ gốc lưới → dx/dy được tôn trọng chính xác với mọi pattern. (3) `matches_params` nhận biết pattern: chu kỳ nhận dạng đúng (staggered lặp 2 hàng, 1/3 lặp 3 hàng, vertical 2 cột, herringbone so exact — thiết kế conservative: nhầm-âm chỉ tốn rebuild, nhầm-dương mới nguy hiểm). (4) Report in label pattern thay vì key. Harness thêm **T9 bất biến phủ kín** (Σ diện tích piece = diện tích sàn, 5 pattern × 3 góc × 3 shift, sai lệch tệ nhất 3e-07 — bắt được cả hở lẫn chồng viên) + **T10** (option pattern mới hợp lệ & khác nhau; staggered shift +2 chu kỳ hàng = layout y hệt) → **34/34 PASS**. Regression: audit_tools clean · audit_ui 0/54 · styles 53/53.
  - **2026-07-13 (bổ sung tiếp, user yêu cầu) — Thêm pattern đợt 2 + tối ưu xử lý**: tổng **8 pattern** — thêm **Running Bond 1/4** (`staggered4`), **Double Herringbone** (`herringbone2` — herringbone tổng quát hóa n thanh/nhánh, lattice `a·(n·he, n·he) + b·(we,−we)`), **Basket Weave** (`basketweave` — ô vuông cạnh `we` xếp bàn cờ, mỗi ô k viên với k = floor(we/he); exact khi we = k·he, lệch tỉ lệ → chỉ hở thêm joint chứ KHÔNG bao giờ chồng viên + status bar cảnh báo). **4 tối ưu**: (1) fast-path giao chữ nhật trong `_intersect_all` khi sàn là hình chữ nhật trục-thẳng và viên không xoay (case phổ biến nhất — bỏ hẳn Sutherland-Hodgman); (2) hoist bbox sàn ra ngoài vòng lặp tile (trước đó tính lại mỗi viên); (3) sweep thích ứng: sàn ước >600 viên/variant → fracs [0, 0.5] thay vì [0,¼,½,¾], kèm **2 variant neo chuẩn ngành mỗi góc** (corner-aligned + centered — T12 chứng minh: sàn lệch gốc 317/253mm, chỉ neo góc mới ra layout 0% waste và nó thắng rank A); (4) progress callback từ `OptionGenerator.generate` → status bar cập nhật "floor i/n — pattern — variant j/m" + Dispatcher pump nên UI không đơ. Kết quả perf: sàn 20m² tile 150mm từ 0.59s → **0.17s** CPython (~3.5×, IronPython lợi hơn). Harness: T9 tự phủ 8 pattern, thêm T11 (basket weave lệch tỉ lệ: covered ≤ floor, không chồng), T12 (neo góc thắng), T13 (perf <5s + fast-path cho stats **y hệt** đường SH qua sàn 5 đỉnh thẳng hàng) → **46/46 PASS**. Regression: audit_tools clean · audit_ui 0/54 · styles 53/53.

- **2026-07-06 — ManaFami (Family Management) không nhận diện được family nào, "Loaded 0 rows." (user báo kèm screenshot)**: `_refresh_data()` chạy tới cuối nhưng 0 row — mọi row bị nuốt bởi `except Exception: pass` per-family. Debug **live qua MCP `send_code_to_revit`** trên chính session Revit của user: model có 192 family (184 editable), toàn bộ 221 row chết vì `AttributeError: Name` khi đọc `symbol.Name` — IronPython bị ẩn **getter** `Name` trên các subclass `ElementType` (`FamilySymbol`/`GroupType`/`AssemblyType`...); `fam.Name` đọc được, và **setter** `element.Name = x` vẫn hoạt động (probe trong transaction rollback) nên `apply_click` rename không cần sửa. Workaround chuẩn (đã dùng rải rác trong repo: `CADToElementsDialog`, `Snippets/_elements.py`): `Element.Name.GetValue(elem)`. **Fix**: thêm helper `elem_name(element)` vào `Snippets/_compat.py` (try `.Name`, fallback `Element.Name.GetValue`), dùng ở cả 5 chỗ đọc tên type trong `_refresh_data` (2 nhánh Loadable, System/`t.Name`, Group/`gt.Name`, Assembly/`at.Name`). Verify live qua MCP: loop y hệt tool build được **221 row** (trước đó 0). Cần user reload pyRevit rồi mở lại tool xác nhận.
