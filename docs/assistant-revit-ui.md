# T3Lab Assistant — giao diện hoà làm 1 với Revit

> Cập nhật 2026-08-03. File liên quan:
> `T3Lab.extension/lib/GUI/RevitTheme.py` ·
> `T3Lab.extension/lib/GUI/Tools/T3LabAssistant.xaml` ·
> `T3Lab.extension/lib/GUI/AssistantPaneControl.py` ·
> `T3Lab.extension/T3Lab Docs.tab/Support.panel/T3LabAssistant.pushbutton/script.py`

Mục tiêu: Assistant không còn là một cửa sổ lạ dán đè lên Revit, mà đọc như
**một panel của chính Revit**.

## 1. Bảng màu lấy thẳng từ Revit (2026-08-03)

Assistant là **chat surface**, không phải tool dialog — nên nó không dùng Lumina
(`.claude/rules/ui-design-standard.md`). Nhưng nó cũng **không** có bảng màu
riêng: mọi token là **màu UI của chính Revit**. Đây là **ngoại lệ có chủ đích**
so với Lumina, đã ghi vào `UI_LOCKED` của `dev/audit_ui.py` và bảng UI-Frozen
trong `.claude/CLAUDE.md` để các đợt sweep UI sau không "sửa" ngược lại.

Neo màu theo từng theme của host:

| Vùng | Revit Light | Revit Dark (2024+) |
|---|---|---|
| Nền pane (chrome, `AppBg`/`ComposerBg`) | `#F0F0F0` | `#2E2E2E` |
| Nền transcript (content, `ChatBg`) | `#FFFFFF` | `#383838` |
| Card / popup / ô nhập (`CardBg`) | `#FFFFFF` | `#414141` |
| Viền control (`CardBorder`) | `#C4C4C4` | `#575757` |
| Đường kẻ (`Divider`) | `#D9D9D9` | `#4A4A4A` |
| Chữ nhãn (`Ink`) | `#000000` | `#FFFFFF` |

Cách chia màu (chỉ **một** bên hội thoại được tô nền):

| Thành phần | Vai trò |
|---|---|
| Tin của **user** | bong bóng xanh nhạt `#DBE8F6` (dark: `#33475A`) — đúng sắc "dòng đang chọn" của Project Browser, bo 14px, canh phải |
| Trả lời của **assistant** | **không bong bóng, không viền, không avatar** — chữ nằm thẳng trên nền pane |
| Accent | xanh Autodesk — nút gửi, chip đang chọn, số lượng selection. Light `#0A72B8` (đậm hơn giá trị brand `#0696D7` một bậc: accent còn dùng cho chữ 11.5px, bản sáng chỉ đạt 2.9:1 trên nền `#F0F0F0`), dark giữ `#0696D7` (sáng hơn nữa thì mũi tên trắng của nút gửi chìm) |
| Copyright | vẫn amber `#F59E0B` theo chuẩn Lumina (audit kiểm tra) |

> Trước 2026-08-03 đây là bảng "paper" ấm (`#FAF9F5` + coral `#D97757`). Nó tự
> nó thì đẹp, nhưng có sắc riêng — cắm cạnh Project Browser thì vẫn đọc ra là
> một app khác dán đè lên Revit, đúng thứ cần bỏ. **Đừng đưa lại.**

Không token màu nào được gõ thẳng vào chỗ dùng: tất cả đi qua
`GUI/RevitTheme.py`. XAML dùng `{DynamicResource T3Theme<Token>}`, code Python
dùng `_bind_fg` / `_bind_bg` / `_bind_border` / `_bind_stroke` (xem §2 — chúng
**theo** token, không chụp lại nó). `_trgb('Token')` / `_theme_color('Token')`
vẫn dùng cho chỗ cần giá trị thô.

## 2. Theme sáng/tối theo Revit

`RevitTheme.current_theme()` đọc `UIThemeManager.CurrentTheme` (Revit 2024+;
host cũ hơn không có dark mode nên trả `light`). `RevitTheme.apply(window)` ghi
30 token vào `Window.Resources`, nên mọi chỗ bind `DynamicResource` đổi màu ngay.

- Áp dụng khi mở cửa sổ **và** mỗi lần cửa sổ được activate (user đổi theme
  giữa chừng trong Options ▸ User Interface, hoặc bằng tool BG Theme).
- `apply()` ghi **hai** khoá cho mỗi token: `T3Theme<Token>` là brush, và
  `T3Theme<Token>Color` là `Color` thô. Vài property có kiểu `Color` chứ không
  phải `Brush` — `DropShadowEffect.Color` là chính — và bind brush vào đó thì
  **im lặng không ăn**. Thiếu khoá này nên bóng đổ của popup từng phải hardcode
  hex, và ở dark mode nó vẫn giữ giá trị của theme sáng nên popup mất tách lớp.
- Card xác nhận thao tác phá huỷ (`#FEF2F2`) cố tình giữ nguyên đỏ sáng ở cả
  hai theme — nó phải nổi bật. **Vì vậy mọi thứ nằm trên card đó cũng phải cố
  định**: chữ mô tả, dòng trạng thái và hai nút Confirm/Cancel dùng màu chết
  (`#3F3F3F` / `#8C6A6A` / `#C42B1C`+trắng / `#E6E6E6`+`#3F3F3F`), không dùng
  token. Trước đây chúng bind `BotText` / `Muted` / `CardBg`, nên ở dark mode
  là chữ gần trắng trên nền hồng nhạt — vô hình (2.1:1).
- `Danger` ở dark (`#F08880`) chỉ dùng cho **chữ** lỗi trên nền tối; nó không
  còn là nền của nút nào nữa (xem gạch đầu dòng trên), nên được chọn thuần theo
  độ đọc trên `#383838`.

### Tin nhắn đã render cũng đổi màu (2026-08-02)

Trước đây các tin nhắn *đã render* giữ nguyên màu lúc render, và ghi chú cũ nói
"render lại transcript giữa chừng có thể đụng bong bóng đang stream nên không
làm". Lý do đó đúng — nhưng **không cần render lại**.

Gốc rễ: `_tb()` trả một `SolidColorBrush` **đóng băng**, tức một *giá trị*.
`apply()` ghi lại token thì mọi `{DynamicResource}` trong XAML tự tính lại,
nhưng một brush đã gán thì không có gì chạy lại.

`_bind_theme(element, prop, token)` trong `script.py` dùng
`SetResourceReference` — đúng bản code-behind của `DynamicResource`: element tự
đăng ký theo khoá và tự vẽ lại khi khoá đổi. Không render lại dòng nào, nên
bong bóng đang stream không hề bị đụng tới. Bốn helper mỏng cho các property
hay dùng: `_bind_fg` / `_bind_bg` / `_bind_border` / `_bind_stroke`.

Không phân giải được `DependencyProperty` thì nó **rơi về đúng phép gán cũ**,
nên trường hợp xấu nhất bằng đúng hành vi trước đây.

> Đừng gán `X.Foreground = _tb('Token')` nữa — `dev/test_assistant_ui.py` sẽ
> báo đỏ. Dùng `_bind_fg(X, 'Token')`.

## 3. Dockable pane: đúng chỗ, đúng viền

- `AssistantPaneControl._apply_initial_dock_state()` đặt
  `InitialState.DockPosition = Right` và `TabBehind = ProjectBrowser`. Trước đây
  không set `InitialState` nên Revit mặc định thả pane **nổi giữa màn hình** —
  đúng cái ấn tượng "app rời" cần bỏ. Revit chỉ dùng `InitialState` ở lần hiện
  pane đầu tiên; sau đó tôn trọng vị trí user tự dock.
- Khi docked, `_flatten_chrome_for_pane()` bỏ bo góc 22px + viền 1.5px của
  `root_chrome` để Assistant lấp kín pane, không còn hở nền Revit ở 4 góc.
  Cụm nút min/max của cửa sổ nổi cũng ẩn (pane đã có nút riêng của Revit).

## 4. Layout hẹp

Pane dock cạnh Project Browser thường chỉ 300–340px. Dưới `COMPACT_WIDTH = 400`
(`_apply_compact_layout`): padding co lại, và footer bỏ dòng disclaimer + dòng
gợi ý phím tắt — **giữ copyright** (bắt buộc theo chuẩn).

Việc trả lời bỏ bong bóng + bỏ avatar cũng trả lại toàn bộ bề ngang cho nội
dung, đây là thay đổi đáng kể nhất cho pane hẹp.

## 5. Hàng icon dưới mỗi câu trả lời

Style `MsgActionBtn` (XAML) + `_make_message_actions()` (script.py). Icon line-art
xám, không nền; hover mới hiện nền bo tròn:

| Icon | Hành vi |
|---|---|
| Copy | chép câu trả lời vào clipboard; icon đổi thành dấu ✓ xanh 1.2s rồi trả lại |
| Try again | chạy lại đúng prompt đã sinh ra câu trả lời đó |
| 👍 / 👎 | chốt đánh giá (bấm lại để bỏ), ghi vào activity log |
| ‹ n / m › | chuyển qua lại giữa các phiên bản trả lời |

"Try again" **không** ghi đè câu trả lời cũ và **không** lặp lại tin nhắn của
user: câu trả lời mới được lưu thành **một version khác của cùng hàng đó**, nên
cặp mũi tên `‹ n/m ›` mới xuất hiện (giống `3/3` trong UI tham chiếu). Trạng thái
này nằm ở `row.Tag` (dict) — IronPython không cho gắn attribute lên object CLR
nên `Tag` là chỗ duy nhất mang được.

> Icon "read aloud" (loa) trong UI tham chiếu **không** làm: chưa có TTS trong
> extension, và một nút không chạy còn tệ hơn không có nút.

Avatar T3Lab xoay khi đang xử lý vẫn còn — nhưng chuyển vào **dòng typing**:
một mark động cho mỗi lượt, đặt đúng chỗ câu trả lời sắp hiện, thay vì mỗi bong
bóng một avatar.

## 6. Scrollbar mỏng kiểu Revit

`RevitThinScrollBar`: không nút mũi tên, không nền — chỉ một thumb bo tròn dày
5px (7px khi hover/kéo), vùng bấm 8px.

### 6.1 Hai hướng, không phải một (sửa 2026-08-02)

Style này ban đầu **chỉ viết cho chiều dọc**: `Width` cố định, thumb `Width="5"`
+ `HorizontalAlignment="Center"`, và `IsDirectionReversed="true"` hardcode trên
`Track`. Đúng cho dọc, sai hoàn toàn cho ngang.

Nó **có** dùng cho thanh ngang: mọi code block trong câu trả lời
(`_make_code_block`) đặt `HorizontalScrollBarVisibility = Auto` và nằm trong
`chat_scroll`. Kết quả: thanh ngang hiện thành vệt **dọc** 5px, và
`IsDirectionReversed` sai làm kéo **ngược chiều**.

Nay tách hai template (`...TemplateV` / `...TemplateH`) và hai thumb style, đổi
qua `<Trigger Property="Orientation" Value="Horizontal">` — đúng cách khối
shared vẫn làm.

### 6.2 Phủ toàn cửa sổ, không phải từng surface

Trước đây áp bằng **nested `<ScrollViewer.Resources>`** trên từng surface cuộn,
vì khối shared khai báo `<Style TargetType="{x:Type ScrollBar}">` *implicit*
(không `x:Key`) và thêm cái thứ hai **cùng dictionary** sẽ lỗi trùng khoá.

Ràng buộc đó chỉ đúng trong **cùng một** dictionary. Một style implicit đặt ở
`<Grid.Resources>` của root Grid nằm ở dictionary **khác**, hợp lệ, và thắng
cho toàn bộ subtree. Nhờ vậy mới phủ được những chỗ cách cũ luôn bỏ sót:

- `chat_input` — nó bị chặn `MaxHeight` và bật `VerticalScrollBarVisibility`
  (`script.py`), nên soạn tin dài là có thanh cuộn;
- dropdown của ComboBox.

Cả hai trước đó rơi về style shared với hex cứng `#A1A1AA`/`#71717A`/`#18181B`
⇒ dark mode vẽ thumb gần như tàng hình.

Các override lồng cũ **vẫn giữ**: nội dung popup nằm ngoài visual tree của
element này nên tra cứu resource không chắc tới được đây.

> Vẫn **không** sửa file master `WPF_styles.xaml` — làm thế sẽ kéo theo 52 tool
> window khác.

## 7. Popup trong pane hẹp

Bề rộng popup được viết cho cửa sổ nổi (`skills_popup` khai `MinWidth="340"`),
trong khi pane cắm cạnh Project Browser thường 300–340px — popup tràn ra ngoài,
đè lên UI của Revit. `_fit_popups_to_width()` ghim bề rộng theo pane.

Nó chạy ở **mọi** lần đổi bề rộng, không chỉ lúc vượt ngưỡng compact: kéo pane
từ 250px lên 350px không cắt qua ngưỡng nào, nên `_apply_compact_layout` thoát
sớm và popup sẽ kẹt ở bề rộng hẹp nhất từng gặp.

## 8. Test

`dev/test_assistant_ui.py` giữ **các nguyên tắc mà cái lock sinh ra để bảo vệ**,
không phải thiết kế thị giác: màu đi qua token, hai palette khớp nhau, scrollbar
đủ hai hướng, khối auto-synced còn nguyên, không style chết, không hex lạc.

Bảng màu light tồn tại **ba bản**: `RevitTheme._LIGHT` (bản chạy), bảng
`<SolidColorBrush>` trong XAML (vẽ trước khi `apply()` chạy) và
`_LIGHT_FALLBACK` trong `script.py` (khi không import được `RevitTheme`).
Ba bản là một cỗ máy sinh drift — đổi màu ở một chỗ thì cửa sổ nháy bảng cũ mỗi
lần mở, hoặc nhánh no-theme vẽ bảng đã bỏ từ lâu. `test_light_palette_is_copied_
faithfully` ghim cả ba khớp nhau từng token.

Trước đó file XAML bespoke nhất codebase **không có gì kiểm tra** — `audit_ui.py`
bỏ qua nó vì nó UI-locked. Đó chính là lý do một style chỉ-dọc bị dùng cho thanh
ngang, và hai style hardcode hoàn toàn nằm không ai dùng suốt nhiều tháng.
