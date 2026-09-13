# pyRevit UI/UX Governance — bộ tài liệu routine

Hệ thống quản trị chất lượng UI/UX cho toàn bộ pyRevit extension của T3Lab: audit,
chấm điểm, chuẩn hoá, phát hiện regression, và cải thiện liên tục theo chu kỳ.

**Chuẩn duy nhất:** `pyRevit UI Design System/` — mọi hệ UI khác trong repo đã bị bỏ.

---

## Bắt đầu từ đâu

| Bạn muốn | Mở file |
|----------|---------|
| Dán prompt vào Routine của Claude | **`ROUTINE_PROMPT.md`** |
| Hiểu agent làm gì | `../../.claude/agents/ui-governance-agent.md` |
| Biết luật thiết kế là gì | `../../pyRevit UI Design System/T3LAB_UI_STANDARD.md` |
| Viết một tool/script MỚI | `../../.claude/rules/new-tool-standard.md` |
| Chạy gate UI | `python3 dev/audit_t3.py --quiet` |
| Biết cái gì được sửa, cái gì không | `08-design-system-authority.md` |
| Xem điểm hiện tại của từng tool | `BASELINE.md` |
| Xem cycle sau làm gì | `PRIORITY_QUEUE.md` |

## Bản đồ file

```
pyRevit UI Design System/           ← CHUẨN DUY NHẤT (nguồn, không sửa khi chưa duyệt)
├── T3LAB_UI_STANDARD.md               luật: token, type, spacing, 10 luật bố cục, P1-P5
└── T3Lab.Styles.xaml                  82 resource key T3.*

.claude/
├── rules/new-tool-standard.md      ← luật cho MỌI tool/script mới
├── skills/xaml-templates.md           snippet T3 cho từng control
├── agents/ui-agent.md                 agent viết XAML
└── agents/ui-governance-agent.md   ← định nghĩa agent governance

dev/audit_t3.py                     ← gate UI (--quiet / --legacy / --file)

docs/ui-governance/
├── README.md                       ← file này
├── ROUTINE_PROMPT.md               ← prompt tổng hợp cho Routine
├── 01-scan.md                         phạm vi quét, chọn tool cho cycle
├── 02-audit-checklist.md              7 nhóm kiểm tra A-G
├── 03-scoring.md                      rubric 0-100 + trần điểm cứng
├── 04-severity-and-issues.md          P0-P4, format issue 9 trường, hàng đợi
├── 05-improvement-and-safety.md       luật sửa · an toàn Revit · 7 bước verify
├── 06-baseline-tracking.md            baseline · trend · regression · change history
├── 07-report-template.md              format report
├── 08-design-system-authority.md   ← ĐỌC TRƯỚC KHI SỬA XAML
├── BASELINE.md                     ← dữ liệu sống: điểm từng tool
├── CHANGELOG.md                    ← dữ liệu sống: lịch sử cycle
└── PRIORITY_QUEUE.md               ← dữ liệu sống: hàng đợi
```

Ba file "dữ liệu sống" được agent cập nhật mỗi cycle. Bảy file `0N-*.md` là quy trình,
chỉ đổi khi quy trình đổi.

## Một cycle làm gì

```
SCAN → AUDIT → SCORE → IDENTIFY → PRIORITIZE → IMPROVE → VERIFY → COMPARE → LOG → REPORT
```

Ngân sách: audit sâu 5–8 tool · in-place fix ≤10 issue · migrate ≤2 file · ≤1 đề xuất
cấp hệ thống. Hết ngân sách thì đẩy phần dư vào `PRIORITY_QUEUE.md`.

## Trạng thái xác minh 2026-09-13

44 pushbutton `script.py`, 59 XAML. Gate T3 và static sạch; 59 XAML qua sanitise
và WPF parse ngoài Revit. Không còn legacy theo gate hiện tại.

Chưa có đủ kiểm chứng để chấm điểm UX hoặc chứng nhận toàn bộ logic trong Revit.
Xem [review và phương án phát triển](REVIEW-2026-09-13.md), [baseline](BASELINE.md)
và [hàng đợi](PRIORITY_QUEUE.md). Các gap deploy stylesheet và copyright đã có
quyết định trong lịch sử; không còn là blocker migration.
