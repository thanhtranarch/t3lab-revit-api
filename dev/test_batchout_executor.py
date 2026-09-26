"""Exercise the shipped direct-export service without Revit, using real temp files."""
import ast
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from tabdir import TAB  # the tab folder name changes

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'T3Lab.extension/lib/Services/batchout_executor.py'
spec = importlib.util.spec_from_file_location('batchout_under_test', path)
executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(executor)


class Event:
    def __init__(self, handler):
        self.handlers = [handler]

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self


class BatchOutExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.save = Mock()
        self.window = SimpleNamespace(
            all_sheets=[SimpleNamespace(SheetNumber='A1', SheetName='Plan', IsSelected=False),
                        SimpleNamespace(SheetNumber='A2', SheetName='Section', IsSelected=False)],
            output_folder=SimpleNamespace(Text='previous'),
            combine_pdf=SimpleNamespace(IsChecked=False),
            _ensure_titleblock_cache=Mock(), _reset_run_state=Mock(),
            _window_closing_save_setup=self.save, _window_closed_dispose=Mock(),
            _failed_items=[], main_tabs=SimpleNamespace(SelectedIndex=0))
        self.window.Closing = Event(self.save)
        self.window.Close = Mock(side_effect=lambda: [h(None, None) for h in self.window.Closing.handlers])
        for attr in set(executor._FMT_ATTRS.values()):
            setattr(self.window, attr, SimpleNamespace(IsChecked=False))
        self.method = Mock(side_effect=self.export)
        for name in ('pdf', 'dwg', 'dwf', 'dgn', 'nwd', 'ifc', 'images'):
            setattr(self.window, 'export_to_' + name, self.method)
        self.mod = SimpleNamespace(ExportManagerWindow=Mock(return_value=self.window))
        self.count = 2

    def export(self, selected, folder):
        # Production delegates need these before their first native call.
        self.assertEqual(self.window._overall_counter, 0)
        self.assertEqual(self.window._overall_total, len(selected))
        self.window._reset_run_state.assert_called_once()
        if self.window.combine_pdf.IsChecked and self.count:
            (Path(folder) / 'combined.pdf').write_bytes(b'PDF output')
        return self.count

    def run_export(self, **config):
        return executor.direct_export(self.mod, dict(folder=self.temp.name, **config))

    def test_zero_outputs_are_failure_and_hidden_window_is_disposed(self):
        self.count = 0
        ok, count, msg = self.run_export()
        self.assertFalse(ok)
        self.assertEqual(count, 0)
        self.assertIn('No PDF outputs', msg)
        self.window.Close.assert_called_once()
        self.window._window_closed_dispose.assert_called_once()
        self.save.assert_not_called()

    def test_partial_count_is_preserved_as_incomplete(self):
        self.count = 1
        self.assertEqual(self.run_export()[:2], (False, 1))

    def test_full_count_is_success(self):
        self.assertEqual(self.run_export()[:2], (True, 2))

    def test_nwc_and_legacy_nwd_use_actual_output_format(self):
        for fmt in ('nwc', 'NWC', 'nwd'):
            with self.subTest(fmt=fmt):
                self.setUp()
                ok, count, message = self.run_export(format=fmt)
                self.assertTrue(ok)
                self.assertEqual(count, 2)
                self.assertIn('NWC output(s)', message)
                self.assertEqual(Path(self.method.call_args.args[1]).name, 'NWC')
                self.assertTrue(self.window.export_nwd.IsChecked)

    def test_natural_language_nwc_request_does_not_default_to_pdf(self):
        self.assertEqual(executor.parse_export_params('export all sheets to NWC'),
                         {'format': 'nwc', 'filter': ''})

    def test_combined_pdf_expects_one_output_for_many_sheets(self):
        self.count = 1
        self.assertEqual(self.run_export(combine=True)[:2], (True, 1))

    def test_failed_item_prevents_full_success_even_with_positive_count(self):
        self.window._failed_items = ['A2 failed']
        ok, count, msg = self.run_export()
        self.assertFalse(ok)
        self.assertEqual(count, 2)
        self.assertIn('A2 failed', msg)

    def test_invalid_formats_never_create_window_or_default_to_pdf(self):
        for fmt in ('', None, 'pdf,dwg', 'csv', ['pdf']):
            with self.subTest(fmt=fmt):
                self.assertFalse(self.run_export(format=fmt)[0])
        self.mod.ExportManagerWindow.assert_not_called()
        self.method.assert_not_called()

    def test_image_alias_selects_checkbox_without_later_unchecking_it(self):
        for fmt in ('img', 'image', ' IMAGE '):
            with self.subTest(fmt=fmt):
                executor.configure_batchout_window(self.window, {'format': fmt})
                self.assertTrue(self.window.export_img.IsChecked)
                self.assertFalse(self.window.export_pdf.IsChecked)

    def test_no_matching_sheets_does_not_call_exporter(self):
        self.assertFalse(self.run_export(filter='ZZ')[0])
        self.method.assert_not_called()
        self.window.Close.assert_called_once()

    def test_progress_callback_exception_does_not_change_success(self):
        callback = Mock(side_effect=RuntimeError('Chat closed'))
        self.assertEqual(executor.direct_export(self.mod, {'folder': self.temp.name}, callback)[:2], (True, 2))

    def test_export_exception_keeps_failure_even_when_reporter_also_fails(self):
        self.method.side_effect = RuntimeError('Export failed')
        result = executor.direct_export(self.mod, {'folder': self.temp.name}, Mock(side_effect=RuntimeError('UI failed')))
        self.assertEqual(result[:2], (False, 0))
        self.assertIn('Export failed', result[2])
        self.window.Close.assert_called_once()

    def test_invalid_count_cannot_be_success(self):
        for count in (-1, True, '2'):
            with self.subTest(count=count):
                self.setUp()
                self.count = count
                self.assertEqual(self.run_export()[:2], (False, 0))

    def test_ifc_stale_file_does_not_confirm_current_export(self):
        folder = Path(self.temp.name) / 'IFC'
        folder.mkdir()
        (folder / 'old.ifc').write_text('old IFC')
        self.count = 1
        ok, count, msg = self.run_export(format='ifc')
        self.assertFalse(ok)
        self.assertEqual(count, 0)
        self.assertIn('could be verified', msg)

    def test_ifc_new_nonempty_file_confirms_single_model_output(self):
        def create_ifc(items, folder):
            (Path(folder) / 'new.ifc').write_text('IFC model')
            return 1
        self.method.side_effect = create_ifc
        self.assertEqual(self.run_export(format='ifc')[:2], (True, 1))

    def test_ifc_empty_file_is_not_success(self):
        def empty_ifc(items, folder):
            (Path(folder) / 'empty.ifc').touch()
            return 1
        self.method.side_effect = empty_ifc
        self.assertEqual(self.run_export(format='ifc')[:2], (False, 0))

    def test_pushbutton_exports_the_class_required_by_assistant_loader(self):
        script = Path(TAB) / 'Views & Sheets.panel/BatchOut.pushbutton/script.py'
        tree = ast.parse(script.read_text(encoding='utf-8-sig'))
        # ast.walk: the import sits in the non-__main__ branch, so running the
        # button keeps it inside ErrorGuard.run_tool.
        names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                 and node.module == 'GUI.BatchOutDialog' for alias in node.names}
        self.assertIn('ExportManagerWindow', names)

    def test_format_controls_exist_in_shipped_xaml(self):
        root = ET.parse(ROOT / 'T3Lab.extension/lib/GUI/Tools/ExportManager.xaml').getroot()
        names = {node.get('{http://schemas.microsoft.com/winfx/2006/xaml}Name')
                 for node in root.iter()}
        self.assertTrue(set(executor._FMT_ATTRS.values()).issubset(names))

    def test_cancel_before_start_never_creates_window(self):
        result = executor.direct_export(self.mod, {}, cancel_check=lambda: True)
        self.assertEqual(result[:2], (False, 0))
        self.mod.ExportManagerWindow.assert_not_called()

    def test_cancel_between_items_keeps_output_and_skips_remaining_items(self):
        stopped = [False]
        def first_item(items, folder):
            self.assertEqual(len(items), 1)
            stopped[0] = True
            return 1
        self.method.side_effect = first_item
        result = executor.direct_export(self.mod, {'folder': self.temp.name},
                                        cancel_check=lambda: stopped[0])
        self.assertEqual(result[:2], (False, 1))
        self.assertIn('kept', result[2])
        self.method.assert_called_once()

    def test_combined_pdf_remains_one_native_call_with_cancel_check(self):
        self.count = 1
        result = executor.direct_export(self.mod, {'folder': self.temp.name, 'combine': True},
                                        cancel_check=lambda: False)
        self.assertEqual(result[:2], (True, 1))
        self.assertEqual(len(self.method.call_args.args[0]), 2)

    def test_combined_pdf_stale_file_cannot_confirm_current_export(self):
        folder = Path(self.temp.name) / 'PDF'
        folder.mkdir()
        (folder / 'old.pdf').write_bytes(b'previous PDF')
        self.method.side_effect = None
        self.method.return_value = 1
        self.assertEqual(self.run_export(combine=True)[:2], (False, 0))

    def test_combined_pdf_empty_file_is_not_success(self):
        def empty_pdf(items, folder):
            (Path(folder) / 'combined.pdf').touch()
            return 1
        self.method.side_effect = empty_pdf
        self.assertEqual(self.run_export(combine=True)[:2], (False, 0))


if __name__ == '__main__':
    unittest.main()
