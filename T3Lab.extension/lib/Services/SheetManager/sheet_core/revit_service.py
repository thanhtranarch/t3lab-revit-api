# -*- coding: utf-8 -*-
"""
Sheet Manager - Revit Service
CLEANED - Sheet Methods Only

Copyright © Dang Quoc Truong (DQT)
"""

from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet, BuiltInParameter


class RevitService(object):
    """Revit service for sheet operations"""
    
    def __init__(self, doc):
        self.doc = doc
    
    def get_all_sheets(self):
        """Get all sheets in the document"""
        try:
            from Services.SheetManager.sheet_core.data_models import SheetModel
        except Exception:
            from .data_models import SheetModel
        
        collector = FilteredElementCollector(self.doc).OfClass(ViewSheet)
        sheets = []
        
        for sheet in collector:
            if not sheet.IsPlaceholder:
                sheets.append(SheetModel(sheet))
        
        return sheets
    
    def update_sheet(self, sheet_model):
        """Update sheet parameters"""
        try:
            sheet = sheet_model.element
            
            # Update sheet number - use direct property
            if sheet_model.sheet_number != sheet_model._original_sheet_number:
                sheet.SheetNumber = sheet_model.sheet_number
            
            # Update sheet name - use direct property
            if sheet_model.sheet_name != sheet_model._original_sheet_name:
                sheet.Name = sheet_model.sheet_name
            
            return True
        except Exception as e:
            print("Error updating sheet: {}".format(str(e)))
            return False
    
    def create_sheet(self, sheet_number, sheet_name, titleblock_id):
        """Create a new sheet"""
        try:
            new_sheet = ViewSheet.Create(self.doc, titleblock_id)
            
            # Set number - use direct property
            new_sheet.SheetNumber = sheet_number
            
            # Set name - use direct property
            new_sheet.Name = sheet_name
            
            return new_sheet
        except Exception as e:
            print("Error creating sheet: {}".format(str(e)))
            return None
    
    