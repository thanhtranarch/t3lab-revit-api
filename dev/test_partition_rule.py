# -*- coding: utf-8 -*-
"""Tests for the partition rule renderer in Snippets/_rebar.py
(render_partition / check_partition) - the text written to a rebar's Partition
parameter by Cast Unit Manager's 'Partition by rule'.
Run: python dev/test_partition_rule.py
"""
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _load_module(name):
    """Exec Snippets/<name>.py with the Revit and .NET imports stubbed out.

    Tests the shipped source rather than a copy; same technique as
    dev/test_group_manager.py.
    """
    class _AnyMeta(type):
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(item):
        return _AnyMeta(str(item), (), {})

    def _stub(stub_name):
        mod = types.ModuleType(stub_name)
        mod.__getattr__ = _make_any
        return mod

    saved = {}
    stubs = {
        'Autodesk': _stub('Autodesk'),
        'Autodesk.Revit': _stub('Autodesk.Revit'),
        'Autodesk.Revit.DB': _stub('Autodesk.Revit.DB'),
        'System': _stub('System'),
    }
    stubs['Autodesk'].Revit = stubs['Autodesk.Revit']
    stubs['Autodesk.Revit'].DB = stubs['Autodesk.Revit.DB']
    for stub_name, mod in stubs.items():
        saved[stub_name] = sys.modules.get(stub_name)
        sys.modules[stub_name] = mod
    try:
        path = os.path.join(LIB_DIR, 'Snippets', name + '.py')
        with open(path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_%s_under_test' % name)
        module.__file__ = path
        exec(compile(source, path, 'exec'), module.__dict__)
        return module
    finally:
        for stub_name, previous in saved.items():
            if previous is None:
                sys.modules.pop(stub_name, None)
            else:
                sys.modules[stub_name] = previous



FULL = {"AssemblyMark": "C-01", "Level": "L02", "HostType": "C400x400",
        "HostMark": "C12", "Workset": "Structure", "Category": "Structural Columns"}


class TestRenderPartition(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')

    def render(self, rule, ctx=None):
        return self.rebar.render_partition(rule, FULL if ctx is None else ctx)

    def test_tokens_resolved(self):
        self.assertEqual(self.render("{AssemblyMark}-{Level}"), "C-01-L02")
        self.assertEqual(self.render("{Category}/{HostType}"), "Structural Columns/C400x400")
        self.assertEqual(self.render("{HostMark}_{Workset}"), "C12_Structure")

    def test_every_documented_token_is_supported(self):
        for token in self.rebar.PARTITION_TOKENS:
            self.assertNotEqual(self.render(token), token, token)

    def test_literal_text_kept(self):
        self.assertEqual(self.render("PT-{Level}"), "PT-L02")
        self.assertEqual(self.render("FIXED"), "FIXED")

    def test_empty_tokens_collapse_separators(self):
        ctx = dict(FULL, Level="")
        self.assertEqual(self.render("{AssemblyMark}-{Level}-{HostType}", ctx), "C-01-C400x400")
        self.assertEqual(self.render("{AssemblyMark}-{Level}", ctx), "C-01")
        self.assertEqual(self.render("{Level}-{AssemblyMark}", ctx), "C-01")
        ctx = dict(FULL, AssemblyMark="", Level="")
        self.assertEqual(self.render("{AssemblyMark}_{Level}_{HostMark}", ctx), "C12")

    def test_missing_ctx_key_counts_as_empty(self):
        self.assertEqual(self.render("{AssemblyMark}-{Level}", {"AssemblyMark": "C-01"}), "C-01")
        self.assertEqual(self.render("{AssemblyMark}-{Level}", {"AssemblyMark": None, "Level": "L1"}), "L1")

    def test_unknown_token_kept(self):
        self.assertEqual(self.render("{AssemblyMark}-{Colour}"), "C-01-{Colour}")
        self.assertEqual(self.render("{Colour}"), "{Colour}")

    def test_all_empty_gives_empty_string(self):
        empty = {k: "" for k in FULL}
        self.assertEqual(self.render("{AssemblyMark}-{Level}", empty), "")
        self.assertEqual(self.render("Part {Level}", empty), "")
        self.assertEqual(self.render("{AssemblyMark}", {}), "")

    def test_whitespace_only_values_are_empty(self):
        self.assertEqual(self.render("{Level}", {"Level": "   "}), "")

    def test_empty_rule_and_none(self):
        self.assertEqual(self.render(""), "")
        self.assertEqual(self.rebar.render_partition(None, FULL), "")

    def test_values_are_trimmed_and_spaces_collapse(self):
        self.assertEqual(self.render("{Level} {HostType}", {"Level": " L02 ", "HostType": "C1"}), "L02 C1")
        self.assertEqual(self.render("{Level}  {HostType}", {"Level": "L02", "HostType": "C1"}), "L02 C1")

    def test_over_64_chars_flagged(self):
        long_value = self.render("{Category}-{Category}-{Category}-{Category}")
        self.assertEqual(len(long_value), 18 * 4 + 3)
        self.assertGreater(len(long_value), 64)
        self.assertIn("64", self.rebar.check_partition(long_value))

    def test_64_chars_or_less_not_flagged(self):
        self.assertIsNone(self.rebar.check_partition("x" * 64))
        self.assertIsNone(self.rebar.check_partition(""))
        self.assertIsNotNone(self.rebar.check_partition("x" * 65))

    def test_render_does_not_truncate(self):
        self.assertEqual(len(self.render("{Category}{Category}{Category}")), 54)
        self.assertEqual(len(self.render("{Category}" * 5)), 90)


if __name__ == '__main__':
    unittest.main()
