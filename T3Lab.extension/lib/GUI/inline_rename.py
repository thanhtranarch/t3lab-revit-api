# -*- coding: utf-8 -*-
"""Inline rename of one DataGrid column — double-click / F2 / Enter.

Used by Style Manager (ManaStyles) for its Line Styles, Line Patterns and
Fill Patterns grids. Kept out of `WPF_Base.py` on purpose.

Why not the DataGrid's own edit mode: `T3.DataGrid` is `IsReadOnly`, the
rows are Python objects (a TwoWay binding cannot write back into a PyObject),
the window's PreviewKeyDown closes it on Esc, and DataGrid starts editing on a
single click of the current cell — which this feature must NOT do.

So the NAME column is a `DataGridTemplateColumn` with `SortMemberPath="name"`
whose cell template holds both parts, the editor collapsed:

    <Grid>
      <TextBlock x:Name="row_name_display" Text="{Binding name}"/>
      <TextBox   x:Name="row_name_editor" Style="{StaticResource T3.TextBox}"
                 Visibility="Collapsed"/>
    </Grid>

and this controller flips them for exactly one row at a time:

  * double-click the NAME cell, or F2 / Enter on the selected row → edit,
    text selected. A single click never starts editing.
  * Enter or focus leaving the editor → commit, Esc → cancel.
    The window must route Esc here first (see `handle_escape`), because
    T3WPFWindow closes the window on Esc in its PreviewKeyDown.

The controller never touches Revit. The dialog supplies three callables:
    can_begin(item)        -> (ok, message)
    validate(item, text)   -> (ok, clean_name, message)   # ok False + "" = unchanged
    apply(item, clean)     -> row object now showing that name (or None)
and `report(message)` to show refusals in the status line.
"""

from System import Action
from System.Windows import Visibility
from System.Windows.Controls import DataGridCell, ContextMenu, MenuItem
from System.Windows.Input import (
    Key, Keyboard, ModifierKeys, MouseButton, KeyboardFocusChangedEventHandler)
from System.Windows.Media import VisualTreeHelper, Visual
from System.Windows.Threading import DispatcherPriority


# `ModifierKeys.None` is a syntax error in Python (`None` is a keyword).
_NO_MODIFIERS = getattr(ModifierKeys, 'None')


def _concrete(obj):
    """pythonnet 3 wraps values typed as an interface (`IInputElement`
    from `e.NewFocus`) in the interface; unwrap to the real control."""
    return getattr(obj, '__implementation__', obj)


def _parent(node):
    """Visual parent, or logical parent for content elements (Run, ...)."""
    try:
        if isinstance(node, Visual):
            return VisualTreeHelper.GetParent(node)
    except Exception:
        pass
    return getattr(node, 'Parent', None)


def _ancestor(node, cls, stop=None):
    while node is not None:
        if isinstance(node, cls):
            return node
        if stop is not None and node == stop:
            return None
        node = _parent(node)
    return None


def _is_inside(node, root):
    while node is not None:
        if node == root:
            return True
        node = _parent(node)
    return False


def _find_named(root, name):
    """First descendant (or root itself) whose x:Name is `name`."""
    if root is None:
        return None
    try:
        if getattr(root, 'Name', None) == name:
            return root
    except Exception:
        pass
    try:
        count = VisualTreeHelper.GetChildrenCount(root)
    except Exception:
        return None
    for i in range(count):
        hit = _find_named(VisualTreeHelper.GetChild(root, i), name)
        if hit is not None:
            return hit
    return None


class InlineRenameController(object):
    """Inline rename for one DataGrid. Plain Python class — no CLR base."""

    def __init__(self, grid, can_begin, validate, apply, report,
                 member_path='name', editor_name='row_name_editor',
                 display_name='row_name_display'):
        self._grid = grid
        self._can_begin = can_begin
        self._validate = validate
        self._apply = apply
        self._report = report
        self._member = member_path
        self._editor_name = editor_name
        self._display_name = display_name

        self._column = None
        for col in grid.Columns:
            if getattr(col, 'SortMemberPath', None) == member_path:
                self._column = col
                break

        self._item = None
        self._editor = None
        self._display = None
        self._busy = False
        self._lost_handler = KeyboardFocusChangedEventHandler(self._on_lost_focus)

        grid.MouseDoubleClick += self._on_double_click
        grid.PreviewKeyDown += self._on_key
        grid.PreviewMouseWheel += self._on_wheel

    # ── state ────────────────────────────────────────────────────────────
    @property
    def is_editing(self):
        return self._editor is not None

    def handle_escape(self):
        """Window-level Esc: cancel the edit. True when it was consumed."""
        if self.is_editing:
            self.cancel()
            return True
        return False

    # ── row lookup ───────────────────────────────────────────────────────
    def _index_of(self, item):
        items = self._grid.Items
        for i in range(items.Count):
            if items[i] is item:
                return i
        return -1

    def _row(self, index):
        """DataGridRow of `index`, realised (scrolled into view) if needed.

        The row itself is the search root for the editor: the NAME column is
        the only one holding `row_name_editor`, and searching the row avoids
        `DataGridColumn.GetCellContent`'s (object) / (DataGridRow) overloads,
        which pythonnet may resolve either way."""
        grid = self._grid
        if index < 0 or self._column is None:
            return None
        row = grid.ItemContainerGenerator.ContainerFromIndex(index)
        if row is None:
            grid.ScrollIntoView(grid.Items[index], self._column)
            grid.UpdateLayout()
            row = grid.ItemContainerGenerator.ContainerFromIndex(index)
        return row

    def focus_item(self, item):
        """Select `item`'s row, scroll it into view and put keyboard focus on
        its NAME cell so F2 / Enter work again straight away."""
        index = self._index_of(item)
        if index < 0:
            return
        grid = self._grid
        try:
            grid.SelectedIndex = index
            grid.ScrollIntoView(grid.Items[index], self._column)
            display = _find_named(self._row(index), self._display_name)
            cell = _ancestor(display, DataGridCell, grid)
            if cell is not None:
                cell.Focus()
        except Exception:
            pass

    # ── begin ────────────────────────────────────────────────────────────
    def begin_selected(self):
        item = self._grid.CurrentItem
        if item is None or self._index_of(item) < 0:
            item = self._grid.SelectedItem
        if item is None:
            return False
        return self.begin(item)

    def begin(self, item, root=None):
        if self._busy or self._column is None:
            return False
        if self.is_editing:
            if self._item is item:
                return True
            self._finish(commit=True, reason='switch')

        ok, message = self._can_begin(item)
        if not ok:
            self._report(message)
            return False

        if root is None:
            root = self._row(self._index_of(item))
        editor = _find_named(root, self._editor_name)
        display = _find_named(root, self._display_name)
        if editor is None:
            return False

        self._item, self._editor, self._display = item, editor, display
        editor.Text = getattr(item, self._member, '') or ''
        if display is not None:
            display.Visibility = Visibility.Hidden
        editor.Visibility = Visibility.Visible
        editor.LostKeyboardFocus += self._lost_handler
        self._focus_editor()
        self._report("Renaming '{}' - type the new name, Enter to save, "
                     "Esc to cancel.".format(editor.Text))
        return True

    def _focus_editor(self):
        editor = self._editor
        if editor is None:
            return
        editor.Focus()
        Keyboard.Focus(editor)
        editor.SelectAll()
        if not editor.IsKeyboardFocused:
            # Just made visible: give layout one pass, then focus.
            def _later():
                if self._editor is editor:
                    editor.Focus()
                    Keyboard.Focus(editor)
                    editor.SelectAll()
            editor.Dispatcher.BeginInvoke(DispatcherPriority.Input, Action(_later))

    # ── end ──────────────────────────────────────────────────────────────
    def _close(self):
        """Hide the editor and forget the row. Returns (item, typed text)."""
        item, editor, display = self._item, self._editor, self._display
        self._item = self._editor = self._display = None
        text = ''
        if editor is not None:
            try:
                editor.LostKeyboardFocus -= self._lost_handler
            except Exception:
                pass
            text = editor.Text or ''
            editor.Visibility = Visibility.Collapsed
        if display is not None:
            display.Visibility = Visibility.Visible
        return item, text

    def cancel(self):
        if self.is_editing and not self._busy:
            self._close()
            self._report("Rename cancelled.")

    def _finish(self, commit, reason):
        """reason: 'enter' (stay in edit on a bad name), 'focus' / 'switch' /
        'wheel' (leave edit mode; a bad name is reported and dropped)."""
        if not self.is_editing or self._busy:
            return
        if not commit:
            self.cancel()
            return
        item, text = self._item, self._editor.Text or ''
        ok, clean, message = self._validate(item, text)
        if not ok:
            if reason == 'enter' and message:
                self._report(message)
                self._editor.SelectAll()
                return                      # keep editing: fix it or press Esc
            self._close()
            self._report(message or "")
            return
        self._close()
        if reason == 'enter':
            self._run_apply(item, clean, refocus=True)
        else:
            # Focus is moving (click on another row / a button). Apply after
            # this input event finished routing but BEFORE the next one (the
            # click's mouse-up), so the rename lands first and rows are not
            # rebuilt in the middle of the grid's own mouse handling.
            self._grid.Dispatcher.BeginInvoke(
                DispatcherPriority.Send,
                Action(lambda: self._run_apply(item, clean, refocus=False)))

    def _run_apply(self, item, clean, refocus):
        self._busy = True
        try:
            row_item = self._apply(item, clean)
        finally:
            self._busy = False
        if refocus and row_item is not None:
            self.focus_item(row_item)

    # ── events ───────────────────────────────────────────────────────────
    def _on_double_click(self, sender, e):
        try:
            if e.ChangedButton != MouseButton.Left:
                return
            source = e.OriginalSource
            if self.is_editing and _is_inside(source, self._editor):
                return                      # double-click selects a word
            cell = _ancestor(source, DataGridCell, self._grid)
            if cell is None or getattr(cell.Column, 'SortMemberPath', None) != self._member:
                return
            item = cell.DataContext
            if item is None:
                return
            if self.begin(item, root=cell):
                e.Handled = True
        except Exception as ex:
            self._report("Could not start renaming: {}".format(ex))

    def _on_key(self, sender, e):
        try:
            key = e.Key
            if self.is_editing:
                if key == Key.Return:
                    self._finish(commit=True, reason='enter')
                    e.Handled = True
                elif key == Key.Escape:
                    self.cancel()
                    e.Handled = True
                return
            if Keyboard.Modifiers != _NO_MODIFIERS:
                return
            if key == Key.F2 or key == Key.Return:
                if self.begin_selected():
                    e.Handled = True
        except Exception as ex:
            self._report("Could not rename: {}".format(ex))

    def _on_wheel(self, sender, e):
        # Rows may be recycled while scrolling — never leave an editor behind.
        if self.is_editing:
            self._finish(commit=True, reason='wheel')

    def _on_lost_focus(self, sender, e):
        new_focus = _concrete(e.NewFocus)
        if new_focus is None:
            return      # window deactivated (Alt+Tab): keep editing
        if isinstance(new_focus, (ContextMenu, MenuItem)):
            return      # the TextBox's own Cut/Copy/Paste menu
        if self._editor is not None and _is_inside(new_focus, self._editor):
            return
        self._finish(commit=True, reason='focus')
