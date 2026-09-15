# -*- coding: utf-8 -*-
"""pyDQT - Select Tools | Compatibility helpers.

Handles Revit 2024-2027 API breaking changes so the rest
of the suite can stay version agnostic.

Key breaking changes handled here:
    - ElementId.IntegerValue (<=2023)  ->  ElementId.Value (2024+)
    - ElementId(int) constructor became AMBIGUOUS in 2024+ because a new
      ElementId(Int64) overload was added alongside ElementId(BuiltInParameter)
      and ElementId(BuiltInCategory). Passing a plain python int makes
      IronPython raise:
          TypeError: Multiple targets could match:
          ElementId(BuiltInParameter), ElementId(BuiltInCategory), ElementId(Int64)
      Fix: never construct ElementId(-1); use ElementId.InvalidElementId,
      and when an int is genuinely needed, cast to System.Int64 explicitly.

Dang Quoc Truong - DQT (c) 2026
"""

from System import Int64
from System.Collections.Generic import List
from Autodesk.Revit.DB import ElementId

# Static property -> exists in every Revit version, avoids the ambiguous
# ElementId(-1) constructor call entirely.
INVALID_ELEMENT_ID = ElementId.InvalidElementId

# Plain integer of the invalid id, handy for comparisons.
INVALID_ELEMENT_ID_INT = -1


def make_eid(value):
    """Build an ElementId from a python int, safely across all versions.

    In Revit 2024+ ``ElementId(some_python_int)`` is ambiguous between the
    Int64 / BuiltInParameter / BuiltInCategory overloads. We force the
    Int64 overload by casting first; on older Revit we fall back to the
    Int32 constructor.

    :param value: int
    :return: ElementId
    """
    try:
        return ElementId(Int64(value))
    except (TypeError, OverflowError):
        return ElementId(int(value))


def eid_int(element_id):
    """Return the integer value of an ElementId across all Revit versions.

    Revit 2024+ replaced ``ElementId.IntegerValue`` with ``ElementId.Value``.
    We try the new property first, then fall back to the legacy one.

    :param element_id: ElementId instance
    :return: int value of the ElementId (python int)
    """
    val = getattr(element_id, 'Value', None)
    if val is None:
        val = getattr(element_id, 'IntegerValue', None)
    # ElementId.Value is a System.Int64 on 2024+; normalise to python int.
    return int(val)


def to_element_id_list(ids):
    """Build a .NET ``List[ElementId]`` from a python iterable of ElementId.

    WPF / Revit selection APIs require a typed .NET list, a plain python
    list will not work for ``Selection.SetElementIds``.

    Build it empty and ``Add`` each item -- do NOT pass a python list to the
    constructor. Under PythonNet 3 (CPython) the python list no longer
    converts to ``IEnumerable<ElementId>`` during overload resolution, so
    ``List[ElementId]([...])`` raises
    ``No method matches given arguments for List`1..ctor: (<class 'list'>)``.

    :param ids: iterable of ElementId (or int element ids)
    :return: List[ElementId]
    """
    out = List[ElementId]()
    for value in ids:
        if value is None:
            continue
        if not isinstance(value, ElementId):
            value = make_eid(value)
        out.Add(value)
    return out


def notify(message, title='DQT - Select'):
    """Lightweight result notification.

    We deliberately avoid ``forms.toast`` because it is Windows-10 only,
    has Unicode issues, and silently fails on some pyRevit builds. A plain
    ``forms.alert`` works everywhere and across Revit 2024-2027.
    """
    try:
        from pyrevit import forms
        forms.alert(message, title=title)
    except Exception:
        # Never let a notification crash the actual operation
        print('{}: {}'.format(title, message))
