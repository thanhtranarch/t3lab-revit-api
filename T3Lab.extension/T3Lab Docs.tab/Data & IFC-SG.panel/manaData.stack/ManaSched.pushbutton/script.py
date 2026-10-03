#! python3
# -*- coding: utf-8 -*-
__title__ = "Schedule\nManager"
__author__ = "Tran Tien Thanh"
__doc__ = "Schedule Manager — unified Export/Import Excel and Schedule Duplication tool."

import os, sys
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

_ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_lib = os.path.join(_ext_dir, 'lib')
if _lib not in sys.path:
    sys.path.insert(0, _lib)

try:
    reload
except NameError:
    try:
        from importlib import reload
    except Exception:
        reload = None

if reload:
    for _mod in ('GUI.WPF_Base', 'GUI.ManaSchedDialog', 'ManaSchedDialog'):
        if _mod in sys.modules:
            try:
                reload(sys.modules[_mod])
            except Exception:
                pass

from GUI.ManaSchedDialog import show_schedule_manager

if __name__ == '__main__':
    show_schedule_manager(os.path.dirname(__file__), __revit__)
