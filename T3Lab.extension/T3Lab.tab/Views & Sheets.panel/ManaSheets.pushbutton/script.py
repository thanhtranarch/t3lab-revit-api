#! python3
# -*- coding: utf-8 -*-
"""
Sheet Manager
Unified tool to manage sheets, sets, views on sheets, parameters, and re-number sheets.

Copyright (c) 2026 T3Lab
"""
__title__ = "Sheet\nManager"
__author__ = "Dang Quoc Truong & Antigravity"

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

# Ensure lib directory is in sys.path
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

try:
    reload
except NameError:
    try:
        from importlib import reload
    except Exception:
        reload = None

if reload:
    # Nạp lại bộ lọc cột TRƯỚC dialog: dialog `from ... import` class này nên
    # nếu module cũ còn trong sys.modules thì click lần 2 vẫn chạy code cũ.
    # An toàn để reload vì nó chỉ chứa class Python thuần (không kế thừa CLR,
    # không `__namespace__`) — xem CLAUDE.md · S15/S17.
    if 'GUI.DataGridColumnFilter' in sys.modules:
        reload(sys.modules['GUI.DataGridColumnFilter'])
    if 'GUI.ManaSheetsDialog' in sys.modules:
        reload(sys.modules['GUI.ManaSheetsDialog'])
    elif 'ManaSheetsDialog' in sys.modules:
        reload(sys.modules['ManaSheetsDialog'])

from GUI.ManaSheetsDialog import show_sheet_manager

if __name__ == '__main__':
    show_sheet_manager()
