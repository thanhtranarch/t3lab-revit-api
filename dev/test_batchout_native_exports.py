"""Run shipped combined PDF/IFC methods with API doubles and real output files."""
import ast
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_service_transactions import FakeTransaction, STATUS


SOURCE = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'


class IdList(list):
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def Add(self, value):
        self.append(value)

    @property
    def Count(self):
        return len(self)


def load_window(transaction, forms):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    wanted = {'_native_output_stamp', '_confirm_native_output', '_wait_for_export_file',
              '_verify_export', '_element_of', 'export_to_pdf', 'export_to_ifc', 'start_export'}
    body = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == '_PendingExportError':
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'ExportManagerWindow':
            node.bases = []
            node.body = [method for method in node.body
                         if isinstance(method, ast.FunctionDef) and method.name in wanted]
            body.append(node)
    tree.body = body
    scope = dict(os=os, time=time, logger=Mock(), forms=forms, GC=Mock(),
                 DB=SimpleNamespace(TransactionStatus=STATUS, ElementId=object,
                                    ExportPaperFormat=SimpleNamespace(Default=0)),
                 List=IdList, PDFExportOptions=SimpleNamespace,
                 IFCExportOptions=SimpleNamespace, HAS_IFC=True,
                 IFCVersion=SimpleNamespace(IFC2x2=0, IFC2x3=1, IFC4=2),
                 Transaction=Mock(return_value=transaction), REVIT_VERSION=2026)
    exec(compile(tree, str(SOURCE), 'exec'), scope)
    return scope['ExportManagerWindow'](), scope['_PendingExportError']


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.transaction = FakeTransaction()
        self.forms = Mock()
        self.window, self.pending_error = load_window(self.transaction, self.forms)
        w = self.window
        self.items = [SimpleNamespace(SheetNumber=name,
                                      Sheet=SimpleNamespace(SheetNumber=name, Id=index))
                      for index, name in enumerate(('A1', 'A2'), 1)]
        w.combine_pdf = SimpleNamespace(IsChecked=True)
        w.api_adapter = None
        w._overall_counter = 0
        w._overall_total = 2
        w._skip_fatal = False
        w._is_known_fatal = Mock(return_value=False)
        w._note_skipped_fatal = Mock()
        w._note_failure = Mock()
        w._failed_items = []
        w._sync_item_labels = Mock()
        w._apply_pdf_quality = Mock()
        w._reserve_filename = lambda folder, name, extension: name
        w._raster_level_for = Mock(return_value=None)
        w._write_crash_marker = Mock()
        w._clear_crash_marker = Mock()
        w._update_progress = Mock()
        w.update_export_item_progress = Mock()
        w.get_export_filename = lambda item: item.SheetNumber
        w.pdf_paper_size = SimpleNamespace(SelectedItem=None)
        w.pdf_auto_detect_size = SimpleNamespace(IsChecked=True)
        for name in ('pdf_hide_ref_planes', 'pdf_hide_scope_boxes',
                     'pdf_hide_crop_boundaries', 'pdf_hide_unreferenced_tags'):
            setattr(w, name, SimpleNamespace(IsChecked=False))
        w.doc = SimpleNamespace(Export=Mock(side_effect=self.write_pdf))
        # Use the actual file-wait implementation, with a zero timeout so stale
        # file tests execute its final check without spending seconds polling.
        real_wait = w._wait_for_export_file
        w._wait_for_export_file = lambda path, previous, timeout: real_wait(path, previous, 0)

    def write_pdf(self, folder, ids, options):
        (Path(folder) / (options.FileName + '.pdf')).write_bytes(b'new PDF output')
        return True

    def write_ifc(self, folder, name, options):
        (Path(folder) / (name + '.ifc')).write_bytes(b'new IFC output')
        return True

    def pdf(self):
        return self.window.export_to_pdf(self.items, self.temp.name)

    def ifc(self):
        return self.window.export_to_ifc(self.items, self.temp.name)

    def individual_pdf_setup(self):
        self.window.combine_pdf.IsChecked = False
        self.window._end_safe_mode = Mock()
        self.window.get_sheet_paper_size_and_orientation = lambda sheet: (None, None)
        for item in self.items:
            item.CustomFilename = ''

    def test_individual_pdf_success_confirms_each_real_file(self):
        self.individual_pdf_setup()
        self.assertEqual(self.pdf(), 2)
        self.assertEqual(self.window._overall_counter, 2)
        self.assertTrue((self.folder / 'A1.pdf').is_file())
        self.assertTrue((self.folder / 'A2.pdf').is_file())
        self.assertEqual(self.window._end_safe_mode.call_count, 2)

    def test_individual_pdf_false_retains_artifact_and_continues_next_item(self):
        self.individual_pdf_setup()
        def export(*args):
            self.write_pdf(*args)
            return args[2].FileName != 'A1'
        self.window.doc.Export.side_effect = export
        self.assertEqual(self.pdf(), 1)
        self.assertTrue((self.folder / 'A1.pdf').is_file())
        self.window._note_failure.assert_called_once()
        self.assertFalse(any(call.args[:3] == ('A1', 'PDF', 100) for call in
                             self.window.update_export_item_progress.call_args_list))
        self.window.update_export_item_progress.assert_any_call('A2', 'PDF', 100)
        self.assertEqual(self.window._clear_crash_marker.call_count, 2)
        self.assertEqual(self.window._end_safe_mode.call_count, 2)

    def test_individual_pdf_adapter_false_is_not_confirmed(self):
        self.individual_pdf_setup()
        self.window.api_adapter = SimpleNamespace(
            configure_pdf_options=lambda options, **kwargs: options,
            export_pdf=lambda folder, name, ids, options: (self.write_pdf(folder, ids, options) and False))
        self.assertEqual(self.pdf(), 0)
        self.assertEqual(self.window._note_failure.call_count, 2)
        self.assertTrue((self.folder / 'A1.pdf').is_file())

    def test_combined_pdf_success_confirms_exact_file_and_rows(self):
        self.assertEqual(self.pdf(), 1)
        self.assertTrue((self.folder / 'A1-A2_Combined.pdf').is_file())
        self.assertEqual(self.window._overall_counter, 2)
        self.window.update_export_item_progress.assert_any_call('A1', 'PDF', 100)
        self.window.update_export_item_progress.assert_any_call('A2', 'PDF', 100)

    def test_combined_pdf_stale_file_is_not_success(self):
        (self.folder / 'A1-A2_Combined.pdf').write_bytes(b'old PDF')
        self.window.doc.Export.side_effect = None
        self.window.doc.Export.return_value = True
        self.assertEqual(self.pdf(), 0)
        self.assertEqual(self.window._overall_counter, 0)
        self.assertEqual(self.window._note_failure.call_count, 2)

    def test_combined_pdf_updated_existing_file_is_confirmed(self):
        (self.folder / 'A1-A2_Combined.pdf').write_bytes(b'old')
        self.assertEqual(self.pdf(), 1)

    def test_combined_pdf_empty_file_is_not_success(self):
        def empty_export(folder, ids, options):
            (Path(folder) / (options.FileName + '.pdf')).touch()
            return True
        self.window.doc.Export.side_effect = empty_export
        self.assertEqual(self.pdf(), 0)

    def test_combined_pdf_false_with_fresh_file_never_marks_rows_successful(self):
        def partial_export(*args):
            self.write_pdf(*args)
            return False
        self.window.doc.Export.side_effect = partial_export
        self.assertEqual(self.pdf(), 0)
        self.assertTrue((self.folder / 'A1-A2_Combined.pdf').is_file())
        self.window._clear_crash_marker.assert_called_once()
        self.assertFalse(any(call.args[2] == 100 for call in
                             self.window.update_export_item_progress.call_args_list))

    def test_pdf_adapter_preserves_native_false_result_and_combined_failure(self):
        adapter_path = SOURCE.parents[1] / 'Intelligence/api_learner.py'
        tree = ast.parse(adapter_path.read_text(encoding='utf-8'))
        adapter_node = next(node for node in tree.body
                            if isinstance(node, ast.ClassDef) and node.name == 'SmartAPIAdapter')
        method = next(node for node in adapter_node.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'export_pdf')
        module = ast.Module(body=[method], type_ignores=[])
        scope = {}
        exec(compile(module, str(adapter_path), 'exec'), scope)
        adapter = SimpleNamespace(doc=self.window.doc,
                                  configure_pdf_options=lambda options, **kwargs: options)
        adapter.export_pdf = lambda *args: scope['export_pdf'](adapter, *args)
        def partial_export(*args):
            self.write_pdf(*args)
            return False
        self.window.doc.Export.side_effect = partial_export
        self.window.api_adapter = adapter
        self.assertEqual(self.pdf(), 0)
        self.assertTrue((self.folder / 'A1-A2_Combined.pdf').is_file())
        self.assertEqual(self.window._note_failure.call_count, 2)

    def test_combined_pdf_unrelated_new_file_cannot_confirm_expected_file(self):
        def unrelated_export(*args):
            (self.folder / 'another-export.pdf').write_bytes(b'not this export')
            return True
        self.window.doc.Export.side_effect = unrelated_export
        self.assertEqual(self.pdf(), 0)

    def test_combined_pdf_skip_and_invalid_rows_never_become_successful(self):
        self.items += [SimpleNamespace(SheetNumber='Bad')]
        self.window._skip_fatal = True
        self.window._is_known_fatal.side_effect = lambda name, fmt: name == 'A2'
        self.assertEqual(self.pdf(), 1)
        self.assertEqual(self.window.doc.Export.call_args.args[1], [1])
        self.assertEqual(self.window._overall_counter, 1)
        successful = [call.args[0] for call in self.window.update_export_item_progress.call_args_list
                      if call.args[2] == 100]
        self.assertEqual(successful, ['A1'])
        self.window._note_skipped_fatal.assert_called_once_with('A2', 'PDF')
        self.window._note_failure.assert_called_once_with('Bad', 'PDF', 'row is neither a sheet nor a view')

    def test_combined_pdf_all_invalid_does_not_call_native_export(self):
        self.items = [SimpleNamespace(SheetNumber='Bad')]
        self.assertEqual(self.pdf(), 0)
        self.window.doc.Export.assert_not_called()

    def test_combined_pdf_retains_view_filename_length_limit(self):
        self.items = [SimpleNamespace(SheetNumber='Long view',
                                     View=SimpleNamespace(Name='V' * 120, Id=1))]
        self.assertEqual(self.pdf(), 1)
        self.assertEqual(self.window.doc.Export.call_args.args[2].FileName,
                         '{}-{}_Combined'.format('V' * 20, 'V' * 20))

    def test_ifc_requires_true_committed_and_new_file(self):
        self.window.doc.Export.side_effect = self.write_ifc
        self.assertEqual(self.ifc(), 1)
        self.assertEqual(self.transaction.status, STATUS.Committed)
        self.assertTrue((self.folder / 'A1-A2_Model_IFC.ifc').is_file())
        self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_ifc_false_result_rolls_back_and_keeps_native_artifact(self):
        def false_export(*args):
            self.write_ifc(*args)
            return False
        self.window.doc.Export.side_effect = false_export
        self.assertEqual(self.ifc(), 0)
        self.assertEqual(self.transaction.rollbacks, 1)
        self.assertTrue((self.folder / 'A1-A2_Model_IFC.ifc').is_file())

    def test_ifc_commit_rolled_back_does_not_report_output_as_success(self):
        self.window.doc.Export.side_effect = self.write_ifc
        self.transaction.outcome = STATUS.RolledBack
        self.assertEqual(self.ifc(), 0)
        self.assertEqual(self.transaction.rollbacks, 0)
        self.assertEqual(self.window._overall_counter, 0)

    def test_ifc_commit_exception_rolls_back_started_transaction(self):
        self.window.doc.Export.side_effect = self.write_ifc
        self.transaction.outcome = RuntimeError('Commit failed')
        self.assertEqual(self.ifc(), 0)
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_ifc_start_failure_does_not_attempt_rollback_or_export(self):
        self.transaction.start_error = True
        self.assertEqual(self.ifc(), 0)
        self.assertEqual(self.transaction.rollbacks, 0)
        self.window.doc.Export.assert_not_called()

    def test_ifc_stale_output_is_not_success(self):
        (self.folder / 'A1-A2_Model_IFC.ifc').write_bytes(b'old IFC')
        self.window.doc.Export.side_effect = None
        self.window.doc.Export.return_value = True
        self.assertEqual(self.ifc(), 0)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_ifc_updated_existing_file_is_confirmed(self):
        (self.folder / 'A1-A2_Model_IFC.ifc').write_bytes(b'old')
        self.window.doc.Export.side_effect = self.write_ifc
        self.assertEqual(self.ifc(), 1)

    def test_ifc_empty_output_is_not_success(self):
        def empty_export(folder, name, options):
            (Path(folder) / (name + '.ifc')).touch()
            return True
        self.window.doc.Export.side_effect = empty_export
        self.assertEqual(self.ifc(), 0)

    def test_ifc_pending_propagates_without_invalid_rollback(self):
        self.window.doc.Export.side_effect = self.write_ifc
        self.transaction.outcome = STATUS.Pending
        with self.assertRaises(self.pending_error):
            self.ifc()
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_ifc_pending_stops_later_formats_in_real_start_export(self):
        w = self.window
        w.doc.Export.side_effect = self.write_ifc
        self.transaction.outcome = STATUS.Pending
        w.all_sheets = self.items
        for item in self.items:
            item.IsSelected = True
        w.selection_mode = 'sheets'
        w.output_folder = SimpleNamespace(Text=self.temp.name)
        for name in ('reverse_order', 'save_split_by_format', 'export_pdf', 'export_dwf',
                     'export_dgn', 'export_nwd'):
            setattr(w, name, SimpleNamespace(IsChecked=False))
        for name in ('export_dwg', 'export_ifc', 'export_img'):
            setattr(w, name, SimpleNamespace(IsChecked=True))
        w.export_to_dwg = Mock(return_value=1)
        w.export_to_images = Mock()
        w._ensure_titleblock_cache = Mock()
        w.save_latest_setup = Mock()
        w.next_button = SimpleNamespace(IsEnabled=True)
        w.back_button = SimpleNamespace(IsEnabled=True)
        w.status_text = SimpleNamespace(Text='')
        w.export_items = [object()]
        w.build_export_preview = Mock()
        w._reset_run_state = Mock()
        w._ask_safe_mode = Mock()
        w._order_risky_last = lambda items: items
        w.start_export()
        w.export_to_images.assert_not_called()
        self.assertIn('awaiting Revit failure resolution', self.forms.alert.call_args.args[0])
        self.assertIn('1 earlier output', self.forms.alert.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
