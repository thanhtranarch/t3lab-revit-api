# 09 · Ribbon Icon Standard — đồng nhất với Revit

> Phạm vi: **toàn bộ 45 icon trên ribbon `T3Lab.tab`.**
> Miễn trừ đúng 4 bundle — 3 logo hãng khác + mascot T3LabAssistant (xem §7).
> Ngày: 2026-09-11.

**Trạng thái thực thi**

| Bước | Việc | Trạng thái |
|------|------|-----------|
| 1 | Hạ tầng: `dev/icons/tokens.json` · `dev/build_icons.py` · `dev/audit_icons.py` | ✅ xong |
| 2 | Pilot ManaSheets · ManaWorkset · AutoDimension | ✅ xong |
| 3 | Vẽ lại **45/45** icon (gồm cả 7 tool T3Lab trong Support panel) | ✅ xong |
| 4 | Dọn tài sản chết — xoá 6 script ghi đè icon | ✅ xong |
| 5 | Chốt tài liệu + bật `STRICT` | ✅ xong |
| — | **QA trong Revit thật** (§8) | ⬜ **chưa làm — cần anh mở Revit** |

Hết nợ migration nên `dev/audit_icons.py` đã bật `STRICT = True`: từ đây một icon
không theo chuẩn là **P0**, gate đỏ ngay.

Sáu script bị xoá ở bước 4 vì đều ghi đè icon ribbon theo hệ cũ — hai cái nguy hiểm
nhất là `scripts/svg_to_png.py` (render 96px rồi **xoá luôn `icon.svg`**) và
`scripts/generate_svg_icons.py` (ghi `icon.svg` viewBox 24 đè lên toàn bộ tab):

```
dev/svg_to_png.py · dev/svg_to_png.js · dev/gen_dark_icons.js
dev/redesign_standards_icons.py · scripts/svg_to_png.py · scripts/generate_svg_icons.py
```

`dev/generate_all_icons.py` **được giữ** nhưng giờ chỉ còn dùng cho mascot
`T3LabAssistant` — 7 tool còn lại của Support panel đã chuyển sang `build_icons.py`.
Chạy lại nó là ghi đè ngược 7 icon đó về hệ Aura cũ, nên coi như script một-lần.

Lệnh hằng ngày:

```
python3 dev/build_icons.py           # sinh dark + render PNG
python3 dev/build_icons.py --check   # không ghi gì, báo file nào lệch
python3 dev/audit_icons.py --quiet   # gate
python3 dev/audit_icons.py --debt    # xem nợ migration còn lại
```

Tài liệu này nói về **icon trên ribbon** (file `icon.png` / `icon.svg` trong mỗi
bundle). Nó **không** thay thế mục "Icon" trong `T3LAB_UI_STANDARD.md` — mục đó nói
về glyph `Segoe MDL2 Assets` **bên trong cửa sổ WPF**, là chuyện khác.

---

## 1 · Vì sao phải làm

Bộ icon hiện tại (gọi là "Direction B") dùng ngôn ngữ hình hoàn toàn khác Revit:

| | T3Lab hiện tại | Revit 2026 (đo thực tế) |
|---|---|---|
| Kiểu | khối đặc nhiều lớp, bo góc `rx=3..4`, có `opacity` | line-art: nét 1px + mảng nền phẳng |
| Màu nét | không có nét — chỉ mảng | `#666666` (ta dùng `#000000`, xem §2.2) |
| Màu nền hình | navy `#182A3E` (71 lần) | `#F3F3F3` |
| Accent | cam `#D07818` (41 lần) | xanh `#178FE6` · hổ phách `#FFAA00` · lục `#82D99F` |
| Số màu / icon | 4–16 | **2–3** |
| Bo góc | có | không (góc vuông) |
| Canvas | 64 viewBox, hình nổi giữa | 32 px, hình gần chạm mép |

Đặt cạnh nhau trên ribbon, icon T3Lab **nặng và tối hơn hẳn** icon Revit ở ngay
panel bên cạnh — người dùng đọc ra ngay là "add-in của bên thứ ba".

Số liệu Revit ở trên **không phải suy đoán**: trích từ `UIFrameworkRes.dll`
(Revit 2026, resource `ribbon/themedimages/*_32_light|dark.png`), đo histogram màu
và dựng bản đồ pixel. Ảnh gốc của Autodesk chỉ dùng để **đo**, không sao chép vào repo.

---

## 2 · Ngôn ngữ hình mới — "T3 Ribbon Icon"

### 2.1 Lưới và hình học

| Luật | Giá trị |
|------|---------|
| Canvas | `viewBox="0 0 32 32"` — **đổi từ 64 sang 32** |
| Live area | 32×32, chừa mép 1 unit là đủ; được phép chạm mép khi hình đòi hỏi |
| Nét | `stroke-width="1"`, tâm nét đặt trên toạ độ `.5` (vd `x="2.5"`) |
| Mảng đặc | toạ độ **nguyên** (vd `x="17" width="10"`) |
| Góc | **vuông** — cấm `rx` / `ry` |
| Cấm | `opacity`, gradient, `filter`, shadow, `stroke-dasharray` |
| Số shape | ≤ 6 ở tier A, ≤ 4 ở tier B (§2.4) |
| Khoảng hở | hai hình **cùng token ở bản dark** không được chạm nhau — chừa ≥ 1 unit trong suốt |

`stroke-width` chỉ nhận **1 hoặc 2**: 1 cho đường viền, 2 cho nét nhấn ở tier B.
Ngoài hai giá trị đó là nét không rơi đúng pixel sau khi thu 2:1.

Luật "khoảng hở" là bắt buộc vì ở dark theme nét và nền gộp thành một tông (§2.3);
hai hình **cùng tông** chạm nhau sẽ dính thành một khối vô nghĩa. Hai hình khác
token (vd dimension line hổ phách chạm witness line xám) thì chạm nhau là bình thường.

**Dấu hiệu đã migrate là `viewBox="0 0 32 32"`**, không phải bảng màu — nếu lấy bảng
màu làm dấu hiệu thì chỉ cần thêm một mã màu lạ là icon tự động thoát khỏi mọi luật
còn lại. `dev/audit_icons.py` dựa vào đúng dấu hiệu này.

### 2.2 Bảng màu — 4 token + 3 accent

Lấy đúng giá trị Revit 2026 đang dùng:

| Token | Light | Dark | Dùng cho |
|-------|-------|------|----------|
| `line` | `#000000` | `#EDEDED` | mọi đường viền — **đen tuyệt đối**, xem dưới |
| `surface` | `#F3F3F3` | `#EDEDED` | nền của hình |
| `detail` | `#858585` | `#A8A8A8` | chi tiết phụ, mảng đặc cấp hai, witness line |
| `deep` | `#3C3C3C` | `#FFFFFF` | mảng đặc nhấn mạnh — tối hơn `detail`, nhạt hơn nét |
| `accent.amber` | `#E07B00` | `#FFC14D` | **accent mặc định của T3Lab** |
| `accent.blue` | `#178FE6` | `#89CBFA` | đối tượng "view / thông tin" |
| `accent.green` | `#57B97A` | `#A8D9B8` | hành động "tạo mới / thêm" |

> **Đổi ngày 2026-09-11 — bảng màu rời khỏi giá trị Revit.** Trước đó mọi giá trị
> light lấy đúng của Revit 2026 (nét `#666666` bù thành `#464646`, amber `#FFAA00`,
> lục `#82D99F`). Yêu cầu mới là icon phải **đọc rõ chức năng ngay trên ribbon**,
> nên nét chuyển sang **đen**, `detail` bù hết độ nhạt, amber và lục đậm lên.
> Giá trị Revit gốc vẫn ghi ở §1 để đối chiếu — nó là **gốc đo được**, không còn là
> **đích phải bám**.

#### `line` — đen tuyệt đối, và vì sao màn hình vẫn không ra đen

Revit vẽ nét icon bằng `#666666`. Ta vẽ bằng `#000000`, và **đó là có ý**: đích là
nét đen, không phải nét giống Revit.

Vẫn phải hiểu cơ chế cũ vì nó không biến mất. Revit ship ảnh 32px gốc nên không bị
thu. pyRevit thì decode PNG 64px rồi để WPF thu 2:1 lúc vẽ, nên nét 1 unit bị trộn
với nền — độ phủ còn lại đo được là **0.810**. Trên nền ribbon `#EFEFEF` (lum 239):

> nét nguồn lum `L` → hiện ra lum `239 + 0.81 × (L − 239)`

| nguồn 64px | sau khi thu về 32px | trông như |
|---|---|---|
| `#666666` (lum 102) — Revit | lum 128 | `#868686` |
| `#464646` (lum 70) — cũ | lum 102 | `#666666` |
| `#000000` (lum 0) — **nay** | lum 45 | `#2D2D2D` |

Tức là `#000000` là mức **đen nhất đường ống này cho phép**. Muốn đậm hơn nữa thì
chỉ còn cách tăng `stroke-width`, không phải đổi màu.

Cùng lý do đó, `detail` đổi `#999999` → `#858585`: `#999999` hiện ra `#A9A9A9` (chìm),
`#858585` hiện ra đúng `#999999` — đúng sắc xám mà icon được thiết kế. `detail` là
**mảng đặc cấp hai**, phải nằm dưới nét đen; đã thử `#767676` và mảng đặc trở nên
nặng, tranh chấp với nét.

Bản dark đi ngược chiều: **sáng lên** (`#D9D9D9` → `#EDEDED`) để cùng tăng tương
phản. Icon dark vẫn là silhouette đặc — nét và mảng nền dùng chung một màu, nên
không có nét 1px nào để mà bù.

**Chỉ một accent cho mỗi icon, ≤ 20% diện tích.** Hổ phách được chọn làm accent mặc
định vì nó gần nhất với cam thương hiệu `#D07818` — giữ được nhận diện T3Lab. Giá trị
Revit `#FFAA00` sau khi thu 2:1 hiện ra `#FCB72D`: vàng nhạt, chìm trên nền ribbon
sáng. Nay dùng `#E07B00` → hiện ra `#E38F2D`, cam đậm rõ, bật tốt cạnh nét đen.
Lục `#82D99F` cũng nhạt cùng kiểu nên đậm lên `#57B97A`.

Navy `#182A3E` **bị loại khỏi icon ribbon** (vẫn giữ trong UI cửa sổ).

### 2.3 Dark theme — silhouette, không phải đảo màu

Revit không vẽ lại icon cho dark; nó **gộp nét và nền thành một tông sáng**, rồi
dùng tông xám tối hơn (`#A8A8A8`, Revit dùng `#949494`) để khoét chi tiết. Kiểm chứng trên
`family_open_32_dark.png`: bản light là nét `#666666` + nền `#F3F3F3`, bản dark là
**một khối đặc `#D9D9D9` duy nhất**.

Nên `icon.dark.svg` sinh **tự động** bằng bảng thay thế ở §2.2 — không vẽ tay.

> Lưu ý phiên bản: `resolve_icon_file()` của pyRevit chỉ dùng `icon.dark.png` khi
> `HOST_APP.is_newer_than(2024)`. Trên **Revit 2023** (máy user đang chạy song song)
> chỉ `icon.png` được nạp — bản light phải tự đứng vững, không được coi dark là bù.

### 2.4 Hai tier kích thước — ràng buộc thật, không phải trang trí

`pyrevit/coreutils/ribbon.py`: `ICON_LARGE = 32`, `ICON_MEDIUM = 24`, `ICON_SMALL = 16`.
Nút top-level của panel lấy `ICON_LARGE`; nút nằm trong **stack / pulldown** lấy
`ICON_MEDIUM`. `create_bitmap()` decode PNG ở `icon_size * 2`, nên:

| Tier | Hiển thị | Nguồn 64px bị resample | Luật hình |
|------|----------|------------------------|-----------|
| **A** — top-level | 32 px | 64 → 64 (1:1), rồi WPF thu 2:1 lúc vẽ → **sắc nhất có thể**, nhưng nét 1px vẫn bị làm mềm (§2.2) | được phép chi tiết 1 unit · ≤ 6 shape |
| **B** — trong stack / pulldown | 24 px | 64 → 48 (0.75×) → **mềm nét** | **mọi mảng đặc ≥ 2 unit** cả hai chiều; bỏ hẳn dòng kẻ 1px · ≤ 4 shape |

Luật tier B nói về **mảng đặc và khoảng hở**, không nói về đường viền: một nét 1 unit
bao quanh hình lớn vẫn đọc được ở 24px vì có tương phản hai bên, còn một mảng đặc
1 unit thì biến mất hẳn.

Tier B hiện chiếm **25/45** icon (Mana.stack, manaData.stack, Create.stack,
Create Elements.pulldown, ElementAdjust.pulldown, Assistant Tools.stack, UI.stack).
Vẽ chi tiết 1px cho nhóm này là vẽ thừa — nó nhoè hết.

### 2.5 Xuất PNG

- Render `icon.svg` (viewBox 32) ra **PNG 64×64** — đúng 2×.
- Tỉ lệ 2:1 là tỉ lệ tốt nhất có thể chọn. Đã đo trên chính bộ icon này: hạ 64→32
  bằng **box filter** cho ra đúng **4 màu** (sắc tuyệt đối); cùng ảnh đó hạ bằng
  **Lanczos** cho ra **97 màu** (nhoè hẳn).
- Nhưng **WPF không dùng box filter** — nó dùng bộ thu riêng, gần bilinear hơn, nên
  thực tế nét 1px vẫn bị làm mềm và nhạt đi. Đó là lý do `line` phải bù màu (§2.2).
  Đừng đọc "2:1" thành "không mất gì".
- Không vượt 96px — `check_icon_size()` của pyRevit cảnh báo.
- Nền trong suốt (RGBA), không viền trắng.

---

## 3 · Hiện trạng tài sản

45 nút cần icon. Nợ lúc bắt đầu — **đã trả hết**:

| Vấn đề | Nút | Trạng thái |
|--------|-----|-----------|
| **Không có `icon.svg`** (chỉ PNG — không re-theme được) | AutoJoin · FamiGen · BatchLink · ManaLoca · ModelAuditor · BatchOut | ✅ đã vẽ mới |
| **Không có bản dark** | FamiGen · BatchLink · ManaGroup · ManaLoca · ModelAuditor | ✅ sinh tự động |
| **PNG sai kích thước** | FamiGen 48×48 · ManaLoca 50×50 · ModelAuditor 48×48 | ✅ 64×64 hết |

`LLMsSetting` cũng thuộc nhóm này (chỉ có PNG 50×50). Giờ cả 45 bundle đều có đủ bộ
4 file, `icon.svg` là nguồn, ba file kia sinh tự động.

---

## 4 · Kế hoạch thực thi — 5 bước

### Bước 1 · Hạ tầng (không đụng icon nào) — ✅ xong

- `dev/icons/tokens.json` — bảng màu §2.2, một nguồn duy nhất.
- `dev/icons/iconlib.py` — quét bundle, phân tier, đổi bảng màu.
  Việc đổi light → dark làm trong **một lượt** `re.sub`: token `deep` sinh ra
  `#F3F3F3`, mà `#F3F3F3` lại là `surface` ở bản light — thay tuần tự sẽ đổi hai
  lần và làm hỏng màu.
- `dev/icons/render.js` — resvg, deterministic.
- `dev/build_icons.py` — quét `T3Lab.tab`, bỏ qua `Support.panel`; với mỗi
  `icon.svg`: sinh `icon.dark.svg` bằng bảng token, render cả hai ra PNG 64×64.
  Icon chưa migrate thì **bỏ qua**, không sinh dark bằng bảng màu không phủ được.
  Thay cho `dev/svg_to_png.py`, `dev/svg_to_png.js`, `dev/gen_dark_icons.js`
  (cả ba hardcode danh sách tool và đã lệch thực tế) — xoá ở bước 4.
- `dev/audit_icons.py` — gate. Icon đã migrate bị soi đủ; icon còn ở hệ cũ ghi vào
  mục **NO MIGRATION**, không tính là lỗi, để gate xanh trong suốt quá trình chuyển
  đổi. Hết nợ thì bật `STRICT = True`. `EXEMPT_PANELS` in ra mỗi lần chạy.
- Đã bổ sung vào checklist `.claude/rules/new-tool-standard.md` §5 và `CLAUDE.md`.

### Bước 2 · Pilot 3 icon — 🔄 chờ QA

ManaSheets (tier A) · ManaWorkset (tier A) · AutoDimension (tier B) — đã vẽ, build
và qua gate. Nằm ngay trong bundle thật, nên **reload pyRevit là thấy trên ribbon**.

**QA trong Revit thật (2023 light + 2026 dark) trước khi đi tiếp.** Không duyệt
bằng ảnh render — xem checklist §8.

### Bước 3 · Vẽ lại theo panel

Thứ tự ưu tiên = độ lộ trên ribbon:

| Đợt | Panel | Số icon | Ghi chú |
|-----|-------|---------|---------|
| 1 | Views & Sheets | 4 | BatchOut phải vẽ mới |
| 2 | Standards & Settings | 6 | 4/6 phải vẽ mới |
| 3 | Annotation & Select | 4 | 3 thuộc tier B |
| 4 | Data & IFC-SG | 6 | 3 thuộc tier B |
| 5 | Modeling & Datum | 16 | lớn nhất, 13 thuộc tier B |
| 6 | Standard | 2 | |
| 7 | Support | 7 | bổ sung 2026-09-11 khi bỏ miễn trừ cả panel |

Mỗi đợt: vẽ → `python3 dev/build_icons.py` → `python3 dev/audit_icons.py --quiet`
→ QA Revit → commit. Không gộp nhiều panel vào một commit.

### Bước 4 · Dọn tài sản chết

Xoá `icon.svg` / `icon.png` mồ côi, chuẩn hoá 3 PNG lệch size, xoá 3 script build cũ.

### Bước 5 · Chốt tài liệu

- Bảng token đặt cạnh `T3Lab.Styles.xaml` trong `pyRevit UI Design System/`.
- Một dòng luật trong `.claude/CLAUDE.md`: "icon ribbon theo
  `docs/ui-governance/09-ribbon-icon-standard.md`; Support panel miễn trừ".
- Cập nhật `.claude/agents/ui-agent.md`.

---

## 5 · Ước lượng

| Việc | Khối lượng |
|------|-----------|
| Hạ tầng (bước 1) | ~1 ngày |
| Pilot + QA | ~0.5 ngày |
| 38 icon (vẽ + dark + render) | ~3–4 ngày |
| QA Revit 2 theme × 2 phiên bản | ~0.5 ngày |

---

## 6 · Rủi ro

| Rủi ro | Xử lý |
|--------|-------|
| Mất nhận diện T3Lab khi bỏ navy + cam | Giữ accent hổ phách `#E07B00` ở **mọi** icon — nó là sợi chỉ đỏ xuyên suốt |
| Tier B nhoè ở 24px | Luật chi tiết ≥ 2 unit (§2.4); pilot bắt buộc có 1 icon tier B |
| Revit 2023 không có dark | Bản light phải tự đứng vững; QA riêng trên 2023 |
| Icon "quá Revit" đến mức không nhận ra panel T3Lab | Accent hổ phách + tên panel giữ nguyên; đánh giá lại sau pilot |
| Sao chép icon Autodesk | **Không** copy pixel / đường nét từ `UIFrameworkRes.dll`; chỉ dùng bảng màu và quy ước lưới |

---

## 7 · Miễn trừ — đúng 4 bundle, không phải cả panel

**Sửa 2026-09-11.** Bản đầu của tài liệu này miễn trừ **cả `Support.panel`** với lý
do "đây là bề mặt sản phẩm T3Lab, trông khác Revit là đúng". Lập luận đó **sai trong
thực tế**: nhìn trên ribbon thật, 7 tool T3Lab trong Support panel chỉ đơn giản là
lạc lõng — silhouette đen đặc kiểu Aura nằm ngay cạnh line-art xám nhạt của Revit,
không đọc ra "nền tảng riêng" mà đọc ra "add-in chưa làm xong". Ranh giới sản phẩm
không phải việc của icon 24px.

Nên 7 tool sau **đã chuyển sang hệ chung**: `Feedback` · `LLMsSetting` ·
`MCPControl` · `PDF import` · `BG Theme` · `ManaTabs` · `Ribbon Names`.

Miễn trừ giờ khai theo **từng bundle** trong `iconlib.EXEMPT_BUNDLES`, mỗi cái một
lý do cụ thể:

| Bundle | Vì sao |
|--------|--------|
| `CloudLinks.stack/Autodesk Forma.urlbutton` | Logo của hãng khác |
| `CloudLinks.stack/Autodesk Health.urlbutton` | Logo của hãng khác |
| `CloudLinks.stack/Bluebeam Status.urlbutton` | Logo của hãng khác |
| `T3LabAssistant.pushbutton` | Mascot sản phẩm |

Ba logo hãng: vẽ lại thành line-art của mình là **vừa mất nhận diện vừa đụng vào
nhãn hiệu của họ** — người dùng tìm nút Forma bằng chính logo Forma.

`T3LabAssistant`: đây là bề mặt trò chuyện, không phải một công cụ Revit — cùng lý do
`T3LabAssistant.xaml` đang bị khoá UI trong `CLAUDE.md`. Đây là **một** icon, không
phải cả panel, nên nó là điểm nhấn có chủ đích chứ không thành mảng lạc lõng.

`dev/audit_icons.py` in danh sách 4 bundle này **mỗi lần chạy**, kèm lý do, để không
ai tưởng là việc còn bỏ sót.

---

## 8 · Checklist QA (cần Revit thật — việc duy nhất còn lại)

Reload pyRevit rồi kiểm trên ribbon. Gate xanh **không** thay được mấy dòng này:
audit đọc SVG, nó không biết pyRevit thu ảnh xuống trông ra sao trên máy thật.

```
[ ] Revit 2023 (light) — cả 7 panel: nét sắc, không nhoè, không viền trắng quanh icon
[ ] Revit 2026 (light) — giống 2023, không lệch
[ ] Revit 2026 (dark)  — silhouette sáng, hổ phách nổi rõ trên nền #4F4F4F
[ ] Nút trong stack/pulldown (25 icon tier B) ở 24px: còn đọc ra được là cái gì
[ ] Đặt cạnh icon Revit ở panel bên: cùng "sức nặng", không tối/nặng hơn hẳn
[ ] 125% display scaling: không vỡ nét
[ ] Hover / disabled: icon không biến mất trên nền highlight
[ ] Support panel: 7 tool T3Lab đã đổi; 3 logo hãng + T3LabAssistant KHÔNG đổi
```

Dòng nào hỏng thì báo tên tool, sửa `icon.svg` rồi chạy lại
`python3 dev/build_icons.py`. Không sửa PNG hay `icon.dark.svg` bằng tay.
