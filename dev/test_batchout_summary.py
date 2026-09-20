"""Run the shipped export coordinator with exporters replaced by result doubles."""
import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ExportSummaryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ExportManagerWindow')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'start_export')
        self.alert = Mock(return_value=False)
        scope = dict(os=os, forms=SimpleNamespace(alert=self.alert), logger=Mock(), GC=Mock())
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), scope)
        self.run_export = scope['start_export']
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = SimpleNamespace(
            selection_mode='sheets', all_sheets=[SimpleNamespace(IsSelected=True)],
            reverse_order=SimpleNamespace(IsChecked=False), output_folder=SimpleNamespace(Text=self.temp.name),
            combine_pdf=SimpleNamespace(IsChecked=False),
            save_split_by_format=SimpleNamespace(IsChecked=False), next_button=SimpleNamespace(),
            back_button=SimpleNamespace(), status_text=SimpleNamespace(), export_items=[object()],
            _ensure_titleblock_cache=Mock(), save_latest_setup=Mock(), _ask_safe_mode=Mock(),
            _order_risky_last=lambda items: items, _update_progress=Mock(), _failed_items=[],
            _crash_history_file='history.json', _reset_run_state=Mock(),
            build_export_preview=Mock())
        for fmt in ('dwg', 'pdf', 'dwf', 'dgn', 'nwd', 'ifc', 'img'):
            setattr(self.window, 'export_' + fmt, SimpleNamespace(IsChecked=fmt == 'pdf'))
            setattr(self.window, 'export_to_' + ('images' if fmt == 'img' else fmt), Mock(return_value=0))

    def test_zero_outputs_never_claims_complete(self):
        self.run_export(self.window)
        self.assertIn('No exports confirmed', self.window.status_text.Text)
        self.assertNotIn('Export complete', self.alert.call_args.args[0])

    def test_no_format_stops_before_saving_or_exporting(self):
        self.window.export_pdf.IsChecked = False
        self.run_export(self.window)
        self.window.save_latest_setup.assert_not_called()
        self.window.export_to_pdf.assert_not_called()
        self.assertIn('Choose at least one', self.alert.call_args.args[0])
        self.assertTrue(self.window.IsEnabled)
        self.assertFalse(self.window._export_running)

    def test_previous_queue_is_rebuilt_for_each_run(self):
        self.run_export(self.window)
        self.window.build_export_preview.assert_called_once()

    def test_partial_results_report_issues_and_count(self):
        self.window.export_to_pdf.return_value = 1
        self.window._failed_items = ['A2: file not verified']
        self.run_export(self.window)
        self.assertIn('Export finished with issues: 1 output(s) confirmed', self.alert.call_args.args[0])
        self.assertIn('EXPORT NOT CONFIRMED', self.alert.call_args.args[0])

    def test_success_reports_confirmed_count(self):
        self.window.export_to_pdf.return_value = 2
        self.run_export(self.window)
        self.assertIn('Export complete: 2 output(s) confirmed', self.alert.call_args.args[0])

    def test_exception_stops_later_formats_and_retains_earlier_count(self):
        self.window.export_to_pdf.return_value = 2
        self.window.export_ifc.IsChecked = True
        self.window.export_img.IsChecked = True
        self.window.export_to_ifc.side_effect = RuntimeError('Pending transaction')
        self.run_export(self.window)
        self.window.export_to_images.assert_not_called()
        self.assertIn('2 earlier output(s) were confirmed', self.alert.call_args.args[0])
        self.assertTrue(self.window.next_button.IsEnabled)
        self.assertTrue(self.window.back_button.IsEnabled)

    def test_unavailable_format_after_success_is_partial_without_item_failures(self):
        self.window.export_to_pdf.return_value = 1
        self.window.export_nwd.IsChecked = True
        self.run_export(self.window)
        message = self.alert.call_args.args[0]
        self.assertIn('Export finished with issues', message)
        self.assertIn('NWC: 0 of 1 expected output(s) confirmed', message)

    def test_combined_pdf_is_one_expected_output_for_multiple_sheets(self):
        self.window.all_sheets.append(SimpleNamespace(IsSelected=True))
        self.window.combine_pdf.IsChecked = True
        self.window.export_to_pdf.return_value = 1
        self.run_export(self.window)
        self.assertIn('Export complete: 1 output(s) confirmed', self.alert.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
