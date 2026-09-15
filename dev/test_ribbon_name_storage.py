"""Exercise real ribbon JSON storage without loading Revit or user settings."""
import ast
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'T3Lab.extension/T3Lab.tab/Support.panel/UI.stack/Ribbon Names.pushbutton/script.py'


class RibbonStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.scope = dict(os=os, json=json, MAP_PATH=str(self.path), ORIG_PATH=str(self.path),
                          STATE_PATH=str(self.path), DEFAULT_MAP={'Architecture': 'Arch'})
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != 'main']
        exec(compile(tree, str(SOURCE), 'exec'), self.scope)

    def test_utf8_roundtrip_preserves_names(self):
        data = {'Kiến trúc': 'KT', '__tab_ids__': {'tab1': 'Kiến trúc'}}
        self.assertTrue(self.scope['_write_json'](str(self.path), data))
        self.assertEqual(self.scope['_read_json'](str(self.path), {}), data)

    def test_failed_serialization_preserves_original_file_and_cleans_temp(self):
        self.path.write_text('{"before": "intact"}', encoding='utf-8')
        before = self.path.read_bytes()
        self.assertFalse(self.scope['_write_json'](str(self.path), {'bad': object()}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_replace_failure_preserves_existing_file(self):
        self.path.write_bytes(b'previous')
        with patch.object(os, 'replace', side_effect=PermissionError('Locked')):
            self.assertFalse(self.scope['_write_json'](str(self.path), {'new': 'data'}))
        self.assertEqual(self.path.read_bytes(), b'previous')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_state_callback_reports_failure(self):
        with patch.object(os, 'replace', side_effect=PermissionError('Locked')):
            self.assertFalse(self.scope['save_state']('short'))

    def test_wrong_json_types_use_safe_defaults(self):
        for value in ([], 'bad', None, 42):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding='utf-8')
                self.assertEqual(self.scope['load_map'](), {'Architecture': 'Arch'})
                self.assertEqual(self.scope['load_originals'](), {})
                self.assertEqual(self.scope['load_state'](), 'full')

    def test_invalid_map_values_are_not_passed_to_dialog(self):
        self.path.write_text(json.dumps({'Architecture': [], 'Structure': 'Struc'}), encoding='utf-8')
        self.assertEqual(self.scope['load_map'](), {'Architecture': 'Arch', 'Structure': 'Struc'})


if __name__ == '__main__':
    unittest.main()
