"""ManaViews behavior against a snapshot-based fake DB; no Revit/WPF imports.

Run: python dev/test_mana_views.py -v
Known production defects are expected failures, with desired behavior asserted.
AST loading executes shipped function/class bodies, but does not exercise CLR
binding, XAML, native failure processing, or actual Revit constraints.
"""
import ast
import copy
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile


LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib'
STATUS = NS(Uninitialized='Uninitialized', Started='Started',
            Committed='Committed', RolledBack='RolledBack', Pending='Pending')
FIELDS = ('name', 'view_template', 'scale', 'detail_level', 'title_on_sheet')


def definitions(relative, scope, names=None):
    path = LIB / relative
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    tree.body = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                 and (names is None or node.name in names)]
    exec(compile(tree, str(path), 'exec'), scope)
    return NS(**scope)


class FakeView:
    def __init__(self, doc, ident, name, template=False):
        self.doc, self.Id = doc, ident
        self.Name, self.IsTemplate = name, template
        self.Scale, self.DetailLevel, self.ViewTemplateId = 100, 'Medium', -1
        self.title = ''
        self.readonly = self.reject_title = self.reject_duplicate_name = False
        self.can_duplicate = self.valid_template = True

    def __setattr__(self, key, value):
        if key == 'Name' and getattr(self, 'reject_duplicate_name', False):
            raise ValueError('duplicate naming rejected')
        object.__setattr__(self, key, value)

    def get_Parameter(self, key):
        def set_title(value):
            self.title = value
            return not self.reject_title
        return NS(IsReadOnly=self.readonly, Set=set_title)

    def IsValidViewTemplate(self, ident):
        return self.valid_template

    def CanViewBeDuplicated(self, option):
        return self.can_duplicate

    def Duplicate(self, option):
        new = self.doc.add(self.Name + ' API copy', self.IsTemplate)
        new.reject_duplicate_name = self.doc.reject_duplicate_name
        return new.Id


class FakeDoc:
    def __init__(self):
        self.views = {}
        self.reject_duplicate_name = False

    def add(self, name, template=False):
        ident = max(self.views, default=0) + 1
        view = FakeView(self, ident, name, template)
        self.views[ident] = view
        return view

    def GetElement(self, ident):
        return self.views[ident]

    def Delete(self, ident):
        del self.views[ident]
        return [ident]


class FakeTransaction:
    def __init__(self, doc, label, outcome, start_status=STATUS.Started):
        self.doc, self.label, self.outcome = doc, label, outcome
        self.start_status = start_status
        self.status = STATUS.Uninitialized
        self.rollbacks = 0
        self.disposed = False
        self.options = Mock()

    def Start(self):
        self.before = {key: (view, copy.deepcopy({k: v for k, v in vars(view).items()
                                                if k != 'doc'}))
                       for key, view in self.doc.views.items()}
        self.status = self.start_status
        return self.status

    def restore(self):
        self.doc.views = {key: view for key, (view, _) in self.before.items()}
        for view, state in self.before.values():
            vars(view).clear()
            vars(view).update(copy.deepcopy(state), doc=self.doc)

    def Commit(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.status = self.outcome
        if self.status == STATUS.RolledBack:
            self.restore()
        return self.status

    def RollBack(self):
        assert self.status == STATUS.Started
        self.restore()
        self.rollbacks += 1
        self.status = STATUS.RolledBack

    def GetStatus(self):
        return self.status

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options

    def Dispose(self):
        self.disposed = True


class ManaViewsTests(unittest.TestCase):
    def setUp(self):
        self.doc = FakeDoc()
        self.view = self.doc.add('Plan')
        self.transactions = []
        self.outcomes = []
        self.start_status = STATUS.Started

        def factory(doc, label):
            tx = FakeTransaction(doc, label, self.outcomes.pop(0) if self.outcomes
                                 else STATUS.Committed, self.start_status)
            self.transactions.append(tx)
            return tx

        db = NS(Transaction=factory, TransactionStatus=STATUS,
                FilteredElementCollector=lambda doc: NS(OfClass=lambda cls: list(doc.views.values())),
                View=FakeView, ElementId=NS(InvalidElementId=-1),
                BuiltInParameter=NS(VIEW_DESCRIPTION=1),
                ViewDetailLevel=NS(Coarse='Coarse', Medium='Medium', Fine='Fine'),
                ViewDuplicateOption=NS(Duplicate='Duplicate'))
        self.core = definitions('core/mana_views.py', {'DB': db})
        self.pend = definitions('GUI/GridPendingEdits.py',
                                {'PENDING_ATTR': '_t3_pending', 'DIRTY_PREFIX': 'dirty_'})
        self.messages = NS(Show=Mock(return_value='Yes'))
        self.scope = dict(T3WPFWindow=object, _pend=self.pend,
                          VIEW_EDIT_FIELDS=FIELDS, TMPL_EDIT_FIELDS=('name',),
                          MessageBox=self.messages, MessageBoxResult=NS(Yes='Yes', No='No'),
                          MessageBoxButton=NS(YesNo=1, YesNoCancel=2, OK=3),
                          MessageBoxImage=NS(Question=1, Warning=2),
                          write_field=self.core.write_field, rename_template=self.core.rename_template,
                          _eid_int=lambda value: value)
        module = definitions('GUI/ManaViewsDialog.py', self.scope, {'ViewManagerWindow'})
        self.window = object.__new__(module.ViewManagerWindow)
        w = self.window
        w.doc, w.all_templates_data = self.doc, []
        w.all_views = [self.row(self.view)]
        w.filtered_views = w.all_views
        w.is_cancelled = False
        for name in ('_load_templates_data', '_apply_views_filters', '_apply_tmpl_filters',
                     'begin_progress', 'end_progress', '_set_status'):
            setattr(w, name, Mock())
        w._flush_edits = Mock(return_value=True)
        w.step_progress = Mock(return_value=True)
        w.views_grid = Mock()
        w._load_views_data = self.reload_views

    def row(self, view):
        row = NS(id=view.Id, element=view, is_selected=False, name=view.Name,
                 view_template='None', scale=str(view.Scale), detail_level=view.DetailLevel,
                 title_on_sheet=view.title)
        self.pend.init_pending(row, FIELDS)
        row._mana_original = {field: getattr(row, field) for field in FIELDS}
        return row

    def reload_views(self):
        w = self.window
        state = w._row_state(w.all_views)
        w.all_views = [self.row(view) for view in self.doc.views.values() if not view.IsTemplate]
        w._restore_state(w.all_views, state)

    def stage(self, row, field, value):
        setattr(row, field, value)
        self.pend.stage(row, field, value)

    def test_all_cell_writes_commit_and_publish(self):
        template = self.doc.add('Template', True)
        row = self.window.all_views[0]
        for field, value, attr, expected in (
                ('name', 'New plan', 'Name', 'New plan'),
                ('scale', ' 50 ', 'Scale', 50),
                ('detail_level', 'Fine', 'DetailLevel', 'Fine'),
                ('title_on_sheet', 'Kitchen', 'title', 'Kitchen'),
                ('view_template', 'Template', 'ViewTemplateId', template.Id),
                ('view_template', 'None', 'ViewTemplateId', -1)):
            with self.subTest(field=field, value=value):
                self.core.write_field(self.doc, row, field, value)
                self.assertEqual(getattr(self.view, attr), expected)
                self.assertEqual(getattr(row, field), value)
                tx = self.transactions[-1]
                self.assertEqual(tx.status, STATUS.Committed)
                self.assertTrue(tx.disposed)
                self.assertTrue(tx.label.startswith('T3Lab: '))
                tx.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_bad_values_rollback_without_publishing(self):
        row = self.window.all_views[0]
        for field, value in [('name', ' '), ('scale', '0'), ('scale', '1.5'),
                             ('detail_level', 'Invalid'), ('view_template', 'Missing'),
                             ('unsupported', 'x')]:
            with self.subTest(field=field):
                before = vars(row).copy()
                with self.assertRaises(ValueError):
                    self.core.write_field(self.doc, row, field, value)
                self.assertEqual(vars(row), before)
                self.assertEqual(self.transactions[-1].rollbacks, 1)
                self.assertTrue(self.transactions[-1].disposed)

    def test_rejected_title_restores_model_value(self):
        self.view.reject_title = True
        row = self.window.all_views[0]
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.core.write_field(self.doc, row, 'title_on_sheet', 'Rejected')
        self.assertEqual(self.view.title, '')
        self.assertEqual(row.title_on_sheet, '')

    def test_incompatible_template_preserves_assignment(self):
        self.doc.add('Template', True)
        self.view.valid_template = False
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.core.write_field(self.doc, self.window.all_views[0], 'view_template', 'Template')
        self.assertEqual(self.view.ViewTemplateId, -1)

    def test_commit_failure_restores_model_and_keeps_row(self):
        for outcome in [STATUS.RolledBack, RuntimeError('commit failed')]:
            with self.subTest(outcome=outcome):
                self.outcomes = [outcome]
                with self.assertRaises(RuntimeError):
                    self.core.write_field(self.doc, self.window.all_views[0], 'name', 'Rejected')
                self.assertEqual(self.view.Name, 'Plan')
                self.assertEqual(self.window.all_views[0].name, 'Plan')
                self.assertTrue(self.transactions[-1].disposed)

    def test_failed_duplicate_leaves_no_orphan(self):
        self.doc.reject_duplicate_name = True
        self.assertEqual(self.core.duplicate_views(self.doc, [self.view]), (0, 1))
        self.assertEqual(list(self.doc.views), [self.view.Id])
        self.assertEqual(self.transactions[0].rollbacks, 1)

    def test_duplicate_names_are_unique_case_insensitively(self):
        self.doc.add('PLAN - COPY')
        self.assertEqual(self.core.duplicate_views(self.doc, [self.view, self.view]), (2, 0))
        self.assertEqual([v.Name for v in self.doc.views.values()][-2:],
                         ['Plan - Copy 2', 'Plan - Copy 3'])

    def test_delete_rejects_used_template_and_continues(self):
        template = self.doc.add('Template', True)
        other = self.doc.add('Other')
        self.view.ViewTemplateId = template.Id
        self.assertEqual(self.core.delete_views(self.doc, [template, other]), (1, 1))
        self.assertIn(template.Id, self.doc.views)
        self.assertNotIn(other.Id, self.doc.views)
        self.assertEqual(len(self.transactions), 1)

    def test_rejected_delete_restores_element(self):
        self.outcomes = [STATUS.RolledBack]
        self.assertEqual(self.core.delete_views(self.doc, [self.view]), (0, 1))
        self.assertIs(self.doc.views[self.view.Id], self.view)

    def test_template_rename_rollback_restores_original(self):
        template = self.doc.add('Template', True)
        self.outcomes = [RuntimeError('commit failed')]
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            self.core.rename_template(self.doc, template, 'Changed')
        self.assertEqual(template.Name, 'Template')
        self.assertEqual(self.transactions[-1].rollbacks, 1)
        self.core.rename_template(self.doc, template, 'Changed')
        self.assertEqual(template.Name, 'Changed')

    def test_inline_staging_and_reverting_never_write_model(self):
        row = self.window.all_views[0]
        editor = NS(Text='Staged')
        args = NS(EditAction='Commit', Row=NS(Item=row),
                  Column=NS(SortMemberPath='name'), EditingElement=editor)
        controls = NS(DataGridEditAction=NS(Cancel='Cancel'))
        self.window._refresh_grid_later = Mock()
        changed = Mock()
        with patch.dict(sys.modules, {'System.Windows.Controls': controls}):
            self.window._stage_cell_edit(args, FIELDS, self.window.views_grid, changed)
            self.assertEqual(self.pend.pending_of(row), {'name': 'Staged'})
            editor.Text = 'Plan'
            self.window._stage_cell_edit(args, FIELDS, self.window.views_grid, changed)
            self.assertFalse(self.pend.pending_of(row))
            editor.Text = ' '
            self.window._stage_cell_edit(args, FIELDS, self.window.views_grid, changed)
            self.assertEqual(editor.Text, 'Plan')
        self.assertEqual(self.view.Name, 'Plan')
        self.assertFalse(self.transactions)

    def test_refresh_preserves_selection_and_pending_without_writes(self):
        row = self.window.all_views[0]
        row.is_selected = True
        self.stage(row, 'name', 'Staged')
        self.reload_views()
        fresh = self.window.all_views[0]
        self.assertTrue(fresh.is_selected)
        self.assertEqual(fresh.name, 'Staged')
        self.assertEqual(fresh._mana_original['name'], 'Plan')
        self.assertTrue(fresh.dirty_name)
        self.assertEqual(self.view.Name, 'Plan')
        self.assertFalse(self.transactions)

    def test_refresh_clears_pending_already_matching_model(self):
        self.stage(self.window.all_views[0], 'name', 'External rename')
        self.view.Name = 'External rename'
        self.reload_views()
        self.assertFalse(self.pend.pending_of(self.window.all_views[0]))

    def test_apply_keeps_failed_cells_staged_and_commits_good_cells(self):
        row = self.window.all_views[0]
        self.stage(row, 'name', 'Renamed')
        self.stage(row, 'scale', '0')
        self.window._on_views_apply(Mock(), None)
        fresh = self.window.all_views[0]
        self.assertEqual(self.view.Name, 'Renamed')
        self.assertEqual(self.view.Scale, 100)
        self.assertEqual(self.pend.pending_of(fresh), {'scale': '0'})
        self.assertEqual(fresh._mana_original['scale'], '100')
        self.window._set_status.assert_called_with('Applied 1 cell, 1 refused.')
        self.window.end_progress.assert_called_once()

    def test_stop_preserves_unprocessed_pending_rows(self):
        second = self.doc.add('Second')
        self.window.all_views.append(self.row(second))
        for row in self.window.all_views:
            self.stage(row, 'name', row.name + ' new')
        self.window.step_progress.side_effect = [True, False]
        self.window._on_views_apply(Mock(), None)
        self.assertEqual(self.view.Name, 'Plan new')
        self.assertEqual(second.Name, 'Second')
        self.assertEqual(self.pend.pending_count(self.window.all_views), 1)

    def test_failed_edit_flush_prevents_apply(self):
        self.stage(self.window.all_views[0], 'name', 'Staged')
        self.window._flush_edits.return_value = False
        self.window._on_views_apply(Mock(), None)
        self.assertFalse(self.transactions)
        self.assertEqual(self.pend.pending_count(self.window.all_views), 1)

    @unittest.expectedFailure
    def test_pending_commit_aborts_batch_and_keeps_transaction_alive(self):
        """BUG: _write disposes Pending and Apply starts the next cell write."""
        self.outcomes = [STATUS.Pending]
        row = self.window.all_views[0]
        self.stage(row, 'name', 'Pending rename')
        self.stage(row, 'scale', '50')
        self.window._on_views_apply(Mock(), None)
        self.assertEqual((len(self.transactions), self.transactions[0].disposed), (1, False))

    @unittest.expectedFailure
    def test_unsuccessful_start_does_not_execute_action(self):
        """BUG: _write ignores Start status and executes the action anyway."""
        self.start_status = STATUS.RolledBack
        action = Mock()
        try:
            self.core._write(self.doc, 'Test', action)
        except RuntimeError:
            pass
        action.assert_not_called()

    def excel_scope(self, path, choice):
        codec = definitions('core/advanced_view_manager.py',
                            dict(os=os, re=re, zipfile=zipfile), {'write_xlsx', 'read_xlsx'})
        self.scope.update(write_xlsx=codec.write_xlsx, read_xlsx=codec.read_xlsx)
        self.messages.Show.return_value = choice
        dialog = NS(FileName=str(path), ShowDialog=lambda: 'OK')
        forms = NS(SaveFileDialog=lambda: dialog, OpenFileDialog=lambda: dialog,
                   DialogResult=NS(OK='OK'))
        return codec, patch.dict(sys.modules, {'System.Windows.Forms': forms})

    @unittest.expectedFailure
    def test_excel_export_uses_actual_writer_contract(self):
        """BUG: handler passes 2 arguments to writer requiring 3."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'T3Lab_ViewManager_Export.xlsx'
            codec, modules = self.excel_scope(path, 'Yes')
            previous = os.getcwd()
            try:
                os.chdir(directory)  # handler assigns a relative FileName
                with modules:
                    self.window._on_views_excel(None, None)
            finally:
                os.chdir(previous)
            self.assertTrue(path.exists(), str(self.messages.Show.call_args))

    @unittest.expectedFailure
    def test_excel_import_stages_real_workbook_without_model_write(self):
        """BUG: reader returns (headers, rows); handler calls updates.items()."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'views.xlsx'
            codec, modules = self.excel_scope(path, 'No')
            codec.write_xlsx(str(path), ['Element ID', 'View Name'], [[self.view.Id, 'Imported']])
            with modules:
                self.window._on_views_excel(None, None)
            self.assertEqual(self.pend.pending_of(self.window.all_views[0]), {'name': 'Imported'},
                             str(self.messages.Show.call_args))
            self.assertEqual(self.view.Name, 'Plan')
            self.assertFalse(self.transactions)


if __name__ == '__main__':
    unittest.main()
