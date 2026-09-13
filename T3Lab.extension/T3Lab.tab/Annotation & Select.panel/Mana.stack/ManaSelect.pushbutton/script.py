#! python3
# -*- coding: utf-8 -*-
"""ManaSelect — Unified smart selection manager.

Consolidates:
  - Explore (counted tree: Category > Family > Type, with scope/sort/filter)
  - Quick Select (Query by parameters/text)
  - Select Similar (Match type/family/category)
  - Select on Sheets (Title blocks & CAD imports)
  - Warnings (model warnings and the elements they flag)

Cửa sổ chạy MODELESS để người dùng bấm chọn trong model mà tool vẫn mở. Điều
đó cần engine thường trú, nên `__persistentengine__ = True` ở dưới; nếu engine
chưa được kích hoạt (chưa reload pyRevit sau khi thêm cờ này), `show_dialog()`
tự rơi về modal thay vì mở ra một cửa sổ chết.

Author: T3Lab
"""
__title__ = "Mana\nSelect"
__author__ = "T3Lab"
__persistentengine__ = True

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

# Add lib directory to system path
# __file__ is T3Lab.extension/T3Lab.tab/Annotation & Select.panel/Mana.stack/ManaSelect.pushbutton/script.py
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# Import and show the dialog
import GUI.ManaSelectDialog as ManaSelectDialog

if __name__ == '__main__':
    # modal=None: show_dialog() tự dò engine thường trú và chọn modeless/modal.
    ManaSelectDialog.show_dialog()
