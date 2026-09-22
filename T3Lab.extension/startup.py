# -*- coding: utf-8 -*-
"""
T3Lab Extension Startup Script
================================
Runs during pyRevit's OnStartup phase.

Responsibilities:
  1. Register the T3Lab Assistant as a native Revit DockablePane.
  2. Register the right-click context-menu entry (Revit 2025+).
  3. Start the file-based task watcher.
  4. Deploy the MCP bridge to %APPDATA%/T3LabAI/bridge.py and auto-start the
     MCP server (default on; "auto_start_mcp": false in mcp_paths.json opts out).

In the startup script context, `__revit__` is the UIControlledApplication
(available during Revit's OnStartup), which is required for DockablePane registration.
"""

from __future__ import unicode_literals

import os
import sys

# ─── Path bootstrap ────────────────────────────────────────────────────────────
# Engine discovery lives in lib/_cpython_bootstrap.py and nowhere else. This file
# used to carry its own copy hardcoding the clone names pyRevit-Master/pyRevit and
# the engine folder CPY3123 — i.e. one developer's layout — so any other machine
# silently started with no Python 3 stdlib on sys.path.
_STARTUP_DIR = os.path.dirname(os.path.abspath(__file__))   # T3Lab.extension/
_LIB_DIR     = os.path.join(_STARTUP_DIR, 'lib')
for _p in (_STARTUP_DIR, _LIB_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
    _cpython_bootstrap.fix_std_streams()
except Exception:
    pass

# ─── Reload-survival probe ─────────────────────────────────────────────────────
# init_cpython_paths() installs the no-op IFormatter that stops `Reload pyRevit`
# from crashing on BinaryFormatter (see _cpython_bootstrap.enable_safe_engine_
# shutdown). CPython here has no output window, so the only way to know whether
# it took is a file — and a silent failure looks exactly like success until the
# next Reload kills the engine. One line per Revit start.
try:
    import datetime as _dt
    _status = getattr(_cpython_bootstrap, 'SAFE_SHUTDOWN_STATUS', 'unavailable')
    _status_log = os.path.join(os.path.expanduser("~"), "T3Lab_AI_Data",
                               "bootstrap_status.log")
    if not os.path.isdir(os.path.dirname(_status_log)):
        os.makedirs(os.path.dirname(_status_log))
    with open(_status_log, "a") as _f:
        _f.write("[{}] safe engine shutdown: {}\n".format(
            _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _status))
except Exception:
    pass

# ─── Attempt DockablePane registration ─────────────────────────────────────────

def _log_dockable(msg):
    try:
        import datetime
        _dlog_path = os.path.join(os.path.expanduser("~"), "T3Lab_AI_Data", "dockable_pane_startup.log")
        _d = os.path.dirname(_dlog_path)
        if not os.path.isdir(_d):
            os.makedirs(_d)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_dlog_path, "a", encoding="utf-8") as f:
            f.write(u"[{}] {}\n".format(stamp, msg))
    except Exception:
        pass


def _get_uicontrolled_app():
    """Resolve the genuine UIControlledApplication instance from pyRevit runtime."""
    # 1. Inspect PyRevitLoaderApplication._uiControlledApplication static field
    try:
        import System
        for assm in System.AppDomain.CurrentDomain.GetAssemblies():
            try:
                name = assm.GetName().Name
                if name.startswith("pyRevitLoader"):
                    t = assm.GetType("PyRevitLoader.PyRevitLoaderApplication")
                    if t:
                        f = t.GetField(
                            "_uiControlledApplication",
                            System.Reflection.BindingFlags.Public
                            | System.Reflection.BindingFlags.NonPublic
                            | System.Reflection.BindingFlags.Static,
                        )
                        if f:
                            app = f.GetValue(None)
                            if app is not None and type(app).__name__ == "UIControlledApplication":
                                _log_dockable(u"Found UIControlledApplication via PyRevitLoaderApplication._uiControlledApplication")
                                return app
            except Exception:
                continue
    except Exception as ex:
        _log_dockable(u"Error scanning PyRevitLoader assemblies: {}".format(ex))

    # 2. Check pyrevit HOST_APP.uicontrolledapp
    try:
        from pyrevit import HOST_APP
        app = getattr(HOST_APP, 'uicontrolledapp', None)
        if app is not None and type(app).__name__ == "UIControlledApplication":
            _log_dockable(u"Found UIControlledApplication via HOST_APP.uicontrolledapp")
            return app
    except Exception:
        pass

    # 3. Check __revit__ if it is UIControlledApplication
    try:
        if '__revit__' in globals():
            app = globals()['__revit__']
            if type(app).__name__ == "UIControlledApplication":
                _log_dockable(u"Found UIControlledApplication via __revit__")
                return app
    except Exception:
        pass

    return None


try:
    import traceback
    import clr
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit.UI import DockablePaneId, DockablePane
    from System import Guid

    from GUI.AssistantPaneControl import ASSISTANT_PANE_GUID, AssistantPaneProvider

    pane_id = DockablePaneId(ASSISTANT_PANE_GUID)

    if DockablePane.PaneExists(pane_id):
        _log_dockable(u"DockablePane 'T3Lab Assistant' already registered.")
    else:
        # Collect candidate host application objects capable of RegisterDockablePane
        _candidates = []

        # 1. Genuine UIControlledApplication from pyRevit runtime
        _uictrld = _get_uicontrolled_app()
        if _uictrld is not None:
            _candidates.append((_uictrld, "UIControlledApplication"))

        # 2. pyRevit HOST_APP.uiapp (the standard pyRevit way used by pyRevitTools)
        try:
            from pyrevit import HOST_APP
            if hasattr(HOST_APP, 'uiapp') and HOST_APP.uiapp is not None:
                _candidates.append((HOST_APP.uiapp, "HOST_APP.uiapp"))
        except Exception as ex_h:
            _log_dockable(u"HOST_APP.uiapp lookup error: {}".format(ex_h))

        # 3. Global __revit__ handle
        try:
            if '__revit__' in globals() and globals()['__revit__'] is not None:
                _candidates.append((globals()['__revit__'], "__revit__"))
        except Exception:
            pass

        _registered = False
        provider = AssistantPaneProvider()

        for app_obj, app_desc in _candidates:
            if hasattr(app_obj, 'RegisterDockablePane'):
                try:
                    app_obj.RegisterDockablePane(pane_id, 'T3Lab Assistant', provider)
                    _log_dockable(u"SUCCESS: RegisterDockablePane succeeded on {}".format(app_desc))
                    _registered = True
                    break
                except Exception as ex_reg:
                    _log_dockable(u"RegisterDockablePane failed on {}: {}\n{}".format(
                        app_desc, ex_reg, traceback.format_exc()))

        if not _registered:
            # Fallback: hook ApplicationInitialized event if not yet fired
            def _on_app_initialized(sender, args):
                try:
                    if not DockablePane.PaneExists(pane_id):
                        from Autodesk.Revit.UI import UIApplication
                        uiapp = UIApplication(sender) if type(sender).__name__ != "UIApplication" else sender
                        prov = AssistantPaneProvider()
                        uiapp.RegisterDockablePane(pane_id, 'T3Lab Assistant', prov)
                        _log_dockable(u"SUCCESS: RegisterDockablePane succeeded in ApplicationInitialized event")
                except Exception as ex_init:
                    _log_dockable(u"FAILED RegisterDockablePane in ApplicationInitialized: {}\n{}".format(
                        ex_init, traceback.format_exc()))

            try:
                if _uictrld is not None and hasattr(_uictrld, 'ControlledApplication'):
                    _uictrld.ControlledApplication.ApplicationInitialized += _on_app_initialized
                    _log_dockable(u"Hooked ApplicationInitialized on uictrld.ControlledApplication")
                else:
                    from pyrevit import HOST_APP
                    if hasattr(HOST_APP, 'app') and hasattr(HOST_APP.app, 'ApplicationInitialized'):
                        HOST_APP.app.ApplicationInitialized += _on_app_initialized
                        _log_dockable(u"Hooked ApplicationInitialized on HOST_APP.app")
            except Exception as ex_hook:
                _log_dockable(u"Could not hook ApplicationInitialized: {}".format(ex_hook))

except Exception as ex_outer:
    _log_dockable(u"Outer exception during DockablePane registration: {}\n{}".format(
        ex_outer, traceback.format_exc()))

# ─── Register right-click context-menu entry (Revit 2025+) ─────────────────────
# Adds a "T3Lab Assistant" item to Revit's native right-click menu. No-op on
# hosts older than Revit 2025 (the Context Menu API doesn't exist there).
try:
    _uictrld_cm = None
    try:
        _uictrld_cm = __revit__  # noqa: F821 — UIControlledApplication at startup
    except NameError:
        try:
            from pyrevit import HOST_APP
            _uictrld_cm = getattr(HOST_APP, 'uicontrolledapp', None) or HOST_APP.uiapp
        except Exception:
            _uictrld_cm = None

    if _uictrld_cm is not None:
        from GUI.AssistantContextMenu import register as _register_ctx_menu
        _register_ctx_menu(_uictrld_cm)
    else:
        _cm_dbg = os.path.join(os.path.expanduser("~"), "T3Lab_AI_Data",
                               "context_menu_debug.log")
        try:
            if not os.path.isdir(os.path.dirname(_cm_dbg)):
                os.makedirs(os.path.dirname(_cm_dbg))
            with open(_cm_dbg, "a") as _f:
                _f.write("[startup] no UIControlledApplication handle — skipped\n")
        except Exception:
            pass
except Exception as _cm_ex:
    # Never crash Revit startup — context-menu entry is best-effort.
    try:
        import traceback as _tb
        _cm_dbg = os.path.join(os.path.expanduser("~"), "T3Lab_AI_Data",
                               "context_menu_debug.log")
        if not os.path.isdir(os.path.dirname(_cm_dbg)):
            os.makedirs(os.path.dirname(_cm_dbg))
        with open(_cm_dbg, "a") as _f:
            _f.write("[startup] context-menu wiring error: {}\n{}\n".format(
                _cm_ex, _tb.format_exc()))
    except Exception:
        pass

# ─── Self-study idle loop (opt-in: agents.self_study) ──────────────────────────
# Subscribe to the application Idling event so the assistant can quietly refresh
# its knowledge and curate a local training dataset while Revit is open and the
# user is not using it. The handler is deliberately trivial — it hands off to a
# throttled facade that early-returns in microseconds unless a full window has
# passed AND the assistant has been idle, then runs one small unit of work on a
# background thread. Off by default (the setting must be enabled); never crashes
# Revit startup. The handler reference is kept alive on the module so the .NET
# event does not drop it.
_IDLING_HANDLERS = []
try:
    _uictrld_idle = None
    try:
        _uictrld_idle = __revit__  # noqa: F821 — UIControlledApplication at startup
    except NameError:
        try:
            from pyrevit import HOST_APP
            _uictrld_idle = getattr(HOST_APP, 'uicontrolledapp', None)
        except Exception:
            _uictrld_idle = None

    if _uictrld_idle is not None and hasattr(_uictrld_idle, 'add_Idling'):
        from Intelligence.learning import loop as _study_loop

        def _t3lab_on_idling(sender, args):
            try:
                _study_loop.on_idling_tick()
            except Exception:
                pass

        _uictrld_idle.Idling += _t3lab_on_idling
        _IDLING_HANDLERS.append(_t3lab_on_idling)   # keep alive
except Exception:
    # Never crash Revit startup — self-study is best-effort.
    pass

# ─── Start file-based task watcher ─────────────────────────────────────────────
# Watches ~/T3Lab_AI_Data/task.json (and task.py) for AI-written tasks.
# Executes them in Revit context via ExternalEvent; result → result.json / result.txt.
# Zero-network alternative to the MCP HTTP server — data never leaves the machine.
try:
    from core.file_watcher import get_task_watcher
    get_task_watcher().start()
except Exception:
    pass

# ─── Deploy MCP bridge + auto-start MCP server ─────────────────────────────────
# deploy_bridge() copies core/bridge.py to %APPDATA%/T3LabAI/bridge.py — the
# machine-stable path Claude Desktop's config points at. Runs on every start
# so extension updates propagate to the deployed bridge automatically, no
# matter where the user installed the extension.
#
# Auto-start is ON by default so a freshly downloaded extension is reachable
# over MCP without any manual step. Set "auto_start_mcp": false in
# %APPDATA%/T3LabAI/mcp_paths.json to opt out. (Read via load_settings, not
# get_setting — get_setting treats a stored false as "absent" and would
# overwrite it with the default.)
#
# Port lifecycle after startup is owned by the extension hooks: closing the
# LAST document releases the port immediately (hooks/doc-closed.py), and
# opening/creating a document brings the server back (hooks/doc-opened.py,
# hooks/doc-created.py) — so an idle Revit at the start page never squats a
# port in the shared 48884-48894 range.
try:
    from Services.mcp_service import MCPService
    MCPService.deploy_bridge()

    from core import paths as _mcp_paths
    _auto = _mcp_paths.load_settings().get('auto_start_mcp')
    if _auto is None:
        _auto = True
        _mcp_paths.set_setting('auto_start_mcp', True)   # make it discoverable/editable

    if _auto:
        # OnStartup runs on Revit's UI thread — a valid API context, so the
        # ExternalEvent that marshals model-editing tools can be created here.
        MCPService.ensure_external_event()
        MCPService.start_server()
except Exception:
    # Never crash Revit startup — MCP can still be started from the ribbon.
    pass
