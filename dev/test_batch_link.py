# -*- coding: utf-8 -*-
"""
Tests for BatchLink logic: file scanning, backup detection, item formatting,
and the Revit-free helpers in Snippets/_links.py (workset name matching,
display-mode mapping).
Run: python dev/test_batch_link.py
"""
import os
import sys
import re
import unittest
import tempfile
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

BACKUP_REGEX = re.compile(r'\.\d{3,4}\.rvt$', re.IGNORECASE)


def _load_links_module():
    """Exec Snippets/_links.py with the Revit and .NET imports stubbed out.

    The module is only importable inside Revit, so the Revit-free helpers are
    exercised against the real shipped source rather than a copy.
    """
    import types

    class _AnyMeta(type):
        """Any attribute lookup yields another permissive placeholder type."""
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(name):
        return _AnyMeta(str(name), (), {})

    def _stub(name):
        mod = types.ModuleType(name)
        mod.__getattr__ = _make_any
        return mod

    saved = {}
    stubs = {
        'Autodesk': _stub('Autodesk'),
        'Autodesk.Revit': _stub('Autodesk.Revit'),
        'Autodesk.Revit.DB': _stub('Autodesk.Revit.DB'),
        'System': _stub('System'),
        'System.Collections': _stub('System.Collections'),
        'System.Collections.Generic': _stub('System.Collections.Generic'),
    }
    stubs['System.Collections.Generic'].List = {}
    # wire the dotted attribute chain so `import a.b.c as x` resolves
    stubs['Autodesk'].Revit = stubs['Autodesk.Revit']
    stubs['Autodesk.Revit'].DB = stubs['Autodesk.Revit.DB']
    stubs['System'].Collections = stubs['System.Collections']
    stubs['System.Collections'].Generic = stubs['System.Collections.Generic']
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    try:
        source_path = os.path.join(LIB_DIR, 'Snippets', '_links.py')
        with open(source_path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_links_under_test')
        module.__file__ = source_path
        exec(compile(source, source_path, 'exec'), module.__dict__)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakeWorkset(object):
    def __init__(self, workset_id, name, is_open=True):
        self.workset_id = workset_id
        self.name = name
        self.is_open = is_open


class _FakeLinkRecord(object):
    """Stands in for Snippets._links.LinkRecord in the instance-fan-out tests."""

    def __init__(self, instance_ids, instance_id=None):
        self.instance_ids = list(instance_ids)
        self.instance_id = (instance_id if instance_id is not None
                            else (instance_ids[0] if instance_ids else None))


class TestLinkHelpers(unittest.TestCase):
    """Revit-free helpers from Snippets/_links.py."""

    @classmethod
    def setUpClass(cls):
        cls.links = _load_links_module()

    def test_split_workset_ids_matches_by_name(self):
        worksets = [_FakeWorkset(1, 'Shared Levels'),
                    _FakeWorkset(2, 'Link Architecture'),
                    _FakeWorkset(3, 'Furniture')]
        open_ids, close_ids = self.links.split_workset_ids(
            worksets, {'Shared Levels', 'Link Architecture'}, {'Furniture'})
        self.assertEqual(open_ids, [1, 2])
        self.assertEqual(close_ids, [3])

    def test_split_workset_ids_leaves_unlisted_untouched(self):
        """A workset named in neither set must not be opened or closed."""
        worksets = [_FakeWorkset(7, 'Only In This Link')]
        open_ids, close_ids = self.links.split_workset_ids(
            worksets, {'Shared Levels'}, {'Furniture'})
        self.assertEqual(open_ids, [])
        self.assertEqual(close_ids, [])

    def test_split_workset_ids_handles_empty(self):
        self.assertEqual(self.links.split_workset_ids(None, set(), set()), ([], []))
        self.assertEqual(self.links.split_workset_ids([], {'x'}, {'y'}), ([], []))

    def test_workset_config_ids_covers_every_workset(self):
        """Worksets the grid never listed keep their own state, not "open"."""
        worksets = [_FakeWorkset(1, 'Shared Levels', is_open=True),
                    _FakeWorkset(2, 'Furniture', is_open=True),
                    _FakeWorkset(3, 'Only In This Link', is_open=False),
                    _FakeWorkset(4, 'Also Only Here', is_open=True)]
        open_ids, close_ids = self.links.workset_config_ids(
            worksets, {'Shared Levels'}, {'Furniture'})
        # 1 ticked open, 4 unlisted but currently open -> open.
        self.assertEqual(sorted(open_ids), [1, 4])
        # 2 ticked closed, 3 unlisted and currently closed -> closed.
        self.assertEqual(sorted(close_ids), [2, 3])

    def test_workset_config_ids_handles_empty(self):
        self.assertEqual(self.links.workset_config_ids(None, set(), set()), ([], []))
        self.assertEqual(self.links.workset_config_ids([], {'x'}, {'y'}), ([], []))

    def test_placed_instance_ids_returns_every_instance(self):
        record = _FakeLinkRecord([11, 12, 13])
        self.assertEqual(self.links._placed_instance_ids(record), [11, 12, 13])

    def test_placed_instance_ids_falls_back_to_single(self):
        record = _FakeLinkRecord([], instance_id=7)
        self.assertEqual(self.links._placed_instance_ids(record), [7])

    def test_placed_instance_ids_of_unplaced_link_is_empty(self):
        self.assertEqual(self.links._placed_instance_ids(None), [])
        self.assertEqual(self.links._placed_instance_ids(_FakeLinkRecord([])), [])

    def test_display_modes_order(self):
        self.assertEqual(self.links.DISPLAY_MODES,
                         ("By Host View", "By Linked View", "Custom"))

    def test_mode_index_round_trip(self):
        self.assertEqual(self.links._mode_index('ByHostView'), 0)
        self.assertEqual(self.links._mode_index('ByLinkView'), 1)
        self.assertEqual(self.links._mode_index('Custom'), 2)
        self.assertEqual(self.links._mode_index('SomethingElse'), 0)

    def test_only_view_filters_accepts_custom(self):
        """Revit rejects LinkVisibility.Custom on every aspect but View Filters."""
        allowing = [key for key, _label, _kind, custom in self.links.CUSTOM_ASPECTS if custom]
        self.assertEqual(allowing, ['ViewFilterType'])
        self.assertEqual(len(self.links.aspect_modes(True)), 3)
        self.assertEqual(len(self.links.aspect_modes(False)), 2)

    def test_every_aspect_has_a_known_kind(self):
        for _key, _label, kind, _custom in self.links.CUSTOM_ASPECTS:
            self.assertIn(kind, ('prop', 'discipline', 'detail'))

class TestBatchLinkLogic(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="t3_batch_link_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_backup_regex(self):
        """Test detection of Revit numeric backup files."""
        self.assertTrue(BACKUP_REGEX.search("Project1.0001.rvt"))
        self.assertTrue(BACKUP_REGEX.search("Tower_A.0123.rvt"))
        self.assertTrue(BACKUP_REGEX.search("Hospital.001.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Project1.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Tower_A_0001.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Project.rvt.txt"))

    def test_item_formatting(self):
        """Test RevitModelItem properties and size formatting."""
        # Mock item
        class MockItem(object):
            def __init__(self, size):
                self.file_size = size
            @property
            def FileSize(self):
                if self.file_size < 1024 * 1024:
                    return "{:.1f} KB".format(self.file_size / 1024.0)
                return "{:.1f} MB".format(self.file_size / (1024.0 * 1024.0))

        item_small = MockItem(512 * 1024)
        self.assertEqual(item_small.FileSize, "512.0 KB")

        item_large = MockItem(15 * 1024 * 1024)
        self.assertEqual(item_large.FileSize, "15.0 MB")

    def test_folder_scanning(self):
        """Test scanning folder for rvt files excluding temp and backup files."""
        # Create test files
        open(os.path.join(self.test_dir, "Model_A.rvt"), "w").close()
        open(os.path.join(self.test_dir, "Model_A.0001.rvt"), "w").close()
        open(os.path.join(self.test_dir, "Model_B.rvt"), "w").close()
        open(os.path.join(self.test_dir, "something.dwg"), "w").close()
        open(os.path.join(self.test_dir, "~$Model_C.rvt"), "w").close()

        # Scan
        found = []
        for f in os.listdir(self.test_dir):
            if not f.lower().endswith(".rvt"):
                continue
            if f.startswith("~$"):
                continue
            if BACKUP_REGEX.search(f):
                continue
            found.append(f)

        found.sort()
        self.assertEqual(found, ["Model_A.rvt", "Model_B.rvt"])

if __name__ == '__main__':
    unittest.main()