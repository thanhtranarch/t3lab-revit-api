# -*- coding: utf-8 -*-
"""
Tests for Family Transfer — the decisions it makes before touching Revit.

  * Snippets/family_transfer.plan_transfer: which family is copied, topped up,
    overwritten or skipped, and with which types.
  * GUI/FamilyTransferDialog row models: the family tick and its type ticks
    must stay consistent, because the transfer reads exactly those flags.

Revit and WPF are not needed: the dialog module is imported against small
stand-ins for System.Windows / T3WPFWindow / T3Dialog.

Run: python dev/test_family_transfer.py
"""
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from Snippets import family_transfer as ft      # noqa: E402


def _install_ui_stubs():
    """Let GUI.FamilyTransferDialog import outside Revit."""
    system = sys.modules.setdefault('System', types.ModuleType('System'))
    windows = types.ModuleType('System.Windows')
    windows.WindowState = types.SimpleNamespace(Minimized=1, Maximized=2, Normal=0)
    system.Windows = windows
    sys.modules['System.Windows'] = windows

    base = types.ModuleType('GUI.WPF_Base')
    base.T3WPFWindow = object
    base.to_items_source = list
    sys.modules['GUI.WPF_Base'] = base

    dialogs = types.ModuleType('GUI.T3Dialog')
    for name in ('show_info', 'show_warning', 'show_error', 'confirm'):
        setattr(dialogs, name, lambda *a, **k: True)
    sys.modules['GUI.T3Dialog'] = dialogs


_install_ui_stubs()
from GUI import FamilyTransferDialog as dlg      # noqa: E402


def _family(name, type_names, editable=True, category="Doors"):
    return ft.FamilyInfo(name, category, "id-" + name,
                         dict((t, "sym-" + t) for t in type_names), editable)


class PlanTransferTests(unittest.TestCase):

    def test_new_family_copies_only_the_selected_types(self):
        door = _family("Door", ["900", "1000", "1200"])
        [a] = ft.plan_transfer([(door, ["900", "1200"])], {}, ft.SKIP)
        self.assertEqual(a.kind, ft.COPY_KIND)
        self.assertEqual(a.types, ["900", "1200"])

    def test_existing_family_is_skipped_in_skip_mode(self):
        door = _family("Door", ["900"])
        [a] = ft.plan_transfer([(door, ["900"])], {"Door": {"900"}}, ft.SKIP)
        self.assertEqual(a.kind, ft.SKIP_KIND)
        self.assertIn("already", a.reason)

    def test_add_mode_copies_only_types_missing_from_target(self):
        door = _family("Door", ["900", "1000", "1200"])
        [a] = ft.plan_transfer([(door, ["900", "1000", "1200"])],
                               {"Door": {"900"}}, ft.ADD)
        self.assertEqual(a.kind, ft.COPY_KIND)
        self.assertEqual(a.types, ["1000", "1200"])

    def test_add_mode_skips_when_nothing_is_missing(self):
        door = _family("Door", ["900", "1000"])
        [a] = ft.plan_transfer([(door, ["900"])], {"Door": {"900", "1000"}}, ft.ADD)
        self.assertEqual(a.kind, ft.SKIP_KIND)

    def test_overwrite_keeps_target_types_and_selected_types(self):
        # Types already in the target may be placed; they must survive the
        # clean-up that removes unselected types after LoadFamily.
        door = _family("Door", ["900", "1000", "1200"])
        [a] = ft.plan_transfer([(door, ["1000"])], {"Door": {"900", "OLD"}}, ft.OVERWRITE)
        self.assertEqual(a.kind, ft.OVERWRITE_KIND)
        self.assertEqual(a.types, ["1000"])
        self.assertEqual(a.keep, {"900", "OLD", "1000"})

    def test_overwrite_of_a_new_family_is_a_plain_copy(self):
        door = _family("Door", ["900", "1000"])
        [a] = ft.plan_transfer([(door, ["900"])], {}, ft.OVERWRITE)
        self.assertEqual(a.kind, ft.COPY_KIND)

    def test_non_editable_family_cannot_be_overwritten(self):
        tag = _family("Tag", ["Standard"], editable=False)
        [a] = ft.plan_transfer([(tag, ["Standard"])], {"Tag": {"Standard"}}, ft.OVERWRITE)
        self.assertEqual(a.kind, ft.SKIP_KIND)
        self.assertIn("cannot be edited", a.reason)

    def test_family_without_selected_types_is_skipped(self):
        door = _family("Door", ["900"])
        [a] = ft.plan_transfer([(door, [])], {}, ft.SKIP)
        self.assertEqual(a.kind, ft.SKIP_KIND)
        self.assertEqual(a.reason, "no types selected")

    def test_unknown_type_names_are_ignored(self):
        door = _family("Door", ["900"])
        [a] = ft.plan_transfer([(door, ["900", "ghost"])], {}, ft.SKIP)
        self.assertEqual(a.types, ["900"])

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            ft.plan_transfer([], {}, "merge")

    def test_summarize_counts_types_of_successful_families_only(self):
        results = [ft.Result("A", "ok", 3, ""), ft.Result("B", "ok", 1, ""),
                   ft.Result("C", "skipped", 0, "x"), ft.Result("D", "failed", 0, "y")]
        self.assertEqual(ft.summarize(results),
                         {"ok": 2, "skipped": 1, "failed": 1, "types": 4})


class RowModelTests(unittest.TestCase):

    def _row(self, names=("900", "1000", "1200")):
        return dlg.FamilyRow(_family("Door", list(names)))

    def test_rows_start_unticked_with_every_type_preselected(self):
        row = self._row()
        self.assertFalse(row.is_selected)
        self.assertEqual(row.selected_type_names, ["1000", "1200", "900"])
        self.assertEqual(row.types_label, "3")

    def test_ticking_a_family_whose_types_are_all_off_selects_them_all(self):
        row = self._row()
        for t in row.types:
            t.is_selected = False
        row.is_selected = True
        self.assertEqual(len(row.selected_type_names), 3)

    def test_ticking_a_family_keeps_a_partial_type_choice(self):
        row = self._row()
        row.types[0].is_selected = False
        row.is_selected = True
        self.assertEqual(row.types_label, "2 / 3")

    def test_bridge_strings_are_parsed(self):
        # The checkbox bridge may hand back "True"/"False".
        row = self._row()
        row.is_selected = "True"
        self.assertTrue(row.is_selected)
        row.types[0].is_selected = "False"
        self.assertFalse(row.types[0].is_selected)

    def test_selection_flag_is_a_real_bool(self):
        # sync_header_checkbox does bool(getattr(row, prop)); the string
        # "False" would read as ticked.
        row = self._row()
        self.assertIs(row.is_selected, False)
        self.assertIs(row.types[0].is_selected, True)

    def test_target_status(self):
        row = self._row()
        row.set_target(None)
        self.assertEqual(row.target_status, "New")
        row.set_target({"900", "1000", "1200"})
        self.assertEqual(row.target_status, "Exists")
        row.set_target({"900"})
        self.assertEqual(row.target_status, "Exists · 2 new types")
        self.assertEqual([t.target_status for t in row.types], ["New", "New", "Exists"])


class _Doc(object):
    """Stand-in for a Revit Document whose Equals is reference-only."""

    def __init__(self, title, path=u""):
        self.Title = title
        self.PathName = path

    def Equals(self, other):
        return self is other


class SameDocTests(unittest.TestCase):

    def test_two_wrappers_of_the_same_saved_model_match(self):
        self.assertTrue(ft.same_doc(_Doc("A", r"C:\a.rvt"), _Doc("A", r"C:\a.rvt")))

    def test_different_models_do_not_match(self):
        self.assertFalse(ft.same_doc(_Doc("A", r"C:\a.rvt"), _Doc("B", r"C:\b.rvt")))

    def test_unsaved_models_compare_by_title(self):
        self.assertFalse(ft.same_doc(_Doc("Project1"), _Doc("Project2")))
        self.assertTrue(ft.same_doc(_Doc("Project1"), _Doc("Project1")))

    def test_none_never_matches(self):
        self.assertFalse(ft.same_doc(None, _Doc("A")))
        self.assertFalse(ft.same_doc(_Doc("A"), None))


class LinkSourceTests(unittest.TestCase):
    """Sources = open projects + loaded links, read without opening the link file."""

    def setUp(self):
        self.a = _Doc("Host A", r"C:\a.rvt")
        self.b = _Doc("Host B", r"C:\b.rvt")
        self.arch = _Doc("ARCH.rvt", r"C:\links\arch.rvt")
        self.mep = _Doc("MEP.rvt", r"C:\links\mep.rvt")
        # Revit hands out a different wrapper per host for the same linked file.
        self.arch_in_b = _Doc("ARCH.rvt", r"C:\links\arch.rvt")
        links = {id(self.a): [self.arch, self.mep], id(self.b): [self.arch_in_b]}
        self.links_of = lambda host: links.get(id(host), [])

    def test_projects_first_then_each_link_once(self):
        entries = ft.list_sources([self.a, self.b], self.links_of)
        self.assertEqual([e.label for e in entries],
                         ["Host A", "Host B", "Link: ARCH.rvt  (in Host A)", "Link: MEP.rvt  (in Host A)"])
        self.assertEqual([e.is_link for e in entries], [False, False, True, True])
        self.assertIs(entries[2].host, self.a)

    def test_single_project_offers_only_its_links(self):
        entries = dlg.usable_sources(ft.list_sources([self.a], self.links_of), [self.a])
        self.assertEqual([e.doc for e in entries], [self.arch, self.mep])

    def test_single_project_without_links_has_nothing(self):
        entries = dlg.usable_sources(ft.list_sources([self.a], lambda h: []), [self.a])
        self.assertEqual(entries, [])

    def test_default_is_the_active_project_when_another_is_open(self):
        sources = ft.list_sources([self.a, self.b], self.links_of)
        self.assertEqual(sources[dlg.pick_source_index(sources, [self.a, self.b], self.b)].doc, self.b)

    def test_default_is_the_first_link_of_the_only_project(self):
        sources = dlg.usable_sources(ft.list_sources([self.a], self.links_of), [self.a])
        entry = sources[dlg.pick_source_index(sources, [self.a], self.a)]
        self.assertEqual(entry.doc, self.arch)
        self.assertIs(entry.host, self.a)

    def test_link_that_fails_to_read_is_skipped(self):
        def broken(host):
            raise RuntimeError("link not loaded")
        self.assertEqual([e.label for e in ft.list_sources([self.a], broken)], ["Host A"])


class TypeTickSyncTests(unittest.TestCase):
    """FamilyTransferDialog._sync_focused_family, run against a fake window."""

    def _sync(self, row, ticked):
        fake = types.SimpleNamespace(_focused=row, _refresh_rows=lambda: None)
        dlg.FamilyTransferDialog._sync_focused_family(fake, ticked)

    def _row(self):
        return dlg.FamilyRow(_family("Door", ["900", "1000", "1200"]))

    def test_ticking_a_type_selects_its_family(self):
        row = self._row()
        self._sync(row, True)
        self.assertTrue(row.is_selected)

    def test_unticking_one_of_several_types_leaves_an_unselected_family_alone(self):
        row = self._row()
        row.types[0].is_selected = False
        self._sync(row, False)
        self.assertFalse(row.is_selected)

    def test_unticking_the_last_type_deselects_the_family(self):
        row = self._row()
        row.is_selected = True
        for t in row.types:
            t.is_selected = False
        self._sync(row, False)
        self.assertFalse(row.is_selected)


class XamlContractTests(unittest.TestCase):

    def test_every_control_the_dialog_reads_exists_in_the_xaml(self):
        import re
        xaml = os.path.join(LIB_DIR, 'GUI', 'Tools', 'FamilyTransfer.xaml')
        py = os.path.join(LIB_DIR, 'GUI', 'FamilyTransferDialog.py')
        with open(xaml, encoding='utf-8') as fh:
            names = set(re.findall(r'x:Name="([^"]+)"', fh.read()))
        with open(py, encoding='utf-8') as fh:
            used = set(re.findall(r'self\.((?:cmb|dg|txt|chk|rb|btn|status|pb|progress)_?\w*)', fh.read()))
        self.assertEqual(sorted(used - names), [])


if __name__ == '__main__':
    unittest.main()
