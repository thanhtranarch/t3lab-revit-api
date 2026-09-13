#! python3
# -*- coding: utf-8 -*-
"""
DQT - Background Theme (Theme Studio)
Full control over the Revit canvas appearance from one themed window:

* Model Background  — HSV colour picker (SV square + hue bar), RGB sliders,
  HEX input, screen eyedropper, live-apply, named custom presets, recent
  colours. Sets Application.BackgroundColor.
* 3D Gradient       — per-3D-view gradient background (Sky / Horizon /
  Ground) with ready-made gradient presets, applied to the active 3D view
  or every 3D view in the project.
* Revit UI Theme    — switch Light / Dark UI theme and canvas theme via
  UIThemeManager (Revit 2024+; hidden on older versions).

SHIFT+Click quick-cycles Black -> Gray -> White like the classic tool.

Dang Quoc Truong - DQT (c) 2026
"""

__title__     = "Background\nTheme"
__author__    = "Dang Quoc Truong (DQT)"
__version__   = "2.0.0"
__copyright__ = "Copyright (c) 2026 by Dang Quoc Truong (DQT)"
__doc__       = """DQT - Background Theme (Theme Studio)

Open a 3-tab theme studio:
Model Background (HSV picker + eyedropper + presets + recents),
3D Gradient (Sky/Horizon/Ground background for 3D views),
Revit UI Theme (Light/Dark, Revit 2024+).

SHIFT+Click quick-cycles Black -> Gray -> White like the classic tool.
Works on Revit 2024 / 2025 / 2026 / 2027 (UI theme tab needs 2024+).
"""

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
import os, sys

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

from Services.bg_theme_service import (
    quick_cycle, load_config, PRESETS, get_theme_callbacks
)
from GUI.BGThemeDialog import show_bg_theme_dialog

try:
    SHIFT_CLICK = __shiftclick__
except Exception:
    SHIFT_CLICK = False


def main():
    if SHIFT_CLICK:
        quick_cycle()
    else:
        config = load_config()
        callbacks = get_theme_callbacks()
        show_bg_theme_dialog(config, PRESETS, callbacks)


if __name__ == "__main__":
    main()
