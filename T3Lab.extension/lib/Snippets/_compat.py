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


# ── Rebar & Assembly helpers (2026-10-02) ────────────────────────────────────
# Spec: dev/plan/rebar-tekla-implementation-spec.md §3.1. Revit 2027 removed
# RebarHookOrientation and added BarTerminationsData (2026) / mass members
# (2027), so every name that moved is looked up BY STRING here and nowhere
# else: `getattr(module, "Name", None)` keeps the compat audit and the import
# of this module green on every release 2022-2027.

FEET_TO_MM = 304.8
FALLBACK_REVIT_YEAR = 2023

# Rebar bar type parameter kept by T3Lab on releases without native mass.
WEIGHT_PARAMETER = "T3_WeightPerMetre"
# Revit 2027 mass per length is converted to kg/m; anything outside this range
# is a unit mistake (Ø6 = 0.22 kg/m, Ø50 = 15.4 kg/m), not a real bar.
MASS_PER_METRE_RANGE = (0.1, 20.0)


def to_mm(feet):
    """Feet (Revit internal length) -> millimetres. Pure."""
    return float(feet) * FEET_TO_MM


def to_feet(mm):
    """Millimetres -> feet (Revit internal length). Pure."""
    return float(mm) / FEET_TO_MM


def short_error(exc):
    """First line of an exception message, for one-line user text. Pure.

    Order: `exc.Message` (.NET exceptions), `str(exc)`, class name.
    """
    text = ""
    try:
        text = str(getattr(exc, "Message", None) or "")
    except Exception:
        text = ""
    if not text.strip():
        try:
            text = str(exc)
        except Exception:
            text = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return type(exc).__name__


def revit_year(doc=None):
    """Revit release year as int (Snippets._host.get_revit_version); never raises."""
    try:
        from Snippets._host import get_revit_version
        return int(get_revit_version(doc))
    except Exception:
        return FALLBACK_REVIT_YEAR


def _revit_type(name):
    """A type from Autodesk.Revit.DB.Structure / Autodesk.Revit.DB by name, or None.

    Rebar types live in the .Structure namespace, the rest in .DB; looking
    them up by string keeps a release that removed the type from breaking
    the import of this module.
    """
    import importlib
    for module_name in ("Autodesk.Revit.DB.Structure", "Autodesk.Revit.DB"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        found = getattr(module, name, None)
        if found is not None:
            return found
    return None


def pick_enum(enum_type, candidates):
    """First member of `enum_type` named in `candidates` (priority order), else None.

    Used for PostableCommand / RebarTerminationOrientation names that changed
    between releases. Never raises.
    """
    if enum_type is None:
        return None
    for name in candidates or ():
        try:
            member = getattr(enum_type, name, None)
        except Exception:
            member = None
        if member is not None:
            return member
    return None


def postable_command_id(candidates):
    """RevitCommandId of the first PostableCommand named in `candidates` that
    exists on this release, or None. Swallows every exception."""
    try:
        from Autodesk.Revit.UI import PostableCommand, RevitCommandId
    except Exception:
        return None
    for name in candidates or ():
        try:
            member = getattr(PostableCommand, name, None)
            if member is None:
                continue
            command_id = RevitCommandId.LookupPostableCommandId(member)
            if command_id is not None:
                return command_id
        except Exception:
            continue
    return None


def bar_nominal_diameter_mm(bar_type):
    """RebarBarType.BarNominalDiameter in mm (there is no BarDiameter property)."""
    return to_mm(bar_type.BarNominalDiameter)


def bar_mass_per_metre(bar_type):
    """(kg per metre, source) for a RebarBarType; source in
    {"revit", "T3_WeightPerMetre", None}.

    1. Revit 2027+ `BarMassPerUnitLength`, converted with
       UnitUtils.ConvertFromInternalUnits(v, UnitTypeId.KilogramsPerMeter).
       [NV] the internal unit is unconfirmed (spike G15): a value outside
       MASS_PER_METRE_RANGE after conversion is treated as unusable.
    2. Shared / project parameter `T3_WeightPerMetre` (a plain number, kg/m).
    3. (None, None) - the caller falls back to rebar_weights.json / 0.006165*d^2.
    """
    if bar_type is None:
        return None, None
    low, high = MASS_PER_METRE_RANGE

    try:
        native = getattr(bar_type, "BarMassPerUnitLength", None)
    except Exception:
        native = None
    if native is not None:
        try:
            from Autodesk.Revit.DB import UnitUtils, UnitTypeId
            value = float(UnitUtils.ConvertFromInternalUnits(
                float(native), UnitTypeId.KilogramsPerMeter))
            if low <= value <= high:
                return value, "revit"
        except Exception:
            pass

    try:
        param = bar_type.LookupParameter(WEIGHT_PARAMETER)
        if param is not None and param.HasValue:
            value = float(param.AsDouble())
            if value > 0.0:
                return value, WEIGHT_PARAMETER
    except Exception:
        pass
    return None, None


def _partition_parameter_raw(element):
    """The Partition parameter of an element (read-only or not), or None."""
    if element is None:
        return None
    param = None
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        built_in = getattr(BuiltInParameter, "NUMBER_PARTITION_PARAM", None)
        if built_in is not None:
            param = element.get_Parameter(built_in)
    except Exception:
        param = None
    if param is None:
        try:
            param = element.LookupParameter("Partition")
        except Exception:
            param = None
    return param


def partition_parameter(element):
    """Writable Partition parameter of `element`, or None when absent / read-only."""
    param = _partition_parameter_raw(element)
    if param is None:
        return None
    try:
        if param.IsReadOnly:
            return None
    except Exception:
        return None
    return param


def rebar_number_text(element):
    """'Rebar Number' as text ('' when unset or absent).

    BuiltInParameter.REBAR_NUMBER was not confirmed in the 2027 enum, so it is
    only used when present; otherwise the parameter is read by name.
    """
    if element is None:
        return ""
    param = None
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        built_in = getattr(BuiltInParameter, "REBAR_NUMBER", None)
        if built_in is not None:
            param = element.get_Parameter(built_in)
    except Exception:
        param = None
    if param is None:
        try:
            param = element.LookupParameter("Rebar Number")
        except Exception:
            param = None
    if param is None:
        return ""
    try:
        text = param.AsString() or param.AsValueString() or ""
    except Exception:
        text = ""
    return text


def _numbering_parameter_id(numbering_parameter):
    """Id of a NumberingParameter. [NV] member name: .ParameterId or .Id."""
    for member in ("ParameterId", "Id"):
        try:
            value = getattr(numbering_parameter, member, None)
        except Exception:
            value = None
        if value is not None:
            return eid_value(value)
    return None


def numbering_partitions_by_partition_param(doc):
    """Revit 2027+: does any enabled rebar NumberingSchema partition by Partition?

    True / False on 2027+, None on <= 2026 or on any error. A schema counts
    when it is enabled, its scope includes OST_Rebar (an unreadable or empty
    scope is taken as "includes") and GetPartitioningParameters() contains
    NUMBER_PARTITION_PARAM. [NV] NumberingParameter member names (spike G11).
    """
    try:
        if revit_year(doc) < 2027:
            return None
        from Autodesk.Revit.DB import BuiltInCategory, BuiltInParameter
        schema_type = _revit_type("NumberingSchema")
        if schema_type is None:
            return None
        partition_id = int(getattr(BuiltInParameter, "NUMBER_PARTITION_PARAM"))
        rebar_cat = int(getattr(BuiltInCategory, "OST_Rebar"))
        for schema in schema_type.GetSchemasInDocument(doc):
            try:
                if not schema.Enabled:
                    continue
            except Exception:
                pass
            scope = []
            try:
                for item in schema.GetScopeDefiningCategories():
                    scope.append(eid_value(getattr(item, "Id", item)))
            except Exception:
                scope = []
            if scope and rebar_cat not in scope:
                continue
            for number_param in schema.GetPartitioningParameters():
                if _numbering_parameter_id(number_param) == partition_id:
                    return True
        return False
    except Exception:
        return None


def _fallthrough_errors():
    """Exceptions meaning "this overload does not exist on this release"."""
    errors = [AttributeError, TypeError, ImportError, NotImplementedError]
    try:
        from System import MissingMemberException
        errors.append(MissingMemberException)
    except Exception:
        pass
    return tuple(errors)


def _set_member(obj, name, value):
    """obj.<name> = value, but an unknown member raises AttributeError.

    pythonnet would otherwise accept a mistyped member name on some objects
    without effect; the spec's BarTerminationsData member names are [NV].
    """
    if not hasattr(obj, name):
        raise AttributeError("%s has no member %s" % (type(obj).__name__, name))
    setattr(obj, name, value)


def create_rebar_from_curves(doc, style, bar_type, host, normal, curves,
                             hook_start=None, hook_end=None,
                             orient_start="Left", orient_end="Left",
                             use_existing_shape=True, create_new_shape=False):
    """Rebar.CreateFromCurves that works on Revit 2022-2027 (spec D16).

    1. 2026+: the BarTerminationsData overload.
    2. 2022-2025 (and a 2026 whose new overload is unusable): the legacy
       overload with RebarHookOrientation, resolved by name because the enum
       is removed in 2027.

    A genuine Revit error from the first overload (bad curves, host not valid)
    is raised as is; only "overload / member not found" falls through. When
    both overloads were tried and failed the RuntimeError names both.
    """
    rebar_type = _revit_type("Rebar")
    curve_type = _revit_type("Curve")
    element_id_type = _revit_type("ElementId")
    if rebar_type is None or curve_type is None:
        raise RuntimeError("Rebar API is not available in this Revit session.")
    curve_list = net_list(curve_type, curves)
    fallthrough = _fallthrough_errors()

    new_error = None
    terminations_type = _revit_type("BarTerminationsData")
    if terminations_type is not None:
        try:
            invalid = element_id_type.InvalidElementId
            data = terminations_type(doc)
            _set_member(data, "HookTypeIdAtStart",
                        hook_start.Id if hook_start is not None else invalid)
            _set_member(data, "HookTypeIdAtEnd",
                        hook_end.Id if hook_end is not None else invalid)
            orientation_type = _revit_type("RebarTerminationOrientation")
            start_o = pick_enum(orientation_type, [orient_start]) if orientation_type else None
            end_o = pick_enum(orientation_type, [orient_end]) if orientation_type else None
            if start_o is not None:
                _set_member(data, "TerminationOrientationAtStart", start_o)
            if end_o is not None:
                _set_member(data, "TerminationOrientationAtEnd", end_o)
            return rebar_type.CreateFromCurves(
                doc, style, bar_type, host, normal, curve_list, data,
                use_existing_shape, create_new_shape)
        except fallthrough as exc:
            new_error = exc

    legacy_error = None
    orientation_enum = _revit_type("RebarHookOrientation")
    try:
        if orientation_enum is None:
            raise AttributeError(
                "RebarHookOrientation is not available on Revit %s" % revit_year(doc))
        start_o = pick_enum(orientation_enum, [orient_start])
        end_o = pick_enum(orientation_enum, [orient_end])
        if start_o is None or end_o is None:
            raise AttributeError(
                "hook orientation %r / %r is not a RebarHookOrientation member"
                % (orient_start, orient_end))
        return rebar_type.CreateFromCurves(
            doc, style, bar_type, hook_start, hook_end, host, normal, curve_list,
            start_o, end_o, use_existing_shape, create_new_shape)
    except Exception as exc:
        legacy_error = exc

    if new_error is None:
        # Only the legacy overload was available: its error is the real one.
        raise legacy_error
    raise RuntimeError(
        "Rebar.CreateFromCurves failed. BarTerminationsData overload: %s. "
        "Legacy RebarHookOrientation overload: %s."
        % (short_error(new_error), short_error(legacy_error)))


def add_to_assembly_of(doc, host, new_ids):
    """Add freshly created elements to the assembly that owns `host` (rule A2).

    Returns (added_count, assembly_id_or_None, error_text_or_None). Does nothing
    when the host is not in an assembly. Call it inside the caller's
    transaction - AddMemberIds needs one open.
    """
    try:
        assembly_id = eid_value(host.AssemblyInstanceId)
    except Exception as exc:
        return 0, None, short_error(exc)
    if assembly_id < 0:
        return 0, None, None
    try:
        from Autodesk.Revit.DB import ElementId
        assembly = doc.GetElement(make_eid(assembly_id))
        if assembly is None:
            return 0, assembly_id, "assembly %d no longer exists" % assembly_id
        wanted = []
        for value in new_ids or ():
            eid = make_eid(eid_value(value))
            try:
                if assembly.IsMember(eid):
                    continue
            except Exception:
                pass
            wanted.append(eid)
        if not wanted:
            return 0, assembly_id, None
        assembly.AddMemberIds(net_list(ElementId, wanted))
        return len(wanted), assembly_id, None
    except Exception as exc:
        return 0, assembly_id, short_error(exc)
