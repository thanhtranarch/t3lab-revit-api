"""Exercise shipped ribbon dialog identity/persistence behavior with tab doubles."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest


GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib' / 'GUI'


class Event:
    def __iadd__(self, handler):
        return self


class Window:
    def __init__(self, path):
        self.Grid = SimpleNamespace(CommitEdit=lambda: True)
        self.HeaderSub = SimpleNamespace()
        for name in ('BtnShort', 'BtnFull', 'BtnSave', 'BtnReset', 'BtnClose'):
            setattr(self, name, SimpleNamespace(Click=Event()))


class Rows(list):
    def Add(self, full, short):
        self.append({'CurrentName': full, 'ShortName': short})


class Table:
    def __init__(self, name):
        self.Columns = SimpleNamespace(Add=lambda *_: None)
        self.Rows = Rows()
        self.DefaultView = self.Rows


class Tab:
    def __init__(self, tab_id, title, fail=False):
        self.Id = tab_id
        self._title = title
        self.fail = fail

    @property
    def Title(self):
        return self._title

    @Title.setter
    def Title(self, value):
        if self.fail:
            raise RuntimeError('Title is read-only')
        self._title = value


class Storage:
    def __init__(self, mapping=None, originals=None):
        self.mapping = mapping or {'Architecture': 'Arch', 'Structure': 'Struc'}
        self.originals = originals or {}
        self.states = []
        self.fail_map = self.fail_originals = self.fail_state = False

    def save_map(self, value):
        if self.fail_map:
            return False
        self.mapping = copy.deepcopy(value)
        return True

    def save_originals(self, value):
        if self.fail_originals:
            return False
        self.originals = copy.deepcopy(value)
        return True

    def save_state(self, value):
        if self.fail_state:
            return False
        self.states.append(value)
        return True


path = GUI / 'RibbonNamesDialog.py'
tree = ast.parse(path.read_text(encoding='utf-8-sig'))
tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)]
scope = {'T3WPFWindow': Window, '_XAML': '', 'DataTable': Table,
         'System_String': str, 'DBNull': SimpleNamespace(Value=object()), '_unicode': str}
exec(compile(tree, str(path), 'exec'), scope)
RibbonWindow = scope['RibbonNameWindow']


class RibbonIdentityTests(unittest.TestCase):
    def dialog(self, tabs, store=None):
        store = store or Storage()
        dialog = RibbonWindow(tabs, store.mapping, store.originals, {},
                              store.save_map, store.save_state, store.save_originals)
        return dialog, store

    def edit(self, dialog, full, alias):
        next(row for row in dialog.table.Rows if row['CurrentName'] == full)['ShortName'] = alias

    def test_repeated_apply_and_restore_keep_original_identity(self):
        tab = Tab('architecture-id', 'Arch')
        dialog, store = self.dialog([tab])
        self.edit(dialog, 'Architecture', 'A')
        dialog._on_apply_short(None, None)
        self.assertEqual(tab.Title, 'A')
        self.edit(dialog, 'Architecture', 'ARC')
        dialog._on_apply_short(None, None)
        self.assertEqual(tab.Title, 'ARC')
        dialog._on_restore_full(None, None)
        self.assertEqual(tab.Title, 'Architecture')
        self.assertEqual(store.states, ['short', 'short', 'full'])
        self.assertEqual(store.originals['__tab_ids__']['architecture-id'], 'Architecture')

    def test_save_map_then_reopen_resolves_old_displayed_alias_by_id(self):
        tab = Tab('architecture-id', 'Arch')
        dialog, store = self.dialog([tab])
        self.edit(dialog, 'Architecture', 'A')
        dialog._on_save(None, None)
        self.assertEqual(tab.Title, 'Arch')
        self.assertEqual(store.states[-1], 'mixed')
        reopened, _ = self.dialog([Tab('architecture-id', 'Arch')], store)
        self.assertEqual(reopened.table.Rows[0]['CurrentName'], 'Architecture')
        reopened._on_restore_full(None, None)
        self.assertEqual(reopened.live_tabs[0].Title, 'Architecture')

    def test_duplicate_aliases_without_saved_id_are_not_guessed(self):
        store = Storage({'Architecture': 'Shared', 'Structure': 'Shared'})
        tab = Tab('unknown-id', 'Shared')
        dialog, _ = self.dialog([tab], store)
        self.assertEqual(len(dialog.table.Rows), 0)
        self.assertIn('Ambiguous title', dialog.message)
        dialog._on_restore_full(None, None)
        self.assertEqual(tab.Title, 'Shared')
        self.assertEqual(store.states[-1], 'mixed')

    def test_duplicate_aliases_with_saved_ids_restore_distinct_full_names(self):
        store = Storage({'Architecture': 'Shared', 'Structure': 'Shared'},
                        {'__tab_ids__': {'a': 'Architecture', 's': 'Structure'}})
        tabs = [Tab('a', 'Shared'), Tab('s', 'Shared')]
        dialog, _ = self.dialog(tabs, store)
        self.assertEqual(len(dialog.table.Rows), 2)
        dialog._on_restore_full(None, None)
        self.assertEqual([tab.Title for tab in tabs], ['Architecture', 'Structure'])

    def test_new_duplicate_alias_is_rejected_before_title_changes(self):
        tabs = [Tab('a', 'Architecture'), Tab('s', 'Structure')]
        dialog, store = self.dialog(tabs)
        self.edit(dialog, 'Architecture', 'Struc')
        dialog._on_apply_short(None, None)
        self.assertEqual([tab.Title for tab in tabs], ['Architecture', 'Structure'])
        self.assertIn('Use distinct names', dialog.message)
        self.assertEqual(store.states, [])

    def test_partial_write_does_not_persist_all_short_state(self):
        tabs = [Tab('a', 'Architecture'), Tab('s', 'Structure', fail=True)]
        dialog, store = self.dialog(tabs)
        dialog._on_apply_short(None, None)
        self.assertEqual([tab.Title for tab in tabs], ['Arch', 'Structure'])
        self.assertEqual(store.states, ['mixed'])
        self.assertIn('1 failed or unresolved', dialog.message)
        self.assertIn('read-only', dialog.message)

    def test_map_failure_does_not_claim_saved_or_all_short_state(self):
        dialog, store = self.dialog([Tab('a', 'Architecture')])
        store.fail_map = True
        self.edit(dialog, 'Architecture', 'A')
        dialog._on_apply_short(None, None)
        self.assertIn('Short-name map was not saved', dialog.message)
        self.assertEqual(store.states, ['mixed'])
        self.assertEqual(dialog.short_map['Architecture'], 'Arch')

    def test_state_callback_failure_is_reported_after_live_success(self):
        dialog, store = self.dialog([Tab('a', 'Architecture')])
        store.fail_state = True
        dialog._on_apply_short(None, None)
        self.assertEqual(dialog.live_tabs[0].Title, 'Arch')
        self.assertIn('Ribbon state was not saved', dialog.message)

    def test_original_identity_save_failure_prevents_title_mutation(self):
        tab = Tab('a', 'Architecture')
        dialog, store = self.dialog([tab])
        store.fail_originals = True
        dialog._on_apply_short(None, None)
        self.assertEqual(tab.Title, 'Architecture')
        self.assertEqual(store.states, [])
        self.assertIn('Original tab identities was not saved', dialog.message)

    def test_missing_id_warns_but_keeps_identity_for_current_session(self):
        tab = Tab('', 'Arch')
        dialog, _ = self.dialog([tab])
        self.assertIn('only in this session', dialog.message)
        self.edit(dialog, 'Architecture', 'A')
        dialog._on_apply_short(None, None)
        dialog._on_restore_full(None, None)
        self.assertEqual(tab.Title, 'Architecture')

    def test_invalid_grid_edit_does_not_apply_or_save(self):
        tab = Tab('a', 'Architecture')
        dialog, store = self.dialog([tab])
        dialog.Grid.CommitEdit = lambda: False
        dialog._on_apply_short(None, None)
        self.assertEqual(tab.Title, 'Architecture')
        self.assertEqual(store.states, [])

    def test_save_callback_exception_is_visible(self):
        dialog, _ = self.dialog([Tab('a', 'Architecture')])

        def fail_save(_):
            raise OSError('Disk unavailable')

        dialog.on_save_callback = fail_save
        dialog._on_save(None, None)
        self.assertIn('Disk unavailable', dialog.message)


if __name__ == '__main__':
    unittest.main()
