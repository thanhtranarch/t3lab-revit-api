# T3Lab — Master Plan: Debug & UI Consistency (thực hiện theo ngày)

> Bắt đầu: 2026-07-02 · Branch làm việc: tạo branch mới cho từng giai đoạn từ `main`
> Nguồn dữ liệu: review tĩnh 41 tool + UI audit 76 XAML (xem `dev/DEBUG_PLAN.md`)

## Cách dùng plan này

1. Mỗi ngày mở đúng file của ngày đó (bảng lịch bên dưới), làm theo checklist, tick `- [x]` trực tiếp vào file.
2. Cuối mỗi ngày chạy bộ kiểm tra regression rồi commit (kể cả file plan đã tick):
   ```bash
   python3 dev/audit_tools.py --quiet      # audit code tĩnh
   python3 dev/audit_ui.py --quiet         # audit UI Lumina
   python3 dev/sync_wpf_styles.py --check  # style block đồng bộ
   ```
3. Gặp lỗi mới → ghi vào mục **"Phát sinh"** cuối file panel tương ứng, đừng sửa lan man ngoài phạm vi ngày.
4. Cập nhật bảng tiến độ bên dưới (cột Trạng thái).

## 4 Giai đoạn

| Giai đoạn | Nội dung | File plan | Số ngày |
|-----------|----------|-----------|---------|
| **1. Sửa lỗi tĩnh & dọn dẹp** | 4 bug đã xác nhận (F1–F4) + archive dead code | `phase-1-cleanup-fixes.md` | 2 |
| **2. UI Consistency (Lumina)** | Sửa 2 outlier T3LabAssistant (palette + copyright) + verify trực quan | `phase-2-ui-consistency.md` | 2 |
| **3. Debug chức năng theo panel** | Smoke test 41 tool trong Revit, chia 6 panel | `panel-1..6-*.md` | 7 |
| **4. Regression & tổng kết** | Chạy lại toàn bộ audit, chốt báo cáo | mục cuối file này | 1 |

> Ghi chú UI: chuẩn copyright là **footer (status bar), bên trái** — codebase vốn đã theo pattern này ở 74/76 file; `ui-design-standard.md` đã được cập nhật khớp thực tế (2026-07-02), nên GĐ2 chỉ còn 2 ngày.

## Lịch thực hiện 12 ngày

| Ngày | Giai đoạn | Việc | File | Trạng thái |
|------|-----------|------|------|-----------|
| 1 | GĐ1 | Sửa F1 FindReplace · F2 CreateFromRooms · F3 UIShowcase | `phase-1-cleanup-fixes.md` §Ngày 1 | ✅ |
| 2 | GĐ1 | Archive 15 XAML mồ côi + 6 dialog chết · chạy lại audit | `phase-1-cleanup-fixes.md` §Ngày 2 | ✅ |
| 3 | GĐ2 | Sửa T3LabAssistant.xaml (palette + copyright) + AssistantPane.xaml → audit UI sạch | `phase-2-ui-consistency.md` §Ngày 3 | ✅ |
| 4 | GĐ2 | Mở từng cửa sổ trong Revit, verify trực quan + screenshot | `phase-2-ui-consistency.md` §Ngày 4 | ✅ **Hoàn thành 2026-07-03** — user xác nhận "tất cả UI đều okay". Panel 1–3 xác nhận qua nhiều screenshot thực tế trước đó (gray-gutter/layout/copyright đã sửa); Panel 4–6 xác nhận đợt này. Screenshot file riêng skip có lý do (QA trực tiếp trong Revit thay vì lưu ảnh). ⚠️ Ngoại lệ: `audit_ui.py` còn 1 lỗi biết trước chưa sửa (IFCSG copyright trùng lặp x2, task nền riêng) — chưa phải audit sạch 100% |
| 5 | GĐ3 | Debug Panel: Annotation & Select (4 tool) | `panel-1-annotation-select.md` | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung "các tool hiện tại đều đã hoạt động tốt" (crash ManaSelect + trang trắng ManaAnno đã sửa 2026-07-03 trước đó) |
| 6 | GĐ3 | Debug Panel: Modeling & Datum — nhóm Create (7 tool) | `panel-2-modeling-datum.md` §Ngày 6 | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung; các fix trước đó: CADToElements (07-03), DoorThreshold width/thickness (07-04), ImageToDrafting/TextToElement/TileLayout UI+path (07-03). Ngoài scope còn lại: DoorThreshold trên curtain wall/wall nghiêng |
| 7 | GĐ3 | Debug Panel: Modeling & Datum — nhóm Adjust/Family (7 tool) | `panel-2-modeling-datum.md` §Ngày 7 | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung. FamiGen "From JSON" prompt v2 (7 file tự chứa + SOFT-FORM RECIPES + case library) shipped cùng ngày; 3 probe chi tiết theo dõi riêng ở `famigen-from-json-roadmap.md` |
| 8 | GĐ3 | Debug Panel: Views & Sheets (4 tool, trọng tâm BatchOut) | `panel-3-views-sheets.md` | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung (UI ManaSheets/ManaViews đã xác nhận riêng 2026-07-03) |
| 9 | GĐ3 | Debug Panel: Data & IFC-SG (6 tool) | `panel-4-data-ifcsg.md` | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung (pre-flight tĩnh sạch 2026-07-04: 3 fix trang trắng còn nguyên, print flood đã xoá) |
| 10 | GĐ3 | Debug Panel: Standards & Settings (4 tool) | `panel-5-standards-settings.md` | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung (đóng theo xác nhận, không có phiên review tĩnh riêng) |
| 11 | GĐ3 | Debug Panel: Support + Standard (9 tool) | `panel-6-support-standard.md` | ✅ **Hoàn thành 2026-07-05** — user xác nhận chung. PDF import từng mở lại do crash + không hiện thông tin + mở chậm khi dùng thật; đã sửa 2 vòng (F11: nạp grid trong `__init__` + bật lại virtualization/`INotifyPropertyChanged`), **user xác nhận đã debug hết 2026-07-05** |
| 12 | GĐ4 | Regression toàn bộ + tổng kết | mục dưới | ✅ **Hoàn thành 2026-07-05** — 3 audit sạch, 41/41 tool ✅, review code GĐ4 phát hiện + sửa F10 (2 điểm ghi JSON unicode không an toàn), tổng kết + danh sách issue còn lại: `dev/DEBUG_PLAN.md` mục 7 |

> Nguyên tắc: **không nhảy giai đoạn**. GĐ1 dọn dead code trước để GĐ2 không tốn công sửa UI cho file sắp xoá; GĐ2 chốt UI trước để GĐ3 smoke test không bị nhiễu bởi thay đổi XAML.

## Giai đoạn 4 — Ngày 12: Regression & tổng kết (✅ hoàn thành 2026-07-05)

- [x] `python3 dev/audit_tools.py --quiet` → exit 0 (2026-07-05)
- [x] `python3 dev/audit_ui.py --quiet` → exit 0 — 0/54 file có vấn đề (lỗi IFCSG copyright trùng ×2 ghi ở Ngày 4 nay đã hết)
- [x] `python3 dev/sync_wpf_styles.py --check` → 53/53, 0 lệch
- [x] Mọi bảng panel: không còn ô ⬜ (41/41 tool ✅ theo xác nhận chung của user 2026-07-05)
- [x] Tổng hợp mục "Phát sinh" của 6 file panel → danh sách issue xếp ưu tiên: `dev/DEBUG_PLAN.md` mục 7. **Đợt fix 2026-07-05 đã đóng toàn bộ issue functional** (PDF import F11 ×2 vòng, ManaSheets Excel, BatchOut F6, DoorThreshold message) — **user xác nhận đã debug hết**; còn lại chỉ tech debt F5 + backlog cố ý hoãn
- [x] Cập nhật `dev/DEBUG_PLAN.md` trạng thái cuối cùng (banner trạng thái, findings F1–F9 chốt, F10/F11 phát hiện + sửa, bảng 41 tool ✅, mục 7 tổng kết + kết luận)
- [x] Screenshot bộ UI đã chuẩn hoá lưu vào `dev/plan/screenshots/` — ⏭️ skip có lý do: user QA trực tiếp trong Revit (tiền lệ Ngày 4)

## Quy ước trạng thái

- ⬜ chưa làm · 🔄 đang làm · ✅ xong · ❌ fail (kèm ghi chú) · ⏭️ skip có lý do

## Định nghĩa "xong" cho 1 tool (GĐ3)

1. Mở từ ribbon không lỗi; chrome window (drag/min/max/close/resize) hoạt động.
2. Happy path chạy đúng trên model test; Ctrl+Z revert đúng 1 bước.
3. Edge case (không chọn gì / model rỗng / cancel) ra thông báo thân thiện, không stacktrace.
4. Không exception bị nuốt trong pyRevit log khi thao tác bình thường.
5. Tick ✅ vào bảng panel + ghi chú nếu có hành vi lạ.

---

## Roadmap độc lập theo tool — ĐÃ ĐÓNG TOÀN BỘ (2026-07-05)

> User xác nhận 2026-07-05: **toàn bộ 4 roadmap độc lập đã thực hiện xong**; 4 file roadmap
> đã xóa cho gọn theo yêu cầu (nội dung đầy đủ vẫn còn trong lịch sử git — xem commit xóa).

| Roadmap | Kết cục |
|---------|---------|
| T3Lab Assistant — Agentic Upgrade | ✅ Đóng — code GĐ A/B/C đã ship trước đó; mục mở cuối cùng (D3 smoke test) xác nhận qua phản hồi chung "các tool hoạt động tốt" + xác nhận đóng roadmap 2026-07-05 |
| AutoDimension — Cải thiện | ✅ Đóng — GĐ A đã ship & hoạt động tốt; backlog cải tiến (~16 mục GĐ B/C/D: idempotent, dim mặt tường host, core-layer thật, stacking manager, EQ, trục xiên, section mở rộng, preset/preview…) **đóng không triển khai** theo quyết định user — nếu cần thì mở roadmap mới |
| FamiGen — From JSON | ✅ Đóng — prompt v2 (7 file tự chứa + SOFT-FORM RECIPES + Object case library + ví dụ sofa 11 part) shipped, smoke test xác nhận chung 2026-07-05; backlog WS4 (cảnh báo part rời rạc) / WS5 (sketch plane tùy ý) **đóng không triển khai** |
| MCP Tools — Expansion | ✅ Đóng — toàn bộ mục đã tick từ trước, smoke test xác nhận chung 2026-07-05 |

---

## Rebar & Assembly toolkit cho người dùng Tekla — Revit 2027 (mở 2026-10-02)

- Roadmap (lý do, phạm vi, 3 lớp): `dev/plan/rebar-tekla-toolkit-roadmap.md`
- Spec triển khai (nguồn chính cho code): `dev/plan/rebar-tekla-implementation-spec.md`
- Hướng dẫn người dùng EN/VI: `docs/tekla-to-revit-2027.md`
- Hàng đợi QA: `docs/ui-governance/PRIORITY_QUEUE.md` § "Rebar & Assembly panel"

| Gói | Nội dung | Code + gate | QA trong Revit 2027 |
|-----|----------|-------------|--------------------|
| WP1 | Nền `_compat` / `_assembly` / `_rebar` + spike | ✅ 108 test | ⬜ chạy spike G1–G16 |
| WP2 | Tekla Bridge + docs + mẫu phím tắt | ✅ 41 test | ⬜ |
| WP3 | Cast Unit Manager | ✅ | ⬜ |
| WP4 | Clone Drawing | ✅ 28 test | ⬜ |
| WP5 | Rebar Check | ✅ 46 test | ⬜ |
| WP6 | BVBS Export (BF2D) | ✅ 85 test | ⬜ đọc file bằng viewer BVBS |
| WP7 | Rebar Wizard (dầm, cột, móng đơn) | ✅ 16 test | ⬜ |

Cột QA chỉ được tick theo phản hồi của user sau khi chạy trong Revit — không tick từ gate tĩnh.

### Kết quả spike (điền sau khi chạy `dev/debug/spike_rebar_assembly.py`)

| Mã | Giả định | Kết quả |
|----|----------|---------|
| G1–G16 | xem spec §3.11 | ⬜ chưa chạy |


---

## Tách tab ribbon — giữ panel đồng nhất (mở 2026-10-03)

- Phương án: `dev/plan/ribbon-tab-split.md` — 1 tab `T3Lab_Dev` (31 slot, 4/7 stack mất chữ) → 2 tab
  Model · Docs (≈ 1 160 / 1 210 px), cùng một bộ luật panel P1–P10.
- Trạng thái: ⬜ chờ chốt D1–D4 (§7 của file phương án) rồi mới vào GĐ0.
