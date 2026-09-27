# -*- coding: utf-8 -*-
"""
Datum Sync Helper
=================
Revit API logic for synchronizing 2D extents, bubble visibility, and leaders
of Grids and Levels between views.

Author: T3Lab
"""
import math
from collections import namedtuple
from Autodesk.Revit import DB

DatumPoint = namedtuple('DatumPoint', ['X', 'Y', 'Z'])


class GridViewData(object):
    def __init__(self, name, curves=None, bubble0=False, bubble0_vis=False,
                 bubble1=False, bubble1_vis=False, has_leader0=False, has_leader1=False):
        self.name = name
        self.curves = curves or []
        self.bubble0 = bubble0
        self.bubble0_vis = bubble0_vis
        self.bubble1 = bubble1
        self.bubble1_vis = bubble1_vis
        self.has_leader0 = has_leader0
        self.has_leader1 = has_leader1


class LevelViewData(object):
    def __init__(self, name, curves=None, bubble0=False, bubble0_vis=False,
                 bubble1=False, bubble1_vis=False, has_leader0=False, has_leader1=False):
        self.name = name
        self.curves = curves or []
        self.bubble0 = bubble0
        self.bubble0_vis = bubble0_vis
        self.bubble1 = bubble1
        self.bubble1_vis = bubble1_vis
        self.has_leader0 = has_leader0
        self.has_leader1 = has_leader1


def is_plan_view(view):
    """Check if view is a horizontal plan view."""
    return view.ViewType in (
        DB.ViewType.FloorPlan,
        DB.ViewType.CeilingPlan,
        DB.ViewType.AreaPlan,
        DB.ViewType.EngineeringPlan,
    )


def is_elevation_or_section(view):
    """Check if view is vertical (elevation or section)."""
    return view.ViewType in (
        DB.ViewType.Elevation,
        DB.ViewType.Section,
        DB.ViewType.Detail,
    )


def get_view_grids_data(doc, view):
    """Collect 2D datum information of all grids visible in the given view."""
    collector = (DB.FilteredElementCollector(doc, view.Id)
                 .OfClass(DB.Grid)
                 .WhereElementIsNotElementType())
    grids_data = {}
    for grid in collector:
        try:
            curves = list(grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view))
            if not curves:
                # If view-specific curves not set yet, fallback to model curves in view
                curves = list(grid.GetCurvesInView(DB.DatumExtentType.Model, view))
            
            b0 = grid.HasBubbleInView(DB.DatumEnds.End0, view)
            b0_vis = grid.IsBubbleVisibleInView(DB.DatumEnds.End0, view) if b0 else False
            b1 = grid.HasBubbleInView(DB.DatumEnds.End1, view)
            b1_vis = grid.IsBubbleVisibleInView(DB.DatumEnds.End1, view) if b1 else False
            
            lead0 = grid.GetLeader(DB.DatumEnds.End0, view) is not None
            lead1 = grid.GetLeader(DB.DatumEnds.End1, view) is not None
            
            data = GridViewData(
                name=grid.Name,
                curves=curves,
                bubble0=b0,
                bubble0_vis=b0_vis,
                bubble1=b1,
                bubble1_vis=b1_vis,
                has_leader0=lead0,
                has_leader1=lead1,
            )
            grids_data[grid.Name] = data
        except Exception:
            continue
    return grids_data


def get_view_levels_data(doc, view):
    """Collect 2D datum information of all levels visible in the given view."""
    if not is_elevation_or_section(view):
        return {}
    collector = (DB.FilteredElementCollector(doc, view.Id)
                 .OfClass(DB.Level)
                 .WhereElementIsNotElementType())
    levels_data = {}
    for level in collector:
        try:
            curves = list(level.GetCurvesInView(DB.DatumExtentType.ViewSpecific, view))
            if not curves:
                curves = list(level.GetCurvesInView(DB.DatumExtentType.Model, view))
            
            b0 = level.HasBubbleInView(DB.DatumEnds.End0, view)
            b0_vis = level.IsBubbleVisibleInView(DB.DatumEnds.End0, view) if b0 else False
            b1 = level.HasBubbleInView(DB.DatumEnds.End1, view)
            b1_vis = level.IsBubbleVisibleInView(DB.DatumEnds.End1, view) if b1 else False
            
            lead0 = level.GetLeader(DB.DatumEnds.End0, view) is not None
            lead1 = level.GetLeader(DB.DatumEnds.End1, view) is not None
            
            data = LevelViewData(
                name=level.Name,
                curves=curves,
                bubble0=b0,
                bubble0_vis=b0_vis,
                bubble1=b1,
                bubble1_vis=b1_vis,
                has_leader0=lead0,
                has_leader1=lead1,
            )
            levels_data[level.Name] = data
        except Exception:
            continue
    return levels_data


def sync_grids_to_view(doc, target_view, source_grids_data,
                       sync_extents=True, sync_bubbles=True, sync_leaders=True):
    """Apply source grid datum data to target view."""
    collector = (DB.FilteredElementCollector(doc, target_view.Id)
                 .OfClass(DB.Grid)
                 .WhereElementIsNotElementType())
    applied_count = 0
    
    is_target_plan = is_plan_view(target_view)

    for grid in collector:
        if grid.Name not in source_grids_data:
            continue
        src = source_grids_data[grid.Name]
        if not src.curves:
            continue
        
        modified = False
        try:
            # 1. Extents (2D Curves)
            if sync_extents:
                tgt_curves = list(grid.GetCurvesInView(DB.DatumExtentType.ViewSpecific, target_view))
                if not tgt_curves:
                    tgt_curves = list(grid.GetCurvesInView(DB.DatumExtentType.Model, target_view))
                
                if tgt_curves and src.curves:
                    src_curve = src.curves[0]
                    tgt_curve = tgt_curves[0]
                    
                    new_curve = None
                    if is_target_plan:
                        # For Plan views, match XY endpoints, keep target view Z
                        p0 = src_curve.GetEndPoint(0)
                        p1 = src_curve.GetEndPoint(1)
                        z_val = tgt_curve.GetEndPoint(0).Z
                        pt0 = DB.XYZ(p0.X, p0.Y, z_val)
                        pt1 = DB.XYZ(p1.X, p1.Y, z_val)
                        
                        if isinstance(src_curve, DB.Arc) and isinstance(tgt_curve, DB.Arc):
                            mid_pt = src_curve.Evaluate(0.5, True)
                            pt_mid = DB.XYZ(mid_pt.X, mid_pt.Y, z_val)
                            new_curve = DB.Arc.Create(pt0, pt1, pt_mid)
                        else:
                            if pt0.DistanceTo(pt1) > 0.001:
                                new_curve = DB.Line.CreateBound(pt0, pt1)
                    else:
                        # For Elevation/Section views
                        p0 = src_curve.GetEndPoint(0)
                        p1 = src_curve.GetEndPoint(1)
                        # Keep target projection plane, set Z elevation
                        t0 = tgt_curve.GetEndPoint(0)
                        t1 = tgt_curve.GetEndPoint(1)
                        pt0 = DB.XYZ(t0.X, t0.Y, p0.Z)
                        pt1 = DB.XYZ(t1.X, t1.Y, p1.Z)
                        if pt0.DistanceTo(pt1) > 0.001:
                            new_curve = DB.Line.CreateBound(pt0, pt1)

                    if new_curve and grid.IsCurveValidInView(DB.DatumExtentType.ViewSpecific, target_view, new_curve):
                        grid.SetDatumExtentType(DB.DatumEnds.End0, target_view, DB.DatumExtentType.ViewSpecific)
                        grid.SetDatumExtentType(DB.DatumEnds.End1, target_view, DB.DatumExtentType.ViewSpecific)
                        grid.SetCurveInView(DB.DatumExtentType.ViewSpecific, target_view, new_curve)
                        modified = True

            # 2. Bubble visibility
            if sync_bubbles:
                if src.bubble0:
                    if src.bubble0_vis:
                        grid.ShowBubbleInView(DB.DatumEnds.End0, target_view)
                    else:
                        grid.HideBubbleInView(DB.DatumEnds.End0, target_view)
                    modified = True
                if src.bubble1:
                    if src.bubble1_vis:
                        grid.ShowBubbleInView(DB.DatumEnds.End1, target_view)
                    else:
                        grid.HideBubbleInView(DB.DatumEnds.End1, target_view)
                    modified = True

            # 3. Leaders
            if sync_leaders:
                if src.has_leader0:
                    if not grid.GetLeader(DB.DatumEnds.End0, target_view):
                        try:
                            grid.AddLeader(DB.DatumEnds.End0, target_view)
                            modified = True
                        except Exception:
                            pass
                if src.has_leader1:
                    if not grid.GetLeader(DB.DatumEnds.End1, target_view):
                        try:
                            grid.AddLeader(DB.DatumEnds.End1, target_view)
                            modified = True
                        except Exception:
                            pass

            if modified:
                applied_count += 1
        except Exception:
            continue

    return applied_count


def sync_levels_to_view(doc, target_view, source_levels_data,
                        sync_extents=True, sync_bubbles=True, sync_leaders=True):
    """Apply source level datum data to target elevation/section view."""
    if not is_elevation_or_section(target_view):
        return 0

    collector = (DB.FilteredElementCollector(doc, target_view.Id)
                 .OfClass(DB.Level)
                 .WhereElementIsNotElementType())
    applied_count = 0

    for level in collector:
        if level.Name not in source_levels_data:
            continue
        src = source_levels_data[level.Name]
        if not src.curves:
            continue

        modified = False
        try:
            # 1. Extents
            if sync_extents:
                tgt_curves = list(level.GetCurvesInView(DB.DatumExtentType.ViewSpecific, target_view))
                if not tgt_curves:
                    tgt_curves = list(level.GetCurvesInView(DB.DatumExtentType.Model, target_view))
                
                if tgt_curves and src.curves:
                    src_curve = src.curves[0]
                    tgt_curve = tgt_curves[0]

                    p0 = src_curve.GetEndPoint(0)
                    p1 = src_curve.GetEndPoint(1)
                    t0 = tgt_curve.GetEndPoint(0)
                    t1 = tgt_curve.GetEndPoint(1)

                    # Project horizontal extents based on view direction
                    if abs(target_view.ViewDirection.X) > abs(target_view.ViewDirection.Y):
                        pt0 = DB.XYZ(t0.X, p0.Y, t0.Z)
                        pt1 = DB.XYZ(t1.X, p1.Y, t1.Z)
                    else:
                        pt0 = DB.XYZ(p0.X, t0.Y, t0.Z)
                        pt1 = DB.XYZ(p1.X, t1.Y, t1.Z)

                    if pt0.DistanceTo(pt1) > 0.001:
                        new_curve = DB.Line.CreateBound(pt0, pt1)
                        if level.IsCurveValidInView(DB.DatumExtentType.ViewSpecific, target_view, new_curve):
                            level.SetDatumExtentType(DB.DatumEnds.End0, target_view, DB.DatumExtentType.ViewSpecific)
                            level.SetDatumExtentType(DB.DatumEnds.End1, target_view, DB.DatumExtentType.ViewSpecific)
                            level.SetCurveInView(DB.DatumExtentType.ViewSpecific, target_view, new_curve)
                            modified = True

            # 2. Bubbles
            if sync_bubbles:
                if src.bubble0:
                    if src.bubble0_vis:
                        level.ShowBubbleInView(DB.DatumEnds.End0, target_view)
                    else:
                        level.HideBubbleInView(DB.DatumEnds.End0, target_view)
                    modified = True
                if src.bubble1:
                    if src.bubble1_vis:
                        level.ShowBubbleInView(DB.DatumEnds.End1, target_view)
                    else:
                        level.HideBubbleInView(DB.DatumEnds.End1, target_view)
                    modified = True

            # 3. Leaders
            if sync_leaders:
                if src.has_leader0 and not level.GetLeader(DB.DatumEnds.End0, target_view):
                    try:
                        level.AddLeader(DB.DatumEnds.End0, target_view)
                        modified = True
                    except Exception:
                        pass
                if src.has_leader1 and not level.GetLeader(DB.DatumEnds.End1, target_view):
                    try:
                        level.AddLeader(DB.DatumEnds.End1, target_view)
                        modified = True
                    except Exception:
                        pass

            if modified:
                applied_count += 1
        except Exception:
            continue

    return applied_count
