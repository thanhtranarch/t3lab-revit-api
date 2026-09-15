# -*- coding: utf-8 -*-
"""
Batch Out Executor

Executes batch export operations for sheets in various formats.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

from __future__ import unicode_literals

__author__  = "Tran Tien Thanh"
__title__   = "Batch Out Executor"

import os
import re


# ─── Format → checkbox attribute name mapping ─────────────────────────────────

_FMT_ATTRS = {
    'pdf': 'export_pdf',
    'dwg': 'export_dwg',
    'dwf': 'export_dwf',
    'dgn': 'export_dgn',
    'nwd': 'export_nwd',
    'ifc': 'export_ifc',
    'img': 'export_img',
    'image': 'export_img',
}

_FMT_SUBFOLDER = {
    'pdf': 'PDF', 'dwg': 'DWG', 'dwf': 'DWF',
    'dgn': 'DGN', 'nwd': 'NWD', 'ifc': 'IFC',
    'img': 'Images', 'image': 'Images',
}


# ─── Public API ───────────────────────────────────────────────────────────────

def configure_batchout_window(window, config):
    """Pre-configure an ExportManagerWindow before ShowDialog().

    Args:
        window: ExportManagerWindow instance (already __init__'d).
        config: dict with keys format, filter, combine, goto_create.
    """
    fmt        = _validated_format(config)
    filter_kw  = (config.get('filter') or '').lower().strip()
    combine    = config.get('combine', False)
    goto_create = config.get('goto_create', True)

    # 1. Select / deselect sheets
    _apply_sheet_filter(window, filter_kw)

    # 2. Enable only the requested format, disable others
    _apply_format(window, fmt, combine)

    # 3. Navigate to Create tab (index 2) if requested
    if goto_create:
        try:
            window.main_tabs.SelectedIndex = 2
        except Exception:
            pass


def direct_export(batchout_mod, config, progress_cb=None, cancel_check=None):
    """Export sheets without showing any UI.

    Args:
        batchout_mod : The loaded BatchOut script module (from _load_script).
        config       : dict with format, filter, folder, combine.
        progress_cb  : optional callable(message: str) for progress updates.
        cancel_check : optional callable returning True to stop before the next
                       export call. Existing output files are never removed.

    Returns:
        (complete_success: bool, exported_count: int, result_message: str).
        Partial output returns False with its nonzero count. Must be called
        inside Revit API context; a native export cannot be interrupted here.
    """
    window = None
    count = 0
    try:
        fmt = _validated_format(config)
        if cancel_check and cancel_check():
            return False, 0, "Export stopped before execution. No files were created."
        filter_kw = (config.get('filter') or '').lower().strip()
        combine = bool(config.get('combine', False))
        folder = config.get('folder') or os.path.join(
            os.path.expanduser('~'), 'Documents', 'Revit Exports')
        # Create window WITHOUT showing it — just to access all export logic
        window = batchout_mod.ExportManagerWindow()

        # Configure selections and format
        _apply_sheet_filter(window, filter_kw)
        _apply_format(window, fmt, combine)

        # Collect selected sheets
        selected = [s for s in window.all_sheets if s.IsSelected]
        if not selected:
            msg = "No sheets match '{}'. Check the sheet filter.".format(filter_kw or 'all sheets')
            _notify(progress_cb, msg)
            return False, 0, msg

        _notify(progress_cb, "Exporting {} sheet(s) to {}...".format(len(selected), fmt.upper()))

        # Ensure output folder exists
        export_folder = os.path.join(folder, _FMT_SUBFOLDER[fmt])
        if not os.path.exists(export_folder):
            os.makedirs(export_folder)

        # start_export normally initializes these; the hidden path bypasses it.
        window.output_folder.Text = folder
        window.selection_mode = 'sheets'
        window._ensure_titleblock_cache()
        window._reset_run_state()
        window._overall_counter = 0
        window._overall_total = len(selected)
        window._skipped_fatal = []
        window._safe_applied = []
        verify_extension = '.ifc' if fmt == 'ifc' else ('.pdf' if fmt == 'pdf' and combine else None)
        before_files = _output_snapshot(export_folder, verify_extension) if verify_extension else None

        # Dispatch to the correct export method
        combined = fmt == 'ifc' or (fmt == 'pdf' and combine)
        batches = [[item] for item in selected] if cancel_check and not combined else [selected]
        stopped = False
        for batch in batches:
            if cancel_check and cancel_check():
                stopped = True
                break
            reported = _run_export_method(window, fmt, batch, export_folder)
            if isinstance(reported, bool) or not isinstance(reported, int) or reported < 0:
                raise RuntimeError("The exporter returned an invalid output count: {!r}".format(reported))
            count += reported
        if cancel_check and cancel_check():
            stopped = True
        failures = list(getattr(window, '_failed_items', []))
        # IFC trusts the API return path; combined PDF can accept an existing
        # filename. Neither a stale nor empty file proves this run produced it.
        if verify_extension and count:
            after_files = _output_snapshot(export_folder, verify_extension)
            if not any(before_files.get(path) != stamp for path, stamp in after_files.items()):
                count = 0
                failures.append("No new or updated nonempty {} file could be verified.".format(fmt.upper()))
        expected = 1 if combined else len(selected)
        success = count == expected and not failures and not stopped
        if stopped:
            msg = "Export stopped. {} {} output(s) already created were kept.".format(count, fmt.upper())
        elif success:
            msg = "Export completed: {} {} output(s).".format(count, fmt.upper())
        elif count:
            msg = "Export incomplete: {} of {} expected {} output(s).".format(count, expected, fmt.upper())
        else:
            msg = "No {} outputs were confirmed. Check the export settings and Revit messages.".format(fmt.upper())
        if failures:
            msg += "\nDetails: " + "; ".join(str(item) for item in failures[:5])
        msg += "\nFolder: {}".format(export_folder)
        _notify(progress_cb, msg)
        return success, count, msg

    except Exception as ex:
        msg = "Export did not complete: {}".format(ex)
        _notify(progress_cb, msg)
        return False, count, msg
    finally:
        if window is not None:
            _close_hidden_window(window)


def _notify(callback, message):
    """Progress reporting must not change an export's result."""
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _validated_format(config):
    value = config.get('format', 'pdf')
    if not isinstance(value, str) or value.strip().lower() not in _FMT_ATTRS:
        raise ValueError("Choose one supported format: PDF, DWG, DWF, DGN, NWD, IFC or Images.")
    fmt = value.strip().lower()
    return 'img' if fmt == 'image' else fmt


def _output_snapshot(folder, extension):
    result = {}
    for entry in os.scandir(folder):
        if entry.name.lower().endswith(extension) and entry.is_file():
            stat = entry.stat()
            if stat.st_size:
                result[entry.path] = (stat.st_size, stat.st_mtime_ns)
    return result


def _close_hidden_window(window):
    """Dispose the hidden window without saving its temporary configuration."""
    try:
        window.Closing -= window._window_closing_save_setup
    except Exception:
        # Avoid Close when the save handler could still overwrite user defaults.
        pass
    else:
        try:
            window.Close()
        except Exception:
            pass
    try:
        window._window_closed_dispose(None, None)
    except Exception:
        pass


# ─── Extract export params from natural language ──────────────────────────────

def parse_export_params(raw_text):
    """Heuristically extract format and filter from a natural-language string.

    Returns:
        dict with 'format' (str) and 'filter' (str).
    """
    cmd = raw_text.lower()

    # Detect format
    fmt = 'pdf'  # sensible default
    for f in ['dwg', 'dwf', 'dgn', 'ifc', 'nwd', 'img', 'image', 'pdf']:
        if f in cmd:
            fmt = f
            break

    # Detect sheet prefix filter
    # Pattern 1: "G sheet" / "G sheets" / "G-sheet"
    m = re.search(r'\b([A-Z])\s*[-–]?\s*(?:sheet|tờ|bản vẽ)', raw_text, re.IGNORECASE)
    if m:
        return {'format': fmt, 'filter': m.group(1).upper()}

    # Pattern 2: standalone uppercase letter at word boundary (e.g. "G" in "toàn bộ G")
    m = re.search(r'\b([A-Z])\b', raw_text)
    if m:
        candidate = m.group(1)
        # Avoid false positives like "PDF" or single chars that are part of words
        if candidate not in ('P', 'D', 'W', 'F', 'I', 'N'):
            return {'format': fmt, 'filter': candidate}

    # Pattern 3: "all" / "toàn bộ" / "tất cả" → no filter
    if any(k in cmd for k in ['tat ca', 'toan bo', 'all', 'het', 'tất cả', 'toàn bộ', 'hết']):
        return {'format': fmt, 'filter': ''}

    return {'format': fmt, 'filter': ''}


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _apply_sheet_filter(window, filter_kw):
    """Select sheets matching filter_kw; select all if filter_kw is empty."""
    kw = filter_kw.lower().strip()

    for item in window.all_sheets:
        label = (item.SheetNumber + ' ' + item.SheetName).lower()
        if not kw:
            item.IsSelected = True
        else:
            item.IsSelected = label.startswith(kw) or (' ' + kw) in label

    # Sync to filtered_sheets (make sure filtered_sheets reflects all_sheets)
    window.filtered_sheets = list(window.all_sheets)

    # Refresh ListView and counter if the window is already initialised
    try:
        if hasattr(window, 'update_sheets_list'):
            window.update_sheets_list()
        elif hasattr(window, 'sheets_listview') and window.sheets_listview:
            from GUI.WPF_Base import to_items_source
            window.sheets_listview.ItemsSource = None
            window.sheets_listview.ItemsSource = to_items_source(window.filtered_sheets)
    except Exception:
        pass
    try:
        window.update_selection_count()
    except Exception:
        pass


def _apply_format(window, fmt, combine=False):
    """Enable only the specified export format; disable all others."""
    fmt = _validated_format({'format': fmt})
    selected_attr = _FMT_ATTRS[fmt]
    for attr in set(_FMT_ATTRS.values()):
        try:
            cb = getattr(window, attr)
            cb.IsChecked = (attr == selected_attr)
        except Exception:
            pass

    # Handle combine PDF option
    if fmt == 'pdf':
        try:
            window.combine_pdf.IsChecked = combine
        except Exception:
            pass


def _run_export_method(window, fmt, selected_items, output_folder):
    """Call the appropriate export_to_* method on the window."""
    method_map = {
        'pdf':   'export_to_pdf',
        'dwg':   'export_to_dwg',
        'dwf':   'export_to_dwf',
        'dgn':   'export_to_dgn',
        'nwd':   'export_to_nwd',
        'ifc':   'export_to_ifc',
        'img':   'export_to_images',
        'image': 'export_to_images',
    }
    method_name = method_map[_validated_format({'format': fmt})]
    method = getattr(window, method_name)
    result = method(selected_items, output_folder)
    return 0 if result is None else result
