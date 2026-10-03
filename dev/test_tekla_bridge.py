# -*- coding: utf-8 -*-
"""
Tests for Tekla Bridge: the data file lib/data/tekla_bridge.json, the helpers in
Snippets/_tekla_bridge.py and the generated outputs of dev/build_tekla_docs.py.

The shipped Snippets source is exec'd with the Revit and .NET imports stubbed
(same approach as dev/test_group_manager.py), so nothing here needs Revit.
Run: python dev/test_tekla_bridge.py
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
SOURCE = os.path.join(LIB_DIR, 'Snippets', '_tekla_bridge.py')
BUILD_SCRIPT = os.path.join(REPO, 'dev', 'build_tekla_docs.py')
XML_FILE = os.path.join(LIB_DIR, 'data', 'KeyboardShortcuts_Tekla.xml')

# Vietnamese letters (diacritics) - a user-facing English string must hold none.
VN_CHARS = re.compile(
    u"[àáạảãâầấậẩẫăằắặẳẵ"
    u"èéẹẻẽêềếệểễìíịỉĩ"
    u"òóọỏõôồốộổỗơờớợởỡ"
    u"ùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE)

# Tekla defaults cited in spec 2.4 (D4). Spelled out here on purpose: the test must
# not read the set from the module it is checking.
CITED_KEYS = {"Ctrl+H", "Ctrl+B", "Ctrl+G", "Shift+I", "Ctrl+Q", "Ctrl+Shift+C", "Ctrl+J", "Alt+Q/W/E"}


def _load_module():
    """Exec Snippets/_tekla_bridge.py with Revit and .NET stubbed out (it stays importable anywhere)."""
    class _AnyMeta(type):
        def __getattr__(cls, item):
            return _AnyMeta(str(item), (), {})

    def _stub(name):
        mod = types.ModuleType(name)
        mod.__getattr__ = lambda item: _AnyMeta(str(item), (), {})
        return mod

    saved = {}
    stubs = {'Autodesk': _stub('Autodesk'),
             'Autodesk.Revit': _stub('Autodesk.Revit'),
             'Autodesk.Revit.DB': _stub('Autodesk.Revit.DB'),
             'System': _stub('System')}
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    try:
        with open(SOURCE, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_tekla_bridge_under_test')
        module.__file__ = SOURCE
        exec(compile(source, SOURCE, 'exec'), module.__dict__)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakePostable(object):
    """Stand-in for Autodesk.Revit.UI.PostableCommand: only two members exist."""
    Phases = "member:Phases"
    Copy = "member:Copy"


class _FakeCommandId(object):
    def __init__(self, name):
        self.Name = name


class _FakeRevitCommandId(object):
    @staticmethod
    def LookupPostableCommandId(member):
        return _FakeCommandId("ID_FOR_" + str(member).split(":")[-1].upper())

    @staticmethod
    def LookupCommandId(name):
        return _FakeCommandId(name) if name == "ID_CAPTURED" else None


class _FakeUiApp(object):
    def __init__(self, can_post=True, raises=None):
        self.can_post = can_post
        self.raises = raises
        self.posted = []

    def CanPostCommand(self, command_id):
        return self.can_post

    def PostCommand(self, command_id):
        if self.raises:
            raise self.raises
        self.posted.append(command_id.Name)


class _StubbedRevitUI(object):
    """Context manager that makes `from Autodesk.Revit.UI import PostableCommand, RevitCommandId` work."""

    def __enter__(self):
        self._saved = {}
        ui = types.ModuleType('Autodesk.Revit.UI')
        ui.PostableCommand = _FakePostable
        ui.RevitCommandId = _FakeRevitCommandId
        revit = types.ModuleType('Autodesk.Revit')
        revit.UI = ui
        root = types.ModuleType('Autodesk')
        root.Revit = revit
        for name, mod in (('Autodesk', root), ('Autodesk.Revit', revit), ('Autodesk.Revit.UI', ui)):
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
        return self

    def __exit__(self, *exc):
        for name, previous in self._saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        return False


class _BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = _load_module()
        cls.rows = cls.bridge.load_rows()
        cls.by_id = dict((r['id'], r) for r in cls.rows)


class TestData(_BridgeTest):
    def test_json_loads_and_validates(self):
        self.assertEqual(self.bridge.validate_rows(self.rows), [])
        self.assertEqual(len(self.rows), 36)
        self.assertEqual(len(self.by_id), 36, "ids must be unique")

    def test_every_group_present_and_rows_ordered(self):
        group_ids = [g for g, _label in self.bridge.GROUPS]
        self.assertEqual(group_ids, ["model", "rebar", "numbering", "drawing", "report", "env"])
        for gid in group_ids:
            orders = [r['order'] for r in self.rows if r['group'] == gid]
            self.assertTrue(orders, "group %s has no row" % gid)
            self.assertEqual(orders, sorted(orders), "group %s is not in ascending order" % gid)
            self.assertEqual(len(orders), len(set(orders)))
        # ordered() walks the groups in GROUPS order, no interleaving
        seen = []
        for row in self.bridge.ordered(self.rows):
            if not seen or seen[-1] != row['group']:
                seen.append(row['group'])
        self.assertEqual(seen, group_ids)

    def test_layer2_rows_name_a_tool(self):
        layer2 = [r for r in self.rows if r['layer'] == 2]
        self.assertTrue(layer2)
        without = sorted(r['id'] for r in layer2 if not r['tool'])
        # env.find describes the Bridge window itself, so it names no tool.
        self.assertEqual(without, ["env.find"])
        self.assertEqual(tuple(without), tuple(self.bridge.LAYER2_NO_TOOL))
        for row in self.rows:
            if row['tool']:
                self.assertIn(row['tool'], self.bridge.TOOL_ENTRY)

    def test_postable_names_are_identifiers(self):
        pattern = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
        for row in self.rows:
            self.assertIsInstance(row['postable'], list)
            for name in row['postable']:
                self.assertRegex(name, pattern, row['id'])

    def test_no_vietnamese_in_user_facing_english(self):
        for row in self.rows:
            for key in ('tekla', 'revit', 'tip_en', 'doc_en'):
                self.assertIsNone(VN_CHARS.search(row[key]), "%s.%s has Vietnamese letters" % (row['id'], key))
            self.assertIsNotNone(VN_CHARS.search(row['doc_vi']), "%s.doc_vi should be Vietnamese" % row['id'])

    def test_every_row_has_both_languages_and_a_doc(self):
        for row in self.rows:
            for key in ('tip_en', 'tip_vi', 'doc_en', 'doc_vi'):
                self.assertTrue(row[key].strip(), "%s.%s empty" % (row['id'], key))

    def test_revit_command_ids_not_invented(self):
        # D4: ids come from the Revit spike (G10) only; none has been captured yet.
        for row in self.rows:
            self.assertIsNone(row['revit_command_id'], row['id'])

    def test_validate_rows_reports_every_problem(self):
        bad = copy.deepcopy(self.rows[:3])
        bad[0]['group'] = 'nope'
        bad[1]['layer'] = 4
        bad[2]['id'] = bad[0]['id']
        bad[2]['postable'] = ['Not An Identifier']
        bad[2]['tekla_shortcut'] = 'Ctrl+Z'
        del bad[2]['tip_en']
        problems = self.bridge.validate_rows(bad)
        text = "\n".join(problems)
        self.assertIn("group 'nope'", text)
        self.assertIn("layer 4", text)
        self.assertIn("misses keys: tip_en", text)
        problems = self.bridge.validate_rows([dict(self.rows[0]), dict(self.rows[0])])
        self.assertTrue(any("repeats an id" in p for p in problems))
        layer2 = copy.deepcopy(self.by_id['report.bvbs'])
        layer2['tool'] = None
        self.assertTrue(any("layer 2 but names no tool" in p for p in self.bridge.validate_rows([layer2])))
        shortcut = copy.deepcopy(self.rows[0])
        shortcut['tekla_shortcut'] = 'Ctrl+Z'
        self.assertTrue(any("not a cited Tekla default" in p for p in self.bridge.validate_rows([shortcut])))
        postable = copy.deepcopy(self.rows[0])
        postable['postable'] = ['Not An Identifier']
        self.assertTrue(any("postable" in p for p in self.bridge.validate_rows([postable])))

    def test_load_rows_raises_value_error_listing_bad_rows(self):
        bad = copy.deepcopy(self.rows)
        bad[5]['group'] = 'nope'
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'tekla_bridge.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(bad, handle)
            with self.assertRaises(ValueError) as ctx:
                self.bridge.load_rows(path)
        self.assertIn(bad[5]['id'], str(ctx.exception))


class TestSearch(_BridgeTest):
    def ids(self, text, group=None):
        return [r['id'] for r in self.bridge.search(self.rows, text, group)]

    def test_empty_text_returns_all_in_bridge_order(self):
        self.assertEqual(self.ids(""), [r['id'] for r in self.bridge.ordered(self.rows)])
        self.assertEqual(len(self.ids("   ")), 36)

    def test_search_matches_tekla_and_revit_and_keywords(self):
        self.assertIn("model.castunit", self.ids("cast unit"))          # Tekla name
        self.assertIn("rebar.mesh", self.ids("fabric sheet"))            # Revit name
        self.assertIn("report.bvbs", self.ids("bf2d"))                   # keyword only
        self.assertIn("env.shortcuts", self.ids("hotkey"))               # keyword only
        self.assertEqual(self.ids("zzzz-no-such-command"), [])

    def test_search_is_case_insensitive_and_all_words_must_match(self):
        self.assertEqual(self.ids("CAST UNIT"), self.ids("cast unit"))
        both = self.ids("assembly numbering")
        self.assertIn("model.castunit.numbering", both)
        self.assertLess(len(both), len(self.ids("assembly")))

    def test_group_filter_and_stable_order(self):
        rebar = self.ids("", "rebar")
        self.assertTrue(rebar and all(self.by_id[i]['group'] == "rebar" for i in rebar))
        self.assertEqual(rebar, [r['id'] for r in self.bridge.ordered(self.rows) if r['group'] == "rebar"])
        self.assertEqual(self.ids("", "all"), self.ids(""))
        self.assertEqual(self.ids("copy"), self.ids("copy"))

    def test_search_does_not_mutate_rows(self):
        before = json.dumps(self.rows, sort_keys=True)
        self.bridge.search(self.rows, "rebar", "rebar")
        self.assertEqual(json.dumps(self.rows, sort_keys=True), before)


class TestDescribe(_BridgeTest):
    def test_ribbon_path_for_year(self):
        settings = self.by_id['numbering.settings']
        self.assertEqual(self.bridge.ribbon_path_for(settings, 2027), "Manage › Numbering")
        self.assertEqual(self.bridge.ribbon_path_for(settings, 2030), "Manage › Numbering")
        self.assertEqual(self.bridge.ribbon_path_for(settings, 2026),
                         "Structure › Reinforcement › Reinforcement Numbering")
        # ribbon_2027 null = same as 2026
        part = self.by_id['model.part']
        self.assertIsNone(part['ribbon_2027'])
        self.assertEqual(self.bridge.ribbon_path_for(part, 2027), part['ribbon_2026'])
        # unknown / bad year falls back to 2026; a row without any path returns None
        self.assertEqual(self.bridge.ribbon_path_for(settings, None),
                         settings['ribbon_2026'])
        self.assertEqual(self.bridge.ribbon_path_for(settings, 0), settings['ribbon_2026'])
        self.assertIsNone(self.bridge.ribbon_path_for(self.by_id['drawing.clone'], 2027))

    def test_layer_labels(self):
        self.assertEqual(self.bridge.layer_label(self.by_id['model.part']), "Revit")
        self.assertEqual(self.bridge.layer_label(self.by_id['drawing.clone']), "T3Lab")
        self.assertEqual(self.bridge.layer_label(self.by_id['model.organizer']), "Revit does it differently")

    def test_primary_and_secondary_actions(self):
        b = self.bridge
        # Revit command with a postable name: Open in Revit, no second button
        self.assertEqual(b.primary_action(self.by_id['model.part']), ("post", "Open in Revit"))
        self.assertIsNone(b.secondary_action(self.by_id['model.part']))
        # layer 1 + tool: two actions (spec 3.8)
        for rid in ("model.castunit", "model.castunit.edit", "rebar.copy", "drawing.list"):
            self.assertIsNotNone(b.secondary_action(self.by_id[rid]), rid)
        self.assertEqual(b.primary_action(self.by_id['model.castunit.edit']), ("ribbon", "Show in ribbon"))
        # layer 2: the tool is the answer
        self.assertEqual(b.primary_action(self.by_id['drawing.clone']), ("tool", "Open T3Lab tool"))
        self.assertIsNone(b.secondary_action(self.by_id['drawing.clone']))
        # nothing to open
        for rid in ("env.find", "drawing.uptodate", "report.unitechnik"):
            self.assertEqual(b.primary_action(self.by_id[rid])[0], None, rid)

    def test_every_row_has_at_most_two_actions_and_all_reachable(self):
        for row in self.rows:
            kind, _label = self.bridge.primary_action(row)
            second = self.bridge.secondary_action(row)
            if row['tool']:
                self.assertTrue(kind == "tool" or second is not None, row['id'])

    def test_summary_text(self):
        self.assertEqual(self.bridge.summary_text(36, 36, 6), u"36 commands · 6 groups")
        self.assertEqual(self.bridge.summary_text(12, 36, 3), u"12 of 36 commands · 3 groups")
        self.assertEqual(self.bridge.summary_text(1, 36, 1), u"1 of 36 commands · 1 group")

    def test_guide_path_points_into_docs(self):
        path = self.bridge.guide_path()
        self.assertTrue(path.endswith(os.path.join("docs", "tekla-to-revit-2027.md")))
        self.assertTrue(os.path.isfile(path), "docs/tekla-to-revit-2027.md must ship with the repository")

    def test_messages_follow_what_where_next(self):
        row = self.by_id['rebar.splice']
        text = self.bridge.post_failure_message(row, "no_command", row['ribbon_2027'], 2027)
        self.assertEqual(text, "Revit 2027 has no postable command for \"Splice / coupler / end anchor\". "
                               "Open it from the ribbon: Concrete Detailing › Reinforcement › Coupler.")
        no_ribbon = self.bridge.post_failure_message(self.by_id['env.find'], "no_command", None, 2026)
        self.assertIn("No ribbon path is recorded", no_ribbon)
        cannot = self.bridge.post_failure_message(row, "cannot_post", "Revit cannot run it in the current state.", 2027)
        self.assertIn("Try again", cannot)
        self.assertIn("Open it from the ribbon:", self.bridge.ribbon_message(row, 2026))


class TestSeenTips(_BridgeTest):
    def test_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'tekla_bridge.json')
            self.assertEqual(self.bridge.load_seen(path), set())
            self.assertFalse(self.bridge.load_tip_once(path))
            self.assertTrue(self.bridge.mark_seen(path, "model.castunit"))
            self.assertTrue(self.bridge.mark_seen(path, "rebar.copy"))
            self.assertTrue(self.bridge.save_tip_once(path, True))
            self.assertEqual(self.bridge.load_seen(path), {"model.castunit", "rebar.copy"})
            self.assertTrue(self.bridge.load_tip_once(path))
            self.assertTrue(self.bridge.mark_seen(path, "rebar.copy"))     # idempotent
            self.assertEqual(len(self.bridge.load_seen(path)), 2)
            self.assertTrue(self.bridge.save_tip_once(path, False))
            self.assertFalse(self.bridge.load_tip_once(path))
            self.assertEqual(self.bridge.load_seen(path), {"model.castunit", "rebar.copy"})

    def test_corrupt_file_reads_as_empty_and_unwritable_path_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'tekla_bridge.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write("{not json")
            self.assertEqual(self.bridge.load_seen(path), set())
            self.assertFalse(self.bridge.load_tip_once(path))
            self.assertFalse(self.bridge.mark_seen(os.path.join(tmp, 'missing-dir', 'x.json'), "a"))


class TestPostCommand(_BridgeTest):
    def test_posted_with_the_first_candidate_this_release_has(self):
        row = copy.deepcopy(self.by_id['model.pour'])          # postable: ["Phases"]
        app = _FakeUiApp()
        with _StubbedRevitUI():
            status, name = self.bridge.post_revit_command(app, row, 2027)
        self.assertEqual((status, name), ("posted", "ID_FOR_PHASES"))
        self.assertEqual(app.posted, ["ID_FOR_PHASES"])

    def test_candidate_list_skips_names_a_release_lacks(self):
        row = copy.deepcopy(self.by_id['rebar.single'])        # ["StructuralRebar", "Rebar"]: neither in the fake enum
        row['postable'] = ["NoSuchCommand", "Copy"]
        app = _FakeUiApp()
        with _StubbedRevitUI():
            status, name = self.bridge.post_revit_command(app, row, 2026)
        self.assertEqual(status, "posted")
        self.assertEqual(app.posted, ["ID_FOR_COPY"])

    def test_no_command_returns_the_ribbon_path_for_the_release(self):
        row = self.by_id['rebar.splice']                       # InsertCoupler: missing in the fake enum
        with _StubbedRevitUI():
            status, path = self.bridge.post_revit_command(_FakeUiApp(), row, 2027)
            self.assertEqual((status, path), ("no_command", "Concrete Detailing › Reinforcement › Coupler"))
            status, path = self.bridge.post_revit_command(_FakeUiApp(), row, 2026)
            self.assertEqual((status, path), ("no_command", "Structure › Reinforcement › Coupler"))

    def test_captured_command_id_is_the_fallback(self):
        row = copy.deepcopy(self.by_id['rebar.splice'])
        row['revit_command_id'] = "ID_CAPTURED"
        app = _FakeUiApp()
        with _StubbedRevitUI():
            status, name = self.bridge.post_revit_command(app, row, 2027)
        self.assertEqual((status, name), ("posted", "ID_CAPTURED"))

    def test_cannot_post_when_revit_refuses_or_raises(self):
        row = self.by_id['model.pour']
        with _StubbedRevitUI():
            status, reason = self.bridge.post_revit_command(_FakeUiApp(can_post=False), row, 2027)
            self.assertEqual(status, "cannot_post")
            self.assertIn("current state", reason)
            status, reason = self.bridge.post_revit_command(_FakeUiApp(raises=RuntimeError("boom\nsecond line")), row, 2027)
            self.assertEqual((status, reason), ("cannot_post", "boom"))
            status, reason = self.bridge.post_revit_command(None, row, 2027)
            self.assertEqual(status, "cannot_post")

    def test_post_never_touches_the_document(self):
        # The fake app has no Document / transaction members at all: posting must still work.
        app = _FakeUiApp()
        self.assertFalse(hasattr(app, "ActiveUIDocument"))
        with _StubbedRevitUI():
            self.assertEqual(self.bridge.post_revit_command(app, self.by_id['model.pour'], 2027)[0], "posted")


class TestRunTool(_BridgeTest):
    def test_no_document_asks_for_a_project(self):
        text = self.bridge.run_tool(self.by_id['model.castunit'], None)
        self.assertEqual(text, "Open a Revit project before running Cast Unit Manager.")

    def test_family_document_is_refused(self):
        doc = types.SimpleNamespace(IsFamilyDocument=True)
        self.assertEqual(self.bridge.run_tool(self.by_id['drawing.clone'], doc),
                         "Clone Drawing works on projects, not on family documents.")

    def test_row_without_tool_says_where_to_look(self):
        text = self.bridge.run_tool(self.by_id['model.part'], types.SimpleNamespace(IsFamilyDocument=False))
        self.assertIn("No T3Lab tool is linked", text)

    def test_missing_module_says_what_where_next(self):
        doc = types.SimpleNamespace(IsFamilyDocument=False)
        row = copy.deepcopy(self.by_id['rebar.component'])
        saved = self.bridge.TOOL_ENTRY['RebarWizard']
        self.bridge.TOOL_ENTRY['RebarWizard'] = ("GUI.NoSuchDialogForTest", "show_rebar_wizard")
        try:
            text = self.bridge.run_tool(row, doc)
        finally:
            self.bridge.TOOL_ENTRY['RebarWizard'] = saved
        self.assertIn("Rebar Wizard is not available in this install", text)
        self.assertIn("restart Revit", text)

    def test_tool_entry_is_called_with_the_document_and_errors_are_reported(self):
        doc = types.SimpleNamespace(IsFamilyDocument=False)
        calls = []
        module = types.ModuleType("t3_fake_tool_dialog")
        module.show_ok = lambda d: calls.append(d)

        def _boom(_doc):
            raise RuntimeError("first line\nstack")
        module.show_boom = _boom
        sys.modules["t3_fake_tool_dialog"] = module
        row = self.by_id['rebar.component']
        saved = self.bridge.TOOL_ENTRY['RebarWizard']
        try:
            self.bridge.TOOL_ENTRY['RebarWizard'] = ("t3_fake_tool_dialog", "show_ok")
            self.assertIsNone(self.bridge.run_tool(row, doc))
            self.assertEqual(calls, [doc])
            self.bridge.TOOL_ENTRY['RebarWizard'] = ("t3_fake_tool_dialog", "show_boom")
            self.assertIn("stopped with an error: first line", self.bridge.run_tool(row, doc))
            self.bridge.TOOL_ENTRY['RebarWizard'] = ("t3_fake_tool_dialog", "missing_function")
            self.assertIn("has no entry point", self.bridge.run_tool(row, doc))
        finally:
            self.bridge.TOOL_ENTRY['RebarWizard'] = saved
            sys.modules.pop("t3_fake_tool_dialog", None)


class TestGeneratedOutputs(_BridgeTest):
    def test_build_docs_check_passes(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = ""
        result = subprocess.run([sys.executable, BUILD_SCRIPT, '--check'], cwd=REPO, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_shortcut_template_only_cited_keys(self):
        for row in self.rows:
            if row['tekla_shortcut'] is not None:
                self.assertIn(row['tekla_shortcut'], CITED_KEYS, row['id'])
        self.assertEqual(set(self.bridge.CITED_SHORTCUT_KEYS), CITED_KEYS)
        with open(XML_FILE, 'r', encoding='utf-8') as handle:
            xml_text = handle.read()
        keys = re.findall(r'Tekla default ([^)]+)\)', xml_text)
        self.assertTrue(keys, "the template should cite at least one Tekla default")
        for key in keys:
            self.assertIn(key, CITED_KEYS)

    def test_shortcut_template_is_well_formed_and_has_no_invented_ids(self):
        import xml.etree.ElementTree as ET
        tree = ET.parse(XML_FILE)
        self.assertEqual(tree.getroot().tag, "Shortcuts")
        ids = set(r['revit_command_id'] for r in self.rows if r['revit_command_id'])
        items = tree.getroot().findall("ShortcutItem")
        self.assertEqual(len(items), len(ids))
        for item in items:
            self.assertEqual(item.get("Shortcuts"), "", "keys stay empty: the user decides")
            self.assertIn(item.get("CommandId"), ids)

    def test_generated_xml_comments_never_hold_a_double_hyphen(self):
        with open(XML_FILE, 'r', encoding='utf-8') as handle:
            body = handle.read()
        for comment in re.findall(r'<!--(.*?)-->', body, re.S):
            self.assertNotIn("--", comment)

    def test_guide_is_bilingual_and_covers_every_row(self):
        with open(self.bridge.guide_path(), 'r', encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn("GENERATED by dev/build_tekla_docs.py", text)
        for row in self.rows:
            self.assertIn(row['tekla'].replace("|", "\\|"), text, row['id'])
            self.assertIn(row['doc_en'], text, row['id'])
            self.assertIn(row['doc_vi'], text, row['id'])
        for heading in ("## 1 · Three layers", "## 3 · Weight schedule template", "## 4 · Shortcuts",
                        "## 5 · Assembly rules", "## 6 · Known limits"):
            self.assertIn(heading, text)
        self.assertEqual(len(re.findall(r'\| A[1-8] \|', text)), 8)
        self.assertIn("T3_WeightPerMetre", text)


class TestSourceHygiene(unittest.TestCase):
    def test_module_has_no_revit_import_at_module_level(self):
        with open(SOURCE, 'r', encoding='utf-8') as handle:
            lines = handle.read().splitlines()
        for number, line in enumerate(lines, start=1):
            if re.match(r'^(import|from)\s+(Autodesk|clr|System|pyrevit)\b', line):
                self.fail("line %d imports Revit/.NET at module level: %s" % (number, line))

    def test_no_print_and_no_python2_syntax(self):
        with open(SOURCE, 'r', encoding='utf-8') as handle:
            source = handle.read()
        self.assertIsNone(re.search(r'^\s*print\s*\(', source, re.M))
        for banned in ('xrange', 'unicode(', 'execfile', '__builtin__', 'urllib2'):
            self.assertNotIn(banned, source)


if __name__ == '__main__':
    unittest.main()
