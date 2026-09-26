# -*- coding: utf-8 -*-
"""Split Elements — event handling for the Split Elements launcher window."""

import os
import builtins as __builtin__

from pyrevit import forms

try:
    from GUI import RevitTheme as _theme
except Exception:
    try:
        import RevitTheme as _theme
    except Exception:
        _theme = None

from GUI.WPF_Base import T3WPFWindow

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'SplitElements.xaml')


class SplitElementsWindow(T3WPFWindow):
    def __init__(self, script_dir, revit):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit

        self._adopt_host_font()
        self._apply_theme()

        if hasattr(self, 'btn_split_walls') and self.btn_split_walls:
            self.btn_split_walls.Click += self._on_split_walls
        if hasattr(self, 'btn_split_columns') and self.btn_split_columns:
            self.btn_split_columns.Click += self._on_split_columns
        if hasattr(self, 'btn_split_floors') and self.btn_split_floors:
            self.btn_split_floors.Click += self._on_split_floors

        if hasattr(self, 'btn_execute') and self.btn_execute:
            self.btn_execute.Click += self._on_execute
        if hasattr(self, 'btn_cancel') and self.btn_cancel:
            self.btn_cancel.Click += self._close_chrome

        if hasattr(self, 'btn_minimize') and self.btn_minimize:
            self.btn_minimize.Click += self._minimize
        if hasattr(self, 'btn_maximize') and self.btn_maximize:
            self.btn_maximize.Click += self._maximize
        if hasattr(self, 'btn_close_chrome') and self.btn_close_chrome:
            self.btn_close_chrome.Click += self._close_chrome

    def _adopt_host_font(self):
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family:
            try:
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

    def tab_chip_checked(self, sender, e):
        """Tab strip (T3.Chip, same as BGTheme): show the tab named by Tag."""
        try:
            self.tab_elements.SelectedIndex = int(sender.Tag)
        except (TypeError, ValueError, AttributeError):
            pass

    def _on_execute(self, sender, e):
        idx = 0
        if hasattr(self, 'tab_elements') and self.tab_elements:
            idx = self.tab_elements.SelectedIndex
        if idx == 0:
            self._on_split_walls(sender, e)
        elif idx == 1:
            self._on_split_columns(sender, e)
        elif idx == 2:
            self._on_split_floors(sender, e)

    def _launch(self, rel_path):
        script_path = os.path.normpath(os.path.join(self._script_dir, rel_path))
        self.Close()
        g = {'__name__': '__main__', '__file__': script_path,
             '__builtins__': __builtin__, '__revit__': self._revit}
        try:
            with open(script_path, 'r', encoding='utf-8') as fh:
                exec(compile(fh.read(), script_path, 'exec'), g)
        except Exception as ex:
            forms.alert("Error launching tool:\n{}".format(ex))

    def _on_split_walls(self, sender, e):
        self._launch("../Split.pulldown/Wall_Split.pushbutton/script.py")

    def _on_split_columns(self, sender, e):
        self._launch("../Split.pulldown/Column_Split.pushbutton/script.py")

    def _on_split_floors(self, sender, e):
        self._launch("../Split.pulldown/Floor_Split.pushbutton/script.py")

    def _minimize(self, sender, e):
        import System.Windows
        self.WindowState = System.Windows.WindowState.Minimized

    def _maximize(self, sender, e):
        import System.Windows
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def _close_chrome(self, sender, e):
        self.Close()


def show_split_elements(script_dir, revit):
    SplitElementsWindow(script_dir, revit).ShowDialog()
