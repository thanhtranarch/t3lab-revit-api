# -*- coding: utf-8 -*-
"""
Sheet Manager - Place Views Service

Copyright © Dang Quoc Truong (DQT)
"""

from Autodesk.Revit.DB import FilteredElementCollector, View, Viewport, XYZ, UV


class PlaceViewsService(object):
    """Handle placing views on sheets"""
    
    def __init__(self, doc):
        self.doc = doc
    
    def get_placeable_views(self):
        """Get all views that can be placed on sheets"""
        try:
            # First, get all viewports to check which views are already placed
            viewports = FilteredElementCollector(self.doc).OfClass(Viewport)
            placed_view_ids = set()
            for vp in viewports:
                placed_view_ids.add(vp.ViewId)
            
            collector = FilteredElementCollector(self.doc).OfClass(View)
            
            placeable_views = []
            for view in collector:
                # Skip templates, schedules on sheets, legends on sheets
                if (not view.IsTemplate and 
                    view.CanBePrinted and
                    hasattr(view, 'ViewType')):
                    
                    # Check if view ID is in placed views
                    on_sheet = view.Id in placed_view_ids
                    
                    placeable_views.append({
                        'element': view,
                        'id': view.Id,
                        'name': view.Name,
                        'type': str(view.ViewType),
                        'on_sheet': on_sheet
                    })
                    
                    if on_sheet:
                        print("DEBUG: View '{}' is already on a sheet".format(view.Name))
            
            return placeable_views
        except Exception as e:
            print("Error getting placeable views: {}".format(str(e)))
            return []
    
    def place_view_on_sheet(self, sheet, view, location=None):
        """Place a view on a sheet"""
        try:
            # Revit allows legends on multiple sheets, but rejects ordinary
            # views already placed and views that cannot host a viewport.
            if not Viewport.CanAddViewToSheet(self.doc, sheet.Id, view.Id):
                return None

            # Default center location if not specified
            if location is None:
                # Get sheet center
                outline = sheet.Outline
                center_x = (outline.Min.U + outline.Max.U) / 2
                center_y = (outline.Min.V + outline.Max.V) / 2
                location = XYZ(center_x, center_y, 0)
            
            # Create viewport
            viewport = Viewport.Create(self.doc, sheet.Id, view.Id, location)
            print("SUCCESS: Placed view '{}' on sheet '{}'".format(view.Name, sheet.SheetNumber))
            return viewport
            
        except Exception as e:
            print("Error placing view on sheet: {}".format(str(e)))
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
            return None
    
    def auto_arrange_views_on_sheet(self, sheet, views, rows=2, cols=2):
        """Auto-arrange multiple views on a sheet"""
        views = list(views)
        if rows < 1 or cols < 1:
            raise ValueError("Rows and columns must be positive")
        if len(views) > rows * cols:
            raise ValueError("Grid has {} cells for {} views; increase rows or columns".format(
                rows * cols, len(views)))
        outline = sheet.Outline
        width = outline.Max.U - outline.Min.U
        height = outline.Max.V - outline.Min.V
        if width <= 0 or height <= 0:
            raise ValueError("Sheet has no usable outline")
        viewports = []
        for index, view in enumerate(views):
            row, col = divmod(index, cols)
            location = XYZ(outline.Min.U + (col + 0.5) * width / cols,
                           outline.Max.V - (row + 0.5) * height / rows, 0)
            viewport = self.place_view_on_sheet(sheet, view, location)
            if viewport is not None:
                viewports.append(viewport)
        return viewports

    def batch_place_views(self, sheets, views, mode='one_per_sheet', rows=2, cols=2):
        """Place every requested view or report invalid capacity before writing.

        The caller owns the Revit transaction. Returned entries are only
        confirmed placements; a non-placeable view is never counted as success.
        """
        sheets, views = list(sheets), list(views)
        if not sheets or not views:
            return []
        if mode == 'one_per_sheet':
            if len(views) > len(sheets):
                raise ValueError("Select at least one sheet per view, or use Distribute")
            groups = [[view] for view in views]
        elif mode == 'all_on_each':
            groups = [views for sheet in sheets]
        elif mode == 'distribute':
            count, remainder = divmod(len(views), len(sheets))
            groups, start = [], 0
            for index in range(len(sheets)):
                size = count + (1 if index < remainder else 0)
                groups.append(views[start:start + size])
                start += size
        else:
            raise ValueError("Unknown placement mode: " + str(mode))
        if mode != 'one_per_sheet':
            if rows < 1 or cols < 1 or any(len(group) > rows * cols for group in groups):
                raise ValueError("The grid is too small. Increase rows or columns before placing views")
        placements = []
        for sheet, group in zip(sheets, groups):
            if mode == 'one_per_sheet':
                viewport = self.place_view_on_sheet(sheet, group[0])
                created = [viewport] if viewport is not None else []
            else:
                created = self.auto_arrange_views_on_sheet(sheet, group, rows, cols)
            placements.extend({'sheet': sheet, 'viewport': viewport} for viewport in created)
        return placements
