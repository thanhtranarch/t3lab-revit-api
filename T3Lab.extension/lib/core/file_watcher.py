# -*- coding: utf-8 -*-
"""
T3Lab File-Based Task Watcher
==============================
Provides a zero-network alternative to the MCP HTTP server.

Protocol
--------
WRITE side (AI agent / external Python script):
    Write a JSON file to TASK_FILE:
        {
          "task_id": "<unique-string>",
          "code":    "<IronPython 2.7 code>",
          "timeout": 30        # optional, seconds
        }
    The watcher detects the new task_id and executes the code inside Revit.

READ side (AI agent):
    Poll RESULT_FILE until its task_id matches what you sent:
        {
          "task_id": "<same-string>",
          "status":  "success" | "error",
          "output":  "<stdout / result value>",
          "error":   "<traceback>",
          "timestamp": <epoch float>
        }

Paths
-----
All files live in ~/T3Lab_AI_Data/:
    task.json   — written by the AI agent
    result.json — written by Revit after execution
    task.py     — plain-Python alternative (watched by mtime); result → result.txt
    result.txt  — plain-text result for task.py mode

The watcher supports BOTH modes simultaneously:
  • JSON mode  (task.json  ↔ result.json)  — preferred; carries task_id
  • Simple mode (task.py   ↔ result.txt)   — for direct script testing

In-Revit code context
---------------------
Executed code receives these names:
    doc    — current Revit Document
    uidoc  — current UIDocument
    app    — Application
    result — set this to return a value  (default None → "OK")
    output — append strings to this list for multi-line output
"""

from __future__ import unicode_literals

import threading
import json
import os
import time

# ── Data directory ──────────────────────────────────────────────────────────────
# Resolved through mcp_paths.json (see core/paths.py) so it's editable
# per-user instead of hardcoded — first run seeds it with ~/T3Lab_AI_Data.
try:
    from core import paths as _paths
    T3LAB_DATA_DIR = _paths.get_setting(
        'data_dir',
        lambda: os.path.join(os.path.expanduser('~'), 'T3Lab_AI_Data'))
except Exception:
    T3LAB_DATA_DIR = os.path.join(os.path.expanduser('~'), 'T3Lab_AI_Data')
TASK_FILE      = os.path.join(T3LAB_DATA_DIR, 'task.json')
RESULT_FILE    = os.path.join(T3LAB_DATA_DIR, 'result.json')
TASK_PY_FILE   = os.path.join(T3LAB_DATA_DIR, 'task.py')
RESULT_TXT     = os.path.join(T3LAB_DATA_DIR, 'result.txt')

# ── Optional Revit UI imports ───────────────────────────────────────────────────
HAS_REVIT_UI = False
try:
    import clr
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
    HAS_REVIT_UI = True
except Exception:
    pass


# ─── ExternalEvent handler ─────────────────────────────────────────────────────

if HAS_REVIT_UI:
    class _FileTaskEventHandler(IExternalEventHandler):
        __namespace__ = "T3Lab.Core"
        """
        Runs inside Revit's main thread (called by ExternalEvent.Raise()).
        Pops the pending task from the watcher, executes it, writes result.
        """

        def __init__(self, watcher):
            self._watcher = watcher

        def Execute(self, app):
            watcher = self._watcher
            task = watcher._pending_task
            if not task:
                return
            task_id  = task.get('task_id', '')
            code     = task.get('code', '')
            is_plain = task.get('_plain', False)

            try:
                uidoc = app.ActiveUIDocument
                doc   = uidoc.Document if uidoc else None
                if not doc:
                    watcher._write_result(task_id, error='No active Revit document', plain=is_plain)
                    return

                local_ctx = {
                    'doc':    doc,
                    'uidoc':  uidoc,
                    'app':    app,
                    'result': None,
                    'output': [],
                }
                exec(code, local_ctx)   # noqa: S102

                result_val   = local_ctx.get('result')
                output_lines = local_ctx.get('output', [])

                if result_val is not None:
                    out_str = str(result_val)
                elif output_lines:
                    out_str = '\n'.join(str(x) for x in output_lines)
                else:
                    out_str = 'OK'

                watcher._write_result(task_id, output=out_str, plain=is_plain)

            except Exception as ex:
                import traceback
                tb = traceback.format_exc()
                watcher._write_result(task_id, error='{}\n{}'.format(ex, tb), plain=is_plain)

            finally:
                watcher._pending_task = None
                watcher._done_event.set()

        def GetName(self):
            return 'T3Lab File Task Handler'
else:
    _FileTaskEventHandler = None


_PROCESS_WATCHER_KEY = '_t3lab_task_watcher_singleton'


def _get_process_watcher():
    try:
        from System import AppDomain
        existing = AppDomain.CurrentDomain.GetData(_PROCESS_WATCHER_KEY)
        if existing is not None:
            return existing
    except Exception:
        pass
    return getattr(sys, _PROCESS_WATCHER_KEY, None)


def _set_process_watcher(inst):
    try:
        from System import AppDomain
        AppDomain.CurrentDomain.SetData(_PROCESS_WATCHER_KEY, inst)
    except Exception:
        pass
    setattr(sys, _PROCESS_WATCHER_KEY, inst)


class TaskFileWatcher(object):
    """
    Singleton background watcher that monitors task.json (and task.py)
    and dispatches execution via Revit's ExternalEvent mechanism.
    """

    _instance = None
    _lock      = threading.Lock()

    @classmethod
    def get_instance(cls):
        existing = _get_process_watcher()
        if existing is not None:
            cls._instance = existing
            return existing
        if cls._instance is None:
            with cls._lock:
                existing = _get_process_watcher()
                if existing is not None:
                    cls._instance = existing
                    return existing
                if cls._instance is None:
                    inst = cls()
                    cls._instance = inst
                    _set_process_watcher(inst)
        return cls._instance

    def __init__(self):
        self._running       = False
        self._thread        = None
        self._last_task_id  = None      # JSON mode
        self._last_py_mtime = None      # plain-Python mode
        self._external_event = None
        self._event_handler  = None
        self._pending_task   = None
        self._done_event     = threading.Event()

        try:
            if not os.path.isdir(T3LAB_DATA_DIR):
                os.makedirs(T3LAB_DATA_DIR)
        except OSError:
            pass

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        """Start the background watcher thread. Safe to call multiple times."""
        if self._running:
            return True

        if HAS_REVIT_UI and _FileTaskEventHandler and not self._external_event:
            try:
                self._event_handler  = _FileTaskEventHandler(self)
                self._external_event = ExternalEvent.Create(self._event_handler)
            except Exception:
                pass  # fallback: no ExternalEvent (no Revit UI context yet)

        self._running = True
        self._thread  = threading.Thread(target=self._watch_loop)
        self._thread.daemon = True
        self._thread.start()
        return True

    def stop(self):
        """Stop the watcher thread."""
        self._running = False

    @property
    def is_running(self):
        return self._running

    # ── watch loop ─────────────────────────────────────────────────────────────

    def _watch_loop(self):
        try:
            while self._running:
                try:
                    self._check_json_task()
                except Exception:
                    pass
                try:
                    self._check_plain_task()
                except Exception:
                    pass
                time.sleep(0.5)
        except Exception:
            pass

    # ── JSON mode ──────────────────────────────────────────────────────────────

    def _check_json_task(self):
        if not os.path.isfile(TASK_FILE):
            return
        try:
            with open(TASK_FILE, 'r') as f:
                task = json.load(f)
        except Exception:
            return

        task_id = task.get('task_id')
        if not task_id or task_id == self._last_task_id:
            return

        self._last_task_id = task_id
        self._dispatch(task)

    # ── Plain-Python mode ──────────────────────────────────────────────────────

    def _check_plain_task(self):
        if not os.path.isfile(TASK_PY_FILE):
            return
        try:
            mtime = os.path.getmtime(TASK_PY_FILE)
        except OSError:
            return

        if mtime == self._last_py_mtime:
            return
        self._last_py_mtime = mtime

        try:
            with open(TASK_PY_FILE, 'r') as f:
                code = f.read()
        except Exception:
            return

        task_id = 'plain_{}'.format(int(mtime * 1000))
        task = {'task_id': task_id, 'code': code, '_plain': True}
        self._dispatch(task)

    # ── dispatch ───────────────────────────────────────────────────────────────

    def _dispatch(self, task):
        """Hand the task off to Revit's main thread via ExternalEvent."""
        self._pending_task = task
        self._done_event.clear()

        if self._external_event:
            self._external_event.Raise()
        else:
            # ExternalEvent not available — attempt direct exec (works only if
            # already on Revit's main thread, e.g. in script context tests).
            self._exec_direct(task)

    def _exec_direct(self, task):
        """Fallback: execute directly (no Revit API calls will work safely).

        Refuses to run without an active document. The ExternalEvent path
        above already checks this (`if not doc: ... 'No active Revit
        document'`); this path did not, and handed the task code a context
        with no `doc` at all. Task code that reaches for the document itself
        then gets None, and `FilteredElementCollector(None)` is not a Python
        exception — it is Revit's own

            FATAL ERROR: The input argument "document" of function
            FilteredElementCollector_constructor ... is null

        which no `except Exception` anywhere up the stack can catch. This
        fallback runs precisely when the ExternalEvent does not exist yet,
        i.e. the window around document-open when there is most likely to be
        no active document. Declining is the only safe answer.
        """
        task_id  = task.get('task_id', '')
        code     = task.get('code', '')
        is_plain = task.get('_plain', False)
        try:
            from pyrevit import revit
            if getattr(revit, 'doc', None) is None:
                self._write_result(
                    task_id,
                    error=('No active Revit document, and the Revit '
                           'ExternalEvent is not available yet — task not '
                           'run. Open a model (or reopen the T3Lab Assistant '
                           'once) and write the task again.'),
                    plain=is_plain)
                self._done_event.set()
                return
        except Exception:
            # pyrevit unavailable (bare unit-test context) — the historical
            # behaviour, where the task is plain Python with no Revit calls.
            pass
        try:
            local_ctx = {'result': None, 'output': []}
            exec(code, local_ctx)   # noqa: S102
            result_val = local_ctx.get('result')
            out_str    = str(result_val) if result_val is not None else 'OK (no Revit context)'
            self._write_result(task_id, output=out_str, plain=is_plain)
        except Exception as ex:
            self._write_result(task_id, error=str(ex), plain=is_plain)
        finally:
            self._done_event.set()

    # ── result writing ─────────────────────────────────────────────────────────

    def _write_result(self, task_id, output=None, error=None, plain=False):
        ts     = time.time()
        status = 'error' if error else 'success'
        result = {
            'task_id':   task_id,
            'status':    status,
            'output':    output or '',
            'error':     error  or '',
            'timestamp': ts,
        }
        # Always write structured JSON
        try:
            with open(RESULT_FILE, 'w') as f:
                json.dump(result, f, indent=2)
        except Exception:
            pass

        # For plain-Python mode, also write human-readable result.txt
        if plain:
            try:
                txt = output if output else 'ERROR: {}'.format(error)
                with open(RESULT_TXT, 'w') as f:
                    f.write(txt)
            except Exception:
                pass

    # ── public API ─────────────────────────────────────────────────────────────


    def get_status(self):
        return {
            'running':        self._running,
            'data_dir':       T3LAB_DATA_DIR,
            'task_file':      TASK_FILE,
            'result_file':    RESULT_FILE,
            'task_py_file':   TASK_PY_FILE,
            'result_txt':     RESULT_TXT,
            'has_ext_event':  self._external_event is not None,
        }


# ── Module-level convenience ────────────────────────────────────────────────────

def get_task_watcher():
    """Return the singleton TaskFileWatcher."""
    return TaskFileWatcher.get_instance()
