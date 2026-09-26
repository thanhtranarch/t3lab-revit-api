#! python3
# -*- coding: utf-8 -*-
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
pushbutton_dir = os.path.dirname(__file__)
panel_dir = os.path.dirname(pushbutton_dir)
tab_dir = os.path.dirname(panel_dir)
extension_dir = os.path.dirname(tab_dir)
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

try:
    reload
except NameError:
    try:
        from importlib import reload
    except Exception:
        reload = None

if reload:
    if 'GUI.WPF_Base' in sys.modules:
        reload(sys.modules['GUI.WPF_Base'])
    if 'GUI.UIShowcaseDialog' in sys.modules:
        reload(sys.modules['GUI.UIShowcaseDialog'])
    elif 'UIShowcaseDialog' in sys.modules:
        reload(sys.modules['UIShowcaseDialog'])

from GUI.UIShowcaseDialog import show_ui_standard_showcase

if __name__ == '__main__':
    show_ui_standard_showcase()
