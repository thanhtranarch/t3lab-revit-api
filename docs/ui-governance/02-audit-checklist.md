# 02 · AUDIT — checklist kiểm tra UI/UX

Đánh giá từng tool theo 7 nhóm A–G. Mỗi dòng là một câu hỏi có/không, trả lời được
bằng cách đọc XAML + script.py, không cần mở Revit (trừ nhóm đánh dấu 🔴).

🔴 = **cần Revit thật để xác minh** → xuất checklist cho user, không tự kết luận.

---

## A · VISUAL CONSISTENCY

| # | Kiểm tra | Chuẩn |
|---|----------|-------|
| A1 | Layout | Đúng một trong 5 pattern P1–P5 |
| A2 | Alignment | Một left rail duy nhất: 16px từ mép window, 12px trong panel lồng |
| A3 | Spacing | Mọi `Margin`/`Padding` chia hết cho 4; chỉ dùng 4/8/12/16/24/32 |
| A4 | Typography | **Segoe UI** (Consolas cho số/ID); không font khác, không trộn font |
| A5 | Font size | Đúng 7 size: 19 · 15 · 13 · 13 SemiBold · 11.5 · 11 · 12.5 mono. Size lạ (12, 13.5, 14…) = vi phạm |
| A6 | Button size | Action button cao **30**; control 28; row 26 |
| A7 | Button hierarchy | Đúng **một** primary; secondary/ghost cho phần còn lại |
| A8 | Color usage | Chỉ `{StaticResource T3.*}`; **không hex cứng**; cam `#C2410C` chỉ cho progress |
| A9 | Icons | Segoe MDL2 cho glyph chrome; `&#xE921;`/`&#xE922;`/`&#xE8BB;` đúng |
| A10 | Images | Logo/icon đúng path, không vỡ, không stretch sai tỉ lệ |
| A11 | Borders | 1px, `T3.Border` / `T3.BorderStrong`; bo góc chỉ 8 (window) · 4 (control) · 2 (pill) · 0 (grid row) |
| A12 | Grouping | Chia section bằng label uppercase + `Separator`, không bằng card lồng |
| A13 | Dialog dimensions | Đúng size class S/M/L; có `MinWidth`/`MinHeight` |

## B · COMPONENT CONSISTENCY

Mọi component phải dùng style trong `T3Lab.Styles.xaml`. **Không** `<Style>` tự chế
trong file tool — style mới phải thêm vào stylesheet (`MANUAL REVIEW REQUIRED`).

| # | Component | Kiểm tra |
|---|-----------|----------|
| B1 | Button | `T3.Button.Primary/.Secondary/.Ghost/.Danger`; không hardcode `Background` hex |
| B2 | TextBox | `T3.TextBox` (hoặc `.Mono`); cao 28; focus = viền 1px `T3.Ink`, không dotted |
| B3 | CheckBox | `T3.CheckBox`; không toggle switch kiểu iOS |
| B4 | RadioButton | Dùng khi loại trừ nhau; ≥3 lựa chọn cân nhắc ComboBox |
| B5 | ComboBox | `T3.ComboBox` + `T3.ComboBoxItem`; cao 28; không auto-size nhảy |
| B6 | List | `T3.ListBox` virtualized; **không** bọc trong `ScrollViewer` |
| B7 | Table | `T3.DataGrid` row 26 + `T3.DataGridColumnHeader/Row/Cell`, `EnableRowVirtualization="True"` |
| B8 | Tabs | `TabItem` ẩn cho wizard; tab thật thì có style nhất quán |
| B9 | Dialog | `T3.TitleBar` (40) + content + `T3.FooterBar` (48); size class S/M/L |
| B10 | Message box | Dùng cùng một helper; không trộn `TaskDialog` với `forms.alert` tuỳ hứng |
| B11 | Progress bar | `T3.ProgressBar` cao 8, `T3.Progress.Track/.Fill`, có `n / total` + tên item hiện tại |
| B12 | Search | Ô filter pinned trên list, có placeholder, không mất khi cuộn |
| B13 | Filter | Chip filter đồng nhất giữa các tool cùng loại |
| B14 | Selection control | All / None ở footer dạng `T3.Button.Ghost`; primary mang số đếm |
| B15 | Callout / pill | `T3.Callout*` và `T3.StatusPill`; pill = nền tint + dot + **chữ** |

## C · UX (Usability)

| # | Kiểm tra |
|---|----------|
| C1 | **Information hierarchy** — nhìn 3 giây có biết cửa sổ này làm gì không? |
| C2 | **Discoverability** — chức năng chính có lộ ra, hay giấu trong Advanced? |
| C3 | **Clarity** — có label nào cần đọc code mới hiểu không? |
| C4 | **Feedback** — mọi hành động có phản hồi trong ≤1 giây? |
| C5 | **Error prevention** — nút phá huỷ có bị bấm nhầm dễ không? Có disable khi input sai? |
| C6 | **Error recovery** — hỏng rồi có làm lại được không, hay phải đóng cửa sổ? |
| C7 | **Input validation** — validate lúc nhập hay lúc bấm Run? Thông báo nói rõ sai ở đâu? |
| C8 | **Loading state** — lúc collector chạy, UI có đơ im lặng không? |
| C9 | **Progress feedback** — tác vụ >2 giây có progress + phase + số lượng? |
| C10 | **Success feedback** — xong rồi có nói làm được bao nhiêu cái không? |
| C11 | **Error feedback** — lỗi ra stacktrace hay ra câu tiếng người? |
| C12 | **Confirmation flow** — thao tác phá huỷ có P5 với số lượng cụ thể? |
| C13 | **Cancel behavior** — Cancel giữa chừng có rollback sạch không? 🔴 |

## D · INTERACTION

| # | Kiểm tra |
|---|----------|
| D1 | Button placement — footer: trái = trạng thái, phải = secondary → **một** primary, gap 8; không nút Close/Cancel/Done nào chỉ để đóng cửa sổ (nút X làm việc đó) |
| D2 | Primary vs secondary — phân biệt được bằng hình thức, không chỉ bằng vị trí |
| D3 | Default action — có `IsDefault` trên primary khi cửa sổ có primary; **trừ** P5 (nút phá huỷ không bao giờ `IsDefault`, Cancel mới là) |
| D4 | Cancel action — `IsCancel` trên nút X title bar (`T3.WinClose`); Esc đóng được cửa sổ |
| D5 | Keyboard accessibility — Enter/Esc hoạt động; access key nếu form dài |
| D6 | Tab order — `TabIndex` đi theo thứ tự đọc, không nhảy lung tung |
| D7 | Selection behavior — chọn nhiều có Shift/Ctrl; có All/None |
| D8 | Consistency of interaction — cùng thao tác thì cùng cách làm giữa các tool |

## E · CONTENT

| # | Kiểm tra |
|---|----------|
| E1 | Naming — tên tool trên ribbon = tên trên title bar |
| E2 | Labels — danh từ ngắn, không viết tắt tự chế |
| E3 | Instructions — câu hướng dẫn nói *làm gì tiếp*, không mô tả phần mềm |
| E4 | Tooltips — có ở nút không hiển nhiên; không lặp lại y hệt label |
| E5 | Messages — một giọng văn, một ngôn ngữ (VI hoặc EN) trong cùng một tool |
| E6 | Error messages — nói *cái gì sai · ở đâu · làm gì tiếp*, không phải mã lỗi |
| E7 | Warning messages — nêu hệ quả kèm **số lượng** |
| E8 | Success messages — nêu kết quả kèm **số lượng** |
| E9 | Terminology — dùng đúng từ của Revit (View Template, Workset, Sheet…), không dịch chế |

## F · ACCESSIBILITY

| # | Kiểm tra |
|---|----------|
| F1 | Readability — body 13px; nhỏ nhất là Caption 11.5 / Label 11 và chỉ cho nhãn phụ |
| F2 | Contrast — chữ trên nền đạt ≥4.5:1; `T3.TextDisabled` chỉ cho trạng thái disabled/empty |
| F3 | Text hierarchy — phân cấp bằng size/weight, không chỉ bằng màu |
| F4 | Clickable area — nút ≥28px cao; icon button ≥24×24 |
| F5 | Clear feedback — hover/press/focus/disabled đều có state nhìn thấy được |
| F6 | Visual complexity — không panel lồng >2 cấp, không viền lồng viền |
| F7 | Đọc được ở 100% **và** 125% display scaling 🔴 |

## G · PYREVIT / REVIT CONTEXT

| # | Kiểm tra |
|---|----------|
| G1 | UI nói rõ thao tác Revit sắp xảy ra (tạo/sửa/xoá element nào, bao nhiêu) |
| G2 | Selection flow rõ: đang dùng selection hiện tại, hay cả document? |
| G3 | Document state rõ: tên model / view đang tác động hiện trên UI |
| G4 | Transaction feedback: người dùng biết Ctrl+Z sẽ revert **một** bước |
| G5 | Tác vụ dài có progress + có thể huỷ; không đóng băng Revit im lặng 🔴 |
| G6 | Không bọc list trong `ScrollViewer` → không realize toàn bộ row (nguyên nhân treo phổ biến nhất) |
| G7 | Model rỗng / không chọn gì → thông báo thân thiện, không stacktrace |
| G8 | Không `print()` rác ra output window trong luồng bình thường |

---

## Cách ghi kết quả audit

Mỗi mục không đạt → một issue theo format ở `04-severity-and-issues.md`.
Mỗi mục 🔴 chưa xác minh được → `NEEDS VERIFICATION`, gom vào checklist Revit cuối report.
Mục đạt → không ghi gì (report chỉ nói về cái lệch).
