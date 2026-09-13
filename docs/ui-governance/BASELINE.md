# BASELINE — kiểm kê xác minh ngày 2026-09-13

> Snapshot này thay thế bảng bootstrap và kết luận UX 100/100 cũ.
> Gate đạt chuẩn T3 không phải điểm UX hoặc chứng nhận logic đúng trong Revit.

| Chỉ số | Kết quả |
|---|---|
| Pushbutton script.py | 44 |
| XAML | 59 |
| T3 static / sanitise / WPF parse ngoài Revit | 59 / 59 / 59 đạt |
| Legacy / UI-locked | 0 / 0 theo gate |
| Static tools gate | clean |
| CPython | 0 P0; 444 P1 cần phân loại |
| Điểm UX | Chưa chấm đủ rubric; NEEDS VERIFICATION |
| Runtime Revit / DPI | NEEDS VERIFICATION |

Chi tiết lỗi, thay đổi, test và kế hoạch: [REVIEW-2026-09-13.md](REVIEW-2026-09-13.md).

## Kiểm kê UI

Kích thước bên dưới lấy từ XAML, không tự suy ra size class hoặc chấm điểm.
PASS là kết quả kiểm tra cấu trúc và nạp WPF ngoài Revit. Mọi dòng còn cần kiểm tra hành vi trong Revit.

| File (tương đối repository) | Root | Width x Height | Static / WPF | UX / Revit |
|---|---|---|---|---|
| `T3Lab.extension/lib/GUI/Tools/AdvancedViewManager.xaml` | Window | 1200 x 800 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/AdvancedViewManagerBatchRename.xaml` | Window | 700 x 540 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/AutoDimension.xaml` | Window | 880 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/AutoJoin.xaml` | Window | 780 x 620 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/AutoWork.xaml` | Window | 1100 x 700 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/BatchLink.xaml` | Window | 1080 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/BCFReader.xaml` | Window | 1180 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/BGTheme.xaml` | Window | 520 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/CADtoBeam.xaml` | Window | 640 x 620 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/CADToElements.xaml` | Window | 940 x 820 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/CadtoFloor.xaml` | Window | 880 x 700 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/CadtoFloorLayerItem.xaml` | Border | auto x auto | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/CadtoWall.xaml` | Window | 880 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/DimText.xaml` | Window | 500 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/DoorThreshold.xaml` | Window | 880 x 620 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/DWGManagement.xaml` | Window | 1100 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ExportManager.xaml` | Window | 1280 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ExportManagerTest.xaml` | Window | 1280 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/FamiGen.xaml` | Window | 1180 x 800 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/Feedback.xaml` | Window | 600 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/FindReplace.xaml` | Window | 440 x 340 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/FoundationVolume.xaml` | Window | 640 x 560 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/IFCSG.xaml` | Window | 1250 x 820 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ImageToDrafting.xaml` | Window | 640 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/LLMSetting.xaml` | Window | 580 x 700 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaAnno.xaml` | Window | 1180 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaContains.xaml` | Window | 1380 x 820 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaFami.xaml` | Window | 1200 x 800 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaGroup.xaml` | Window | 1080 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaLoca.xaml` | Window | 1200 x 740 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaPara.xaml` | Window | 1100 x 750 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaSched.xaml` | Window | 1160 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaSelect.xaml` | Window | 560 x 840 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaSheets.xaml` | Window | 1200 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaStyles.xaml` | Window | 1260 x 780 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaTabs.xaml` | Window | 460 x 560 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaViews.xaml` | Window | 1200 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ManaWorkset.xaml` | Window | 1080 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/MCPControl.xaml` | Window | 480 x 820 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ModelAuditor.xaml` | Window | 1200 x 800 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/ParameterSelector.xaml` | Window | 760 x 560 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/PDFImport.xaml` | Window | 1080 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/PointCloud.xaml` | Window | 960 x 680 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/PropertyLine.xaml` | Window | 1080 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/QuickElement.xaml` | Window | 980 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/RibbonNames.xaml` | Window | 640 x 520 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/RoomToFloor.xaml` | Window | 880 x 620 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/SelectFromDict.xaml` | Window | 560 x 560 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/SheetGen.xaml` | Window | 1100 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/SplitElements.xaml` | Window | 480 x 360 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/SubtypeDefinerColMap.xaml` | Window | 520 x 420 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/T3Dialog.xaml` | Window | 440 x 240 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/T3LabAssistant.xaml` | Window | 560 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/TagChecker.xaml` | Window | 540 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/TextToElement.xaml` | Window | 760 x 820 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/TileLayout.xaml` | Window | 960 x 700 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/UIStandardShowcase.xaml` | Window | 1180 x 720 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/WallAdjustBase.xaml` | Window | 480 x 430 | PASS | NEEDS VERIFICATION |
| `T3Lab.extension/lib/GUI/Tools/WallCutProfile.xaml` | Window | 580 x 580 | PASS | NEEDS VERIFICATION |
