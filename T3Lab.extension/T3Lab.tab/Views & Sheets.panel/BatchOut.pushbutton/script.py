#! python3
# -*- coding: utf-8 -*-
"""
Batch Out

Batch export sheets and views to PDF, DWG, DWF, NWD, IFC and image formats.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Batch Out"
__version__ = "1.0.0"
__persistentengine__ = True

import os
import sys

_script_dir = os.path.dirname(__file__)
_ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(_script_dir)))
_lib_dir = os.path.join(_ext_dir, 'lib')
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass

from GUI.BatchOutDialog import ExportManagerWindow, show_batchout_dialog

if __name__ == '__main__':
    show_batchout_dialog()
