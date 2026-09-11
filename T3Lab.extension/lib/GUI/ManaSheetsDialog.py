# -*- coding: utf-8 -*-
"""
Sheet Manager Dialog
Unified Sheet Manager including sheet lists and re-numbering inside a Lumina UI.
"""

import os
import sys
import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

import System
from System.Windows import (Thickness, GridLength, GridUnitType,
                             MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult, WindowState)
from System.Windows.Controls import (RowDefinition, ColumnDefinition, Border,
                                      StackPanel, TextBlock, TextBox, Button,
                                      ComboBox, ComboBoxItem, DataGrid, Orientation,
                                      DataGridTextColumn, ScrollViewer)
from System.Windows.Media import SolidColorBrush
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs

# CRITICAL: Import WPF Grid BEFORE Revit wildcard import
from System.Windows.Controls import Grid as WPFGrid

from pyrevit import revit, DB, forms
from GUI.WPF_Base import T3WPFWindow
from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet, Transaction

from Snippets._compat import eid_value

# Add Services/SheetManager directory to sys.path so we can import modules
GUI_DIR = os.path.dirname(__file__)
EXT_DIR = os.path.dirname(os.path.dirname(GUI_DIR))
SERVICES_DIR = os.path.join(EXT_DIR, 'lib', 'Services', 'SheetManager')
if SERVICES_DIR not in sys.path:
    sys.path.append(SERVICES_DIR)

# Import Sheet Manager services
try:
    from Services.SheetManager.sheet_core.revit_service import RevitService
    from Services.SheetManager.sheet_core.data_models import ChangeTracker, SheetModel
    from Services.SheetManager.excel_service import ExcelService
    from Services.SheetManager.viewsheet_sets_service import ViewSheetSetsService
    from Services.SheetManager.place_views_service import PlaceViewsService
    from Services.SheetManager.custom_parameters_service import CustomParametersService
except Exception as e:
    # Print error in case imports fail
    print("Error importing services: {}".format(e))

try:
    from GUI import RevitTheme as _theme
except Exception:
    try:
        import RevitTheme as _theme
    except Exception:
        _theme = None

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'ManaSheets.xaml')

from GUI.ProgressPauseMixin import ProgressPauseMixin
from GUI.DataGridColumnFilter import ColumnFilterController
from GUI import GridPendingEdits as _pend

# Row fields the grid lets the user edit. Each needs a matching `dirty_<field>`
# flag on the row and a CellStyle DataTrigger in ManaSheets.xaml bound to it,
# otherwise the amber "waiting for Apply" highlight never shows.
SHEET_EDIT_FIELDS = ("sheet_number", "sheet_name", "designed_by",
                     "checked_by", "approved_by", "drawn_by")


# =====================================================
# RENUMBER WRAPPER MODEL
# =====================================================

try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class RenumberItem(_Reactive):
    """Wrapper class for Sheet Renumber preview grid"""
    def __init__(self, sheet_model):
        self._property_changed_handlers = []
        self.sheet_model = sheet_model
        self.orig_number = sheet_model.sheet_number
        self.name = sheet_model.sheet_name
        self._preview_number = sheet_model.sheet_number
        self._is_selected = False

    @property
    def preview_number(self):
        return self._preview_number
    @preview_number.setter
    def preview_number(self, value):
        if self._preview_number != value:
            self._preview_number = value
            self.OnPropertyChanged("preview_number")

    @property
    def IsSelected(self):
        return self._is_selected
    @IsSelected.setter
    def IsSelected(self, value):
        if self._is_selected != value:
            self._is_selected = value
            self.OnPropertyChanged("IsSelected")

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        try:
            if PropertyChangedEventArgs is not None:
                args = PropertyChangedEventArgs(property_name)
                for handler in list(self._property_changed_handlers):
                    try:
                        handler(self, args)
                    except Exception:
                        pass
        except Exception:
            pass


# =====================================================
# MAIN WINDOW CONTROLLER
# =====================================================

class SheetManagerWindow(T3WPFWindow):

    # ProgressPauseMixin — ManaSheets.xaml status-bar progress panel
    PP_PANEL      = "ms_progress_panel"
    PP_BAR        = "ms_pb"
    PP_PAUSE      = "ms_btn_pause"
    PP_STOP       = "ms_btn_stop"
    PP_PAUSE_ICON = "ms_btn_pause_icon"
    PP_PAUSE_TEXT = "ms_btn_pause_label"
    PP_STATUS     = "txt_status_bar"
    PP_STOP_MSG   = u"Stopping… finishing current sheet"

    def __init__(self):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = revit.doc

        self._adopt_host_font()
        self._apply_theme()
        
        # Initialize Core Services
        self.revit_service = RevitService(self.doc)
        self.change_tracker = ChangeTracker()
        
        try:
            self.excel_service = ExcelService()
        except:
            self.excel_service = None
            
        try:
            self.sheet_sets_service = ViewSheetSetsService(self.doc)
        except:
            self.sheet_sets_service = None
            
        try:
            self.place_views_service = PlaceViewsService(self.doc)
        except:
            self.place_views_service = None
            
        try:
            self.params_service = CustomParametersService(self.doc, __revit__.Application)
        except:
            self.params_service = None

        # Data collection
        self.all_sheets = []
        self.filtered_sheets = ObservableCollection[Object]()
        self.renumber_items = ObservableCollection[Object]()
        
        # Chrome controls
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close.Click += self._close_chrome
        
        # Radio Tab navigation
        self.nav_sheets.Checked += self._on_tab_changed
        self.nav_renumber.Checked += self._on_tab_changed
        
        # Bind events: SHEETS Tab
        self.sheets_search_box.TextChanged += self._on_sheets_search_changed
        self.sheets_filter_combo.SelectionChanged += self._on_sheets_filter_changed
        self.sheets_select_all_btn.Click += self._on_sheets_select_all
        self.sheets_clear_btn.Click += self._on_sheets_clear_all

        # Progress Pause/Stop (ProgressPauseMixin)
        if getattr(self, "ms_btn_pause", None) is not None:
            self.ms_btn_pause.Click += self.pause_resume_clicked
        if getattr(self, "ms_btn_stop", None) is not None:
            self.ms_btn_stop.Click += self.stop_clicked

        self.sheets_sets_btn.Click += self._on_sheets_sets
        self.sheets_place_views_btn.Click += self._on_sheets_place_views
        self.sheets_custom_params_btn.Click += self._on_sheets_custom_params
        
        self.sheets_excel_btn.Click += self._on_sheets_excel
        self.sheets_refresh_btn.Click += self._on_sheets_refresh
        self.sheets_apply_btn.Click += self._on_sheets_apply
        self.sheets_close_btn.Click += self._on_close
        
        self.sheets_grid.SelectionChanged += self._on_sheets_selection_changed
        self.sheets_grid.CellEditEnding += self._on_sheets_cell_edit
        self.sheets_grid.ItemsSource = self.filtered_sheets
        
        # Bind events: RENUMBER Tab
        self.renum_refresh_btn.Click += self._on_renum_refresh
        self.renum_preview_btn.Click += self._on_renum_preview
        self.renum_run_btn.Click += self._on_renum_run
        self.renum_close_btn.Click += self._on_close
        self.renum_grid.ItemsSource = self.renumber_items

        # Column filters (nút phễu trên header — GUI/DataGridColumnFilter.py).
        # Chỉ gắn cho bảng SHEETS: bảng Renumber là preview của chính danh sách
        # đã lọc ở tab SHEETS, lọc thêm ở đó sẽ làm lệch dải số sinh ra.
        self.sheets_col_filter = ColumnFilterController(
            self, self.sheets_grid,
            columns=[("NUMBER", "sheet_number"),
                     ("SHEET NAME", "sheet_name"),
                     ("DESIGNED BY", "designed_by"),
                     ("CHECKED BY", "checked_by"),
                     ("APPROVED BY", "approved_by"),
                     ("DRAWN BY", "drawn_by")],
            source=lambda: self.all_sheets,
            on_changed=self._apply_sheets_filters,
            status_setter=self._set_status)

        # Load initial data
        self._load_sheets_data()
        self._apply_sheets_filters()
        self._load_renumber_preview_data()

        # Force initial tab content to render: nav_sheets.IsChecked was already
        # True when the XAML was parsed, so its Checked event fired before this
        # handler was wired above and tab_control.SelectedIndex was never set.
        self.tab_control.SelectedIndex = 0

    def _set_status(self, text):
        """Ghi một câu trạng thái ra footer."""
        try:
            self.txt_status_bar.Text = text
        except Exception:
            pass

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

    # ── Chrome Event Handlers ────────────────────────────────────
    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def _close_chrome(self, sender, e):
        self.Close()
        
    def _on_close(self, sender, args):
        self.Close()

    def _on_tab_changed(self, sender, e):
        if not hasattr(self, 'tab_control'):
            return
        if self.nav_sheets.IsChecked:
            self.tab_control.SelectedIndex = 0
        elif self.nav_renumber.IsChecked:
            self.tab_control.SelectedIndex = 1
            # Refresh renumber tab data whenever switching to it
            self._load_renumber_preview_data()

    # ── SHEETS Tab Logics ─────────────────────────────────────────
    def _load_sheets_data(self):
        self.all_sheets = self.revit_service.get_all_sheets()
        for item in self.all_sheets:
            _pend.init_pending(item, SHEET_EDIT_FIELDS)
        self.change_tracker.clear_all()
        self._update_sheets_summary()

    def _apply_sheets_filters(self):
        self.filtered_sheets.Clear()
        
        search_text = self.sheets_search_box.Text.lower() if self.sheets_search_box.Text else ""
        filter_index = self.sheets_filter_combo.SelectedIndex
        
        for item in self.all_sheets:
            # Search
            if search_text:
                if search_text not in item.sheet_number.lower() and \
                   search_text not in item.sheet_name.lower():
                    continue
            # Placeholder Filter
            if filter_index == 1: # Placeholder Only
                # Check direct property on element
                if not item.element.IsPlaceholder:
                    continue
            elif filter_index == 2: # Non-Placeholder Only
                if item.element.IsPlaceholder:
                    continue
            # Bộ lọc theo cột (nút phễu trên header)
            if not self.sheets_col_filter.passes(item):
                continue

            self.filtered_sheets.Add(item)

        self._update_sheets_summary()
        self.sheets_col_filter.refresh_glyphs()

    def _update_sheets_summary(self):
        self.sheets_total_text.Text = str(len(self.all_sheets))
        self.sheets_selected_text.Text = str(len([s for s in self.filtered_sheets if s.is_selected]))
        
        categories = set()
        for s in self.all_sheets:
            # Categorization based designed_by or drawn_by if any
            if s.designed_by and s.designed_by != "-":
                categories.add(s.designed_by)
        self.sheets_categories_text.Text = str(len(categories)) if categories else "1"
        
        # Count the amber cells, not the tracked rows: two edits on one sheet is
        # two pending changes to the person looking at the grid.
        pending_cells = _pend.pending_count(self.all_sheets)
        self.sheets_changes_text.Text = str(pending_cells)
        try:
            self.sheets_apply_btn.IsEnabled = pending_cells > 0
        except Exception:
            pass

    def _on_sheets_search_changed(self, sender, args):
        self._apply_sheets_filters()

    def _on_sheets_filter_changed(self, sender, args):
        self._apply_sheets_filters()

    def _on_sheets_select_all(self, sender, args):
        for s in self.filtered_sheets:
            s.is_selected = True
        self.sheets_grid.Items.Refresh()
        self._update_sheets_summary()

    def _on_sheets_clear_all(self, sender, args):
        for s in self.filtered_sheets:
            s.is_selected = False
        self.sheets_grid.Items.Refresh()
        self._update_sheets_summary()

    def _on_sheets_selection_changed(self, sender, args):
        self._update_sheets_summary()

    def sheets_row_checkbox_changed(self, sender, args):
        """Fires immediately when a row's selection checkbox is toggled (template column,
        so CellEditEnding does not fire for it) — keep the SELECTED counter live."""
        self._update_sheets_summary()

    def _on_sheets_cell_edit(self, sender, args):
        """Stage the edit and paint the cell amber. Apply Changes writes it.

        Dispatch is on the column's BINDING PATH. This used to compare
        `column.Header` against "Sheet Number" while the XAML said "NUMBER", so
        no branch ever matched: nothing was tracked, the amber never appeared,
        and Apply kept reporting "No pending changes to apply" however much the
        user had typed.
        """
        from System.Windows.Controls import DataGridEditAction
        if args.EditAction == DataGridEditAction.Cancel:
            return

        try:
            item = args.Row.Item
            field = _pend.column_key(args.Column)
            if not field or field not in SHEET_EDIT_FIELDS:
                return

            typed = _pend.editor_text(args.EditingElement)
            current = getattr(item, field, None)

            if _pend.same_text(typed, current):
                _pend.unstage(item, field)      # typed it back to how it was
            elif field in ("sheet_number", "sheet_name") and not (typed or "").strip():
                # Revit refuses both outright, so the cell is bounced here
                # rather than at Apply time when it is buried among the rest.
                _pend.revert_editor(args.EditingElement, current)
                self._set_status(
                    "A sheet number and a sheet name cannot be empty — "
                    "the cell was left unchanged.")
                return
            else:
                _pend.stage(item, field, typed)
                # The binding writes `typed` into the row right after this
                # returns, so the row already carries the new value; the tracker
                # just needs to know the row is dirty for Apply to pick it up.
                self.change_tracker.track_modification(item)

            if not _pend.has_pending(item):
                self._untrack(item)

            self._refresh_sheets_grid_later()
            self._update_sheets_summary()
        except Exception as e:
            self._set_status("Could not read that edit: {}".format(str(e)))

    def _untrack(self, item):
        """Drop a row from the tracker once its last amber cell is gone."""
        try:
            item.check_if_modified()
        except Exception:
            pass
        try:
            self.change_tracker.modified_items.remove(item)
        except Exception:
            pass

    def _refresh_sheets_grid_later(self):
        """Redraw once the edit has finished committing.

        SheetModel carries no INotifyPropertyChanged, so the amber DataTrigger
        only re-reads `dirty_<field>` on a refresh — and calling Refresh() while
        the cell is still committing throws "not allowed during an EditItem
        transaction". Hence the trip through the dispatcher.
        """
        try:
            from System.Windows.Threading import DispatcherPriority
            from System import Action
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(lambda: self.sheets_grid.Items.Refresh()))
        except Exception:
            try:
                self.sheets_grid.Items.Refresh()
            except Exception:
                pass

    def _on_sheets_sets(self, sender, args):
        if self.sheet_sets_service:
            try:
                from Services.SheetManager.viewsheet_sets_dialog import ViewSheetSetsDialog
                dialog = ViewSheetSetsDialog(self.doc, self.sheet_sets_service)
                dialog.ShowDialog()
                self._load_sheets_data()
                self._apply_sheets_filters()
            except Exception as e:
                MessageBox.Show("Error showing ViewSheet Sets dialog:\n{}".format(str(e)), "Error")

    def _on_sheets_place_views(self, sender, args):
        if self.place_views_service:
            try:
                from Services.SheetManager.place_views_dialog import PlaceViewsDialog
                dialog = PlaceViewsDialog(self.doc, self.place_views_service)
                dialog.ShowDialog()
                self._load_sheets_data()
                self._apply_sheets_filters()
            except Exception as e:
                MessageBox.Show("Error showing Place Views dialog:\n{}".format(str(e)), "Error")

    def _on_sheets_custom_params(self, sender, args):
        if self.params_service:
            try:
                from Services.SheetManager.custom_parameters_dialog import CustomParametersDialog
                dialog = CustomParametersDialog(self.doc, self.params_service)
                dialog.ShowDialog()
                self._load_sheets_data()
                self._apply_sheets_filters()
            except Exception as e:
                MessageBox.Show("Error showing Custom Parameters dialog:\n{}".format(str(e)), "Error")

    def _on_sheets_excel(self, sender, args):
        if not self.excel_service:
            MessageBox.Show("Excel Service is not initialized.", "Error")
            return
            
        from System.Windows.Forms import SaveFileDialog, OpenFileDialog, DialogResult
        result = MessageBox.Show(
            "Do you want to EXPORT the current list to Excel?\n(Select No if you want to IMPORT from Excel)",
            "Excel Import/Export", MessageBoxButton.YesNoCancel, MessageBoxImage.Question
        )
        
        if result == MessageBoxResult.Yes:
            # EXPORT
            sfd = SaveFileDialog()
            sfd.Filter = "Excel Files (*.xlsx)|*.xlsx"
            sfd.FileName = "T3Lab_SheetManager_Export.xlsx"
            if sfd.ShowDialog() == DialogResult.OK:
                try:
                    sheets_list = list(self.filtered_sheets)
                    # Convert model properties to dictionary format for excel_service
                    data_to_export = []
                    for s in sheets_list:
                        data_to_export.append({
                            "sheet_number": s.sheet_number,
                            "sheet_name": s.sheet_name,
                            "designed_by": s.designed_by,
                            "checked_by": s.checked_by,
                            "drawn_by": s.drawn_by,
                            "approved_by": s.approved_by,
                            "id": eid_value(s.id)
                        })
                    self.excel_service.export_sheets(sfd.FileName, data_to_export)
                    MessageBox.Show("Successfully exported sheets to Excel.", "Export Successful")
                except Exception as ex:
                    MessageBox.Show("Error exporting Excel: {}".format(str(ex)), "Error")
                    
        elif result == MessageBoxResult.No:
            # IMPORT
            ofd = OpenFileDialog()
            ofd.Filter = "Excel Files (*.xlsx)|*.xlsx"
            if ofd.ShowDialog() == DialogResult.OK:
                try:
                    imported_data = self.excel_service.import_sheets(ofd.FileName)
                    if not imported_data:
                        MessageBox.Show("No valid sheet updates found in Excel file.", "Excel Import")
                        return
                        
                    t = Transaction(self.doc, "Excel Sync Sheet Parameters")
                    t.Start()
                    
                    sheets_by_id = {eid_value(s.id): s for s in self.all_sheets}
                    sheets_by_number = {}
                    for s in self.all_sheets:
                        sheets_by_number.setdefault(str(s.sheet_number), s)
                    success = 0
                    failed = 0

                    self.begin_progress(len(imported_data))
                    for _idx, row in enumerate(imported_data):
                        if self.is_cancelled:
                            break
                        self.step_progress(_idx, "Syncing sheet {}/{}...".format(_idx + 1, len(imported_data)))
                        # Match by ElementId first (exported files carry it, so
                        # number/name can be freely edited); fall back to the
                        # original sheet number for hand-made files without an ID.
                        item = sheets_by_id.get(row.get("id"))
                        if item is None:
                            item = sheets_by_number.get(str(row.get("sheet_number", "")))
                        if item is not None:
                            try:
                                if "sheet_number" in row:
                                    item.sheet_number = str(row["sheet_number"])
                                if "sheet_name" in row:
                                    item.sheet_name = str(row["sheet_name"])
                                
                                # Assign standard parameters
                                for param_name in ["designed_by", "checked_by", "drawn_by", "approved_by"]:
                                    if param_name in row:
                                        setattr(item, param_name, str(row[param_name]))
                                        
                                # Lookup and update Revit parameters
                                element = item.element
                                for map_name, rev_name in [("designed_by", "Designed By"), 
                                                           ("checked_by", "Checked By"), 
                                                           ("drawn_by", "Drawn By"), 
                                                           ("approved_by", "Approved By")]:
                                    val = row.get(map_name)
                                    if val:
                                        param = element.LookupParameter(rev_name)
                                        if param and not param.IsReadOnly:
                                            param.Set(str(val))
                                            
                                # Apply sheet number & name update in transaction
                                self.revit_service.update_sheet(item)
                                item.commit_changes()
                                success += 1
                            except:
                                failed += 1
                                
                    t.Commit()
                    _cancelled = self.is_cancelled
                    self.end_progress()
                    _pfx = "Excel Sync Cancelled" if _cancelled else "Excel Sync Completed"
                    MessageBox.Show("{}.\nUpdated: {}\nFailed: {}".format(_pfx, success, failed), "Excel Import")
                    self._on_sheets_refresh(None, None)
                except Exception as ex:
                    self.end_progress()
                    MessageBox.Show("Error importing Excel: {}".format(str(ex)), "Error")

    def _on_sheets_refresh(self, sender, args):
        self._load_sheets_data()
        self._apply_sheets_filters()

    def _on_sheets_apply(self, sender, args):
        # Drive off the staged cells, not the tracker alone: the tracker is a
        # row-level flag, while what the user sees waiting on screen is the
        # amber cells. Keeping the two in step is what makes the counter honest.
        staged = _pend.pending_rows(self.all_sheets)
        for item in staged:
            self.change_tracker.track_modification(item)
        if not staged:
            MessageBox.Show("No pending changes to apply.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return

        modified = len(staged)
        cells = _pend.pending_count(self.all_sheets)
        msg = "Apply {} edited cell{} on {} sheet{}?".format(
            cells, "" if cells == 1 else "s",
            modified, "" if modified == 1 else "s")

        result = MessageBox.Show(msg, "Confirm Changes", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result == MessageBoxResult.Yes:
            t = Transaction(self.doc, "Apply Sheet Manager Changes")
            t.Start()
            self.begin_progress(len(self.change_tracker.modified_items), disable=[sender])
            try:
                success = 0
                failed = 0
                for _idx, item in enumerate(self.change_tracker.modified_items):
                    if self.is_cancelled:
                        break
                    self.step_progress(_idx, "Applying sheet {}/{}...".format(
                        _idx + 1, len(self.change_tracker.modified_items)))
                    try:
                        # Update standard params in Revit
                        element = item.element
                        for val, p_name in [(item.designed_by, "Designed By"),
                                            (item.checked_by, "Checked By"),
                                            (item.drawn_by, "Drawn By"),
                                            (item.approved_by, "Approved By")]:
                            if val and val != "-":
                                p = element.LookupParameter(p_name)
                                if p and not p.IsReadOnly:
                                    p.Set(val)
                                    
                        # Update Number & Name
                        if self.revit_service.update_sheet(item):
                            item.commit_changes()
                            _pend.clear_pending(item)
                            success += 1
                        else:
                            failed += 1
                    except:
                        failed += 1
                        
                t.Commit()
                _pfx = "Cancelled — updated" if self.is_cancelled else "Successfully updated"
                msg = "{}: {}".format(_pfx, success)
                if failed > 0:
                    msg += "\nFailed: {}".format(failed)
                MessageBox.Show(msg, "Apply Complete", MessageBoxButton.OK, MessageBoxImage.Information)
                self._load_sheets_data()
                self._apply_sheets_filters()
            except Exception as e:
                t.RollBack()
                MessageBox.Show("Error applying changes: {}".format(str(e)), "Error")
            finally:
                self.end_progress()

    # ── RENUMBER Tab Logics ───────────────────────────────────────
    def _load_renumber_preview_data(self):
        self.renumber_items.Clear()
        for s in self.filtered_sheets:
            self.renumber_items.Add(RenumberItem(s))

    def _on_renum_refresh(self, sender, args):
        self._load_sheets_data()
        self._apply_sheets_filters()
        self._load_renumber_preview_data()

    def _on_renum_preview(self, sender, args):
        selected_preview = [item for item in self.renumber_items if item.IsSelected]
        if not selected_preview:
            MessageBox.Show("Please select sheets in the preview grid first.", "Info")
            return
            
        prefix = self.renum_prefix_box.Text or ""
        suffix = self.renum_suffix_box.Text or ""
        
        try:
            start_num = int(self.renum_start_box.Text)
            step_num = int(self.renum_step_box.Text)
        except ValueError:
            MessageBox.Show("Starting Number and Increment Step must be integers.", "Error")
            return

        for index, item in enumerate(selected_preview):
            new_num = "{}{}{}".format(prefix, start_num + index * step_num, suffix)
            item.preview_number = new_num

        try:
            self.renum_grid.Items.Refresh()
        except Exception:
            pass

    def _on_renum_run(self, sender, args):
        selected_preview = [item for item in self.renumber_items if item.IsSelected]
        if not selected_preview:
            MessageBox.Show("Please select sheets in the preview grid to renumber.", "Info")
            return
            
        # First preview them in case user hasn't previewed
        self._on_renum_preview(None, None)
        
        result = MessageBox.Show(
            "Renumber {} selected sheet(s)?".format(len(selected_preview)),
            "Confirm Renumber", MessageBoxButton.YesNo, MessageBoxImage.Question
        )
        
        if result == MessageBoxResult.Yes:
            t = Transaction(self.doc, "Batch Renumber Sheets")
            t.Start()
            self.begin_progress(len(selected_preview), disable=[sender])
            try:
                success = 0
                failed = 0
                for _idx, item in enumerate(selected_preview):
                    if self.is_cancelled:
                        break
                    self.step_progress(_idx, "Renumbering sheet {}/{}...".format(_idx + 1, len(selected_preview)))
                    sheet = item.sheet_model.element
                    new_num = item.preview_number
                    try:
                        sheet.SheetNumber = new_num
                        item.sheet_model.sheet_number = new_num
                        item.sheet_model.commit_changes()
                        success += 1
                    except Exception as ex:
                        print("Renumber failed for sheet '{}': {}".format(item.name, ex))
                        failed += 1
                t.Commit()

                _pfx = "Cancelled — renumbered" if self.is_cancelled else "Renumbered successfully"
                msg = "{}: {}".format(_pfx, success)
                if failed > 0:
                    msg += "\nFailed: {}".format(failed)
                MessageBox.Show(msg, "Renumber Complete", MessageBoxButton.OK, MessageBoxImage.Information)

                self._on_renum_refresh(None, None)
            except Exception as e:
                t.RollBack()
                MessageBox.Show("Error during renumbering: {}".format(str(e)), "Error")
            finally:
                self.end_progress()

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_sheets_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua sheets_grid."""
        self.toggle_all_rows(self.sheets_grid, "is_selected", sender.IsChecked)

    def select_all_renum_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua renum_grid."""
        self.toggle_all_rows(self.renum_grid, "IsSelected", sender.IsChecked)


# =====================================================
# LAUNCHER FUNCTION
# =====================================================

def show_sheet_manager():
    """Launch the unified Sheet Manager Dialog"""
    try:
        window = SheetManagerWindow()
        window.ShowDialog()
    except Exception as e:
        print("\nFATAL ERROR: {}".format(str(e)))
        import traceback
        try:                     # ScriptIO has no write() under CPython
            traceback.print_exc()
        except Exception:
            pass
        MessageBox.Show(
            "Error starting Sheet Manager:\n\n{}".format(str(e)),
            "Error", MessageBoxButton.OK, MessageBoxImage.Error
        )
