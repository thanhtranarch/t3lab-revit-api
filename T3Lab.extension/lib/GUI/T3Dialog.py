# -*- coding: utf-8 -*-
"""
T3Dialog — Modern replacement for Revit TaskDialog and forms.alert.
Provides Info, Warning, Error (Danger), and Confirm modal dialogs conforming to T3 UI standard.
"""

import os

# Python.NET 3 refuses to convert a plain int into a .NET enum, so every
# Visibility assignment must use the real System.Windows.Visibility member --
# `x.Visibility = 2` raises "int can not be converted to Enum implicitly".
from System.Windows import Visibility as _Visibility

try:
    from GUI.WPF_Base import T3WPFWindow as _WPFWindow
except Exception:
    try:
        from WPF_Base import T3WPFWindow as _WPFWindow
    except Exception:
        # pyrevit.forms raises PyRevitCPythonNotSupported on *any* attribute
        # access under CPython, so this last resort must stay guarded.
        try:
            from pyrevit import forms as _forms
            _WPFWindow = _forms.WPFWindow
        except Exception:
            _WPFWindow = object

try:
    from GUI import RevitTheme as _theme
except Exception:
    try:
        import RevitTheme as _theme
    except Exception:
        _theme = None

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'T3Dialog.xaml')


class T3Dialog(_WPFWindow):
    """Universal T3 Modal Dialog for alerts, warnings, errors and confirmations."""

    MODE_INFO = "info"
    MODE_WARNING = "warning"
    MODE_ERROR = "error"
    MODE_CONFIRM = "confirm"

    def __init__(self, message, title="Notification", details=None,
                 mode="info", ok_text="OK", cancel_text="Cancel", danger=False, owner=None):
        _WPFWindow.__init__(self, _XAML)
        self.result = False
        self._mode = mode
        self._danger = danger

        if owner:
            try:
                self.Owner = owner
            except Exception:
                pass

        self._adopt_host_font()
        self._apply_theme()

        # Wire Title and Texts
        if hasattr(self, 'dlg_title') and self.dlg_title:
            self.dlg_title.Text = title
        if hasattr(self, 'txt_message') and self.txt_message:
            self.txt_message.Text = message
        if hasattr(self, 'txt_details') and self.txt_details:
            if details:
                self.txt_details.Text = str(details)
                self.txt_details.Visibility = _Visibility.Visible
            else:
                self.txt_details.Text = ""
                self.txt_details.Visibility = _Visibility.Collapsed

        # Configure Style & Icon based on mode
        self._configure_appearance(mode, ok_text, cancel_text, danger)

        # Wire Events
        if hasattr(self, 'btn_ok') and self.btn_ok:
            self.btn_ok.Click += self._on_ok
        if hasattr(self, 'btn_cancel') and self.btn_cancel:
            self.btn_cancel.Click += self._on_cancel
        if hasattr(self, 'btn_close_chrome') and self.btn_close_chrome:
            self.btn_close_chrome.Click += self._on_cancel

    def _adopt_host_font(self):
        if _theme is None:
            return
        try:
            family, size = _theme.host_font()
            if family:
                self.FontFamily = family
                if size and size > 0:
                    self.FontSize = size
        except Exception:
            pass

    def _apply_theme(self, theme=None):
        if _theme is None:
            return
        try:
            _theme.apply(self, theme)
        except Exception:
            pass

    def _configure_appearance(self, mode, ok_text, cancel_text, danger):
        # Configure buttons
        if hasattr(self, 'btn_ok') and self.btn_ok:
            self.btn_ok.Content = ok_text
            if danger:
                danger_style = self.TryFindResource("T3.Button.Danger")
                if danger_style:
                    self.btn_ok.Style = danger_style

        if hasattr(self, 'btn_cancel') and self.btn_cancel:
            self.btn_cancel.Content = cancel_text
            if mode == self.MODE_CONFIRM:
                self.btn_cancel.Visibility = _Visibility.Visible
            else:
                self.btn_cancel.Visibility = _Visibility.Collapsed

        # Configure Icon Glyph and Color
        # Glyph codes from Segoe MDL2 Assets:
        # Info:  (Info) or  (CheckMark)
        # Warning:  (Warning)
        # Danger/Error:  (ErrorBadge) or  (Cancel)
        glyph = u""
        fill_res = "T3.Success.Fill"
        fg_res = "T3.Success.Text"

        if mode == self.MODE_WARNING:
            glyph = u""
            fill_res = "T3.Warning.Fill"
            fg_res = "T3.Warning.Text"
        elif mode == self.MODE_ERROR or danger:
            glyph = u""
            fill_res = "T3.Danger.Fill"
            fg_res = "T3.Danger.Text"
        elif mode == self.MODE_CONFIRM:
            glyph = u"" if danger else u""
            fill_res = "T3.Warning.Fill" if danger else "T3.SurfaceSunken"
            fg_res = "T3.Warning.Text" if danger else "T3.Text"

        if hasattr(self, 'icon_glyph') and self.icon_glyph:
            self.icon_glyph.Text = glyph
            fg_brush = self.TryFindResource(fg_res)
            if fg_brush:
                self.icon_glyph.Foreground = fg_brush

        if hasattr(self, 'icon_bg') and self.icon_bg:
            bg_brush = self.TryFindResource(fill_res)
            if bg_brush:
                self.icon_bg.Background = bg_brush

    def _on_ok(self, sender, e):
        self.result = True
        self.Close()

    def _on_cancel(self, sender, e):
        self.result = False
        self.Close()


# ── LAST-RESORT FALLBACK ─────────────────────────────────────────────────────
# A dialog whose whole job is to carry a message must never be the thing that
# swallows it. If the WPF window cannot be built for any reason -- XamlReader
# unavailable in this engine, a stale module in the persistent CPython engine,
# a broken resource -- fall back to Revit's own TaskDialog so the user still
# reads the message instead of a traceback.

def _task_dialog(message, title, details, buttons=1):
    """Revit's native TaskDialog. Returns True on OK/Yes, False otherwise."""
    from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
    dialog = TaskDialog(title or "T3Lab")
    dialog.MainInstruction = message or ""
    if details:
        dialog.MainContent = details
    if buttons == 2:
        dialog.CommonButtons = (TaskDialogCommonButtons.Yes |
                                TaskDialogCommonButtons.No)
        dialog.DefaultButton = TaskDialogResult.No
        return dialog.Show() == TaskDialogResult.Yes
    dialog.CommonButtons = TaskDialogCommonButtons.Close
    dialog.Show()
    return True


def _run(build, message, title, details, buttons=1):
    """Show the T3 dialog, or degrade to TaskDialog rather than raise."""
    try:
        dlg = build()
        dlg.ShowDialog()
        return dlg.result if buttons == 2 else True
    except Exception as exc:
        try:
            note = details or ""
            # Say why the styled dialog is missing: silently swapping chrome
            # looks like a different bug next time somebody reports it.
            if note:
                note += "\n\n"
            note += "(T3 dialog unavailable: %s)" % exc
            return _task_dialog(message, title, note, buttons)
        except Exception:
            return False if buttons == 2 else True


def show_info(message, title="Information", details=None, owner=None):
    """Show an info modal dialog."""
    return _run(lambda: T3Dialog(message, title=title, details=details,
                                 mode=T3Dialog.MODE_INFO, owner=owner),
                message, title, details)


def show_warning(message, title="Warning", details=None, owner=None):
    """Show a warning modal dialog."""
    return _run(lambda: T3Dialog(message, title=title, details=details,
                                 mode=T3Dialog.MODE_WARNING, owner=owner),
                message, title, details)


def show_error(message, title="Error", details=None, owner=None):
    """Show an error modal dialog."""
    return _run(lambda: T3Dialog(message, title=title, details=details,
                                 mode=T3Dialog.MODE_ERROR, ok_text="Close",
                                 owner=owner),
                message, title, details)


def confirm(message, title="Confirm Action", ok_text="Proceed", cancel_text="Cancel",
            danger=False, details=None, owner=None):
    """Show a confirmation dialog. Returns True if confirmed, False otherwise."""
    return _run(lambda: T3Dialog(message, title=title, details=details,
                                 mode=T3Dialog.MODE_CONFIRM, ok_text=ok_text,
                                 cancel_text=cancel_text, danger=danger,
                                 owner=owner),
                message, title, details, buttons=2)
