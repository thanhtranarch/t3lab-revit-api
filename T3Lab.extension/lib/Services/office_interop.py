# -*- coding: utf-8 -*-
"""
Office interop guard.

Excel is an OPTIONAL dependency: IFC-SG, Parameter Manager and Sheet Manager
read and write .xlsx through Microsoft.Office.Interop.Excel, which only resolves
when the desktop Excel is installed. On a machine without it,
``clr.AddReference`` throws a raw .NET FileNotFoundException that Revit shows as
the generic "Command Failure for External Command" dialog — telling the user
nothing about what is actually missing.

Route every Excel import through ``require_excel()`` so that machine reports a
sentence the user can act on instead.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__ = "Office Interop"

EXCEL_MISSING_MESSAGE = (
    "Microsoft Excel is required for this action but is not available on this "
    "machine.\n\n"
    "Install the Microsoft Excel desktop application, or use the CSV import / "
    "export instead."
)


class ExcelNotAvailable(Exception):
    """Raised instead of a raw COM/CLR error when Excel interop cannot load."""


def excel_available():
    """True when Microsoft.Office.Interop.Excel can be loaded. Never raises."""
    try:
        require_excel()
        return True
    except Exception:
        return False


def require_excel():
    """Return the Excel interop namespace, or raise ExcelNotAvailable.

    The caller shows ``str(exc)`` — it is already a complete, English,
    user-facing message.
    """
    try:
        import clr
        clr.AddReference('Microsoft.Office.Interop.Excel')
        from Microsoft.Office.Interop import Excel
        return Excel
    except Exception as exc:
        raise ExcelNotAvailable('%s\n\n(%s)' % (EXCEL_MISSING_MESSAGE, exc))
