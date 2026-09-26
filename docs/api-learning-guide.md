# Self-Learning API System Documentation

## Tổng quan

Hệ thống **Self-Learning API** cho phép BatchOut tool tự động học và thích nghi với các thay đổi của Revit API qua các phiên bản 2022-2026 và cả các phiên bản tương lai.

## Tính năng chính

### 1. **Smart API Adapter** (`api_learner.py`)
Bộ điều hợp API thông minh tự động xử lý sự khác biệt giữa các phiên bản.

**Khả năng:**
- Tự động detect phiên bản Revit
- Load API signatures phù hợp
- Cache thông tin API để dùng offline
- Graceful fallback khi không có mạng

**Cách sử dụng:**
```python
from api_learner import SmartAPIAdapter

# Khởi tạo adapter
adapter = SmartAPIAdapter(doc, revit_version)

# Export DWG với API version-aware
adapter.export_dwg(folder, filename, view_ids, options)

# Export PDF với API version-aware
adapter.export_pdf(folder, filename, view_ids, options)

# Configure options thông minh
dwg_options = adapter.configure_dwg_options(options, prop_override_mode)
pdf_options = adapter.configure_pdf_options(options, hide_scope_boxes=True)
```

### 2. **API Learner** (`api_learner.py`)
Hệ thống học API signatures từ documentation.

**Khả năng:**
- Học API signatures tự động
- Cache thông tin trong 30 ngày
- Load từ web khi cần update
- Hỗ trợ offline với cached data

**Cache location:**
```
~/.t3lab/api_cache/
  ├── api_compatibility_2022.json
  ├── api_compatibility_2023.json
  ├── api_compatibility_2024.json
  ├── api_compatibility_2025.json
  └── api_compatibility_2026.json
```

### 3. **Auto Updater** (`api_updater.py`)
Tự động kiểm tra và cập nhật API mỗi thứ 6.

> **2026-09-26:** BatchOut **không còn** gọi `auto_check_and_update()` lúc mở
> tool. Hàm này tải revitapidocs.com bằng `WebClient` đồng bộ trên UI thread
> của Revit (timeout mặc định 100 s) và kết quả chỉ ghi vào debug log — nên nó
> chỉ làm chậm lúc mở cửa sổ. Module vẫn giữ để gọi tay; muốn dùng lại trong
> tool thì phải chạy ngoài UI thread.

**Khả năng:**
- Tự động check mỗi thứ 6 (khi revitapidocs.com update)
- Detect phiên bản Revit mới
- Download API documentation
- Parse "What's New" changes
- Thông báo khi có update quan trọng

**Cách sử dụng:**
```python
from api_updater import auto_check_and_update

# Tự động check (chỉ chạy vào thứ 6 hoặc chưa check)
result = auto_check_and_update()

if result['updates_found']:
    for notification in result['notifications']:
        print(notification['message'])
```

## Quy trình tự động

### Khi Tool khởi động:
```
1. Load SmartAPIAdapter
2. Check cache (30 days)
3. Load API info từ cache (không gọi web lúc mở tool)
4. Apply version-appropriate APIs
```

### Weekly Auto-Update (Thứ 6):
```
1. Connect to revitapidocs.com
2. Detect new Revit versions
3. Download API documentation
4. Parse API changes
5. Update cache
6. Notify user
```

## API Information Structure

### Cached API Info Format:
```json
{
  "revit_version": 2023,
  "cached_date": "2025-12-05",
  "learned_from": "web",
  "export_api": {
    "dwg_export": {
      "signature": "Export(String, String, ICollection<ElementId>, DWGExportOptions)",
      "supports_element_id_collection": true
    },
    "pdf_export": {
      "signature": "Export(String, String, IList<ElementId>, PDFExportOptions)",
      "requires_separate_folder_filename": true
    }
  },
  "dwg_export_options": {
    "prop_overrides": {
      "type": "PropOverrideMode",
      "accepts_enum_only": true,
      "supported_values": ["ByEntity", "ByLayer"]
    },
    "exporting_areas": {
      "available": false
    }
  },
  "pdf_export_options": {
    "hide_scope_boxes": {
      "available": true
    },
    "hide_crop_boundaries": {
      "available": true
    }
  },
  "version_notes": {
    "2023": "PropOverrides requires enum"
  }
}
```

## Cách hoạt động

### 1. Version Detection
```python
# Tự động detect Revit version
REVIT_VERSION = int(revit.doc.Application.VersionNumber)
# => 2023, 2024, 2025, 2026...
```

### 2. API Learning
```python
# Load cached API info
learner = RevitAPILearner(REVIT_VERSION)

# Check if should learn from web
if learner.should_update():
    learner.learn_from_web()
    learner.save_cache()
```

### 3. Smart Export
```python
# Adapter tự động chọn API signature đúng
if supports_element_id_collection:
    doc.Export(folder, filename, view_ids, options)  # 2022-2026
else:
    doc.Export(folder, filename, viewset, options)   # Older
```

## Integration với BatchOut

### DWG Export với Smart Adapter:
```python
# Configure options thông minh
if self.api_adapter:
    dwg_options = self.api_adapter.configure_dwg_options(
        dwg_options,
        prop_override_mode=PropOverrideMode.ByEntity
    )

    # Export với version-aware API
    self.api_adapter.export_dwg(folder, filename, view_ids, dwg_options)
else:
    # Fallback to manual
    self.doc.Export(folder, filename, view_ids, dwg_options)
```

### PDF Export với Smart Adapter:
```python
# Configure options thông minh
if self.api_adapter:
    pdf_options = self.api_adapter.configure_pdf_options(
        pdf_options,
        hide_scope_boxes=True,
        hide_crop_boundaries=True,
        hide_unreferenced_tags=True
    )

    # Export với version-aware API
    self.api_adapter.export_pdf(folder, filename, view_ids, pdf_options)
else:
    # Fallback to manual
    self.doc.Export(folder, filename, view_ids, pdf_options)
```

## Update Schedule

- **Thứ 6 hàng tuần**: Auto-check từ revitapidocs.com
- **Mỗi 30 ngày**: Cache expiry, force re-learn
- **Khi khởi động**: Quick check nếu cần
- **Manual**: User có thể force update bất kỳ lúc nào

## Security & Privacy

- Chỉ connect đến revitapidocs.com (official docs)
- Không gửi data của user lên server
- Cache local only (~/.t3lab/api_cache/)
- Graceful fallback nếu không có mạng
- Không require admin permissions

## Benefits

### Future-Proof
- Tự động hỗ trợ Revit 2027, 2028, 2029...
- Không cần update code thủ công
- Zero downtime khi có phiên bản mới

### Intelligent
- Học từ official documentation
- Tự động detect API changes
- Smart fallback strategies

### Offline-Capable
- Cache 30 days
- Hoạt động không cần internet
- Background updates không block UI

### Low Maintenance
- Tự update mỗi tuần
- Không cần can thiệp thủ công
- Error handling tự động

## Testing

### Test API Learner:
```python
from api_learner import RevitAPILearner

learner = RevitAPILearner(2023)
learner.learn_from_web()
print(learner.get_version_notes())
```

### Test Auto Updater:
```python
from api_updater import RevitAPIUpdater

updater = RevitAPIUpdater()
result = updater.check_for_updates()
print(result)
```

### Test Smart Adapter:
```python
from api_learner import SmartAPIAdapter

adapter = SmartAPIAdapter(doc, 2023)
info = adapter.get_learner_info()
print(info)
```

## Logs & Debugging

### Enable Debug Logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Check Learner Status:
```python
if self.api_adapter:
    info = self.api_adapter.get_learner_info()
    print("Cache date:", info['cached_date'])
    print("Learned from:", info['learned_from'])
    print("Last web check:", info['last_web_check'])
```

### View Update Tracker:
```python
from api_updater import RevitAPIUpdater

updater = RevitAPIUpdater()
summary = updater.get_update_summary()
print("Last check:", summary['last_check'])
print("Known versions:", summary['known_versions'])
print("Latest version:", summary['latest_version'])
```

## Manual Operations

### Force Update:
```python
from api_updater import RevitAPIUpdater

updater = RevitAPIUpdater()
result = updater.check_for_updates()
```

### Clear Cache:
```python
import os
import shutil

cache_dir = os.path.join(os.path.expanduser('~'), '.t3lab', 'api_cache')
if os.path.exists(cache_dir):
    shutil.rmtree(cache_dir)
```

### Disable Auto-Update:
```python
from api_updater import RevitAPIUpdater

updater = RevitAPIUpdater()
updater.disable_auto_update()
```

### Enable Auto-Update:
```python
from api_updater import RevitAPIUpdater

updater = RevitAPIUpdater()
updater.enable_auto_update()
```

## API References

### RevitAPILearner
- `learn_from_web()` - Học API từ web
- `auto_update()` - Tự động update nếu cần
- `get_export_signature(type)` - Lấy export signature
- `supports_property(class, property)` - Check property support
- `get_version_notes()` - Lấy version notes

### SmartAPIAdapter
- `export_dwg(folder, filename, view_ids, options)` - Smart DWG export
- `export_pdf(folder, filename, view_ids, options)` - Smart PDF export
- `export_dwf(folder, filename, viewset, options)` - Smart DWF export
- `configure_dwg_options(options, mode)` - Configure DWG options
- `configure_pdf_options(options, ...)` - Configure PDF options
- `get_learner_info()` - Lấy thông tin learner

### RevitAPIUpdater
- `should_check_for_updates()` - Check nếu cần update
- `check_for_updates()` - Check updates từ web
- `fetch_api_info_for_version(version)` - Fetch API info
- `get_available_versions()` - Lấy danh sách versions
- `get_update_summary()` - Lấy update summary
- `enable_auto_update()` / `disable_auto_update()` - Toggle auto-update

## Best Practices

1. **Luôn dùng SmartAPIAdapter** khi có thể
2. **Fallback to manual** nếu adapter không available
3. **Check learner info** khi debug
4. **Monitor update notifications** để biết API changes
5. **Keep cache fresh** (30 days max)

## Future Enhancements

Các tính năng có thể thêm trong tương lai:

- [ ] Machine learning để predict API patterns
- [ ] Automatic code generation cho new APIs
- [ ] Multi-language documentation parsing
- [ ] API deprecation warnings
- [ ] Performance analytics
- [ ] Cloud sync for shared teams
- [ ] API migration assistant

---

**Developed by T3Lab**
**Version:** 1.0.0
**Last Updated:** December 2025
