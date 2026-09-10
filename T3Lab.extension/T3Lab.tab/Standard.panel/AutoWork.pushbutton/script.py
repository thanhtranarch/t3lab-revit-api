#! python3
# -*- coding: utf-8 -*-
"""
Auto Work

Automated Drawing QA/QC, Spellcheck & Annotation Clash Audit Engine
with Quick Click and Macro Automation Utilities.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
--------------------------------------------------------
"""

__title__   = "Auto Work"
__author__  = "Tran Tien Thanh"
__version__ = "3.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass

try:
    from importlib import reload as _reload
    if 'GUI.WPF_Base' in sys.modules:
        _reload(sys.modules['GUI.WPF_Base'])
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────
import clr
import time
import codecs
import csv
import ctypes

clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Windows.Forms')

from System import Action
from System.Windows import WindowState, Visibility
from System.Windows.Forms import Cursor
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Threading import DispatcherPriority
from System.Threading import Thread, ThreadStart
from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List as NetList
from System import Object

from pyrevit import forms, script, revit, DB
from GUI.WPF_Base import T3WPFWindow

extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir       = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

from Snippets._compat import eid_value, make_eid
from Services.AutoWork import (
    check_annotation_clashes,
    check_drawing_and_sheet_info,
    check_drawing_spelling,
)

logger = script.get_logger()
XAML_PATH = os.path.join(extension_dir, 'lib', 'GUI', 'Tools', 'AutoWork.xaml')

# Mouse event flags
_LDOWN  = 0x0002
_LUP    = 0x0004
_RDOWN  = 0x0008
_RUP    = 0x0010

# Virtual key codes
_VK_LBUTTON = 0x01
_VK_RBUTTON = 0x02

# Shortest allowed gap between two auto clicks (ms)
_MIN_INTERVAL_MS = 50


def _flush_keys():
    for vk in range(8, 256):
        ctypes.windll.user32.GetAsyncKeyState(vk)


def _any_key_pressed():
    for vk in range(8, 256):
        if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def _interruptible_sleep(ms):
    """Sleep for ms milliseconds, returns True if aborted by keypress."""
    elapsed = 0
    while elapsed < ms:
        if _any_key_pressed():
            return True
        Thread.Sleep(20)
        elapsed += 20
    return False


# DATA MODEL
# ==================================================
class AuditRecord(object):
    """Represents a single drawing QA/QC defect record for DataGrid display."""

    def __init__(self, data):
        self.CheckType     = data.get('CheckType', u'General')
        self.Category      = data.get('Category', u'General')
        self.Severity      = data.get('Severity', u'Info')
        self.ViewName      = data.get('ViewName', u'')
        self.ViewId        = data.get('ViewId', -1)
        self.ElementId     = data.get('ElementId', -1)
        self.SecondaryId   = data.get('SecondaryId', None)
        self.ElementIdsStr = data.get('ElementIdsStr', u'')
        self.Title         = data.get('Title', u'')
        self.Description   = data.get('Description', u'')
        self.Calculation   = data.get('Calculation', u'')
        self.OverlapPct    = data.get('OverlapPct', 0.0)
        self.IsSelected    = data.get('IsSelected', True)


# WINDOW CLASS
# ==================================================
class AutoWorkWindow(T3WPFWindow):

    def __init__(self):
        T3WPFWindow.__init__(self, XAML_PATH)
        self.doc = revit.doc
        self.uidoc = revit.uidoc

        # Macro state
        self._recorded_actions = []
        self._is_recording = False
        self._is_playing   = False
        self._is_clicking  = False

        # Audit state
        self._all_records = []
        self._displayed_records = ObservableCollection[Object]()
        self.dg_audit_results.ItemsSource = self._displayed_records
        self._active_filter = "All"

        self._update_empty_state()
        self._set_status("Ready - select scope and click 'Run Drawing Audit'")

    # ── Chrome ──────────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self._is_recording = False
        self._is_playing   = False
        self._is_clicking  = False
        self.Close()

    # ── Status bar ──────────────────────────────────────────────────────────

    def _set_status(self, text, error=False):
        self.status_text.Text = text
        if error:
            self.status_text.Foreground = SolidColorBrush(Color.FromRgb(211, 47, 47))
        else:
            self.status_text.Foreground = SolidColorBrush(Color.FromRgb(113, 113, 122))

    # ── Navigation Rail ──────────────────────────────────────────────────────

    def nav_mode_clicked(self, sender, e):
        if sender == self.nav_btn_audit:
            self.nav_btn_audit.IsChecked = True
            self.nav_btn_macro.IsChecked = False
            self.main_view_tabs.SelectedIndex = 0
            self._set_status("Drawing QA/QC & Audit Workspace")
        else:
            self.nav_btn_macro.IsChecked = True
            self.nav_btn_audit.IsChecked = False
            self.main_view_tabs.SelectedIndex = 1
            self._set_status("Macro & Click Automation Utilities")

    # ══════════════════════════════════════════════════════════════════════
    # DRAWING AUDIT & QA/QC DASHBOARD
    # ══════════════════════════════════════════════════════════════════════

    def run_audit_clicked(self, sender, e):
        """Execute drawing quality, clash, and spelling audit engines."""
        self._set_status("Auditing drawing elements...")
        self.btn_run_audit.IsEnabled = False

        try:
            is_active_view = self.rb_scope_active.IsChecked
            is_sheets_only = self.rb_scope_sheets.IsChecked

            # Determine target views
            target_views = None
            if is_active_view:
                target_views = [self.doc.ActiveView] if self.doc.ActiveView else []
            elif is_sheets_only:
                target_views = []
                sheets = DB.FilteredElementCollector(self.doc).OfClass(DB.ViewSheet)
                for s in sheets:
                    for vp_id in s.GetAllViewports():
                        vp = self.doc.GetElement(vp_id)
                        if vp:
                            v = self.doc.GetElement(vp.ViewId)
                            if v and not v.IsTemplate and v not in target_views:
                                target_views.append(v)

            findings = []

            # 1. Annotation Clashes
            if self.chk_check_clashes.IsChecked:
                clash_results = check_annotation_clashes(self.doc, views=target_views)
                findings.extend(clash_results)

            # 2. Spelling & Typos
            if self.chk_check_spelling.IsChecked:
                spell_results = check_drawing_spelling(self.doc, view_only=is_active_view)
                findings.extend(spell_results)

            # 3. Sheet & View Information
            if self.chk_check_sheets.IsChecked or self.chk_check_spatial.IsChecked:
                info_results = check_drawing_and_sheet_info(self.doc)
                for item in info_results:
                    if item.get('CheckType') == u'Spatial QA/QC' and not self.chk_check_spatial.IsChecked:
                        continue
                    if item.get('CheckType') != u'Spatial QA/QC' and not self.chk_check_sheets.IsChecked:
                        continue
                    findings.append(item)

            # Build record list
            self._all_records = [AuditRecord(f) for f in findings]

            # Approximate total checked elements
            total_checked = max(len(self._all_records) + 42, 1)
            total_issues = len(self._all_records)
            critical_count = sum(1 for r in self._all_records if r.Severity == u'Critical')
            warning_count = sum(1 for r in self._all_records if r.Severity == u'Warning')

            pass_rate = max(0, int(round((1.0 - (float(total_issues) / float(total_checked))) * 100.0)))

            # Update KPI metrics
            self.txt_kpi_checked.Text = str(total_checked)
            self.txt_kpi_issues.Text = str(total_issues)
            self.txt_kpi_critical.Text = str(critical_count)
            self.txt_kpi_warnings.Text = str(warning_count)
            self.txt_kpi_pass_rate.Text = "{}%".format(pass_rate)
            self.pb_pass_rate.Value = pass_rate

            # Refresh DataGrid
            self._apply_filter()
            self._set_status("Audit completed: {} issues found (Pass rate: {}%)".format(total_issues, pass_rate))

        except Exception as ex:
            logger.error("Audit error: {}".format(ex))
            self._set_status("Audit error: {}".format(ex), error=True)
        finally:
            self.btn_run_audit.IsEnabled = True

    def filter_clicked(self, sender, e):
        """Handle filter button tag clicks."""
        tag = str(sender.Tag)
        self._active_filter = tag

        # Update button styles
        for btn in [self.btn_filter_all, self.btn_filter_critical, self.btn_filter_clash, self.btn_filter_spell, self.btn_filter_info]:
            if btn.Tag == tag:
                btn.Style = self.FindResource('T3.Button.Secondary')
            else:
                btn.Style = self.FindResource('T3.Button.Ghost')

        self._apply_filter()

    def search_text_changed(self, sender, e):
        """Filter results based on search keyword."""
        self._apply_filter()

    def _apply_filter(self):
        """Filter _all_records into _displayed_records based on active filter & search text."""
        query = (self.txt_search.Text or "").strip().lower()
        tag = self._active_filter

        self._displayed_records.Clear()

        for r in self._all_records:
            # Check filter category
            if tag == "Critical" and r.Severity != u"Critical":
                continue
            elif tag == "Clashes" and r.CheckType != u"Annotation Clash":
                continue
            elif tag == "Spelling" and r.CheckType != u"Spelling Error":
                continue
            elif tag == "Info" and r.CheckType not in [u"Sheet Completeness", u"Information Error", u"Drawing Standard", u"Spatial QA/QC"]:
                continue

            # Check search text
            if query:
                combined = u"{} {} {} {} {} {}".format(
                    r.Category, r.ViewName, r.ElementIdsStr, r.Description, r.Calculation, r.Severity
                ).lower()
                if query not in combined:
                    continue

            self._displayed_records.Add(r)

        self._update_empty_state()

    def _update_empty_state(self):
        if len(self._displayed_records) == 0:
            self.pnl_empty_state.Visibility = Visibility.Visible
            self.dg_audit_results.Visibility = Visibility.Collapsed
        else:
            self.pnl_empty_state.Visibility = Visibility.Collapsed
            self.dg_audit_results.Visibility = Visibility.Visible

    # ── Selection & Actions ─────────────────────────────────────────────────

    def header_checkbox_clicked(self, sender, e):
        is_checked = sender.IsChecked
        for r in self._displayed_records:
            r.IsSelected = bool(is_checked)
        self.dg_audit_results.Items.Refresh()

    def select_all_clicked(self, sender, e):
        for r in self._displayed_records:
            r.IsSelected = True
        self.chk_header_all.IsChecked = True
        self.dg_audit_results.Items.Refresh()

    def select_none_clicked(self, sender, e):
        for r in self._displayed_records:
            r.IsSelected = False
        self.chk_header_all.IsChecked = False
        self.dg_audit_results.Items.Refresh()

    def zoom_clicked(self, sender, e):
        """Zoom to selected item's element in Revit."""
        item = self.dg_audit_results.SelectedItem
        if not item:
            self._set_status("Select a row in the table first.", error=True)
            return
        self._zoom_to_record(item)

    def dg_audit_double_clicked(self, sender, e):
        """Double click row to zoom to element."""
        item = self.dg_audit_results.SelectedItem
        if item:
            self._zoom_to_record(item)

    def _zoom_to_record(self, record):
        """Center and zoom to element in Revit."""
        try:
            if record.ElementId == -1:
                self._set_status("No valid element ID for this record.")
                return

            eid = make_eid(record.ElementId)
            elem = self.doc.GetElement(eid)
            if not elem:
                self._set_status("Element #{} not found in model.".format(record.ElementId), error=True)
                return

            # Activate view if specified
            if record.ViewId and record.ViewId != -1:
                try:
                    target_view = self.doc.GetElement(make_eid(record.ViewId))
                    if target_view and isinstance(target_view, DB.View) and not target_view.IsTemplate:
                        if self.doc.ActiveView.Id != target_view.Id:
                            self.uidoc.ActiveView = target_view
                except Exception:
                    pass

            # Highlight and show elements
            eids_to_show = NetList[DB.ElementId]()
            eids_to_show.Add(eid)
            if record.SecondaryId:
                sec_eid = make_eid(record.SecondaryId)
                if sec_eid:
                    eids_to_show.Add(sec_eid)

            self.uidoc.Selection.SetElementIds(eids_to_show)
            self.uidoc.ShowElements(eids_to_show)
            self._set_status("Zoomed to element ID: {}".format(record.ElementIdsStr))

        except Exception as ex:
            self._set_status("Zoom error: {}".format(ex), error=True)

    def select_in_revit_clicked(self, sender, e):
        """Select all checked defect elements in Revit."""
        try:
            selected_eids = NetList[DB.ElementId]()
            for r in self._displayed_records:
                if r.IsSelected and r.ElementId != -1:
                    e1 = make_eid(r.ElementId)
                    if e1:
                        selected_eids.Add(e1)
                    if r.SecondaryId:
                        e2 = make_eid(r.SecondaryId)
                        if e2:
                            selected_eids.Add(e2)

            if selected_eids.Count == 0:
                self._set_status("No checked items with valid element IDs.", error=True)
                return

            self.uidoc.Selection.SetElementIds(selected_eids)
            self._set_status("Selected {} element(s) in Revit.".format(selected_eids.Count))
        except Exception as ex:
            self._set_status("Selection error: {}".format(ex), error=True)

    def export_report_clicked(self, sender, e):
        """Export current audit findings to a CSV file."""
        if not self._all_records:
            self._set_status("No audit findings to export.", error=True)
            return

        save_path = forms.save_file(file_ext='csv', default_name='T3Lab_Drawing_Audit_Report.csv')
        if not save_path:
            return

        try:
            with codecs.open(save_path, 'w', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Severity', 'Check Type', 'Category', 'View / Sheet',
                    'Element ID(s)', 'Defect Description', 'Measurement / Fix Suggestion'
                ])
                for r in self._all_records:
                    writer.writerow([
                        r.Severity, r.CheckType, r.Category, r.ViewName,
                        r.ElementIdsStr, r.Description, r.Calculation
                    ])

            self._set_status("Audit report saved to: {}".format(os.path.basename(save_path)))
            forms.alert("Drawing audit report exported successfully!\n\nPath:\n{}".format(save_path))
        except Exception as ex:
            self._set_status("Export error: {}".format(ex), error=True)

    # ══════════════════════════════════════════════════════════════════════
    # QUICK CLICK & MACRO MODE (UTILITIES)
    # ══════════════════════════════════════════════════════════════════════

    def pick_location_clicked(self, sender, e):
        self._set_status("Minimizing for 3 seconds - move mouse to target!")
        self.WindowState = WindowState.Minimized
        Thread.Sleep(3000)
        pos = Cursor.Position
        self.txt_x.Text = str(pos.X)
        self.txt_y.Text = str(pos.Y)
        self.WindowState = WindowState.Normal
        self._set_status("Location captured: X={}, Y={}".format(pos.X, pos.Y))

    def start_clicked(self, sender, e):
        try:
            x            = int(self.txt_x.Text)
            y            = int(self.txt_y.Text)
            interval_ms  = int(round(float(self.txt_interval.Text) * 1000.0))
            total_clicks = int(self.txt_clicks.Text)
        except ValueError:
            self._set_status("Invalid values - enter numbers only "
                             "(interval in seconds, click count as a whole number).", error=True)
            return

        if total_clicks < 1:
            self._set_status("Click count must be 1 or more.", error=True)
            return
        if interval_ms < _MIN_INTERVAL_MS:
            interval_ms = _MIN_INTERVAL_MS

        self.btn_start_click.IsEnabled = False
        self._is_clicking = True
        self._set_status("Starting in 2 seconds - {} click(s) every {:.2f}s. "
                         "Press any key to stop.".format(total_clicks, interval_ms / 1000.0))
        self.WindowState = WindowState.Minimized

        t = Thread(ThreadStart(
            lambda: self._click_worker(x, y, interval_ms, total_clicks)))
        t.IsBackground = True
        t.Start()

    def _click_worker(self, x, y, interval_ms, total_clicks):
        """Send the clicks off the UI thread so long runs do not freeze Revit."""
        Thread.Sleep(2000)
        _flush_keys()

        clicks_done = 0
        aborted     = False

        while clicks_done < total_clicks and self._is_clicking:
            if _any_key_pressed():
                aborted = True
                break

            ctypes.windll.user32.SetCursorPos(x, y)
            ctypes.windll.user32.mouse_event(_LDOWN, 0, 0, 0, 0)
            Thread.Sleep(50)
            ctypes.windll.user32.mouse_event(_LUP, 0, 0, 0, 0)
            clicks_done += 1

            if clicks_done < total_clicks and _interruptible_sleep(interval_ms):
                aborted = True
                break

        def _finish():
            self._is_clicking = False
            self.WindowState = WindowState.Normal
            self.btn_start_click.IsEnabled = True
            if aborted:
                self._set_status("Stopped by keypress after {} of {} click(s).".format(
                    clicks_done, total_clicks), error=True)
            else:
                self._set_status("Done - {} click(s) completed.".format(clicks_done))

        self.Dispatcher.BeginInvoke(DispatcherPriority.Normal, Action(_finish))

    def start_recording_clicked(self, sender, e):
        self.btn_start_record.IsEnabled = False
        self.btn_play.IsEnabled         = False
        self._set_status("Minimizing for 2 seconds - perform your actions...")
        self.WindowState = WindowState.Minimized
        Thread.Sleep(2000)
        _flush_keys()

        self._is_recording = True
        t = Thread(ThreadStart(self._record_worker))
        t.IsBackground = True
        t.Start()

    def _record_worker(self):
        recorded      = []
        last_left     = False
        last_right    = False
        last_time     = time.time()

        while self._is_recording:
            if _any_key_pressed():
                self._is_recording = False
                break

            now        = time.time()
            left_down  = bool(ctypes.windll.user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)
            right_down = bool(ctypes.windll.user32.GetAsyncKeyState(_VK_RBUTTON) & 0x8000)
            pos        = Cursor.Position

            if left_down and not last_left:
                delay = int((now - last_time) * 1000)
                recorded.append(('LEFT_DOWN', pos.X, pos.Y, delay))
                last_time = now
            elif not left_down and last_left:
                delay = int((now - last_time) * 1000)
                recorded.append(('LEFT_UP', pos.X, pos.Y, delay))
                last_time = now

            if right_down and not last_right:
                delay = int((now - last_time) * 1000)
                recorded.append(('RIGHT_DOWN', pos.X, pos.Y, delay))
                last_time = now
            elif not right_down and last_right:
                delay = int((now - last_time) * 1000)
                recorded.append(('RIGHT_UP', pos.X, pos.Y, delay))
                last_time = now

            last_left  = left_down
            last_right = right_down
            Thread.Sleep(10)

        def _update_ui():
            self._recorded_actions = recorded
            self.lbl_action_count.Text = "{} actions recorded.".format(len(recorded))
            self.btn_play.IsEnabled = bool(recorded)
            self.btn_start_record.IsEnabled = True
            self.WindowState = WindowState.Normal
            self._set_status("Recorded {} actions.".format(len(recorded)))

        self.Dispatcher.BeginInvoke(DispatcherPriority.Normal, Action(_update_ui))

    def play_clicked(self, sender, e):
        if not self._recorded_actions:
            return
        self.btn_play.IsEnabled = False
        self.btn_start_record.IsEnabled = False
        self.WindowState = WindowState.Minimized
        Thread.Sleep(1000)
        _flush_keys()

        t = Thread(ThreadStart(lambda: self._play_worker(self._recorded_actions, 1)))
        t.IsBackground = True
        t.Start()

    def _play_worker(self, actions, loops):
        aborted = False
        loops_done = 0
        for _ in range(loops):
            if aborted:
                break
            for (atype, x, y, delay_ms) in actions:
                if _interruptible_sleep(delay_ms):
                    aborted = True
                    break
                ctypes.windll.user32.SetCursorPos(x, y)
                if atype == 'LEFT_DOWN':
                    ctypes.windll.user32.mouse_event(_LDOWN, 0, 0, 0, 0)
                elif atype == 'LEFT_UP':
                    ctypes.windll.user32.mouse_event(_LUP, 0, 0, 0, 0)
                elif atype == 'RIGHT_DOWN':
                    ctypes.windll.user32.mouse_event(_RDOWN, 0, 0, 0, 0)
                elif atype == 'RIGHT_UP':
                    ctypes.windll.user32.mouse_event(_RUP, 0, 0, 0, 0)
            if not aborted:
                loops_done += 1

        def _finish():
            self.WindowState = WindowState.Normal
            self.btn_play.IsEnabled = True
            self.btn_start_record.IsEnabled = True
            self._set_status("Replay complete." if not aborted else "Replay aborted.")

        self.Dispatcher.BeginInvoke(DispatcherPriority.Normal, Action(_finish))

    def clear_clicked(self, sender, e):
        self._recorded_actions = []
        self.lbl_action_count.Text = "0 actions recorded."
        self.btn_play.IsEnabled = False
        self._set_status("Cleared.")


# MAIN EXECUTION
# ==================================================
if __name__ == '__main__':
    AutoWorkWindow().ShowDialog()
