# -*- coding: utf-8 -*-
"""
ErrorGuard
==========
Stops Python errors from ever reaching Revit's generic
"Command Failure for External Command" dialog.

pyRevit's CPython engine has no output window, so an exception that escapes a
script has nowhere to be printed: Revit catches it at the external-command
boundary and shows its own dialog ("Revit could not complete the external
command ... identity: .") with no cause at all. The same thing happens when an
exception escapes a WPF event handler, a Dispatcher callback or a data binding
while a modal tool window is open. Inside a modeless window it is worse — the
exception reaches Revit's own message loop and Revit dies.

Three layers, all used by the shared window base and pushbutton scripts:

  run_tool(name, main)             wraps a pushbutton's whole body
  guard_handler(fn, owner, name)   wraps one WPF event handler
  handle_dispatcher_exception()    Dispatcher.UnhandledException filter

Every failure is logged to %APPDATA%\\T3LabAI\\errors.log (with the full
traceback) and shown ONCE in an English T3 dialog, so a report can be
diagnosed without digging through the Revit journal.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Error Guard"

import io
import os
import sys
import traceback
from datetime import datetime


LOG_MAX_BYTES = 512 * 1024

# Only exceptions whose .NET stack runs through these are ours to swallow:
# Python callbacks (pythonnet) and WPF data binding. Anything else on Revit's
# UI thread is Revit's own and is left alone.
_OWNED_MARKERS = ('Python.Runtime', 'System.Windows.Data', 'MS.Internal.Data')

# A binding that throws can throw again on every layout pass; cap the logging
# per window so the log file does not fill with the same trace.
_MAX_DISPATCHER_LOGS = 20

_NEXT_STEP = ("Run pyRevit > Reload once and try again. If it happens again, "
              "send the log file below to T3Lab.")


def log_path():
    base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI', 'errors.log')


def log_exception(tool, context, details):
    """Append one failure to errors.log. Returns the log path, or None."""
    path = log_path()
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            try:
                os.replace(path, path + '.1')
            except Exception:
                os.remove(path)
        with io.open(path, 'a', encoding='utf-8') as f:
            f.write(u'[{}] {} | {}\n{}\n\n'.format(
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                tool, context, details))
        return path
    except Exception:
        return None


def _one_line(exc):
    """'TypeName: message' — a .NET exception wrapped by pythonnet included."""
    if exc is None:
        return 'Unknown error'
    name = type(exc).__name__
    try:
        text = u'{}'.format(exc).strip()
    except Exception:
        text = ''
    if not text:
        try:
            text = u'{}'.format(exc.Message).strip()
        except Exception:
            text = ''
    line = u'{}: {}'.format(name, text) if text else name
    return line.splitlines()[0] if line else name


def format_exception(exc):
    """Full traceback: Python frames, or the CLR stack for a .NET exception."""
    tb = getattr(exc, '__traceback__', None)
    if tb is None and hasattr(exc, 'StackTrace'):
        # Handed to us by WPF (Dispatcher.UnhandledException): the CLR
        # ToString() already carries the stack and every inner exception.
        try:
            return u'{}'.format(exc.ToString())
        except Exception:
            return _one_line(exc)
    parts = []
    try:
        parts.append(u''.join(traceback.format_exception(type(exc), exc, tb)).rstrip())
    except Exception:
        parts.append(_one_line(exc))
    inner = getattr(exc, 'InnerException', None)
    depth = 0
    while inner is not None and depth < 5:
        try:
            parts.append(u'Inner .NET exception: {}'.format(inner.ToString()))
        except Exception:
            parts.append(u'Inner .NET exception: {}'.format(inner))
        inner = getattr(inner, 'InnerException', None)
        depth += 1
    return u'\n'.join(p for p in parts if p)


def _show_error(message, title, details, owner=None):
    """T3 error dialog; Revit TaskDialog if WPF is unusable. Never raises."""
    try:
        from GUI.T3Dialog import show_error
        show_error(message, title=title, details=details, owner=owner)
        return
    except Exception:
        pass
    try:
        from Autodesk.Revit.UI import TaskDialog
        dialog = TaskDialog(title)
        dialog.MainInstruction = message
        dialog.MainContent = details
        dialog.Show()
    except Exception:
        pass


def report_exception(tool, context, exc, owner=None, show=True):
    """Log a failure and tell the user what failed, where, and what to do next."""
    details = format_exception(exc)
    path = log_exception(tool, context, details)
    if show:
        message = u'{} hit an error while {}.\n\n{}'.format(tool, context, _one_line(exc))
        footer = _NEXT_STEP
        if path:
            footer += u'\nLog: {}'.format(path)
        _show_error(message, u'{} error'.format(tool), footer + u'\n\n' + details,
                    owner=owner)
    return path


def run_tool(tool, main, *args, **kwargs):
    """Run a pushbutton body; any error becomes a readable dialog, never
    Revit's "Command Failure for External Command"."""
    try:
        return main(*args, **kwargs)
    except SystemExit:
        return None          # forms.alert(exitscript=True) and friends
    except BaseException as exc:
        report_exception(tool, 'running the tool', exc)
        return None


# ── WPF event handlers ────────────────────────────────────────────────────────

def _tool_name(owner):
    try:
        title = u'{}'.format(owner.Title or '').strip()
        if title:
            return title
    except Exception:
        pass
    return u'T3Lab'


def _set_status(owner, text):
    for name in ('status_text', 'txt_status', 'lbl_status', 'status_label'):
        ctrl = getattr(owner, name, None)
        if ctrl is not None and hasattr(ctrl, 'Text'):
            try:
                ctrl.Text = text
                return
            except Exception:
                pass


def _first_report(owner, key):
    """True the first time `key` fails on this window — one dialog, not a storm."""
    try:
        seen = getattr(owner, '_t3_reported_errors', None)
        if seen is None:
            seen = set()
            owner._t3_reported_errors = seen
        if key in seen:
            return False
        seen.add(key)
        return True
    except Exception:
        return True


def handler_failed(owner, name, exc):
    """A WPF handler raised: log it, say so once, keep the window alive."""
    tool = _tool_name(owner)
    if _first_report(owner, name):
        report_exception(tool, u'handling "{}"'.format(name), exc, owner=owner)
    else:
        log_exception(tool, u'handling "{}"'.format(name), format_exception(exc))
    _set_status(owner, u'Action failed: {} (details in {})'.format(
        _one_line(exc), log_path()))


class _GuardedHandler(object):
    """Callable stand-in for a WPF event handler that cannot raise into .NET.

    Hashes and compares like the wrapped handler: pythonnet removes an event
    handler by hash, so `ctrl.Click -= self.on_click` still detaches the
    guarded copy that the XAML loader attached.
    """

    __slots__ = ('_fn', '_owner', '_name', '_hash')

    def __init__(self, fn, owner=None, name=None):
        self._fn = fn
        self._owner = owner
        self._name = name or getattr(fn, '__name__', 'handler')
        try:
            self._hash = hash(fn)
        except TypeError:
            self._hash = id(fn)

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        if isinstance(other, _GuardedHandler):
            other = other._fn
        return self._fn == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __call__(self, *args):
        try:
            return self._fn(*args)
        except SystemExit:
            return None
        except BaseException as exc:
            try:
                handler_failed(self._owner, self._name, exc)
            except BaseException:
                pass
            return None


def guard_handler(fn, owner=None, name=None):
    """Wrap `fn` so an exception inside it is reported instead of crashing Revit."""
    if fn is None or isinstance(fn, _GuardedHandler):
        return fn
    return _GuardedHandler(fn, owner, name)


# ── Dispatcher.UnhandledException ─────────────────────────────────────────────

def is_owned_exception(exc):
    """True when a dispatcher exception came from Python code or a WPF binding.

    Reads the .NET type names and stack traces down the InnerException chain.
    """
    texts = []
    cur = exc
    depth = 0
    while cur is not None and depth < 8:
        try:
            texts.append(u'{}'.format(cur.GetType().FullName or ''))
        except Exception:
            texts.append(type(cur).__name__)
        try:
            texts.append(u'{}'.format(cur.StackTrace or ''))
        except Exception:
            pass
        cur = getattr(cur, 'InnerException', None)
        depth += 1
    blob = u'\n'.join(texts)
    return any(marker in blob for marker in _OWNED_MARKERS)


def handle_dispatcher_exception(owner, exc):
    """Decide whether to swallow an unhandled dispatcher exception.

    Returns True when the caller should set `e.Handled = True`. The dialog is
    posted to the dispatcher instead of shown inline: the exception may come
    from inside a layout pass, where a nested modal loop is not safe.
    """
    if exc is None or not is_owned_exception(exc):
        return False
    tool = _tool_name(owner)
    try:
        count = getattr(owner, '_t3_dispatcher_errors', 0) + 1
        owner._t3_dispatcher_errors = count
    except Exception:
        count = 1
    _set_status(owner, u'Action failed: {} (details in {})'.format(
        _one_line(exc), log_path()))
    if _first_report(owner, '__dispatcher__'):
        # report_exception logs too, so the first one is written exactly once.
        def _show():
            report_exception(tool, u'responding to a window event', exc,
                             owner=owner)
        try:
            from System import Action
            from System.Windows.Threading import DispatcherPriority
            owner.Dispatcher.BeginInvoke(DispatcherPriority.Background, Action(_show))
        except Exception:
            log_exception(tool, u'window event (dispatcher)', format_exception(exc))
    elif count <= _MAX_DISPATCHER_LOGS:
        log_exception(tool, u'window event (dispatcher)', format_exception(exc))
    return True
