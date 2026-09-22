"""ManaSheets behavior tests with model/transaction doubles; no Revit or WPF.

Run: python3 dev/test_mana_sheets.py
Real Revit failure processing, PythonNet and WPF binding/dispatcher need host QA.
"""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
from GUI import GridPendingEdits as pend
from Services.SheetManager.sheet_core.data_models import ChangeTracker

STATUS = NS(Started='Started', Committed='Committed', RolledBack='RolledBack')
FIELDS = ('sheet_number', 'sheet_name', 'designed_by', 'checked_by', 'drawn_by', 'approved_by')
PARAMS = {field: 'SHEET_' + field.upper() for field in FIELDS[2:]}


class Parameter:
    def __init__(self):
        self.value = 'Original'
        self.IsReadOnly = False
        self.reject = False

    def AsString(self):
        return self.value

    def Set(self, value):
        if self.reject:
            return False
        self.value = value
        return True


class Sheet:
    def __init__(self, doc, number):
        self.doc = doc
        self.Id = len(doc.sheets) + 1
        self._number = number
        self.Name = 'Sheet ' + number
        self.parameters = {key: Parameter() for key in PARAMS.values()}
        self.reject_numbers = set()
        doc.sheets.append(self)

    @property
    def SheetNumber(self):
        return self._number

    @SheetNumber.setter
    def SheetNumber(self, value):
        if value in self.reject_numbers:
            raise ValueError('Number rejected')
        if any(s is not self and s.SheetNumber.casefold() == value.casefold()
               for s in self.doc.sheets):
            raise ValueError('Duplicate number')
        self._number = value

    def get_Parameter(self, key):
        return self.parameters.get(key)


class Transaction:
    def __init__(self, doc, label):
        self.doc = doc
        self.label = label
        self.status = None
        self.disposed = False
        self.rollbacks = 0
        doc.transactions.append(self)

    def Start(self):
        if self.doc.start_failure:
            raise RuntimeError('Start failed')
        self.snapshot = [(s._number, s.Name, {k: p.value for k, p in s.parameters.items()})
                         for s in self.doc.sheets]
        self.status = STATUS.Started
        return self.status

    def GetFailureHandlingOptions(self):
        return NS(SetForcedModalHandling=lambda value: None)

    def SetFailureHandlingOptions(self, options):
        pass

    def restore(self):
        for sheet, (number, name, params) in zip(self.doc.sheets, self.snapshot):
            sheet._number, sheet.Name = number, name
            for key, value in params.items():
                sheet.parameters[key].value = value

    def Commit(self):
        if self.doc.before_commit:
            self.doc.before_commit()
        if isinstance(self.doc.outcome, Exception):
            raise self.doc.outcome
        self.status = self.doc.outcome
        if self.status == STATUS.RolledBack:
            self.restore()
        return self.status

    def GetStatus(self):
        return self.status

    def RollBack(self):
        self.rollbacks += 1
        self.restore()
        self.status = STATUS.RolledBack

    def Dispose(self):
        self.disposed = True


def load_core():
    db = NS(Transaction=Transaction, TransactionStatus=STATUS,
            BuiltInParameter=NS(**{key: key for key in PARAMS.values()}), ViewSheet=Sheet,
            FilteredElementCollector=lambda doc: NS(OfClass=lambda cls: doc.sheets))
    spec = importlib.util.spec_from_file_location('mana_sheets_test_core', LIB / 'core/mana_sheets.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pyrevit': NS(DB=db)}):
        spec.loader.exec_module(module)
    return module


def load_controller(core):
    # Execute shipped controller methods without importing CLR/WPF dependencies.
    tree = ast.parse((LIB / 'GUI/ManaSheetsDialog.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SheetManagerWindow')
    cls.bases = [ast.Name(id='object', ctx=ast.Load())]
    messages = Mock()
    messages.Show.return_value = 'Yes'
    env = {'_pend': pend, '_sheets': core, 'SHEET_EDIT_FIELDS': FIELDS,
           'MessageBox': messages, 'MessageBoxResult': NS(Yes='Yes'),
           'MessageBoxButton': NS(OK='OK', YesNo='YesNo'),
           'MessageBoxImage': NS(Information='Info', Question='Question')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(LIB / 'GUI/ManaSheetsDialog.py'), 'exec'), env)
    return env['SheetManagerWindow'], messages


class ManaSheetsTests(unittest.TestCase):
    def setUp(self):
        self.core = load_core()
        self.doc = NS(sheets=[], transactions=[], outcome=STATUS.Committed,
                      before_commit=None, start_failure=False)
        self.a, self.b = Sheet(self.doc, 'A'), Sheet(self.doc, 'B')

    def numbers(self):
        return [s.SheetNumber for s in self.doc.sheets]

    def controller(self):
        cls, self.messages = load_controller(self.core)
        ui = cls.__new__(cls)
        ui.doc = self.doc
        ui.all_sheets = []
        ui.change_tracker = ChangeTracker()
        def row(sheet):
            item = NS(element=sheet, id=sheet.Id, is_selected=False,
                      **self.core.sheet_values(sheet))
            item.commit_changes = Mock()
            return item
        ui.revit_service = NS(get_all_sheets=lambda: [row(s) for s in self.doc.sheets])
        ui._update_sheets_summary = Mock()
        ui._apply_sheets_filters = Mock()
        ui._flush_edits = Mock(return_value=True)
        ui.begin_progress = Mock()
        ui.end_progress = Mock()
        ui.step_progress = Mock(return_value=True)
        ui.is_cancelled = False
        ui._load_sheets_data()
        ui.renumber_items = [NS(IsSelected=True, sheet_model=r, preview_number='STALE')
                             for r in ui.all_sheets]
        ui.renum_start_box, ui.renum_step_box = NS(Text='1'), NS(Text='1')
        ui.renum_prefix_box, ui.renum_suffix_box = NS(Text=''), NS(Text='')
        ui.renum_grid = NS(Items=NS(Refresh=Mock()))
        return ui

    def stage(self, row, **changes):
        for field, value in changes.items():
            setattr(row, field, value)
            pend.stage(row, field, value)

    def test_empty_and_dash_are_real_text_only_staged_fields_written(self):
        for value in ('', '-'):
            self.core.update_sheet(self.doc, self.a, {'designed_by': value})
            values = self.core.sheet_values(self.a)
            self.assertEqual(values['designed_by'], value)
            self.assertEqual(values['checked_by'], 'Original')
            self.assertEqual(self.a.Name, 'Sheet A')

    def test_row_failure_rolls_back_earlier_fields(self):
        for mode in ('readonly', 'missing', 'rejected'):
            with self.subTest(mode=mode):
                self.setUp()
                p = self.a.parameters[PARAMS['checked_by']]
                if mode == 'readonly':
                    p.IsReadOnly = True
                elif mode == 'missing':
                    del self.a.parameters[PARAMS['checked_by']]
                else:
                    p.reject = True
                with self.assertRaises(ValueError):
                    self.core.update_sheet(self.doc, self.a,
                                           {'sheet_name': 'Changed', 'checked_by': ''})
                self.assertEqual(self.a.Name, 'Sheet A')
                self.assertEqual(self.doc.transactions[-1].rollbacks, 1)
                self.assertTrue(self.doc.transactions[-1].disposed)

    def test_empty_required_fields_and_unknown_fields_rollback(self):
        for changes in ({'sheet_name': ''}, {'sheet_number': ' '}, {'unknown': 'x'}):
            with self.assertRaises(ValueError):
                self.core.update_sheet(self.doc, self.a, changes)
        self.assertEqual(self.numbers(), ['A', 'B'])

    def test_swaps_and_cycles(self):
        self.assertEqual(self.core.renumber_sheets(self.doc, [(self.a, 'B'), (self.b, 'A')]), 2)
        c = Sheet(self.doc, 'C')
        self.core.renumber_sheets(self.doc, [(self.a, 'A'), (self.b, 'C'), (c, 'B')])
        self.assertEqual(self.numbers(), ['A', 'C', 'B'])
        self.assertTrue(all(t.label.startswith('T3Lab: ') for t in self.doc.transactions))

    def test_invalid_plans_never_start_transaction(self):
        plans = [[(self.a, '')], [(self.a, None)], [(self.a, 'b')],
                 [(self.a, 'X'), (self.b, 'x')], [(self.a, 'X'), (self.a, 'Y')]]
        for pairs in plans:
            with self.assertRaises(ValueError):
                self.core.renumber_sheets(self.doc, pairs)
        self.assertEqual(self.doc.transactions, [])

    def test_stop_at_every_checkpoint_restores_entire_batch(self):
        for stop in range(5):
            with self.subTest(stop=stop):
                self.setUp()
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    self.core.renumber_sheets(self.doc, [(self.a, 'B'), (self.b, 'A')],
                                             lambda index, total: index != stop)
                self.assertEqual(self.numbers(), ['A', 'B'])
                self.assertTrue(self.doc.transactions[-1].disposed)

    def test_second_pass_failure_restores_all_numbers(self):
        self.b.reject_numbers.add('A')
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.core.renumber_sheets(self.doc, [(self.a, 'B'), (self.b, 'A')])
        self.assertEqual(self.numbers(), ['A', 'B'])

    def test_commit_failure_never_reports_success(self):
        for outcome in (STATUS.RolledBack, RuntimeError('Commit failed')):
            for operation in ('update', 'renumber'):
                with self.subTest(outcome=outcome, operation=operation):
                    self.setUp()
                    self.doc.outcome = outcome
                    with self.assertRaises(RuntimeError):
                        if operation == 'update':
                            self.core.update_sheet(self.doc, self.a, {'sheet_name': 'Changed'})
                        else:
                            self.core.renumber_sheets(self.doc, [(self.a, 'B'), (self.b, 'A')])
                    self.assertEqual(self.numbers(), ['A', 'B'])
                    self.assertEqual(self.a.Name, 'Sheet A')
                    self.assertTrue(self.doc.transactions[-1].disposed)

    def test_start_failure_disposes_without_rollback(self):
        self.doc.start_failure = True
        with self.assertRaisesRegex(RuntimeError, 'Start failed'):
            self.core.update_sheet(self.doc, self.a, {'sheet_name': 'Changed'})
        self.assertEqual(self.doc.transactions[-1].rollbacks, 0)
        self.assertTrue(self.doc.transactions[-1].disposed)

    def test_apply_preserves_failed_edits_selection_and_clears_only_committed(self):
        ui = self.controller()
        good, bad = ui.all_sheets
        bad.is_selected = True
        self.stage(good, designed_by='')
        self.stage(bad, sheet_name='Changed', checked_by='')
        self.b.parameters[PARAMS['checked_by']].reject = True
        self.doc.before_commit = lambda: self.assertTrue(pend.has_pending(good))
        ui._on_sheets_apply(None, None)
        self.assertFalse(pend.has_pending(ui.all_sheets[0]))
        self.assertEqual(self.a.parameters[PARAMS['designed_by']].value, '')
        self.assertEqual(self.b.Name, 'Sheet B')
        self.assertEqual(pend.pending_of(ui.all_sheets[1]), {'sheet_name': 'Changed', 'checked_by': ''})
        self.assertTrue(ui.all_sheets[1].is_selected)
        self.assertTrue(ui.all_sheets[1].dirty_checked_by)
        self.assertIn(ui.all_sheets[1], ui.change_tracker.modified_items)
        ui.end_progress.assert_called_once()

    def test_apply_commit_rejection_retains_pending_and_original_baseline(self):
        ui = self.controller()
        self.stage(ui.all_sheets[0], sheet_name='Changed')
        self.doc.outcome = STATUS.RolledBack
        ui._on_sheets_apply(None, None)
        row = ui.all_sheets[0]
        self.assertEqual(pend.pending_of(row), {'sheet_name': 'Changed'})
        self.assertEqual(row._mana_original['sheet_name'], 'Sheet A')

    def test_apply_stop_after_dispatch_skips_current_and_remaining_rows(self):
        ui = self.controller()
        for row in ui.all_sheets:
            self.stage(row, designed_by='')
        ui.step_progress.side_effect = [True, False]
        ui._on_sheets_apply(None, None)
        self.assertFalse(pend.has_pending(ui.all_sheets[0]))
        self.assertTrue(pend.has_pending(ui.all_sheets[1]))
        self.assertEqual(len(self.doc.transactions), 1)

    def test_invalid_preview_blocks_run_without_confirmation_or_writes(self):
        for start, step in [('bad', '1'), ('1', '0')]:
            ui = self.controller()
            ui.renum_start_box.Text, ui.renum_step_box.Text = start, step
            ui._on_renum_run(None, None)
            self.assertEqual(self.doc.transactions, [])
            ui.begin_progress.assert_not_called()
            self.assertEqual(self.messages.Show.call_count, 1)
            self.assertEqual([i.preview_number for i in ui.renumber_items], ['STALE', 'STALE'])

    def test_valid_preview_normalizes_and_run_revalidates_inputs(self):
        ui = self.controller()
        ui.renum_prefix_box.Text = ' '
        self.assertTrue(ui._on_renum_preview(None, None))
        self.assertEqual([i.preview_number for i in ui.renumber_items], ['1', '2'])
        ui.renum_start_box.Text = 'bad'
        ui._on_renum_run(None, None)
        self.assertEqual(self.doc.transactions, [])

    def test_renumber_ui_stop_preserves_preview_and_pending(self):
        ui = self.controller()
        self.stage(ui.all_sheets[0], designed_by='')
        ui.step_progress.side_effect = [True, True, True, False]
        ui._on_renum_run(None, None)
        self.assertEqual(self.numbers(), ['A', 'B'])
        self.assertEqual(pend.pending_of(ui.all_sheets[0]), {'designed_by': ''})
        self.assertEqual([i.preview_number for i in ui.renumber_items], ['1', '2'])
        ui.end_progress.assert_called_once()

    def test_successful_renumber_reload_preserves_independent_pending_edits(self):
        ui = self.controller()
        self.stage(ui.all_sheets[0], sheet_number='Future', designed_by='')
        ui._load_renumber_preview_data = Mock()
        ui._on_renum_run(None, None)
        self.assertEqual(self.numbers(), ['1', '2'])
        self.assertEqual(ui.all_sheets[0]._mana_original['sheet_number'], '1')
        self.assertEqual(pend.pending_of(ui.all_sheets[0]), {'sheet_number': 'Future', 'designed_by': ''})
        ui.end_progress.assert_called_once()

    def test_validation_flush_failure_blocks_apply_and_renumber(self):
        ui = self.controller()
        self.stage(ui.all_sheets[0], sheet_name='Changed')
        ui._flush_edits.return_value = False
        ui._on_sheets_apply(None, None)
        ui._on_renum_run(None, None)
        self.assertEqual(self.doc.transactions, [])
        self.messages.Show.assert_not_called()

    def test_renumber_ui_can_swap_existing_numbers(self):
        self.a._number, self.b._number = '1', '2'
        ui = self.controller()
        ui.renum_start_box.Text, ui.renum_step_box.Text = '2', '-1'
        ui._load_renumber_preview_data = Mock()
        ui._on_renum_run(None, None)
        self.assertEqual(self.numbers(), ['2', '1'])

    def test_child_dialog_constructors_receive_service_doc_and_elements(self):
        cases = (
            ('place_views', 'PlaceViewsDialog', 'place_views_service', '_on_sheets_place_views'),
            ('custom_parameters', 'CustomParametersDialog', 'params_service', '_on_sheets_custom_params'),
        )
        for module, class_name, service_name, handler in cases:
            for checked in (True, False):
                with self.subTest(dialog=module, checked=checked):
                    ui = self.controller()
                    ui.filtered_sheets = list(ui.all_sheets)
                    ui.all_sheets[0].is_selected = checked
                    ui.sheets_grid = NS(SelectedItems=[ui.all_sheets[1]])
                    service = object()
                    setattr(ui, service_name, service)
                    self.stage(ui.all_sheets[0], designed_by='')
                    constructor = Mock()
                    name = 'Services.SheetManager.' + module + '_dialog'
                    with patch.dict(sys.modules, {name: NS(**{class_name: constructor})}):
                        getattr(ui, handler)(None, None)
                    constructor.assert_called_once_with(
                        service, self.doc, [self.a if checked else self.b])
                    constructor.return_value.ShowDialog.assert_called_once()
                    self.assertEqual(pend.pending_of(ui.all_sheets[0]), {'designed_by': ''})

    def test_child_dialogs_show_no_selection_without_opening(self):
        for handler in ('_on_sheets_place_views', '_on_sheets_custom_params'):
            ui = self.controller()
            ui.filtered_sheets = ui.all_sheets
            ui.sheets_grid = NS(SelectedItems=[])
            getattr(ui, handler)(None, None)
            self.messages.Show.assert_called_once()
            self.assertEqual(self.messages.Show.call_args.args[1], 'No Selection')


if __name__ == '__main__':
    unittest.main()
