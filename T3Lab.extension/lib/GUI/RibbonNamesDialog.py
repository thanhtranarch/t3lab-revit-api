# -*- coding: utf-8 -*-
"""Ribbon Name Manager Dialog class."""

import os
from pyrevit import forms
from GUI.WPF_Base import T3WPFWindow
import clr
clr.AddReference("System.Data")
from System import String as System_String
from System import DBNull
from System.Data import DataTable
from System.Windows import WindowState

# unicode() shim for IronPython 2 / CPython 3
try:
    _unicode = unicode
except NameError:
    _unicode = str

# Absolute path to XAML
_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'RibbonNames.xaml')

class RibbonNameWindow(T3WPFWindow):
    def __init__(self, live_tabs, short_map, originals, default_map, on_save_callback, on_state_callback, on_originals_callback):
        # T3WPFWindow.__init__ loads XAML and registers named controls
        T3WPFWindow.__init__(self, _XAML)
        
        self.live_tabs = list(live_tabs)
        self.short_map = dict(short_map)
        self.originals = dict(originals)
        self.default_map = default_map
        
        self.on_save_callback = on_save_callback
        self.on_state_callback = on_state_callback
        self.on_originals_callback = on_originals_callback
        self.message = None
        self._snapshot_identities()

        # Build DataTable and bind to DataGrid (which has x:Name="Grid")
        self.table = DataTable("tabs")
        self.table.Columns.Add("CurrentName", System_String)
        self.table.Columns.Add("ShortName", System_String)
        self._build_rows()
        self.Grid.ItemsSource = self.table.DefaultView

        # Wire buttons
        self.BtnShort.Click += self._on_apply_short
        self.BtnFull.Click += self._on_restore_full
        self.BtnSave.Click += self._on_save
        self.BtnReset.Click += self._on_reset
        self.BtnClose.Click += self._on_close
        if self._identity_errors or self._identity_warnings:
            self._update_sub(" ".join(self._identity_errors + self._identity_warnings))

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
        self.Close()

    def _snapshot_identities(self):
        """Resolve once, before the editable alias map or live titles can change."""
        stored = self.originals.get("__tab_ids__", {})
        identities = dict(stored) if isinstance(stored, dict) else {}
        self._tab_identities = []
        self._identity_errors = []
        self._identity_warnings = []
        self._originals_dirty = False
        tab_ids = []
        for tab in self.live_tabs:
            try:
                tab_id = str(tab.Id).strip() if tab.Id is not None else ""
            except Exception:
                tab_id = ""
            tab_ids.append(tab_id)

        for tab, tab_id in zip(self.live_tabs, tab_ids):
            try:
                title = str(tab.Title)
            except Exception as exc:
                self._identity_errors.append("Cannot read a ribbon tab title: {}.".format(exc))
                continue
            if tab_id and tab_ids.count(tab_id) > 1:
                self._identity_errors.append("Duplicate tab ID for '{}'; no title change will be made.".format(title))
                continue
            full = identities.get(tab_id) if tab_id else None
            if not isinstance(full, str) or not full:
                candidates = {name for name, alias in self.short_map.items() if alias == title}
                if title in self.short_map or self.originals.get(title) == title:
                    candidates.add(title)
                if len(candidates) > 1:
                    self._identity_errors.append(
                        "Ambiguous title '{}': {}. Restore its full title and reopen this tool.".format(
                            title, ", ".join(sorted(candidates))))
                    continue
                full = next(iter(candidates)) if candidates else title
            self._tab_identities.append((tab, full))
            if tab_id:
                if identities.get(tab_id) != full:
                    identities[tab_id] = full
                    self._originals_dirty = True
            else:
                self._identity_warnings.append(
                    "Tab '{}' has no stable ID; its identity is available only in this session.".format(title))
            if self.originals.get(full) != full:
                self.originals[full] = full
                self._originals_dirty = True
        self.originals["__tab_ids__"] = identities

    def _full_name_of(self, tab):
        for original_tab, full in self._tab_identities:
            if original_tab is tab:
                return full
        return None

    def _build_rows(self):
        seen = set()
        for _tab, full in self._tab_identities:
            if full in seen:
                continue
            seen.add(full)
            short = self.short_map.get(full, full)
            self.table.Rows.Add(full, short)

    def _collect_map_from_grid(self):
        m = {}
        for row in self.table.Rows:
            full = self._cell(row, "CurrentName")
            short = self._cell(row, "ShortName").strip()
            if not short:
                short = full
            m[full] = short
        return m

    @staticmethod
    def _cell(row, col):
        try:
            val = row[col]
        except Exception:
            return u""
        if val is None or val == DBNull.Value:
            return u""
        try:
            return _unicode(val)
        except Exception:
            return str(val)

    def _commit_grid(self):
        try:
            if not self.Grid.CommitEdit() or not self.Grid.CommitEdit():
                self._update_sub("Finish correcting the current cell before applying or saving names.")
                return False
            return True
        except Exception as exc:
            self._update_sub("Could not finish editing the current cell: {}.".format(exc))
            return False

    @staticmethod
    def _persist(callback, value, label):
        if callback is None:
            return False, "{} was not saved: no save callback is available.".format(label)
        try:
            if callback(value):
                return True, ""
            return False, "{} was not saved. Check write access and retry.".format(label)
        except Exception as exc:
            return False, "{} was not saved: {}.".format(label, exc)

    def _ensure_originals_saved(self):
        if not self._originals_dirty:
            return True
        ok, error = self._persist(self.on_originals_callback, self.originals, "Original tab identities")
        if not ok:
            self._update_sub(error + " No title changes were attempted.")
            return False
        self._originals_dirty = False
        return True

    def _candidate_map(self):
        candidate = dict(self.short_map)
        candidate.update(self._collect_map_from_grid())
        aliases = {}
        for full, alias in candidate.items():
            if alias in aliases and aliases[alias] != full:
                self._update_sub("Short name '{}' is shared by '{}' and '{}'. Use distinct names before saving or applying.".format(
                    alias, aliases[alias], full))
                return None
            aliases[alias] = full
        return candidate

    def _apply_titles(self, names):
        changed = unchanged = 0
        failures = list(self._identity_errors)
        for tab, full in self._tab_identities:
            target = names.get(full, full)
            try:
                if tab.Title == target:
                    unchanged += 1
                else:
                    tab.Title = target
                    if tab.Title != target:
                        raise RuntimeError("the ribbon did not accept the new title")
                    changed += 1
            except Exception as exc:
                failures.append("Tab '{}': {}.".format(full, exc))
        return changed, unchanged, failures

    def _current_state(self, names):
        if self._identity_errors or not self._tab_identities:
            return "mixed"
        try:
            if all(tab.Title == full for tab, full in self._tab_identities):
                return "full"
            if all(tab.Title == names.get(full, full) for tab, full in self._tab_identities):
                return "short"
        except Exception:
            pass
        return "mixed"

    def _on_apply_short(self, sender, args):
        if not self._commit_grid():
            return
        candidate = self._candidate_map()
        if candidate is None or not self._ensure_originals_saved():
            return
        applied, unchanged, failures = self._apply_titles(candidate)
        map_ok, map_error = self._persist(self.on_save_callback, candidate, "Short-name map")
        if map_ok:
            self.short_map = candidate
        state = "mixed" if failures or not map_ok else "short"
        _state_ok, state_error = self._persist(self.on_state_callback, state, "Ribbon state")
        report = "Applied short names to {} tab(s); {} unchanged; {} failed or unresolved.".format(
            applied, unchanged, len(failures))
        report += " Map saved." if map_ok else " " + map_error
        self._update_sub(" ".join([report] + failures + [state_error] + self._identity_warnings).strip())

    def _on_restore_full(self, sender, args):
        if not self._commit_grid() or not self._ensure_originals_saved():
            return
        restored, unchanged, failures = self._apply_titles({})
        state = "mixed" if failures else "full"
        _ok, state_error = self._persist(self.on_state_callback, state, "Ribbon state")
        report = "Restored full names on {} tab(s); {} unchanged; {} failed or unresolved.".format(
            restored, unchanged, len(failures))
        self._update_sub(" ".join([report] + failures + [state_error] + self._identity_warnings).strip())

    def _on_save(self, sender, args):
        if not self._commit_grid():
            return
        candidate = self._candidate_map()
        if candidate is None or not self._ensure_originals_saved():
            return
        ok, error = self._persist(self.on_save_callback, candidate, "Short-name map")
        state_error = ""
        if ok:
            self.short_map = candidate
            _state_ok, state_error = self._persist(
                self.on_state_callback, self._current_state(candidate), "Ribbon state")
        self._update_sub(" ".join(
            (["Short-name map saved. Current tab titles have not been changed."] if ok else [error])
            + [state_error] + self._identity_errors + self._identity_warnings).strip())

    def _on_reset(self, sender, args):
        if not self._commit_grid():
            return
        for row in self.table.Rows:
            full = self._cell(row, "CurrentName")
            row["ShortName"] = self.default_map.get(full, full)
        self._update_sub("Short names reset to defaults (not yet applied).")

    def _on_close(self, sender, args):
        self.Close()

    def _update_sub(self, text):
        self.message = text
        self.HeaderSub.Text = text
        self.HeaderSub.ToolTip = text

def show_ribbon_names_dialog(live_tabs, short_map, originals, default_map, on_save_callback, on_state_callback, on_originals_callback):
    """Factory function to show the Ribbon Name dialog."""
    dlg = RibbonNameWindow(live_tabs, short_map, originals, default_map, on_save_callback, on_state_callback, on_originals_callback)
    dlg.ShowDialog()
    return dlg
