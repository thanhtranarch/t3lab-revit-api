# -*- coding: utf-8 -*-
"""Saved print sets. Writes require a caller-owned Revit transaction.

API contract checked against the installed Revit 2025 RevitAPI.xml:
ViewSheetSetting.SaveAs/Save/Rename/Delete, ViewSheetSet.Views and
ViewSet.Insert(View). Read paths never access PrintManager.
"""
from contextlib import contextmanager

from Autodesk.Revit.DB import (
    FilteredElementCollector, PrintRange, ViewSet, ViewSheet, ViewSheetSet,
)


class ViewSheetSetsService(object):
    def __init__(self, doc):
        self.doc = doc

    @staticmethod
    def _id_key(element_id):
        # Value supports 64-bit IDs (2024+); IntegerValue supports older Revit.
        try:
            return element_id.Value
        except AttributeError:
            return element_id.IntegerValue

    def get_all_sheet_sets(self):
        """Return persisted elements and their real ElementIds; propagate errors."""
        with FilteredElementCollector(self.doc) as collector:
            elements = collector.OfClass(ViewSheetSet).ToElements()
        return sorted(
            [{'element': item, 'name': item.Name, 'id': item.Id}
             for item in elements], key=lambda item: item['name'].casefold())

    def get_available_sheets(self):
        with FilteredElementCollector(self.doc) as collector:
            elements = collector.OfClass(ViewSheet).ToElements()
        return sorted(
            [sheet for sheet in elements
             if not sheet.IsPlaceholder and sheet.CanBePrinted],
            key=lambda sheet: (sheet.SheetNumber, sheet.Name))

    def _require_transaction(self):
        if not self.doc.IsModifiable:
            raise RuntimeError('A caller-owned transaction is required for sheet set changes.')

    def _saved_set(self, sheet_set):
        if (not isinstance(sheet_set, ViewSheetSet)
                or not sheet_set.IsValidObject or sheet_set.Document != self.doc):
            raise ValueError('Select a saved sheet set in the current document.')
        element = self.doc.GetElement(sheet_set.Id)
        if not isinstance(element, ViewSheetSet):
            raise ValueError('The selected sheet set no longer exists.')
        return element

    def _name(self, name, sheet_set=None):
        name = (name or '').strip()
        if not name:
            raise ValueError('Enter a non-empty sheet set name.')
        for item in self.get_all_sheet_sets():
            if (item['name'].casefold() == name.casefold()
                    and (sheet_set is None or item['id'] != sheet_set.Id)):
                raise ValueError('A sheet set named "{}" already exists.'.format(name))
        return name

    def _sheets(self, sheet_ids):
        sheets = {}
        for element_id in sheet_ids:
            sheet = self.doc.GetElement(element_id)
            if (not isinstance(sheet, ViewSheet) or sheet.IsPlaceholder
                    or not sheet.CanBePrinted):
                raise ValueError('Every selected ID must identify a printable sheet.')
            sheets[self._id_key(sheet.Id)] = sheet
        return list(sheets.values())

    @staticmethod
    def _view_set(views):
        result = ViewSet()
        for view in views:
            result.Insert(view)  # ViewSet accepts View, never ElementId.
        return result

    @contextmanager
    def _setting(self):
        """Use local print settings; never Apply or SubmitPrint global settings."""
        manager = self.doc.PrintManager
        previous_range = manager.PrintRange
        setting = None
        previous_set = None
        in_session_views = None
        try:
            manager.PrintRange = PrintRange.Select
            setting = manager.ViewSheetSetting
            previous_set = setting.CurrentViewSheetSet
            in_session_views = self._view_set(setting.InSession.Views)
            yield setting
        finally:
            try:
                if setting is not None and in_session_views is not None:
                    setting.InSession.Views = in_session_views
                    # Delete invalidates the old current element immediately.
                    setting.CurrentViewSheetSet = (
                        previous_set if previous_set.IsValidObject else setting.InSession)
            finally:
                manager.PrintRange = previous_range

    @staticmethod
    def _check(result, operation):
        if not result:
            raise RuntimeError('Revit could not {} the sheet set.'.format(operation))

    def create_sheet_set(self, name, sheet_ids=None):
        self._require_transaction()
        name = self._name(name)
        views = self._view_set(self._sheets(sheet_ids or []))
        with self._setting() as setting:
            setting.CurrentViewSheetSet = setting.InSession
            setting.CurrentViewSheetSet.Views = views
            self._check(setting.SaveAs(name), 'create')
            # Resolve the persisted element rather than returning an in-session wrapper.
            for item in self.get_all_sheet_sets():
                if item['name'] == name:
                    return item['element']
            raise RuntimeError('Revit did not return the newly saved sheet set.')

    def delete_sheet_set(self, set_name):
        self._require_transaction()
        sheet_set = next((item['element'] for item in self.get_all_sheet_sets()
                          if item['name'] == set_name), None)
        if sheet_set is None:
            raise ValueError('The sheet set no longer exists: {}'.format(set_name))
        with self._setting() as setting:
            setting.CurrentViewSheetSet = sheet_set
            self._check(setting.Delete(), 'delete')
        return True

    def rename_sheet_set(self, sheet_set, new_name):
        self._require_transaction()
        sheet_set = self._saved_set(sheet_set)
        new_name = self._name(new_name, sheet_set)
        if new_name == sheet_set.Name:
            return True
        with self._setting() as setting:
            setting.CurrentViewSheetSet = sheet_set
            self._check(setting.Rename(new_name), 'rename')
        return True

    def _save_views(self, sheet_set, views):
        old_ids = {self._id_key(view.Id) for view in sheet_set.Views}
        new_ids = {self._id_key(view.Id) for view in views}
        if old_ids == new_ids:
            return True  # Save throws when the current set is unchanged.
        with self._setting() as setting:
            setting.CurrentViewSheetSet = sheet_set
            setting.CurrentViewSheetSet.Views = self._view_set(views)
            self._check(setting.Save(), 'save')
        return True

    def add_sheets_to_set(self, sheet_set, sheet_ids):
        self._require_transaction()
        sheet_set = self._saved_set(sheet_set)
        views = {self._id_key(view.Id): view for view in sheet_set.Views}
        for sheet in self._sheets(sheet_ids):
            views[self._id_key(sheet.Id)] = sheet
        return self._save_views(sheet_set, list(views.values()))

    def remove_sheets_from_set(self, sheet_set, sheet_ids):
        self._require_transaction()
        sheet_set = self._saved_set(sheet_set)
        remove = {self._id_key(sheet.Id) for sheet in self._sheets(sheet_ids)}
        views = [view for view in sheet_set.Views if self._id_key(view.Id) not in remove]
        return self._save_views(sheet_set, views)

    def replace_sheets_in_set(self, sheet_set, sheet_ids):
        """Atomic membership update, preserving non-sheet views in mixed print sets."""
        self._require_transaction()
        sheet_set = self._saved_set(sheet_set)
        sheets = self._sheets(sheet_ids)  # Validate everything before assigning Views.
        views = [view for view in sheet_set.Views if not isinstance(view, ViewSheet)]
        return self._save_views(sheet_set, views + sheets)

    def get_sheets_in_set(self, sheet_set):
        """Return sheet ElementIds, not View objects or non-sheet view IDs."""
        return [view.Id for view in self._saved_set(sheet_set).Views
                if isinstance(view, ViewSheet)]
