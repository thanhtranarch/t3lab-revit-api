# -*- coding: utf-8 -*-
"""ManaWorkset — Dialog and business logic for managing Revit worksets."""

import os
import sys

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._compat import eid_value
from Snippets._host import get_revit_version


import clr
clr.AddReference('System')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Action
from System.ComponentModel import ListSortDirection
from System.Windows import WindowState, Visibility
from System.Windows.Controls import (
    DataGridCell,
    DataGridCellInfo,
    DataGridEditAction,
    DataGridEditingUnit,
    TextBox,
)
from System.Windows.Input import Key, Keyboard
from System.Windows.Media import VisualTreeHelper
from System.Windows.Threading import DispatcherPriority
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    FilteredWorksetCollector,
    Workset,
    WorksetKind,
    Transaction,
    WorksetTable,
    DeleteWorksetSettings,
    DeleteWorksetOption,
    View3D,
    ViewFamilyType,
    ViewFamily,
    WorksetVisibility,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaWorkset.xaml')
DEFAULT_LIST_FILE = os.path.join(os.path.dirname(__file__), 'workset_list.txt')
logger = script.get_logger()
REVIT_VERSION = get_revit_version()

DEFAULT_WORKSET_LIST = [
    "01_Shared Levels and Grids_CORE_OFF",
    "01_Shared Levels and Grids_PH_OFF",
    "01_Shared Levels and Grids_RA_OFF",
    "01_Shared Levels and Grids_SA_OFF",
    "01_Shared Levels and Grids_ROOF_OFF",
    "01_Shared Levels and Grids_for Coordination",
    "02_Link Architecture Models_OFF",
    "02_Link Architecture Models_Attachment",
    "03_Link Structural Models_OFF",
    "04_Link Interior Models_OFF",
    "05_Link Facade Models_OFF",
    "06_Link Site Models_OFF",
    "07_Link Landscape Models_OFF",
    "08_Link Other 3D Data_OFF",
    "09_Link MEP Models_OFF",
    "10_Do not use_OFF",
    "11_Link Cad Consultant_OFF",
    "11_Link Cad Internal_OFF",
    "11_Link Cad Subcon_OFF",
    "12_Link PBU Models",
    "ARC_3DLine-3DText",
    "ARC_3DRoomTag",
    "ARC_Ancillary",
    "ARC_AreaRoomSpace",
    "ARC_BMU",
    "ARC_Ceiling",
    "ARC_DoorAndWindow",
    "ARC_ExteriallWallAndFacade",
    "ARC_ExteriorRoofAndCanopy",
    "ARC_FireProvision",
    "ARC_FloorFinish",
    "ARC_FloorStructural_OFF",
    "ARC_Floor",
    "ARC_Furniture",
    "ARC_Matchline",
    "ARC_Misc",
    "ARC_NonPBU",
    "ARC_NonStructureWall",
    "ARC_ParkingLots",
    "ARC_PlantingSoil",
    "ARC_Railing",
    "ARC_Ramp",
    "ARC_RoadAndPavement",
    "ARC_SanitaryAndDrainage",
    "ARC_Signage",
    "ARC_StructuralCore_OFF",
    "ARC_StructuralColumn_OFF",
    "ARC_StructuralSlabElement_OFF",
    "ARC_StructureWall_OFF",
    "ARC_Temporary_OFF",
    "ARC_Tile Line (Model)",
    "ARC_Toilets",
    "ARC_WallExterior",
    "ARC_WallFinish",
    "ARC_WallInterior",
    "Workset1",
]


class WorksetItem(object):
    """View-model for a single user workset row in the DataGrid."""
    def __init__(self, index, ws, active_id=None):
        self.Number = index
        self.Name = ws.Name
        self.IsOpen = ws.IsOpen
        self.CanEdit = ws.IsEditable
        self.Owner = ws.Owner or ""
        self.IsActive = (
            active_id is not None
            and eid_value(ws.Id) == eid_value(active_id)
        )
        self._id = ws.Id


from Services.workset_service import (
    load_workset_list,
    save_workset_list,
    get_user_worksets,
    get_workset_names,
    get_active_workset_id,
    enable_worksharing,
    create_worksets,
    create_workset_views,
    delete_workset,
    get_workset_by_id,
    workset_blocking_owner,
    is_workset_name_unique,
    rename_workset,
)
from Services.workset_naming import natural_sort_key, validate_workset_rename

# SortMemberPath of the sortable columns (XAML) -> natural sort key of a row.
_SORT_KEYS = {
    "Name": lambda item: natural_sort_key(item.Name),
    "Owner": lambda item: (natural_sort_key(item.Owner), natural_sort_key(item.Name)),
}


def _lcs(str1, str2):
    m, n = len(str1), len(str2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if str1[i - 1] == str2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    result = ""
    i, j = m, n
    while i > 0 and j > 0:
        if str1[i - 1] == str2[j - 1]:
            result = str1[i - 1] + result
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return result


def _find_best_match(target, candidates):
    best, best_len = None, 0
    for c in candidates:
        length = len(_lcs(target, c))
        if length > best_len:
            best_len = length
            best = c
    return best


def _remove_workset(doc, ws_delete_name, ws_move_name, all_worksets):
    ws_del = next((ws for ws in all_worksets if ws.Name == ws_delete_name), None)
    ws_move = next((ws for ws in all_worksets if ws.Name == ws_move_name), None)
    if not ws_del or not ws_move:
        return False
    ok, error = delete_workset(doc, ws_delete_name, ws_move_name)
    if not ok:
        forms.alert("Failed to delete '{}':\n{}".format(ws_delete_name, error))
    return ok




def _confirm(message, title="Confirm"):
    td = TaskDialog(title)
    td.MainContent = message
    td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return td.Show() == TaskDialogResult.Yes


class WorksetManagerWindow(T3WPFWindow):

    def __init__(self, doc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._doc = doc or revit.doc



        self.list_file_path = DEFAULT_LIST_FILE
        self._update_list_path_display()

        # Grid state: rows in displayed order, natural sort, inline rename.
        self._items = []
        self._sort_member = "Name"
        self._sort_desc = False
        self._edit_armed = False        # True only while _begin_rename calls BeginEdit
        self._editing_item = None
        self._edit_box = None
        self._refocus_on_reject = True
        try:
            self.ws_grid.Sorting += self._on_ws_grid_sorting
            self.ws_grid.PreparingCellForEdit += self._on_ws_grid_preparing_edit
            self.ws_grid.LostKeyboardFocus += self._on_ws_grid_lost_focus
        except Exception as ex:
            logger.debug("ws_grid events not wired: {}".format(ex))

        if not self._doc or not self._doc.IsWorkshared:
            self._set_worksharing_state(enabled=False)
        else:
            self._set_worksharing_state(enabled=True)
            self._refresh_worksets()
        self._update_status()

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    def nav_toggle_clicked(self, sender, e):
        try:
            # PythonNet can return different Python wrappers for the same CLR
            # control. Route by the stable XAML name, not Python identity.
            index = {
                "nav_worksets": 0,
                "nav_bulk": 1,
                "nav_views": 2,
            }.get(getattr(sender, "Name", None))
            if index is not None:
                self.tab_control.SelectedIndex = index
        except Exception:
            pass

    def _set_worksharing_state(self, enabled):
        self.btn_enable_ws.IsEnabled = not enabled
        for btn in [self.btn_create, self.btn_delete,
                    self.btn_create_list, self.btn_remove_unused,
                    self.btn_create_views, self.btn_refresh]:
            btn.IsEnabled = enabled
        try:
            self.worksharing_banner.Visibility = (
                Visibility.Collapsed if enabled else Visibility.Visible
            )
        except Exception:
            pass
        if not enabled:
            self.status_text.Text = "Worksharing is not enabled on this document."

    def _refresh_worksets(self, select_names=None):
        """Reload rows from the document, natural-sorted.

        Keeps the current selection (by workset id) unless `select_names` is
        given, in which case those worksets are selected instead.
        """
        if not self._doc or not self._doc.IsWorkshared:
            return
        if select_names:
            wanted = set(select_names)
            keep = lambda item: item.Name in wanted
        else:
            selected_ids = set(eid_value(i._id) for i in self._selected_items())
            keep = lambda item: eid_value(item._id) in selected_ids
        active_id = get_active_workset_id(self._doc)
        worksets = get_user_worksets(self._doc)
        items = [WorksetItem(i + 1, ws, active_id) for i, ws in enumerate(worksets)]
        self._show_items(items, [item for item in items if keep(item)])
        count = len(items)
        self.ws_status.Text = "{} workset{}".format(count, "s" if count != 1 else "")

    # ── Natural sort ──────────────────────────────────────────────────────
    def _selected_items(self):
        try:
            return list(self.ws_grid.SelectedItems)
        except Exception:
            return []

    def _show_items(self, items, selected=None):
        """Sort `items` naturally, renumber "#", bind, restore selection."""
        self._end_pending_edit()
        key = _SORT_KEYS.get(self._sort_member, _SORT_KEYS["Name"])
        items = sorted(items, key=key, reverse=self._sort_desc)
        for i, item in enumerate(items):
            item.Number = i + 1
        self._items = items
        self.ws_grid.ItemsSource = to_items_source(items)
        self._update_sort_glyphs()
        try:
            self.ws_grid_empty.Visibility = (
                Visibility.Collapsed if items else Visibility.Visible)
        except Exception:
            pass
        selected = [i for i in (selected or []) if i in items]
        if selected:
            try:
                self.ws_grid.SelectedItem = selected[0]
                for extra in selected[1:]:
                    self.ws_grid.SelectedItems.Add(extra)
                self.ws_grid.ScrollIntoView(selected[0])
            except Exception as ex:
                logger.debug("Could not restore selection: {}".format(ex))

    def _update_sort_glyphs(self):
        direction = (ListSortDirection.Descending if self._sort_desc
                     else ListSortDirection.Ascending)
        for col in self.ws_grid.Columns:
            try:
                col.SortDirection = (direction if col.SortMemberPath == self._sort_member
                                     else None)
            except Exception:
                pass

    def _on_ws_grid_sorting(self, sender, e):
        """Header click: same natural comparer as the default order."""
        e.Handled = True            # WPF's own sort compares PyObjects, not names
        member = getattr(e.Column, "SortMemberPath", None)
        if member not in _SORT_KEYS:
            return
        if self._editing_item is not None and not self._commit_rename():
            return
        if member == self._sort_member:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_member, self._sort_desc = member, False
        self._show_items(list(self._items), self._selected_items())

    # ── Inline rename (double-click / F2 / Enter on WORKSET NAME) ─────────
    def _name_column(self):
        for col in self.ws_grid.Columns:
            if getattr(col, "SortMemberPath", None) == "Name":
                return col
        return None

    @staticmethod
    def _find_ancestor(node, clr_type):
        while node is not None:
            if isinstance(node, clr_type):
                return node
            try:
                node = VisualTreeHelper.GetParent(node)
            except Exception:
                return None
        return None

    @staticmethod
    def _find_descendant(node, clr_type):
        if node is None:
            return None
        if isinstance(node, clr_type):
            return node
        try:
            count = VisualTreeHelper.GetChildrenCount(node)
        except Exception:
            return None
        for i in range(count):
            found = WorksetManagerWindow._find_descendant(
                VisualTreeHelper.GetChild(node, i), clr_type)
            if found is not None:
                return found
        return None

    def _begin_rename(self, item):
        """Open the WORKSET NAME cell of `item` for editing."""
        col = self._name_column()
        if item is None or col is None or not self._doc:
            return
        ws = get_workset_by_id(self._doc, item._id)
        if ws is None:
            self.status_text.Text = (
                "Workset '{}' no longer exists. Click Refresh to reload the list."
                .format(item.Name))
            return
        owner = workset_blocking_owner(self._doc, ws)
        if owner:
            self.status_text.Text = (
                "Cannot rename '{}': it is owned by {}. Ask {} to relinquish it, "
                "then click Refresh.".format(item.Name, owner, owner))
            return
        try:
            self.ws_grid.ScrollIntoView(item, col)
            self.ws_grid.CurrentCell = DataGridCellInfo(item, col)
            self._edit_armed = True
            self.ws_grid.BeginEdit()
        except Exception as ex:
            logger.debug("BeginEdit failed: {}".format(ex))
        finally:
            self._edit_armed = False

    def ws_grid_beginning_edit(self, sender, e):
        # Single click on a selected cell, typing, F2 handled natively: all
        # cancelled. Only _begin_rename (double-click / F2 / Enter) arms it.
        if not self._edit_armed or getattr(e.Column, "SortMemberPath", None) != "Name":
            e.Cancel = True
            return
        self._editing_item = e.Row.Item
        self.status_text.Text = "Renaming '{}' (Enter to apply, Esc to cancel).".format(
            self._editing_item.Name)

    def _on_ws_grid_preparing_edit(self, sender, e):
        presenter = e.EditingElement
        window = self

        def _focus():
            box = WorksetManagerWindow._find_descendant(presenter, TextBox)
            if box is None:
                return
            window._edit_box = box
            box.Focus()
            Keyboard.Focus(box)
            box.SelectAll()

        self.Dispatcher.BeginInvoke(DispatcherPriority.Input, Action(_focus))

    def _refocus_edit_box(self):
        box = self._edit_box
        if box is None:
            return

        def _focus():
            try:
                Keyboard.Focus(box)
                box.SelectAll()
            except Exception:
                pass

        self.Dispatcher.BeginInvoke(DispatcherPriority.Input, Action(_focus))

    def ws_grid_double_click(self, sender, e):
        cell = self._find_ancestor(getattr(e, "OriginalSource", None), DataGridCell)
        if cell is None or cell.IsEditing:
            return
        if getattr(cell.Column, "SortMemberPath", None) != "Name":
            return
        e.Handled = True
        self._begin_rename(cell.DataContext)

    def ws_grid_preview_key_down(self, sender, e):
        if self._editing_item is not None:
            if e.Key in (Key.Enter, Key.Return):
                e.Handled = True        # also keeps the IsDefault Create… button out of it
                self._commit_rename()
            elif e.Key == Key.Escape:
                e.Handled = True
                self._cancel_rename()
            return
        if e.Key in (Key.F2, Key.Enter, Key.Return):
            item = self.ws_grid.SelectedItem
            if item is None:
                return
            e.Handled = True
            self._begin_rename(item)

    def _handle_esc_key(self, sender, args):
        # T3WPFWindow closes the window on Esc (window-level PreviewKeyDown runs
        # before the grid's). While renaming, Esc cancels the edit instead.
        try:
            if args.Key == Key.Escape and getattr(self, "_editing_item", None) is not None:
                args.Handled = True
                self._cancel_rename()
                return
        except Exception:
            pass
        T3WPFWindow._handle_esc_key(self, sender, args)

    def _commit_rename(self, refocus=True):
        """True when the edit ended (renamed, unchanged or refused by Revit)."""
        self._refocus_on_reject = refocus
        try:
            done = bool(self.ws_grid.CommitEdit(DataGridEditingUnit.Row, True))
        except Exception as ex:
            logger.debug("CommitEdit failed: {}".format(ex))
            done = False
        finally:
            self._refocus_on_reject = True
        return done

    def _cancel_rename(self):
        name = self._editing_item.Name if self._editing_item is not None else ""
        try:
            self.ws_grid.CancelEdit(DataGridEditingUnit.Row)
        except Exception:
            pass
        self._editing_item = None
        self._edit_box = None
        self.status_text.Text = "Rename of '{}' cancelled.".format(name)

    def _end_pending_edit(self):
        """Before rebinding the grid: commit an open rename, else drop it."""
        if self._editing_item is None:
            return
        try:
            if not self.ws_grid.CommitEdit(DataGridEditingUnit.Row, True):
                self.ws_grid.CancelEdit(DataGridEditingUnit.Row)
        except Exception:
            pass
        self._editing_item = None
        self._edit_box = None

    def _on_ws_grid_lost_focus(self, sender, e):
        # Focus moved out of the grid while renaming (click on a button, the
        # sidebar...) -> commit. Moves inside the grid are committed by WPF.
        # NewFocus None = the window lost activation: keep the edit open.
        if self._editing_item is None:
            return
        try:
            new_focus = e.NewFocus
            if new_focus is None or self.ws_grid.IsAncestorOf(new_focus):
                return
        except Exception:
            return
        # A refused name stays in edit mode with its message; focus is not
        # pulled back so the click the user just made still lands.
        self._commit_rename(refocus=False)

    def ws_grid_cell_edit_ending(self, sender, e):
        item = self._editing_item or e.Row.Item
        if e.EditAction != DataGridEditAction.Commit or item is None:
            self._editing_item = None
            self._edit_box = None
            return
        box = self._edit_box or self._find_descendant(e.EditingElement, TextBox)
        typed = box.Text if box is not None else item.Name
        old = item.Name

        ok, new, message = validate_workset_rename(
            old, typed, [i.Name for i in self._items],
            lambda name: is_workset_name_unique(self._doc, name))
        if not ok and message:
            e.Cancel = True             # stay in edit mode so the user can fix it
            self.status_text.Text = message
            if getattr(self, "_refocus_on_reject", True):
                self._refocus_edit_box()
            return

        self._editing_item = None
        self._edit_box = None
        if not ok:                      # unchanged name: nothing to do
            self.status_text.Text = "Name of '{}' unchanged.".format(old)
            return

        renamed, error = rename_workset(self._doc, item._id, new)
        if not renamed:
            # Model untouched (rolled back); the cell shows the old name again.
            self.status_text.Text = "Could not rename workset '{}': {}".format(old, error)
            return

        item.Name = new
        ws = get_workset_by_id(self._doc, item._id)
        if ws is not None:
            item.Owner = ws.Owner or ""
            item.CanEdit = ws.IsEditable
            item.IsOpen = ws.IsOpen
        self.status_text.Text = "Renamed workset '{}' to '{}'.".format(old, new)

        def _resort():
            # After the edit transaction ends: re-sort, keep the row selected.
            self._show_items(list(self._items), [item])

        self.Dispatcher.BeginInvoke(DispatcherPriority.Background, Action(_resort))

    def _update_status(self):
        if not self._doc or not self._doc.IsWorkshared:
            self.status_text.Text = "Not workshared — enable worksharing first."
        else:
            count = len(get_user_worksets(self._doc))
            self.status_text.Text = "Ready  —  {} user workset{} loaded.".format(
                count, "s" if count != 1 else "")

    def _update_list_path_display(self):
        try:
            self.list_path_text.Text = self.list_file_path
        except Exception:
            pass

    def btn_enable_ws_click(self, sender, e):
        if not _confirm(
            "Enable worksharing on this document?\n\n"
            "This will create two default worksets:\n"
            "  • _SHARED LEVELS & GRIDS\n"
            "  • _ARCHITECT",
            title="Enable Worksharing"
        ):
            return
        if enable_worksharing(self._doc):
            self._set_worksharing_state(enabled=True)
            self._refresh_worksets()
            self.status_text.Text = "Worksharing enabled successfully."

    def btn_create_click(self, sender, e):
        name = forms.ask_for_string(
            prompt="Enter a name for the new workset:",
            title="Create Workset",
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return
        existing = get_workset_names(self._doc)
        if name in existing:
            forms.alert("Workset '{}' already exists.".format(name), title="Duplicate")
            return
        created = create_worksets(self._doc, [name], existing)
        if created:
            self._refresh_worksets(select_names=created)
            self.status_text.Text = "Created workset: {}.".format(name)

    def btn_delete_click(self, sender, e):
        selected = list(self.ws_grid.SelectedItems)
        if not selected:
            forms.alert("Select one or more worksets to delete.", title="No Selection")
            return
        names = [item.Name for item in selected]
        if not _confirm(
            "Delete {} workset(s)?\n\n{}\n\n"
            "Elements will be moved to the closest matching workset.".format(
                len(names), "\n".join("  • " + n for n in names)),
            title="Delete Workset(s)"
        ):
            return
        all_names = get_workset_names(self._doc)
        deleted = 0
        for name in names:
            keep = [n for n in all_names if n != name]
            dest = _find_best_match(name, keep)
            if dest:
                current = get_user_worksets(self._doc)
                if _remove_workset(self._doc, name, dest, current):
                    all_names = keep
                    deleted += 1
            else:
                forms.alert(
                    "Cannot delete '{}': no other workset to move elements to.".format(name),
                    title="Delete Failed"
                )
        self._refresh_worksets()
        self.status_text.Text = "Deleted {} of {} workset(s).".format(deleted, len(names))

    def btn_import_list_click(self, sender, e):
        picked = forms.pick_file(file_ext="txt")
        if not picked:
            return
        self.list_file_path = picked
        self._update_list_path_display()
        self.status_text.Text = "Workset list source set to: {}".format(picked)

    def btn_create_list_click(self, sender, e):
        workset_list = load_workset_list(self.list_file_path)
        existing = get_workset_names(self._doc)
        to_create = [n for n in workset_list if n not in existing]
        if not to_create:
            forms.alert(
                "All {} worksets in the list already exist.".format(len(workset_list)),
                title="Nothing to Create"
            )
            return
        if not _confirm(
            "Create {} new workset(s) from:\n{}?".format(len(to_create), self.list_file_path),
            title="Create from List"
        ):
            return
        created = create_worksets(self._doc, to_create, existing)
        self._refresh_worksets()
        self.status_text.Text = "Created {} workset(s) from list.".format(len(created))

    def btn_remove_unused_click(self, sender, e):
        workset_list = load_workset_list(self.list_file_path)
        existing_ws = get_user_worksets(self._doc)
        existing_names = [ws.Name for ws in existing_ws]
        unused = [ws for ws in existing_ws if ws.Name not in workset_list]
        if not unused:
            forms.alert("No unused worksets found.", title="Remove Unused")
            return
        selected_names = forms.SelectFromList.show(
            sorted([ws.Name for ws in unused]),
            title="Remove Unused Worksets",
            button_name="Remove Selected",
            multiselect=True,
        )
        if not selected_names:
            return
        keep_names = [n for n in existing_names if n not in selected_names]
        deleted = 0
        for name in selected_names:
            dest = _find_best_match(name, keep_names)
            if dest:
                current = get_user_worksets(self._doc)
                if _remove_workset(self._doc, name, dest, current):
                    deleted += 1
            else:
                logger.warning("No destination for '{}', skipping.".format(name))
        self._refresh_worksets()
        self.status_text.Text = "Removed {} workset(s).".format(deleted)

    def btn_create_views_click(self, sender, e):
        self.status_text.Text = "Creating workset views…"
        created, skipped, error = create_workset_views(self._doc)
        if error:
            forms.alert("Error: {}".format(error), title="Create Workset Views")
            self.status_text.Text = "Error creating views."
            return
        msg = "Created {} view(s).".format(len(created))
        if skipped:
            msg += "  Skipped {} (already exist).".format(len(skipped))
        self.status_text.Text = msg

    def btn_refresh_click(self, sender, e):
        self._refresh_worksets()
        self._update_status()


def quick_remove_unused(doc=None):
    d = doc or revit.doc
    if not d:
        TaskDialog.Show("Workset Manager", "No active Revit document found.")
        return
    if not d.IsWorkshared:
        TaskDialog.Show("Workset Manager", "Document is not workshared.")
        return

    workset_list = load_workset_list()
    existing_worksets = get_user_worksets(d)
    existing_names = [ws.Name for ws in existing_worksets]
    unused = [ws for ws in existing_worksets if ws.Name not in workset_list]

    if not unused:
        TaskDialog.Show("Workset Manager", "No unused worksets found.")
        return

    selected_names = forms.SelectFromList.show(
        sorted([ws.Name for ws in unused]),
        title="Remove Unused Worksets",
        button_name="Remove Selected",
        multiselect=True,
    )
    if not selected_names:
        return

    keep_names = [n for n in existing_names if n not in selected_names]
    deleted = 0
    for name in selected_names:
        dest = _find_best_match(name, keep_names)
        if dest:
            current = get_user_worksets(d)
            if _remove_workset(d, name, dest, current):
                deleted += 1
        else:
            logger.warning("No destination found for '{}', skipping".format(name))

    TaskDialog.Show(
        "Workset Manager",
        "Removed {} of {} selected workset(s).".format(deleted, len(selected_names)),
    )


def show_workset_manager(doc=None):
    d = doc or revit.doc
    if not d:
        TaskDialog.Show("Workset Manager", "No active Revit document found.")
        return
    if not d.IsWorkshared:
        res = TaskDialog.Show(
            "Workset Manager",
            "Document is not workshared.\n\nWould you like to enable worksharing now?",
            TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
        )
        if res == TaskDialogResult.Yes:
            if enable_worksharing(d):
                win = WorksetManagerWindow(d)
                win.ShowDialog()
        return
    win = WorksetManagerWindow(d)
    win.ShowDialog()
