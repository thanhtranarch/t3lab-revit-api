# -*- coding: utf-8 -*-
"""ErrorGuard: no Python error may reach Revit's "Command Failure for External
Command" dialog, and every one is logged with its traceback.

Runs the shipped lib/GUI/ErrorGuard.py (pure Python) plus the WPF_Base wiring
and the bootstrap's changed-only reload, without Revit or WPF.

Run: python dev/test_error_guard.py
"""
import ast
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
GUARD = os.path.join(LIB, 'GUI', 'ErrorGuard.py')
WPF_BASE = os.path.join(LIB, 'GUI', 'WPF_Base.py')
BOOTSTRAP = os.path.join(LIB, '_cpython_bootstrap.py')


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Window(object):
    """Stand-in for a T3WPFWindow: a Title and a status line."""

    def __init__(self, title='BatchOut'):
        self.Title = title
        self.status_text = SimpleNamespace(Text='')
        self.Dispatcher = mock.Mock()


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.guard = _load(GUARD, '_t3_error_guard_under_test')
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {'APPDATA': self.tmp})
        env.start()
        self.addCleanup(env.stop)
        # The real dialog needs WPF; record what would have been shown.
        self.shown = []
        self.guard._show_error = lambda message, title, details, owner=None: \
            self.shown.append((message, title, details))

    def log_text(self):
        with open(self.guard.log_path(), encoding='utf-8') as f:
            return f.read()


class RunTool(GuardTestCase):
    def test_returns_the_result_when_nothing_fails(self):
        self.assertEqual(self.guard.run_tool('BatchOut', lambda: 42), 42)
        self.assertEqual(self.shown, [])

    def test_error_is_shown_and_logged_instead_of_raised(self):
        def main():
            raise AttributeError("'ExportManagerWindow' has no attribute 'x'")
        self.assertIsNone(self.guard.run_tool('BatchOut', main))
        message, title, details = self.shown[0]
        self.assertIn('BatchOut', message)
        self.assertIn("AttributeError: 'ExportManagerWindow' has no attribute 'x'", message)
        self.assertIn('pyRevit > Reload', details)     # what to do next
        self.assertIn('Traceback', details)             # where it failed
        self.assertIn('AttributeError', self.log_text())

    def test_import_error_inside_main_is_caught(self):
        def main():
            import t3lab_module_that_does_not_exist  # noqa: F401
        self.guard.run_tool('BatchOut', main)
        self.assertIn('ModuleNotFoundError', self.shown[0][0])

    def test_exitscript_is_a_quiet_exit(self):
        def main():
            sys.exit(0)
        self.assertIsNone(self.guard.run_tool('BatchOut', main))
        self.assertEqual(self.shown, [])


class GuardHandler(GuardTestCase):
    def test_exception_never_reaches_dotnet(self):
        win = Window()

        def go_next(sender, e):
            raise KeyError('selection')
        wrapped = self.guard.guard_handler(go_next, win, 'go_next')
        self.assertIsNone(wrapped(None, None))            # no raise
        self.assertIn('go_next', self.shown[0][0])
        self.assertIn('Action failed', win.status_text.Text)

    def test_one_dialog_per_handler_then_log_only(self):
        win = Window()

        def boom(sender, e):
            raise ValueError('again')
        wrapped = self.guard.guard_handler(boom, win, 'boom')
        for _ in range(5):
            wrapped(None, None)
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(self.log_text().count('ValueError: again'), 5)

    def test_passes_arguments_and_return_value_through(self):
        seen = []
        wrapped = self.guard.guard_handler(lambda s, e: seen.append((s, e)) or 'ok',
                                           Window(), 'h')
        self.assertEqual(wrapped('sender', 'args'), 'ok')
        self.assertEqual(seen, [('sender', 'args')])

    def test_hashes_like_the_handler_so_minus_equals_still_detaches(self):
        class Dialog(object):
            def on_click(self, sender, e):
                pass
        dlg = Dialog()
        wrapped = self.guard.guard_handler(dlg.on_click, dlg, 'on_click')
        # pythonnet removes an event handler by hash(handler)
        self.assertEqual(hash(wrapped), hash(dlg.on_click))
        self.assertEqual(wrapped, dlg.on_click)
        self.assertIs(self.guard.guard_handler(wrapped), wrapped)

    def test_system_exit_in_handler_does_not_escape(self):
        wrapped = self.guard.guard_handler(lambda s, e: sys.exit(0), Window(), 'h')
        self.assertIsNone(wrapped(None, None))
        self.assertEqual(self.shown, [])


class FakeClrException(object):
    """Just the members of System.Exception the guard reads."""

    def __init__(self, type_name, stack, inner=None, message='boom'):
        self._type = type_name
        self.StackTrace = stack
        self.InnerException = inner
        self.Message = message

    def GetType(self):
        return SimpleNamespace(FullName=self._type)

    def ToString(self):
        return '{}: {}\n{}'.format(self._type, self.Message, self.StackTrace)

    def __str__(self):
        return self.Message


class DispatcherGuard(GuardTestCase):
    PY = FakeClrException('Python.Runtime.PythonException',
                          '   at Python.Runtime.Dispatcher.TrueDispatch(Object[] args)')
    BINDING = FakeClrException(
        'System.InvalidOperationException',
        '   at System.Windows.Data.BindingExpression.Activate(Object item)',
        message="A TwoWay or OneWayToSource binding cannot work on the read-only "
                "property 'IsPlaceholder'")
    REVIT = FakeClrException('Autodesk.Revit.Exceptions.InvalidOperationException',
                             '   at Autodesk.Revit.UI.Ribbon.Foo()')

    def test_python_and_binding_exceptions_are_ours(self):
        self.assertTrue(self.guard.is_owned_exception(self.PY))
        self.assertTrue(self.guard.is_owned_exception(self.BINDING))
        wrapped = FakeClrException('System.Reflection.TargetInvocationException',
                                   '   at System.RuntimeMethodHandle.Invoke()',
                                   inner=self.PY)
        self.assertTrue(self.guard.is_owned_exception(wrapped))

    def test_revit_own_exceptions_are_left_alone(self):
        win = Window()
        self.assertFalse(self.guard.handle_dispatcher_exception(win, self.REVIT))
        win.Dispatcher.BeginInvoke.assert_not_called()

    def test_owned_exception_is_handled_and_reported_once(self):
        win = Window()
        dotnet = {'System': SimpleNamespace(Action=lambda fn: fn),
                  'System.Windows': SimpleNamespace(),
                  'System.Windows.Threading': SimpleNamespace(
                      DispatcherPriority=SimpleNamespace(Background=4))}
        with mock.patch.dict(sys.modules, dotnet):
            for _ in range(3):
                self.assertTrue(self.guard.handle_dispatcher_exception(win, self.BINDING))
        # The dialog is posted, not shown inside the failing layout pass.
        self.assertEqual(win.Dispatcher.BeginInvoke.call_count, 1)
        self.assertEqual(self.shown, [])
        _priority, show = win.Dispatcher.BeginInvoke.call_args[0]
        show()
        self.assertIn('IsPlaceholder', self.shown[0][0])
        self.assertIn('read-only property', win.status_text.Text)
        self.assertIn('IsPlaceholder', self.log_text())

    def test_clr_trace_is_what_gets_logged(self):
        details = self.guard.format_exception(self.BINDING)
        self.assertIn('System.Windows.Data.BindingExpression.Activate', details)


class WpfBaseWiring(unittest.TestCase):
    """Every XAML handler and templated click goes through the guard."""

    def setUp(self):
        with open(WPF_BASE, encoding='utf-8') as f:
            self.src = f.read()

    def test_xaml_handlers_are_attached_guarded(self):
        self.assertIn('evt += _guard_handler(handler, self, handler_name)', self.src)
        self.assertNotIn('evt += handler\n', self.src)

    def test_templated_clicks_are_guarded(self):
        self.assertIn('_guard_handler(handler, self, handler_name)(node, args)', self.src)

    def test_dispatcher_guard_is_installed_and_removed(self):
        tree = ast.parse(self.src)
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == 'T3WPFWindow')
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        for name in ('_install_dispatcher_guard', '_remove_dispatcher_guard',
                     '_on_dispatcher_unhandled'):
            self.assertIn(name, methods)
        load_src = ast.get_source_segment(self.src, methods['load_xaml'])
        self.assertIn('self._install_dispatcher_guard()', load_src)

    def test_handler_marks_owned_exceptions_handled(self):
        tree = ast.parse(self.src)
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == 'T3WPFWindow')
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_on_dispatcher_unhandled']
        scope = {'_handle_dispatcher_exception': lambda owner, exc: exc == 'ours'}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), WPF_BASE, 'exec'), scope)
        win = scope['T3WPFWindow']()
        for exc, expected in (('ours', True), ('revit', False)):
            e = SimpleNamespace(Handled=False, Exception=exc)
            win._on_dispatcher_unhandled(None, e)
            self.assertIs(e.Handled, expected)


class ChangedOnlyReload(unittest.TestCase):
    """init_cpython_paths() runs on every click; it must not recompile
    unchanged modules, and must still pick up an edited one."""

    def setUp(self):
        self.boot = _load(BOOTSTRAP, '_t3_bootstrap_reload_under_test')
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 't3_hot_module.py')
        with open(self.path, 'w') as f:
            f.write('LOADS = globals().get("LOADS", 0) + 1\n')
        past = time.time() - 3600
        os.utime(self.path, (past, past))           # older than engine start
        sys.path.insert(0, self.tmp)
        self.addCleanup(sys.path.remove, self.tmp)
        import t3_hot_module
        self.mod = t3_hot_module
        self.addCleanup(sys.modules.pop, 't3_hot_module', None)

    def test_unchanged_module_is_not_reloaded(self):
        for _ in range(3):
            self.assertEqual(self.boot.reload_changed_modules(('t3_hot_module',)), [])
        self.assertEqual(self.mod.LOADS, 1)

    def test_edited_module_is_reloaded_once(self):
        future = time.time() + 60
        os.utime(self.path, (future, future))
        self.assertEqual(self.boot.reload_changed_modules(('t3_hot_module',)),
                         ['t3_hot_module'])
        self.assertEqual(self.boot.reload_changed_modules(('t3_hot_module',)), [])
        self.assertEqual(sys.modules['t3_hot_module'].LOADS, 2)

    def test_modules_not_loaded_yet_are_skipped(self):
        self.assertEqual(self.boot.reload_changed_modules(('t3_never_imported',)), [])


if __name__ == '__main__':
    unittest.main()
