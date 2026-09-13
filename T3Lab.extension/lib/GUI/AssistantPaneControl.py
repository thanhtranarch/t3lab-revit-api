# -*- coding: utf-8 -*-
"""
T3Lab Assistant — Dockable Pane Provider

Registers the T3Lab AI Assistant as a native Revit DockablePane so it can dock
alongside the Properties panel and Project Browser. The pane hosts the FULL
assistant window (SetupDockablePane loads T3LabAssistantWindow and re-parents
its content), which is why there is no separate pane chat implementation here.

Removed 2026-07-27: AssistantPaneController (~350 lines) and its
Tools/AssistantPane.xaml (~1370 lines). SetupDockablePane never constructed the
controller, so get_pane_controller() always returned None and the entire class —
along with two live bugs inside it — was unreachable. Recoverable from git
history if a lightweight pane chat is ever wanted.
"""

from __future__ import unicode_literals

import os
import sys

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('System')
clr.AddReference('RevitAPIUI')

from System import Guid
from Autodesk.Revit.UI import IDockablePaneProvider, DockablePaneProviderData, DockablePaneState

# ─── Path bootstrap ────────────────────────────────────────────────────────────
_GUI_DIR  = os.path.dirname(__file__)                         # lib/GUI
_LIB_DIR  = os.path.dirname(_GUI_DIR)                        # lib
_EXT_DIR  = os.path.dirname(_LIB_DIR)                        # T3Lab.extension
for _p in (_LIB_DIR, _EXT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─── Shared pane GUID (must match startup.py) ──────────────────────────────────
ASSISTANT_PANE_GUID = Guid('7F3A9B2E-C4D1-4E8F-A6B5-1234567890AB')


# ─── Initial dock position ─────────────────────────────────────────────────────

def _apply_initial_dock_state(data):
    """Dock the pane to the right, tabbed behind the Project Browser.

    Revit only honours InitialState the FIRST time a pane is shown on a given
    machine; after that the user's own docking is remembered, which is the
    behaviour we want — this only decides where it lands out of the box.

    Everything here is best-effort. TabBehind is the part most likely to be
    refused (a host where the Project Browser has been closed or re-docked),
    and it must not take the plain DockPosition down with it, so the two are
    set in separate guarded steps rather than one.
    """
    try:
        from Autodesk.Revit.UI import DockPosition
        state = DockablePaneState()
        state.DockPosition = DockPosition.Left
        try:
            from Autodesk.Revit.UI import DockablePanes
            state.TabBehind = DockablePanes.BuiltInDockablePanes.ProjectBrowser
        except Exception:
            try:
                from Autodesk.Revit.UI import DockablePanes
                state.TabBehind = DockablePanes.BuiltInDockablePanes.PropertiesPalette
            except Exception:
                pass
        data.InitialState = state
        return True
    except Exception as ex:
        import logging
        logging.getLogger("T3LabAssistant").debug(
            "InitialState not applied: %s", ex)
        return False

def _log_pane(msg):
    try:
        import datetime
        _dlog_path = os.path.join(os.path.expanduser("~"), "T3Lab_AI_Data", "dockable_pane_startup.log")
        _d = os.path.dirname(_dlog_path)
        if not os.path.isdir(_d):
            os.makedirs(_d)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_dlog_path, "a", encoding="utf-8") as f:
            f.write(u"[{}] [PaneProvider] {}\n".format(stamp, msg))
    except Exception:
        pass


# ─── IDockablePaneProvider ─────────────────────────────────────────────────────

class AssistantPaneProvider(IDockablePaneProvider):
    """
    Revit calls SetupDockablePane() the first time the pane is shown.
    We load the UserControl XAML here and attach the controller.
    """
    __namespace__ = "T3Lab.GUI.AssistantPaneProvider"

    def SetupDockablePane(self, data):
        _log_pane(u"SetupDockablePane invoked by Revit")
        try:
            if _LIB_DIR not in sys.path:
                sys.path.insert(0, _LIB_DIR)
            if _EXT_DIR not in sys.path:
                sys.path.insert(0, _EXT_DIR)

            win = None
            try:
                from GUI.T3LabAssistantDialog import T3LabAssistantWindow
                win = T3LabAssistantWindow(is_docked=True)
            except Exception as ex_import:
                _log_pane(u"Direct import failed, attempting script fallback: {}".format(ex_import))
                tab_dir = os.path.join(_EXT_DIR, 'T3Lab.tab')
                script_path = os.path.join(
                    tab_dir, 'Support.panel', 'T3LabAssistant.pushbutton', 'script.py'
                )
                if os.path.isfile(script_path):
                    try:
                        import importlib.util
                        spec = importlib.util.spec_from_file_location('t3lab_assistant_full', script_path)
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules['t3lab_assistant_full'] = mod
                        spec.loader.exec_module(mod)
                    except Exception:
                        import imp
                        mod = imp.load_source('t3lab_assistant_full', script_path)
                    if hasattr(mod, 'T3LabAssistantWindow'):
                        win = mod.T3LabAssistantWindow(is_docked=True)

            if win is not None:
                # Detach visual content
                    content = win.Content
                    win.Content = None

                    # Enforce minimum width on the hosted dockable pane root element
                    try:
                        content.MinWidth = 380
                    except Exception:
                        pass

                    # Keep the window class instance alive
                    self._win_ref = win

                    data.FrameworkElement = content

                    # Dock the pane where Revit's own panels live instead of
                    # letting it come up floating in the middle of the screen.
                    _apply_initial_dock_state(data)

                    from Autodesk.Revit.UI import EditorInteraction, EditorInteractionType
                    data.EditorInteraction = EditorInteraction(EditorInteractionType.KeepAlive)
                    _log_pane(u"SetupDockablePane successfully initialized FrameworkElement and InitialState")
                    return
            
            raise Exception("pushbutton script.py not found: " + script_path)

        except Exception as ex:
            import logging
            logging.basicConfig()
            logger = logging.getLogger("T3LabAssistant")
            logger.error("Error setting up DockablePane: %s", ex, exc_info=True)
            _log_pane(u"ERROR in SetupDockablePane: {}".format(ex))

            from System.Windows.Controls import Border, TextBlock
            from System.Windows import HorizontalAlignment, VerticalAlignment, Thickness, TextWrapping
            from System.Windows.Media import Brushes

            border = Border()
            border.Background = Brushes.Crimson
            border.Padding = Thickness(20)

            lbl = TextBlock()
            lbl.Text = u'T3Lab Assistant pane could not load:\n{}'.format(ex)
            lbl.Foreground = Brushes.White
            lbl.TextWrapping = TextWrapping.Wrap
            lbl.HorizontalAlignment = HorizontalAlignment.Center
            lbl.VerticalAlignment   = VerticalAlignment.Center

            border.Child = lbl
            data.FrameworkElement   = border
