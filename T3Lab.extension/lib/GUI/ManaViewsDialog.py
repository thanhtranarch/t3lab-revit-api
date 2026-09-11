# -*- coding: utf-8 -*-
"""
View Manager Dialog
Combines Advanced View Manager and View Template Manager into a single, unified Lumina UI.
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
                                      DataGridTextColumn, ScrollViewer, ContextMenu, MenuItem)
from System.Windows.Media import SolidColorBrush
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs

# CRITICAL: Import WPF Grid BEFORE Revit wildcard import
from System.Windows.Controls import Grid as WPFGrid

from pyrevit import revit, DB, forms
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Autodesk.Revit.DB import (
    FilteredElementCollector, View, ViewType, ElementId,
    BuiltInParameter, ViewDetailLevel, StorageType, Transaction
)

# Import Core/Execution functions
from core.advanced_view_manager import (
    _eid_int,
    EnhancedViewItem,
    build_viewport_map,
    update_view_name,
    update_view_template,
    update_scale,
    update_detail_level,
    update_title_on_sheet,
    duplicate_views,
    delete_views,
    write_xlsx,
    read_xlsx
)

from core.view_template import (
    calculate_viewtemplate_usage,
    rename_template,
    batch_rename_templates,
    duplicate_templates,
    delete_templates
)

# Import Batch Rename dialog
from GUI.AdvancedViewManagerDialog import BatchRenameDialog

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None

# XAML Path
GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'ManaViews.xaml')

try:
    from GUI import RevitTheme as _theme
except Exception:
    try:
        import RevitTheme as _theme
    except Exception:
        _theme = None

from GUI.ProgressPauseMixin import ProgressPauseMixin
from GUI.DataGridColumnFilter import ColumnFilterController
from GUI import GridPendingEdits as _pend

# Row fields the grids let the user edit. Each one also needs a `dirty_<field>`
# flag on the row (init_pending creates them) and a CellStyle DataTrigger in the
# XAML bound to that flag, or the amber "pending" highlight never appears.
VIEW_EDIT_FIELDS = ("name", "view_template", "scale", "detail_level", "title_on_sheet")
TMPL_EDIT_FIELDS = ("name",)


# =====================================================
# VIEW TEMPLATE ITEM WRAPPER
# =====================================================

try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class ViewTemplateItem(_Reactive):
    """Wrapper class for View Template with WPF binding support"""
    def __init__(self, view_template):
        self._property_changed_handlers = []
        self._view_template = view_template
        self._is_selected = False
        self._name = view_template.Name
        self._id = _eid_int(view_template.Id)
        
        try:
            self._view_type = str(view_template.ViewType)
        except:
            self._view_type = "Unknown"
            
        try:
            scale_param = view_template.get_Parameter(BuiltInParameter.VIEW_SCALE_PULLDOWN_METRIC)
            if scale_param and scale_param.HasValue:
                self._scale = scale_param.AsValueString()
            else:
                self._scale = "N/A"
        except:
            self._scale = "N/A"
            
        self._usage_count = 0
        self._usage_percentage = 0.0

    @property
    def element(self):
        return self._view_template

    @property
    def view_template(self):
        return self._view_template

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name
    @name.setter
    def name(self, value):
        if self._name != value:
            self._name = value
            self.OnPropertyChanged("name")

    @property
    def view_type(self):
        return self._view_type

    @property
    def scale(self):
        return self._scale

    @property
    def usage_count(self):
        return self._usage_count
    @usage_count.setter
    def usage_count(self, value):
        if self._usage_count != value:
            self._usage_count = value
            self.OnPropertyChanged("usage_count")

    @property
    def usage_percentage(self):
        return "{:.1f}%".format(self._usage_percentage)
    @usage_percentage.setter
    def usage_percentage(self, value):
        if self._usage_percentage != value:
            self._usage_percentage = value
            self.OnPropertyChanged("usage_percentage")

    @property
    def is_selected(self):
        return self._is_selected
    @is_selected.setter
    def is_selected(self, value):
        if self._is_selected != value:
            self._is_selected = value
            self.OnPropertyChanged("is_selected")

    def add_PropertyChanged(self, handler):
        self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        args = PropertyChangedEventArgs(property_name)
        for handler in self._property_changed_handlers:
            handler(self, args)


# =====================================================
# VIEW MANAGER DIALOG CLASS
# =====================================================

class ViewManagerWindow(T3WPFWindow):

    # ProgressPauseMixin — ManaViews.xaml status-bar progress panel
    PP_PANEL      = "mv_progress_panel"
    PP_BAR        = "mv_pb"
    PP_PAUSE      = "mv_btn_pause"
    PP_STOP       = "mv_btn_stop"
    PP_PAUSE_ICON = "mv_btn_pause_icon"
    PP_PAUSE_TEXT = "mv_btn_pause_label"
    PP_STATUS     = "txt_status_bar"
    PP_STOP_MSG   = u"Stopping… finishing current view"

    def __init__(self):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = revit.doc
        self.uidoc = revit.uidoc

        self._adopt_host_font()
        self._apply_theme()
        
        # Data collections
        self.all_views = []
        self.filtered_views = ObservableCollection[Object]()
        self.all_templates_data = []
        self.filtered_templates = ObservableCollection[Object]()
        
        # Chrome controls
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close.Click += self._close_chrome
        
        # Radio Tab navigation
        self.nav_views.Checked += self._on_tab_changed
        self.nav_templates.Checked += self._on_tab_changed
        
        # Bind events: VIEWS Tab
        self.views_search_box.TextChanged += self._on_views_search_changed
        self.views_type_combo.SelectionChanged += self._on_views_filter_changed
        self.views_template_combo.SelectionChanged += self._on_views_filter_changed
        self.views_sheets_combo.SelectionChanged += self._on_views_filter_changed
        self.views_select_all_btn.Click += self._on_views_select_all
        self.views_clear_btn.Click += self._on_views_clear_all

        # Progress Pause/Stop (ProgressPauseMixin)
        if getattr(self, "mv_btn_pause", None) is not None:
            self.mv_btn_pause.Click += self.pause_resume_clicked
        if getattr(self, "mv_btn_stop", None) is not None:
            self.mv_btn_stop.Click += self.stop_clicked
        
        self.views_excel_btn.Click += self._on_views_excel
        self.views_refresh_btn.Click += self._on_views_refresh
        self.views_rename_btn.Click += self._on_views_batch_rename
        self.views_dup_btn.Click += self._on_views_duplicate
        self.views_del_btn.Click += self._on_views_delete
        self.views_close_btn.Click += self._on_close
        
        self.views_apply_btn.Click += self._on_views_apply
        self.views_grid.SelectionChanged += self._on_views_selection_changed
        self.views_grid.CellEditEnding += self._on_views_cell_edit
        self.views_grid.ItemsSource = self.filtered_views
        
        # Bind events: TEMPLATES Tab
        self.tmpl_search_box.TextChanged += self._on_tmpl_search_changed
        self.tmpl_usage_combo.SelectionChanged += self._on_tmpl_filter_changed
        self.tmpl_type_combo.SelectionChanged += self._on_tmpl_filter_changed
        self.tmpl_select_all_btn.Click += self._on_tmpl_select_all
        self.tmpl_clear_btn.Click += self._on_tmpl_clear_all
        self.tmpl_select_unused_btn.Click += self._on_tmpl_select_unused
        
        self.tmpl_refresh_btn.Click += self._on_tmpl_refresh
        self.tmpl_rename_btn.Click += self._on_tmpl_rename
        self.tmpl_batch_btn.Click += self._on_tmpl_batch_rename
        self.tmpl_dup_btn.Click += self._on_tmpl_duplicate
        self.tmpl_del_btn.Click += self._on_tmpl_delete
        self.tmpl_close_btn.Click += self._on_close
        
        self.tmpl_apply_btn.Click += self._on_tmpl_apply
        self.tmpl_grid.SelectionChanged += self._on_tmpl_selection_changed
        self.tmpl_grid.CellEditEnding += self._on_tmpl_cell_edit
        self.tmpl_grid.ItemsSource = self.filtered_templates
        
        # Column filters (nút phễu trên header — GUI/DataGridColumnFilter.py)
        self.views_col_filter = ColumnFilterController(
            self, self.views_grid,
            columns=[("VIEW NAME", "name"),
                     ("TYPE", "view_type"),
                     ("LEVEL", "level_name"),
                     ("VIEW TEMPLATE", "view_template"),
                     ("SCALE 1:", "scale"),
                     ("DETAIL LEVEL", "detail_level"),
                     ("TITLE ON SHEET", "title_on_sheet"),
                     ("SHEET NO.", "sheet_number")],
            source=lambda: self.all_views,
            on_changed=self._apply_views_filters,
            status_setter=self._set_status)

        self.tmpl_col_filter = ColumnFilterController(
            self, self.tmpl_grid,
            columns=[("TEMPLATE NAME", "name"),
                     ("VIEW TYPE", "view_type"),
                     ("SCALE 1:", "scale"),
                     ("USAGE", "usage_count"),
                     ("USAGE %", "usage_percentage")],
            source=lambda: self.all_templates_data,
            on_changed=self._apply_tmpl_filters,
            status_setter=self._set_status)

        # Load combobox data
        self.template_items = self._get_all_templates_names()
        self.col_template.ItemsSource = to_items_source(self.template_items)
        self.col_detail.ItemsSource = to_items_source(["Coarse", "Medium", "Fine"])
        
        # Load initial data
        self._load_views_data()
        self._load_templates_data()
        self._apply_views_filters()
        self._apply_tmpl_filters()

        # Force initial tab content to render: nav_views.IsChecked was already
        # True when the XAML was parsed, so its Checked event fired before this
        # handler was wired above and tab_control.SelectedIndex was never set.
        self.tab_control.SelectedIndex = 0

    def _set_status(self, text):
        """Ghi một câu trạng thái ra footer (dùng chung cho cả 2 tab)."""
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
        """Toggle active Tab based on RadioButton selection"""
        if not hasattr(self, 'tab_control'):
            return
        if self.nav_views.IsChecked:
            self.tab_control.SelectedIndex = 0
        elif self.nav_templates.IsChecked:
            self.tab_control.SelectedIndex = 1

    # ── VIEWS Tab Logics ──────────────────────────────────────────
    def _load_views_data(self):
        self.all_views = []
        viewport_map = build_viewport_map(self.doc)
        collector = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType()
        for view in collector:
            if view.ViewType in [ViewType.ProjectBrowser, ViewType.SystemBrowser,
                                 ViewType.Undefined, ViewType.Internal]:
                continue
            if view.IsTemplate:
                continue
            try:
                item = EnhancedViewItem(view, self.doc, viewport_map)
                _pend.init_pending(item, VIEW_EDIT_FIELDS)
                self.all_views.append(item)
            except:
                pass

    def _get_all_templates_names(self):
        templates = ["None"]
        collector = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType()
        for view in collector:
            if view.ViewType in [ViewType.ProjectBrowser, ViewType.SystemBrowser,
                                 ViewType.Undefined, ViewType.Internal]:
                continue
            try:
                if view.IsTemplate:
                    templates.append(view.Name)
            except:
                pass
        return templates

    def _get_combo_value(self, combo, default=""):
        if combo and combo.SelectedItem:
            item = combo.SelectedItem
            if hasattr(item, 'Content'):
                return str(item.Content)
            return str(item)
        return default

    def _apply_views_filters(self):
        self.filtered_views.Clear()
        
        type_filter = self._get_combo_value(self.views_type_combo, "All Views")
        template_filter = self._get_combo_value(self.views_template_combo, "All Views")
        sheets_filter = self._get_combo_value(self.views_sheets_combo, "All Views")
        search_text = self.views_search_box.Text.lower() if self.views_search_box.Text else ""

        for view in self._views_sorted():
            if search_text and search_text not in view.name.lower():
                continue
            if type_filter != "All Views" and view.view_type != type_filter:
                continue
            if template_filter == "With Template" and view.view_template == "None":
                continue
            elif template_filter == "Without Template" and view.view_template != "None":
                continue
            if sheets_filter == "On Sheets" and view.on_sheets == 0:
                continue
            elif sheets_filter == "Not On Sheets" and view.on_sheets > 0:
                continue
            # Bộ lọc theo cột (nút phễu trên header)
            if not self.views_col_filter.passes(view):
                continue

            self.filtered_views.Add(view)

        self._update_views_summary()
        self.views_col_filter.refresh_glyphs()

    def _views_sorted(self):
        """Thứ tự mặc định của bảng views: TYPE trước, VIEW NAME sau.

        Bảng luôn dựng theo thứ tự này sau mỗi lần lọc. Muốn sắp khác thì dùng
        hai nút Sort trong popup lọc của cột (click header = mở bộ lọc).
        """
        def key(item):
            return ((item.view_type or u"").lower(), (item.name or u"").lower())
        try:
            return sorted(self.all_views, key=key)
        except Exception:
            return list(self.all_views)

    def _update_views_summary(self):
        self.views_total_text.Text = str(len(self.all_views))
        
        types = set(v.view_type for v in self.all_views)
        self.views_types_text.Text = str(len(types))
        
        filters_active = (self._get_combo_value(self.views_type_combo, "All Views") != "All Views" or
                          self._get_combo_value(self.views_template_combo, "All Views") != "All Views" or
                          self._get_combo_value(self.views_sheets_combo, "All Views") != "All Views" or
                          (self.views_search_box.Text != "") or
                          self.views_col_filter.is_active())
        self.views_filters_text.Text = "Yes" if filters_active else "No"
        
        self.views_selected_text.Text = str(len([v for v in self.filtered_views if v.is_selected]))
        self._update_views_pending()

    def _update_views_pending(self):
        """Footer caption + Apply button state for the staged edits."""
        cells = _pend.pending_count(self.all_views)
        rows = len(_pend.pending_rows(self.all_views))
        try:
            self.views_apply_btn.IsEnabled = cells > 0
            self.views_apply_btn.Content = (
                "Apply Changes" if not cells
                else "Apply {} Change{}".format(cells, "" if cells == 1 else "s"))
        except Exception:
            pass
        if cells:
            self._set_status(
                "{} edited cell{} on {} view{} waiting for Apply — "
                "nothing is written to the model yet.".format(
                    cells, "" if cells == 1 else "s",
                    rows, "" if rows == 1 else "s"))

    def _on_views_search_changed(self, sender, args):
        self._apply_views_filters()

    def _on_views_filter_changed(self, sender, args):
        self._apply_views_filters()

    def _on_views_select_all(self, sender, args):
        for v in self.filtered_views:
            v.is_selected = True
        self.views_grid.Items.Refresh()
        self.views_selected_text.Text = str(len([v for v in self.filtered_views if v.is_selected]))

    def _on_views_clear_all(self, sender, args):
        for v in self.filtered_views:
            v.is_selected = False
        self.views_grid.Items.Refresh()
        self.views_selected_text.Text = str(len([v for v in self.filtered_views if v.is_selected]))

    def _on_views_selection_changed(self, sender, args):
        self.views_selected_text.Text = str(len([v for v in self.filtered_views if v.is_selected]))

    def _on_views_cell_edit(self, sender, args):
        """Stage the edit and paint the cell amber. Apply Changes writes it."""
        self._stage_cell_edit(args, VIEW_EDIT_FIELDS, self.views_grid,
                              self._update_views_summary)

    def _on_tmpl_cell_edit(self, sender, args):
        """Same staging for the TEMPLATE NAME cell on the Templates tab."""
        self._stage_cell_edit(args, TMPL_EDIT_FIELDS, self.tmpl_grid,
                              self._update_tmpl_summary)

    def _stage_cell_edit(self, args, fields, grid, on_changed):
        """Record what was typed without touching the model.

        Dispatch is on the column's BINDING PATH. This used to compare
        `column.Header` against "View Name" while the XAML said "VIEW NAME", so
        no branch ever ran and every inline edit was silently thrown away.
        """
        from System.Windows.Controls import DataGridEditAction
        if args.EditAction == DataGridEditAction.Cancel:
            return
        try:
            item = args.Row.Item
            field = _pend.column_key(args.Column)
            if not field or field not in fields:
                return

            typed = _pend.editor_text(args.EditingElement)
            current = getattr(item, field, None)

            if _pend.same_text(typed, current):
                _pend.unstage(item, field)      # typed it back to how it was
            elif field == "name" and not (typed or "").strip():
                # An empty name is the one edit that cannot ever be applied, so
                # it is refused here rather than at Apply time.
                _pend.revert_editor(args.EditingElement, current)
                self._set_status("A name cannot be empty — the cell was left unchanged.")
                return
            else:
                _pend.stage(item, field, typed)

            self._refresh_grid_later(grid)
            on_changed()
        except Exception as e:
            self._set_status("Could not read that edit: {0}".format(str(e)))

    def _refresh_grid_later(self, grid):
        """Redraw the grid once the edit has finished committing.

        The rows carry no INotifyPropertyChanged, so the amber DataTrigger only
        re-reads `dirty_<field>` on a refresh — and calling Refresh() while the
        cell is still committing throws "not allowed during an EditItem
        transaction". Hence the trip through the dispatcher.
        """
        try:
            from System.Windows.Threading import DispatcherPriority
            from System import Action
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background, Action(lambda: grid.Items.Refresh()))
        except Exception:
            try:
                grid.Items.Refresh()
            except Exception:
                pass

    def _on_views_excel(self, sender, args):
        from System.Windows.Forms import SaveFileDialog, OpenFileDialog, DialogResult
        
        result = MessageBox.Show(
            "Do you want to EXPORT the current list to Excel?\n(Select No if you want to IMPORT from Excel)",
            "Excel Import/Export", MessageBoxButton.YesNoCancel, MessageBoxImage.Question
        )
        
        if result == MessageBoxResult.Yes:
            # EXPORT
            sfd = SaveFileDialog()
            sfd.Filter = "Excel Files (*.xlsx)|*.xlsx"
            sfd.FileName = "T3Lab_ViewManager_Export.xlsx"
            if sfd.ShowDialog() == DialogResult.OK:
                try:
                    views_list = list(self.filtered_views)
                    write_xlsx(sfd.FileName, views_list)
                    MessageBox.Show("Successfully exported views data to Excel.", "Export Successful")
                except Exception as ex:
                    MessageBox.Show("Error exporting: {}".format(str(ex)), "Error")
                    
        elif result == MessageBoxResult.No:
            # IMPORT
            ofd = OpenFileDialog()
            ofd.Filter = "Excel Files (*.xlsx)|*.xlsx"
            if ofd.ShowDialog() == DialogResult.OK:
                try:
                    updates = read_xlsx(ofd.FileName)
                    if not updates:
                        MessageBox.Show("No valid updates found in Excel file.", "Import Excel")
                        return
                    
                    t = Transaction(self.doc, "Excel Sync View Parameters")
                    t.Start()
                    success = 0
                    failed = 0
                    
                    # Convert internal views to dict by ID for fast lookup
                    views_dict = {v.id: v for v in self.all_views}

                    self.begin_progress(len(updates))
                    for _idx, (view_id, data) in enumerate(updates.items()):
                        if self.is_cancelled:
                            break
                        self.step_progress(_idx, "Syncing view {}/{}...".format(_idx + 1, len(updates)))
                        if view_id in views_dict:
                            item = views_dict[view_id]
                            try:
                                if "name" in data:
                                    update_view_name(self.doc, item, data["name"])
                                if "view_template" in data:
                                    update_view_template(self.doc, item, data["view_template"])
                                if "scale" in data:
                                    update_scale(self.doc, item, data["scale"])
                                if "detail_level" in data:
                                    update_detail_level(self.doc, item, data["detail_level"])
                                if "title_on_sheet" in data:
                                    update_title_on_sheet(self.doc, item, data["title_on_sheet"])
                                success += 1
                            except:
                                failed += 1
                                
                    t.Commit()
                    _cancelled = self.is_cancelled
                    self.end_progress()
                    _pfx = "Excel Sync Cancelled" if _cancelled else "Excel Sync Completed"
                    MessageBox.Show("{}.\nUpdated: {}\nFailed: {}".format(_pfx, success, failed), "Excel Import")
                    self._on_views_refresh(None, None)
                except Exception as ex:
                    self.end_progress()
                    MessageBox.Show("Error importing Excel: {}".format(str(ex)), "Error")

    # ── APPLY: the only place a grid edit reaches the model ──────────────
    # One writer per field, so Apply stays a table lookup rather than a ladder
    # of ifs that has to be kept in step with the columns.
    def _view_writers(self):
        return {
            "name":           update_view_name,
            "view_template":  update_view_template,
            "scale":          update_scale,
            "detail_level":   update_detail_level,
            "title_on_sheet": update_title_on_sheet,
        }

    def _on_views_apply(self, sender, args):
        """Write every staged cell on the Views tab, then clear the amber."""
        rows = _pend.pending_rows(self.all_views)
        if not rows:
            self._set_status("Nothing to apply — no cell has been edited.")
            return

        cells = _pend.pending_count(self.all_views)
        result = MessageBox.Show(
            "Apply {} edited cell{} to {} view{}?".format(
                cells, "" if cells == 1 else "s",
                len(rows), "" if len(rows) == 1 else "s"),
            "Apply Changes", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        writers = self._view_writers()
        applied = 0
        failed = []
        self.begin_progress(len(rows), disable=[sender])
        try:
            for index, item in enumerate(rows):
                if self.is_cancelled:
                    break
                self.step_progress(index, "Applying {}/{}...".format(index + 1, len(rows)))
                for field, value in _pend.pending_of(item).items():
                    writer = writers.get(field)
                    if writer is None:
                        _pend.unstage(item, field)
                        continue
                    try:
                        # Each writer opens its own transaction, so one rejected
                        # cell cannot roll back the cells that already worked.
                        writer(self.doc, item, value)
                        _pend.unstage(item, field)
                        applied += 1
                    except Exception as ex:
                        failed.append(u"{} · {}: {}".format(
                            getattr(item, "name", "?"), field,
                            str(ex).split("\n")[0][:60]))
        finally:
            self.end_progress()

        # Re-read the model: a rejected cell must go back to showing what Revit
        # actually holds, not the value that was refused.
        self._load_views_data()
        self._apply_views_filters()

        summary = "Applied {} cell{}".format(applied, "" if applied == 1 else "s")
        if failed:
            summary += ", {} refused".format(len(failed))
            MessageBox.Show(
                "These edits were refused by Revit and are not in the model:\n\n"
                + "\n".join(failed[:12])
                + ("\n..." if len(failed) > 12 else ""),
                "Some edits were refused", MessageBoxButton.OK, MessageBoxImage.Warning)
        if self.is_cancelled:
            summary = "Cancelled — " + summary
        self._set_status(summary + ".")

    def _on_tmpl_apply(self, sender, args):
        """Write every staged TEMPLATE NAME, then clear the amber."""
        rows = _pend.pending_rows(self.all_templates_data)
        if not rows:
            self._set_status("Nothing to apply — no template name has been edited.")
            return

        result = MessageBox.Show(
            "Rename {} view template{}?".format(
                len(rows), "" if len(rows) == 1 else "s"),
            "Apply Changes", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        applied = 0
        failed = []
        for item in rows:
            new_name = _pend.pending_of(item).get("name")
            if new_name is None:
                continue
            try:
                if rename_template(self.doc, item.view_template, new_name):
                    item.name = new_name
                    _pend.unstage(item, "name")
                    applied += 1
                else:
                    failed.append(u"{}: rejected".format(item.name))
            except Exception as ex:
                failed.append(u"{}: {}".format(item.name, str(ex).split("\n")[0][:60]))

        self._load_templates_data()
        self._apply_tmpl_filters()

        summary = "Renamed {} template{}".format(applied, "" if applied == 1 else "s")
        if failed:
            summary += ", {} refused".format(len(failed))
            MessageBox.Show(
                "These renames were refused by Revit:\n\n" + "\n".join(failed[:12]),
                "Some renames were refused", MessageBoxButton.OK, MessageBoxImage.Warning)
        self._set_status(summary + ".")

    def _on_views_refresh(self, sender, args):
        self._load_views_data()
        self._apply_views_filters()

    def _on_views_batch_rename(self, sender, args):
        selected = [item for item in self.views_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select at least one View to rename.", "Info")
            return
            
        dialog = BatchRenameDialog(selected, self.doc)
        if dialog.ShowDialog():
            self._on_views_refresh(None, None)

    def _on_views_duplicate(self, sender, args):
        checked = [item for item in self.all_views if item.is_selected]
        selected = checked if checked else [item for item in self.views_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select or check at least one View to duplicate.", "Info")
            return
            
        result = MessageBox.Show(
            "Duplicate {} selected view(s)?".format(len(selected)),
            "Confirm Duplicate", MessageBoxButton.YesNo, MessageBoxImage.Question
        )
        if result == MessageBoxResult.Yes:
            try:
                success_count, error_count = duplicate_views(self.doc, [item.element for item in selected])
                msg = "Duplicated: {}".format(success_count)
                if error_count > 0:
                    msg += "\nFailed: {}".format(error_count)
                MessageBox.Show(msg, "Result")
                self._on_views_refresh(None, None)
            except Exception as ex:
                MessageBox.Show("Error: {}".format(str(ex)), "Error")

    def _on_views_delete(self, sender, args):
        checked = [item for item in self.all_views if item.is_selected]
        selected = checked if checked else [item for item in self.views_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select or check at least one View to delete.", "Info")
            return
            
        result = MessageBox.Show(
            "Are you sure you want to DELETE {} selected view(s)?".format(len(selected)),
            "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Warning
        )
        if result == MessageBoxResult.Yes:
            try:
                success_count, error_count = delete_views(self.doc, [item.element for item in selected])
                msg = "Deleted: {}".format(success_count)
                if error_count > 0:
                    msg += "\nFailed: {}".format(error_count)
                MessageBox.Show(msg, "Result")
                self._on_views_refresh(None, None)
            except Exception as ex:
                MessageBox.Show("Error: {}".format(str(ex)), "Error")

    # ── TEMPLATES Tab Logics ──────────────────────────────────────
    def _load_templates_data(self):
        self.all_templates_data = []
        self.tmpl_types = set()
        
        collector = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType()
        for vt in collector:
            try:
                if vt.IsTemplate:
                    item = ViewTemplateItem(vt)
                    _pend.init_pending(item, TMPL_EDIT_FIELDS)
                    self.all_templates_data.append(item)
                    if item.view_type:
                        self.tmpl_types.add(item.view_type)
            except:
                pass
        
        # Calculate template usage (updates items in-place)
        calculate_viewtemplate_usage(self.doc, self.all_templates_data)
        
        # Populate tmpl_type_combo if first load
        if self.tmpl_type_combo.Items.Count <= 1:
            for vt_type in sorted(list(self.tmpl_types)):
                self.tmpl_type_combo.Items.Add(vt_type)

    def _apply_tmpl_filters(self):
        self.filtered_templates.Clear()
        
        usage_filter = self._get_combo_value(self.tmpl_usage_combo, "All Templates")
        type_filter = self._get_combo_value(self.tmpl_type_combo, "All View Types")
        search_text = self.tmpl_search_box.Text.lower() if self.tmpl_search_box.Text else ""

        for item in self._templates_sorted():
            if search_text and search_text not in item.name.lower():
                continue
            if type_filter != "All View Types" and item.view_type != type_filter:
                continue
            if usage_filter == "In Use Only" and item.usage_count == 0:
                continue
            elif usage_filter == "Unused Only" and item.usage_count > 0:
                continue
            # Bộ lọc theo cột (nút phễu trên header)
            if not self.tmpl_col_filter.passes(item):
                continue

            self.filtered_templates.Add(item)

        self._update_tmpl_summary()
        self.tmpl_col_filter.refresh_glyphs()

    def _templates_sorted(self):
        """Thứ tự mặc định của bảng template: VIEW TYPE trước, TEMPLATE NAME sau.

        Bảng luôn dựng theo thứ tự này sau mỗi lần lọc. Muốn sắp khác thì dùng
        hai nút Sort trong popup lọc của cột (click header = mở bộ lọc).
        """
        def key(item):
            return ((item.view_type or u"").lower(), (item.name or u"").lower())
        try:
            return sorted(self.all_templates_data, key=key)
        except Exception:
            return list(self.all_templates_data)

    def _update_tmpl_summary(self):
        self.tmpl_total_text.Text = str(len(self.all_templates_data))
        self.tmpl_selected_text.Text = str(len([t for t in self.filtered_templates if t.is_selected]))

        used = sum(1 for item in self.all_templates_data if item.usage_count > 0)
        unused = len(self.all_templates_data) - used
        self.tmpl_used_text.Text = str(used)
        self.tmpl_unused_text.Text = str(unused)
        self._update_tmpl_pending()

    def _update_tmpl_pending(self):
        cells = _pend.pending_count(self.all_templates_data)
        try:
            self.tmpl_apply_btn.IsEnabled = cells > 0
            self.tmpl_apply_btn.Content = (
                "Apply Changes" if not cells
                else "Apply {} Change{}".format(cells, "" if cells == 1 else "s"))
        except Exception:
            pass
        if cells:
            self._set_status(
                "{} renamed template{} waiting for Apply — nothing is written "
                "to the model yet.".format(cells, "" if cells == 1 else "s"))

    def _on_tmpl_search_changed(self, sender, args):
        self._apply_tmpl_filters()

    def _on_tmpl_filter_changed(self, sender, args):
        self._apply_tmpl_filters()

    def _on_tmpl_select_all(self, sender, args):
        for item in self.filtered_templates:
            item.is_selected = True
        self.tmpl_grid.Items.Refresh()
        self.tmpl_selected_text.Text = str(len([t for t in self.filtered_templates if t.is_selected]))

    def _on_tmpl_clear_all(self, sender, args):
        for item in self.filtered_templates:
            item.is_selected = False
        self.tmpl_grid.Items.Refresh()
        self.tmpl_selected_text.Text = str(len([t for t in self.filtered_templates if t.is_selected]))

    def _on_tmpl_select_unused(self, sender, args):
        for item in self.filtered_templates:
            item.is_selected = (item.usage_count == 0)
        self.tmpl_grid.Items.Refresh()
        self.tmpl_selected_text.Text = str(len([t for t in self.filtered_templates if t.is_selected]))

    def _on_tmpl_selection_changed(self, sender, args):
        self.tmpl_selected_text.Text = str(len([t for t in self.filtered_templates if t.is_selected]))

    def _on_tmpl_refresh(self, sender, args):
        self._load_templates_data()
        self._apply_tmpl_filters()

    def _on_tmpl_rename(self, sender, args):
        selected = [item for item in self.tmpl_grid.SelectedItems]
        if len(selected) != 1:
            MessageBox.Show("Please select exactly one template to rename.", "Info")
            return
            
        item = selected[0]
        new_name = forms.ask_for_string(
            default=item.name,
            prompt="Enter a new name for the View Template:",
            title="Rename View Template"
        )
        if new_name and new_name != item.name:
            try:
                if rename_template(self.doc, item.view_template, new_name):
                    item.name = new_name
                    self._on_tmpl_refresh(None, None)
            except Exception as e:
                MessageBox.Show("Error renaming: {}".format(str(e)), "Error")

    def _on_tmpl_batch_rename(self, sender, args):
        selected = [item for item in self.tmpl_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select at least one template to rename.", "Info")
            return
            
        from GUI.AdvancedViewManagerDialog import BatchRenameDialog
        dialog = BatchRenameDialog(selected, self.doc)
        if dialog.ShowDialog():
            self._on_tmpl_refresh(None, None)

    def _on_tmpl_duplicate(self, sender, args):
        checked = [item for item in self.all_templates_data if item.is_selected]
        selected = checked if checked else [item for item in self.tmpl_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select or check at least one template to duplicate.", "Info")
            return
            
        result = MessageBox.Show(
            "Duplicate {} selected template(s)?".format(len(selected)),
            "Confirm Duplicate", MessageBoxButton.YesNo, MessageBoxImage.Question
        )
        if result == MessageBoxResult.Yes:
            try:
                success_count, error_count = duplicate_templates(self.doc, [item.view_template for item in selected])
                msg = "Duplicated: {}".format(success_count)
                if error_count > 0:
                    msg += "\nFailed: {}".format(error_count)
                MessageBox.Show(msg, "Result")
                self._on_tmpl_refresh(None, None)
            except Exception as ex:
                MessageBox.Show("Error: {}".format(str(ex)), "Error")

    def _on_tmpl_delete(self, sender, args):
        checked = [item for item in self.all_templates_data if item.is_selected]
        selected = checked if checked else [item for item in self.tmpl_grid.SelectedItems]
        if not selected:
            MessageBox.Show("Please select or check at least one template to delete.", "Info")
            return
            
        in_use = [item for item in selected if item.usage_count > 0]
        can_delete = [item for item in selected if item.usage_count == 0]
        
        if in_use:
            msg = "WARNING: {} template(s) are IN USE and CANNOT be deleted:\n\n".format(len(in_use))
            for item in in_use[:5]:
                msg += "  - '{}': {} views\n".format(item.name, item.usage_count)
            if len(in_use) > 5:
                msg += "  ... and {} more\n".format(len(in_use) - 5)
            MessageBox.Show(msg, "Templates In Use", MessageBoxButton.OK, MessageBoxImage.Warning)
            
        if not can_delete:
            MessageBox.Show("No deletable templates selected.\nOnly UNUSED templates can be deleted.",
                           "Info", MessageBoxButton.OK, MessageBoxImage.Information)
            return
            
        result = MessageBox.Show(
            "Delete {} unused template(s)?".format(len(can_delete)),
            "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Question
        )
        
        if result == MessageBoxResult.Yes:
            try:
                templates_to_delete = [item.view_template for item in can_delete]
                success_count, error_count = delete_templates(self.doc, templates_to_delete)
                msg = "Deleted: {}".format(success_count)
                if error_count > 0:
                    msg += "\nFailed: {}".format(error_count)
                MessageBox.Show(msg, "Result")
                self._on_tmpl_refresh(None, None)
            except Exception as ex:
                MessageBox.Show("Error: {}".format(str(ex)), "Error")

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_views_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua views_grid."""
        self.toggle_all_rows(self.views_grid, "is_selected", sender.IsChecked)

    def select_all_tmpl_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua tmpl_grid."""
        self.toggle_all_rows(self.tmpl_grid, "is_selected", sender.IsChecked)


# =====================================================
# LAUNCHER FUNCTION
# =====================================================

def show_view_manager():
    """Launch the unified View Manager Dialog"""
    try:
        window = ViewManagerWindow()
        window.ShowDialog()
    except Exception as e:
        print("\nFATAL ERROR: {}".format(str(e)))
        import traceback
        try:                     # ScriptIO has no write() under CPython
            traceback.print_exc()
        except Exception:
            pass
        MessageBox.Show(
            "Error starting View Manager:\n\n{}".format(str(e)),
            "Error", MessageBoxButton.OK, MessageBoxImage.Error
        )
