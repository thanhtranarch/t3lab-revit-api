# -*- coding: utf-8 -*-
"""
Revit API version-compatibility shims.

Author: Tran Tien Thanh
"""


def eid_value(element_id):
    """Return the integer value of an ElementId, version-safe.

    Revit 2024+ replaced ElementId.IntegerValue with ElementId.Value (Int64).
    Falls back to IntegerValue for Revit 2023 and earlier.
    """
    if element_id is None:
        return -1
    if isinstance(element_id, (int, float)):
        return int(element_id)
    try:
        return int(element_id.Value)          # Revit 2024+ (Int64 -> plain int)
    except Exception:
        try:
            return int(element_id.IntegerValue)   # Revit 2023 and earlier
        except Exception:
            return -1


# Cached constructor strategy: None = not probed yet, False = ElementId(int)
# works (Revit 2024 and earlier), True = must wrap in System.Int64 (2025+).
_EID_NEEDS_INT64 = None


def make_eid(value):
    """Construct an ElementId from an integer, version-safe.

    Revit 2025+ removed the ElementId(Int32) constructor; IronPython then
    cannot pick between the remaining overloads (Int64 / BuiltInCategory /
    BuiltInParameter) for a plain Python int and raises TypeError
    "Multiple targets could match". Wrapping the value in System.Int64
    forces the Int64 overload. Counterpart of eid_value().
    """
    global _EID_NEEDS_INT64
    from Autodesk.Revit.DB import ElementId
    if value is None:
        return ElementId.InvalidElementId
    if isinstance(value, ElementId):
        return value
    v = int(value)
    if _EID_NEEDS_INT64:
        import System
        return ElementId(System.Int64(v))
    try:
        eid = ElementId(v)
        _EID_NEEDS_INT64 = False
        return eid
    except (TypeError, OverflowError):
        _EID_NEEDS_INT64 = True
        import System
        return ElementId(System.Int64(v))


def elem_name(element):
    """Return element.Name, IronPython-safe.

    Some Element subclasses (FamilySymbol, ElementType, GroupType, ...) hide
    the Name property getter from IronPython, so `element.Name` raises
    `AttributeError: Name` even though the element has a perfectly good name.
    Reading through the base Element property descriptor always works.
    (Only the getter is affected; `element.Name = x` assignment works fine.)
    """
    try:
        return element.Name
    except AttributeError:
        from Autodesk.Revit.DB import Element
        return Element.Name.GetValue(element)


class disposing(object):
    """``with disposing(DB.Transaction(doc, "Name")) as t:`` — `with` cho object .NET.

    IronPython biến mọi ``IDisposable`` thành context manager, nên code cũ viết
    ``with DB.Transaction(doc, "x") as t:``. pythonnet 3 (CPython) thì KHÔNG:
    dòng đó ném ``TypeError: 'Transaction' object does not support the context
    manager protocol`` và thao tác chính của tool chết ngay (Datum Sync,
    2026-09-29). Dùng cho Transaction / TransactionGroup / SubTransaction /
    FilteredElementCollector.

    Giữ đúng ngữ nghĩa của IronPython: trả về chính object (KHÔNG tự Start —
    thân khối vẫn gọi ``t.Start()`` / ``t.Commit()``), và lúc ra khỏi khối,
    kể cả khi có exception, rollback phần còn mở rồi ``Dispose()``.
    Exception trong khối vẫn được ném tiếp.
    """

    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self.obj

    def __exit__(self, exc_type, exc_value, traceback):
        obj = self.obj
        try:
            if obj.HasStarted() and not obj.HasEnded():
                obj.RollBack()
        except Exception:
            pass                # collectors have no HasStarted; nothing to roll back
        try:
            obj.Dispose()
        except Exception:
            pass
        return False


def net_list(item_type, items):
    """``List[item_type]`` .NET dựng từ bất kỳ iterable nào (list Python hay collection .NET).

    Dưới PythonNet 3 (CPython), ``List[ElementId](python_list)`` KHÔNG còn tự
    chuyển list Python sang ``IEnumerable<T>`` khi chọn overload, nên ném
    ``No method matches given arguments for List`1..ctor: (<class 'list'>)``.
    Dựng list rỗng rồi ``Add`` từng phần tử thì chạy trên mọi engine.
    """
    from System.Collections.Generic import List
    out = List[item_type]()
    for item in items or ():
        if item is not None:
            out.Add(item)
    return out


# ── Family parameters (FamilyManager) ────────────────────────────────────────
# Revit 2022 added the ForgeTypeId overload
#   FamilyManager.AddParameter(name, GroupTypeId, SpecTypeId, isInstance);
# the BuiltInParameterGroup / ParameterType overload is gone on 2023 / 2025+.
# Every supported release (2022-2027) has the ForgeTypeId one, so it is tried
# first; the legacy enums are only looked up by name, inside the fallback.
_FAMILY_PARAM_GROUPS = {
    'materials': ('Materials', 'PG_MATERIALS'),
    'geometry': ('Geometry', 'PG_GEOMETRY'),
    'data': ('Data', 'PG_DATA'),
    'text': ('Text', 'PG_TEXT'),
}
_FAMILY_PARAM_SPECS = {
    'material': (('Reference', 'Material'), 'Material'),
    'length': (('Length',), 'Length'),
    'number': (('Number',), 'Number'),
    'integer': (('Int', 'Integer'), 'Integer'),
    'text': (('String', 'Text'), 'Text'),
}


def _spec_type_id(spec):
    from Autodesk.Revit import DB as _DB
    value = _DB.SpecTypeId
    for attr in _FAMILY_PARAM_SPECS[spec][0]:
        value = getattr(value, attr)
    return value


def add_family_parameter(family_manager, name, group, spec, is_instance=False):
    """Add a family parameter, version-safe.

    `group`: 'materials' | 'geometry' | 'data' | 'text'.
    `spec`: 'material' | 'length' | 'number' | 'integer' | 'text'.
    """
    from Autodesk.Revit import DB as _DB
    try:
        group_id = getattr(_DB.GroupTypeId, _FAMILY_PARAM_GROUPS[group][0])
        spec_id = _spec_type_id(spec)
    except AttributeError:
        # Pre-2022 API only (outside the supported range): legacy enums by name.
        legacy_group = getattr(getattr(_DB, 'BuiltInParameterGroup'), _FAMILY_PARAM_GROUPS[group][1])
        legacy_type = getattr(getattr(_DB, 'ParameterType'), _FAMILY_PARAM_SPECS[spec][1])
        return family_manager.AddParameter(name, legacy_group, legacy_type, bool(is_instance))
    return family_manager.AddParameter(name, group_id, spec_id, bool(is_instance))


def _type_id_stem(forge_type_id):
    """'autodesk.spec.aec:length-2.0.0' -> 'autodesk.spec.aec:length'."""
    try:
        return (forge_type_id.TypeId or '').rsplit('-', 1)[0]
    except Exception:
        return ''


def family_parameter_kind(definition, storage_type=None):
    """'length' | 'number' | 'integer' | 'text' | 'material' | 'other'.

    Reads Definition.GetDataType() (2022+). Without it, falls back on the
    parameter's StorageType, treating every Double as a length - FamiGen's
    behaviour before data types existed.
    """
    try:
        stem = _type_id_stem(definition.GetDataType())
    except Exception:
        stem = ''
    if stem:
        for kind in ('length', 'number', 'integer', 'text', 'material'):
            try:
                if stem == _type_id_stem(_spec_type_id(kind)):
                    return kind
            except Exception:
                continue
        return 'other'
    name = str(storage_type or '')
    return {'Double': 'length', 'Integer': 'integer', 'String': 'text',
            'ElementId': 'material'}.get(name.rsplit('.', 1)[-1], 'other')
