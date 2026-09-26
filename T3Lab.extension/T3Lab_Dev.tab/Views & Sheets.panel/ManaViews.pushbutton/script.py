#! python3
# -*- coding: utf-8 -*-
"""
View Manager
Unified tool to manage views and view templates.

Copyright (c) 2026 T3Lab
"""
__title__ = "View\nManager"
__author__ = "Dang Quoc Truong & Antigravity"

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
# CPython engine paths come from lib/_cpython_bootstrap.py below - it finds
# the engine whatever the pyRevit clone is named or wherever it is installed.

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
    if 'GUI.ManaViewsDialog' in sys.modules:
        reload(sys.modules['GUI.ManaViewsDialog'])
    elif 'ManaViewsDialog' in sys.modules:
        reload(sys.modules['ManaViewsDialog'])

from GUI.ManaViewsDialog import show_view_manager

if __name__ == '__main__':
    show_view_manager()
