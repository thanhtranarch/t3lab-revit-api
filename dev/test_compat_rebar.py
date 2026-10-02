# -*- coding: utf-8 -*-
"""
Tests for the rebar / assembly helpers appended to Snippets/_compat.py:
unit conversion, error text, enum picking, the version-proof
create_rebar_from_curves (BarTerminationsData on 2026+, RebarHookOrientation
on 2022-2025) and the small parameter readers.

Revit is replaced by fake `Autodesk.Revit.*` modules installed for the
duration of a test, so the shipped _compat source is what runs.
Run: python dev/test_compat_rebar.py
"""
import contextlib
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from Snippets import _compat as compat     # noqa: E402


# ── fake Revit ───────────────────────────────────────────────────────────────

class FakeElementId(object):
    InvalidElementId = None

    def __init__(self, value):
        self.Value = int(value)

    def __repr__(self):
        return "Id(%d)" % self.Value


FakeElementId.InvalidElementId = FakeElementId(-1)


class FakeCurve(object):
    pass


def _enum(class_name, *names):
    cls = type(class_name, (), {})
    for name in names:
        setattr(cls, name, "%s.%s" % (class_name, name))
    return cls


class FakeRebar(object):
    """Records every CreateFromCurves call; `reject_arg_counts` simulates a
    release that has no such overload (pythonnet raises TypeError)."""
    calls = []
    reject_arg_counts = ()
    raise_instead = None

    @classmethod
    def CreateFromCurves(cls, *args):
        cls.calls.append(args)
        if cls.raise_instead is not None and len(args) not in cls.reject_arg_counts:
            raise cls.raise_instead
        if len(args) in cls.reject_arg_counts:
            raise TypeError("No method matches given arguments for CreateFromCurves")
        return ("rebar", len(args))


class FakeTerminations(object):
    def __init__(self, doc):
        self.doc = doc
        self.HookTypeIdAtStart = None
        self.HookTypeIdAtEnd = None
        self.TerminationOrientationAtStart = None
        self.TerminationOrientationAtEnd = None


@contextlib.contextmanager
def fake_revit(structure=None, db=None, ui=None):
    """Install fake Autodesk.Revit.DB / .DB.Structure / .UI modules."""
    names = ['Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB',
             'Autodesk.Revit.DB.Structure', 'Autodesk.Revit.UI']
    saved = dict((n, sys.modules.get(n)) for n in names)
    mods = dict((n, types.ModuleType(n)) for n in names)
    mods['Autodesk'].Revit = mods['Autodesk.Revit']
    mods['Autodesk.Revit'].DB = mods['Autodesk.Revit.DB']
    mods['Autodesk.Revit'].UI = mods['Autodesk.Revit.UI']
    mods['Autodesk.Revit.DB'].Structure = mods['Autodesk.Revit.DB.Structure']
    mods['Autodesk.Revit.DB'].ElementId = FakeElementId
    mods['Autodesk.Revit.DB'].Curve = FakeCurve
    for key, value in (structure or {}).items():
        setattr(mods['Autodesk.Revit.DB.Structure'], key, value)
    for key, value in (db or {}).items():
        setattr(mods['Autodesk.Revit.DB'], key, value)
    for key, value in (ui or {}).items():
        setattr(mods['Autodesk.Revit.UI'], key, value)
    sys.modules.update(mods)
    old_net_list = compat.net_list
    compat.net_list = lambda item_type, items: [i for i in (items or ()) if i is not None]
    old_flag = compat._EID_NEEDS_INT64
    compat._EID_NEEDS_INT64 = None
    try:
        yield
    finally:
        compat.net_list = old_net_list
        compat._EID_NEEDS_INT64 = old_flag
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class FakeDoc(object):
    def __init__(self, year=2027, elements=None):
        self.Application = types.SimpleNamespace(VersionNumber=str(year))
        self.elements = elements or {}

    def GetElement(self, eid):
        return self.elements.get(eid.Value)


class Hook(object):
    def __init__(self, ident):
        self.Id = FakeElementId(ident)


# ── tests ────────────────────────────────────────────────────────────────────

class TestUnitsAndErrors(unittest.TestCase):

    def test_to_mm_feet_roundtrip(self):
        self.assertAlmostEqual(compat.to_mm(1.0), 304.8)
        self.assertAlmostEqual(compat.to_feet(304.8), 1.0)
        for mm in (0.0, 12.0, 400.0, 6000.5):
            self.assertAlmostEqual(compat.to_mm(compat.to_feet(mm)), mm, places=9)
        self.assertAlmostEqual(compat.to_feet(compat.to_mm(2.5)), 2.5, places=12)

    def test_short_error_first_line(self):
        self.assertEqual(compat.short_error(ValueError("first line\nsecond line")), "first line")
        self.assertEqual(compat.short_error(ValueError("\n\n  real text  \nmore")), "real text")

    def test_short_error_prefers_dotnet_message(self):
        class DotNetError(Exception):
            Message = "Revit says no\nstack trace here"
        self.assertEqual(compat.short_error(DotNetError("ignored")), "Revit says no")

    def test_short_error_falls_back_to_class_name(self):
        self.assertEqual(compat.short_error(KeyError()), "KeyError")
        self.assertEqual(compat.short_error(RuntimeError("")), "RuntimeError")

    def test_revit_year_never_raises(self):
        self.assertIsInstance(compat.revit_year(FakeDoc(2026)), int)
        self.assertEqual(compat.revit_year(FakeDoc(2026)), 2026)
        self.assertIsInstance(compat.revit_year(object()), int)
        self.assertIsInstance(compat.revit_year(), int)

    def test_bar_nominal_diameter_mm(self):
        bar = types.SimpleNamespace(BarNominalDiameter=compat.to_feet(16.0))
        self.assertAlmostEqual(compat.bar_nominal_diameter_mm(bar), 16.0)


class TestPickEnum(unittest.TestCase):

    def test_pick_enum_first_candidate(self):
        enum = _enum("RebarTerminationOrientation", "Left", "Right")
        self.assertEqual(compat.pick_enum(enum, ["Right", "Left"]), "RebarTerminationOrientation.Right")
        self.assertEqual(compat.pick_enum(enum, ["Missing", "Left"]), "RebarTerminationOrientation.Left")

    def test_pick_enum_none_when_nothing_matches(self):
        enum = _enum("E", "A")
        self.assertIsNone(compat.pick_enum(enum, ["B", "C"]))
        self.assertIsNone(compat.pick_enum(enum, []))
        self.assertIsNone(compat.pick_enum(enum, None))
        self.assertIsNone(compat.pick_enum(None, ["A"]))

    def test_postable_command_id_first_existing(self):
        postable = _enum("PostableCommand", "Numbering", "Beam")

        class CommandId(object):
            @staticmethod
            def LookupPostableCommandId(member):
                return "id:" + member

        with fake_revit(ui={"PostableCommand": postable, "RevitCommandId": CommandId}):
            self.assertEqual(compat.postable_command_id(["ReinforcementNumbers", "Numbering"]),
                             "id:PostableCommand.Numbering")
            self.assertIsNone(compat.postable_command_id(["Nope", "AlsoNope"]))
            self.assertIsNone(compat.postable_command_id([]))

    def test_postable_command_id_swallows_lookup_errors(self):
        postable = _enum("PostableCommand", "Beam", "Copy")

        class CommandId(object):
            @staticmethod
            def LookupPostableCommandId(member):
                if member.endswith("Beam"):
                    raise RuntimeError("boom")
                return "ok"

        with fake_revit(ui={"PostableCommand": postable, "RevitCommandId": CommandId}):
            self.assertEqual(compat.postable_command_id(["Beam", "Copy"]), "ok")

    def test_postable_command_id_without_ui_assembly_is_none(self):
        # No fake installed and no real Revit: must not raise.
        self.assertIsNone(compat.postable_command_id(["Beam"]))


class TestCreateRebarFromCurves(unittest.TestCase):

    def setUp(self):
        FakeRebar.calls = []
        FakeRebar.reject_arg_counts = ()
        FakeRebar.raise_instead = None
        self.curves = [FakeCurve(), FakeCurve()]
        self.host = object()
        self.normal = object()

    def call(self, doc=None, **kwargs):
        return compat.create_rebar_from_curves(
            doc or FakeDoc(2027), "Standard", "BarType", self.host, self.normal,
            self.curves, **kwargs)

    def test_create_rebar_from_curves_prefers_terminations_data(self):
        structure = {"Rebar": FakeRebar, "BarTerminationsData": FakeTerminations,
                     "RebarTerminationOrientation": _enum("RebarTerminationOrientation", "Left", "Right")}
        with fake_revit(structure=structure):
            result = self.call(hook_start=Hook(5), hook_end=None,
                               orient_start="Right", orient_end="Left")
        self.assertEqual(result, ("rebar", 9))
        self.assertEqual(len(FakeRebar.calls), 1)
        args = FakeRebar.calls[0]
        self.assertEqual(args[1:6], ("Standard", "BarType", self.host, self.normal, self.curves))
        data = args[6]
        self.assertIsInstance(data, FakeTerminations)
        self.assertEqual(data.HookTypeIdAtStart.Value, 5)
        self.assertEqual(data.HookTypeIdAtEnd.Value, -1)             # no hook -> InvalidElementId
        self.assertEqual(data.TerminationOrientationAtStart, "RebarTerminationOrientation.Right")
        self.assertEqual(data.TerminationOrientationAtEnd, "RebarTerminationOrientation.Left")
        self.assertEqual(args[7:], (True, False))

    def test_create_rebar_from_curves_falls_back_to_legacy(self):
        """No BarTerminationsData (Revit 2022-2025): the legacy overload runs."""
        structure = {"Rebar": FakeRebar,
                     "RebarHookOrientation": _enum("RebarHookOrientation", "Left", "Right")}
        start_hook, end_hook = Hook(5), Hook(6)
        with fake_revit(structure=structure):
            result = self.call(doc=FakeDoc(2024), hook_start=start_hook, hook_end=end_hook,
                               orient_start="Left", orient_end="Right",
                               use_existing_shape=False, create_new_shape=True)
        self.assertEqual(result, ("rebar", 12))
        self.assertEqual(len(FakeRebar.calls), 1)
        args = FakeRebar.calls[0]
        self.assertEqual(args[1:9], ("Standard", "BarType", start_hook, end_hook,
                                     self.host, self.normal, self.curves, "RebarHookOrientation.Left"))
        self.assertEqual(args[9:], ("RebarHookOrientation.Right", False, True))

    def test_falls_back_when_the_new_overload_is_missing_on_this_release(self):
        """BarTerminationsData exists but Rebar has no 9-argument overload -> TypeError -> legacy."""
        FakeRebar.reject_arg_counts = (9,)
        structure = {"Rebar": FakeRebar, "BarTerminationsData": FakeTerminations,
                     "RebarTerminationOrientation": _enum("RebarTerminationOrientation", "Left"),
                     "RebarHookOrientation": _enum("RebarHookOrientation", "Left", "Right")}
        with fake_revit(structure=structure):
            result = self.call()
        self.assertEqual(result, ("rebar", 12))
        self.assertEqual([len(c) for c in FakeRebar.calls], [9, 12])

    def test_falls_back_when_terminations_member_is_unknown(self):
        class OddTerminations(object):
            def __init__(self, doc):
                pass                                  # none of the expected members
        structure = {"Rebar": FakeRebar, "BarTerminationsData": OddTerminations,
                     "RebarHookOrientation": _enum("RebarHookOrientation", "Left")}
        with fake_revit(structure=structure):
            result = self.call()
        self.assertEqual(result, ("rebar", 12))

    def test_genuine_revit_error_from_new_overload_is_not_masked(self):
        class RevitArgumentError(Exception):
            pass
        FakeRebar.raise_instead = RevitArgumentError("curves do not form a valid shape")
        structure = {"Rebar": FakeRebar, "BarTerminationsData": FakeTerminations,
                     "RebarHookOrientation": _enum("RebarHookOrientation", "Left")}
        with fake_revit(structure=structure):
            with self.assertRaises(RevitArgumentError):
                self.call()
        self.assertEqual(len(FakeRebar.calls), 1)               # legacy never tried

    def test_both_overloads_failing_names_both(self):
        FakeRebar.reject_arg_counts = (9, 12)
        structure = {"Rebar": FakeRebar, "BarTerminationsData": FakeTerminations,
                     "RebarHookOrientation": _enum("RebarHookOrientation", "Left")}
        with fake_revit(structure=structure):
            with self.assertRaises(RuntimeError) as ctx:
                self.call()
        text = str(ctx.exception)
        self.assertIn("BarTerminationsData", text)
        self.assertIn("Legacy", text)
        self.assertIn("No method matches", text)

    def test_revit_2027_without_legacy_enum_explains_itself(self):
        FakeRebar.reject_arg_counts = (9,)
        structure = {"Rebar": FakeRebar, "BarTerminationsData": FakeTerminations}
        with fake_revit(structure=structure):
            with self.assertRaises(RuntimeError) as ctx:
                self.call(doc=FakeDoc(2027))
        self.assertIn("RebarHookOrientation is not available on Revit 2027", str(ctx.exception))

    def test_no_rebar_api_raises_clear_error(self):
        with fake_revit():
            with self.assertRaises(RuntimeError):
                self.call()


class TestAddToAssemblyOf(unittest.TestCase):

    class Assembly(object):
        def __init__(self, members=(), error=None):
            self.members = set(members)
            self.added = []
            self.error = error

        def IsMember(self, eid):
            return eid.Value in self.members

        def AddMemberIds(self, ids):
            if self.error:
                raise self.error
            self.added = [i.Value for i in ids]

    def host(self, assembly_id):
        return types.SimpleNamespace(AssemblyInstanceId=FakeElementId(assembly_id))

    def test_host_not_in_assembly_does_nothing(self):
        with fake_revit():
            self.assertEqual(compat.add_to_assembly_of(FakeDoc(), self.host(-1), [1, 2]), (0, None, None))

    def test_adds_new_members_only(self):
        assembly = self.Assembly(members=[2])
        doc = FakeDoc(elements={900: assembly})
        with fake_revit():
            result = compat.add_to_assembly_of(doc, self.host(900), [1, 2, FakeElementId(3)])
        self.assertEqual(result, (2, 900, None))
        self.assertEqual(assembly.added, [1, 3])

    def test_nothing_new_to_add(self):
        assembly = self.Assembly(members=[1])
        with fake_revit():
            result = compat.add_to_assembly_of(FakeDoc(elements={900: assembly}), self.host(900), [1])
        self.assertEqual(result, (0, 900, None))
        self.assertEqual(assembly.added, [])

    def test_revit_refusal_is_returned_not_raised(self):
        assembly = self.Assembly(error=RuntimeError("element is not valid for assembly\ndetails"))
        with fake_revit():
            count, asm_id, error = compat.add_to_assembly_of(
                FakeDoc(elements={900: assembly}), self.host(900), [1])
        self.assertEqual((count, asm_id), (0, 900))
        self.assertEqual(error, "element is not valid for assembly")

    def test_missing_assembly_is_reported(self):
        with fake_revit():
            count, asm_id, error = compat.add_to_assembly_of(FakeDoc(), self.host(900), [1])
        self.assertEqual((count, asm_id), (0, 900))
        self.assertIn("900", error)


class TestMassAndParameters(unittest.TestCase):

    class Param(object):
        def __init__(self, text=None, double=0.0, read_only=False, has_value=True):
            self.text = text
            self.double = double
            self.IsReadOnly = read_only
            self.HasValue = has_value

        def AsString(self):
            return self.text

        def AsValueString(self):
            return None

        def AsDouble(self):
            return self.double

    class Holder(object):
        def __init__(self, by_name=None, by_builtin=None, **attrs):
            self.by_name = by_name or {}
            self.by_builtin = by_builtin or {}
            self.__dict__.update(attrs)

        def LookupParameter(self, name):
            return self.by_name.get(name)

        def get_Parameter(self, built_in):
            return self.by_builtin.get(built_in)

    def units(self, factor=1.0):
        class UnitUtils(object):
            @staticmethod
            def ConvertFromInternalUnits(value, unit):
                return value * factor
        return {"UnitUtils": UnitUtils, "UnitTypeId": types.SimpleNamespace(KilogramsPerMeter="kg/m")}

    def test_mass_from_revit_when_in_range(self):
        bar = self.Holder(BarMassPerUnitLength=1.58)
        with fake_revit(db=self.units(1.0)):
            self.assertEqual(compat.bar_mass_per_metre(bar), (1.58, "revit"))

    def test_mass_absurd_native_value_falls_back_to_parameter(self):
        bar = self.Holder(by_name={"T3_WeightPerMetre": self.Param(double=0.888)},
                          BarMassPerUnitLength=1.58)
        with fake_revit(db=self.units(1000.0)):           # 1580 kg/m: unit mix-up
            self.assertEqual(compat.bar_mass_per_metre(bar), (0.888, "T3_WeightPerMetre"))

    def test_mass_from_parameter_before_2027(self):
        bar = self.Holder(by_name={"T3_WeightPerMetre": self.Param(double=2.466)})
        with fake_revit(db=self.units()):
            self.assertEqual(compat.bar_mass_per_metre(bar), (2.466, "T3_WeightPerMetre"))

    def test_mass_unknown(self):
        with fake_revit(db=self.units()):
            self.assertEqual(compat.bar_mass_per_metre(self.Holder()), (None, None))
            self.assertEqual(compat.bar_mass_per_metre(None), (None, None))
            empty = self.Holder(by_name={"T3_WeightPerMetre": self.Param(double=0.0)})
            self.assertEqual(compat.bar_mass_per_metre(empty), (None, None))
            unset = self.Holder(by_name={"T3_WeightPerMetre": self.Param(double=1.0, has_value=False)})
            self.assertEqual(compat.bar_mass_per_metre(unset), (None, None))

    def test_partition_parameter_builtin_then_name_and_read_only(self):
        bip = _enum("BuiltInParameter", "NUMBER_PARTITION_PARAM")
        writable = self.Param(text="A")
        with fake_revit(db={"BuiltInParameter": bip}):
            element = self.Holder(by_builtin={bip.NUMBER_PARTITION_PARAM: writable})
            self.assertIs(compat.partition_parameter(element), writable)
            by_name = self.Holder(by_name={"Partition": writable})
            self.assertIs(compat.partition_parameter(by_name), writable)
            locked = self.Holder(by_builtin={bip.NUMBER_PARTITION_PARAM: self.Param(read_only=True)})
            self.assertIsNone(compat.partition_parameter(locked))
            self.assertIsNone(compat.partition_parameter(self.Holder()))
            self.assertIsNone(compat.partition_parameter(None))

    def test_rebar_number_text(self):
        bip = _enum("BuiltInParameter", "SOMETHING_ELSE")          # no REBAR_NUMBER member
        with fake_revit(db={"BuiltInParameter": bip}):
            element = self.Holder(by_name={"Rebar Number": self.Param(text="12")})
            self.assertEqual(compat.rebar_number_text(element), "12")
            self.assertEqual(compat.rebar_number_text(self.Holder()), "")
            self.assertEqual(compat.rebar_number_text(None), "")
            blank = self.Holder(by_name={"Rebar Number": self.Param(text=None)})
            self.assertEqual(compat.rebar_number_text(blank), "")
        bip_with = _enum("BuiltInParameter", "REBAR_NUMBER")
        with fake_revit(db={"BuiltInParameter": bip_with}):
            element = self.Holder(by_builtin={bip_with.REBAR_NUMBER: self.Param(text="7")})
            self.assertEqual(compat.rebar_number_text(element), "7")


class TestNumberingPartitions(unittest.TestCase):

    class Schema(object):
        def __init__(self, params, enabled=True, scope=(-2009000,)):
            self.Enabled = enabled
            self._params = params
            self._scope = scope

        def GetPartitioningParameters(self):
            return [types.SimpleNamespace(ParameterId=FakeElementId(p)) for p in self._params]

        def GetScopeDefiningCategories(self):
            return [FakeElementId(c) for c in self._scope]

    def run_with(self, schemas, year=2027):
        schema_type = type("NumberingSchema", (), {
            "GetSchemasInDocument": staticmethod(lambda doc: schemas)})
        db = {"NumberingSchema": schema_type,
              "BuiltInParameter": types.SimpleNamespace(NUMBER_PARTITION_PARAM=-1140031),
              "BuiltInCategory": types.SimpleNamespace(OST_Rebar=-2009000)}
        with fake_revit(db=db):
            return compat.numbering_partitions_by_partition_param(FakeDoc(year))

    def test_none_before_2027(self):
        self.assertIsNone(self.run_with([], year=2026))

    def test_true_when_a_rebar_schema_partitions_by_partition(self):
        self.assertTrue(self.run_with([self.Schema([-5, -1140031])]))

    def test_false_when_no_schema_does(self):
        self.assertFalse(self.run_with([self.Schema([-5])]))
        self.assertFalse(self.run_with([]))

    def test_disabled_or_other_scope_schemas_do_not_count(self):
        self.assertFalse(self.run_with([self.Schema([-1140031], enabled=False)]))
        self.assertFalse(self.run_with([self.Schema([-1140031], scope=(-2000011,))]))

    def test_none_on_api_error(self):
        schema_type = type("NumberingSchema", (), {
            "GetSchemasInDocument": staticmethod(lambda doc: 1 / 0)})
        db = {"NumberingSchema": schema_type,
              "BuiltInParameter": types.SimpleNamespace(NUMBER_PARTITION_PARAM=-1140031),
              "BuiltInCategory": types.SimpleNamespace(OST_Rebar=-2009000)}
        with fake_revit(db=db):
            self.assertIsNone(compat.numbering_partitions_by_partition_param(FakeDoc(2027)))


if __name__ == '__main__':
    unittest.main()
