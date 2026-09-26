# Hướng dẫn & Lưu ý quan trọng khi thiết kế UI WPF (pyRevit)

Tài liệu này tổng hợp các lưu ý thiết kế, kỹ thuật xử lý và bài học kinh nghiệm trong quá trình hoàn thiện hệ thống giao diện chuẩn (UI Standard) cho các công cụ T3Lab Revit API.

---

## 1. Tách biệt giao diện khỏi Worksheet Revit (Contrast & Border)
* **Vấn đề**: Bản vẽ Revit thường có nền trắng tinh (`#FFFFFF`). Nếu cửa sổ công cụ (đặc biệt là thanh tiêu đề, thanh bên sidebar, hoặc footer) cũng dùng màu trắng tinh, cửa sổ sẽ bị "chìm" và mất đi ranh giới rõ ràng với phần mềm Revit.
* **Giải pháp**:
  - Bao bọc toàn bộ cửa sổ bằng một thẻ `<Border>` ngoài cùng với viền rõ ràng (ví dụ: `BorderBrush="#A1A1AA"`, `BorderThickness="1.5"`, `CornerRadius="22"`).
  - Tông màu nền chính của App nên hạ xuống màu xám nhẹ (ví dụ: `T3MainAppBgBrush` là `#E4E4E7` hoặc `#F4F4F6`) thay vì trắng tinh. Các khu vực nhập liệu, bảng tính (DataGrid) sẽ giữ màu trắng để tạo độ tương phản cao, nổi bật thông tin.

---

## 2. Tùy biến thanh cuộn siêu mỏng (Ultra-Thin Scrollbar)
* **Vấn đề**: Mặc định WPF hiển thị thanh cuộn của Windows (rất dày ~17px, có nút mũi tên lên xuống thô cứng). Khi thiết lập `Width="4"` hoặc `Height="4"` trong Style, hệ thống thường bỏ qua và vẫn hiển thị dày do giới hạn kích thước tối thiểu của hệ thống.
* **Giải pháp**:
  - Trong Style của `ScrollBar`, **bắt buộc phải ghi đè** thuộc tính `MinWidth` và `MinHeight` về `0`:
    ```xml
    <Style TargetType="{x:Type ScrollBar}">
        <Setter Property="MinWidth" Value="0"/>
        <Setter Property="MinHeight" Value="0"/>
        ...
    </Style>
    ```
  - Thiết lập kích thước thanh cuộn siêu mỏng: cuộn dọc `Width="4"`, cuộn ngang `Height="4"`.
  - Thiết lập thẻ `Thumb` với `Margin="0"` và `CornerRadius="2"` để tạo hình viên thuốc bo tròn tinh tế và sắc nét.

---

## 3. Định nghĩa Grid trong WPF (XML Syntax Error)
* **Vấn đề**: Trình phân tích cú pháp XAML (`XamlReader`) trong môi trường pyRevit/Revit rất nhạy cảm với cách viết thẻ. Việc sử dụng tiền tố lớp trong danh sách định nghĩa cột/dòng (như `<Grid.ColumnDefinition />`) sẽ gây lỗi phân tích cú pháp `Unexpected 'EMPTYPROPERTYELEMENT'`.
* **Giải pháp**:
  - Luôn sử dụng cú pháp chuẩn hóa không chứa tiền tố lớp bên trong bộ định nghĩa:
    ```xml
    <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>      <!-- ĐÚNG -->
        <ColumnDefinition Width="Auto"/>   <!-- ĐÚNG -->
    </Grid.ColumnDefinitions>
    ```
  - Tránh viết `<Grid.ColumnDefinition />` bên trong `<Grid.ColumnDefinitions>`.

---

## 4. Căn chỉnh vị trí & Tránh chồng lấn (Alignment & Overlap Prevention)
* **Vấn đề 1**: Trạng thái (chấm tròn `Ellipse` và TextBlock) bị lệch hàng, chấm tròn thường bị dịch xuống dưới so với dòng text.
  - *Giải pháp*: Tất cả các thành phần nằm ngang cạnh nhau trong `StackPanel` cần được thiết lập tường minh thuộc tính `VerticalAlignment="Center"` để căn giữa tuyệt đối theo trục dọc.
* **Vấn đề 2**: Các nút chức năng bên trái và thông tin trạng thái/bản quyền bên phải bị đè lên nhau (overlap) khi người dùng thu nhỏ cửa sổ.
  - *Giải pháp*: Không để hai cụm này tự do trong Grid không chia cột. Hãy chia Footer Grid làm 2 cột rõ ràng:
    - Cột 0 (`Width="*"`) chứa các nút thao tác căn trái.
    - Cột 1 (`Width="Auto"`) chứa thông tin trạng thái căn phải.
    Khi thu hẹp cửa sổ, chúng sẽ tự động bị cắt biên (clip) tại ranh giới cột thay vì đè đè lên nhau.
  - *Tối ưu không gian*: Chuyển thông tin bản quyền (Copyright) dài từ 1 hàng ngang thành 2 hàng dọc (sử dụng StackPanel dọc) để giảm bớt chiều rộng chiếm dụng, nhường không gian cho các nút bấm.

---

## 5. Tương tác với Custom Title Bar (WindowChrome Hit-Testing)
* **Vấn đề**: Khi sử dụng `WindowChrome` để tùy biến thanh tiêu đề tự chế (để có thể kéo thả cửa sổ), các nút bấm (Minimize, Maximize, Close), ô tìm kiếm, hoặc các nút tương tác khác nằm trong vùng thanh tiêu đề (`CaptionHeight`) sẽ bị mất sự kiện Click/Hover (do hệ thống Windows chặn lại để xử lý thao tác kéo cửa sổ).
* **Giải pháp**:
  - Gán thuộc tính đính kèm `WindowChrome.IsHitTestVisibleInChrome="True"` cho tất cả các phần tử tương tác nằm trong vùng Header để hệ thống cho phép bắt sự kiện click:
    ```xml
    <Button x:Name="btn_close" WindowChrome.IsHitTestVisibleInChrome="True" ... />
    ```

---

## 6. Thiết kế bo góc dạng viên thuốc (Pill Shape CornerRadius)
* **Vấn đề**: Sử dụng `CornerRadius="999"` cho các phần tử có chiều cao tự động có thể gây lỗi hiển thị méo mó hoặc treo bộ dựng hình WPF trên một số máy trạm.
* **Giải pháp**:
  - Xác định chiều cao cố định của phần tử và đặt `CornerRadius` bằng đúng **một nửa chiều cao** đó (Ví dụ: Thanh tìm kiếm cao `36px` thì đặt `CornerRadius="18"`; thẻ Card cao `44px` thì đặt `CornerRadius="22"`).

---

## 7. Tự động co giãn Icon Vector (Path Stretch)
* **Vấn đề**: Dữ liệu đồ họa Vector (`Path.Data`) khi thay đổi kích thước thủ công không đi kèm tỷ lệ sẽ bị cắt đứt hoặc biến dạng.
* **Giải pháp**:
  - Luôn thêm thuộc tính `Stretch="Uniform"` vào thẻ `<Path>` cùng với kích thước `Width` và `Height` mong muốn để đồ họa tự động co giãn đều bên trong khung hiển thị.

---

## 8. Sử dụng đường dẫn tương đối (Portable & Relative Paths)
* **Vấn đề**: Dự án được lưu trữ và chạy trên các máy tính khác nhau của nhiều thành viên phát triển, dẫn đến việc đường dẫn tuyệt đối (ví dụ: `C:\Users\...` hoặc `D:\01. T3Lab...`) sẽ bị lỗi không tìm thấy file khi chạy trên máy khác.
* **Giải pháp**:
  - **Tuyệt đối không** sử dụng đường dẫn tuyệt đối bắt đầu bằng ký tự ổ đĩa (như `C:\` hay `D:\`) trong tất cả tài liệu hướng dẫn (`.md`), cấu hình tác vụ, hoặc mã nguồn.
  - Luôn sử dụng đường dẫn tương đối bắt đầu từ thư mục của extension (ví dụ: `.claude/standard/UIStandardShowcase.xaml` hoặc `T3Lab.extension/...`).
  - Trong Python, sử dụng thư viện `os.path` để tính toán đường dẫn tương đối động dựa trên vị trí file hiện tại (ví dụ: dùng `os.path.dirname(__file__)` làm mốc gốc) để đảm bảo mã chạy chính xác bất kể thư mục gốc của extension được cài đặt ở đâu trên hệ thống.
  - Sử dụng các API dựng sẵn của pyRevit để truy xuất các thư mục mở rộng một cách tự động khi cần thiết.
