# -*- coding: utf-8 -*-
"""
Crop Sync Helper
================
Revit API logic for synchronizing Crop Region shapes, Crop Box visibility/activity,
and Annotation Crop across views.

Author: T3Lab
"""
from Autodesk.Revit import DB


class ViewCropData(object):
    def __init__(self, is_active=False, is_visible=False, annotation_active=False,
                 can_have_shape=False, is_shaped=False, curve_loops=None,
                 width=0.0, height=0.0):
        self.is_active = is_active
        self.is_visible = is_visible
        self.annotation_active = annotation_active
        self.can_have_shape = can_have_shape
        self.is_shaped = is_shaped
        self.curve_loops = curve_loops or []
        self.width = width
        self.height = height


def get_view_crop_data(view):
    """Retrieve detailed crop region parameters and geometry for a given view."""
    if not hasattr(view, "GetCropRegionShapeManager"):
        return None
    try:
        shape_mgr = view.GetCropRegionShapeManager()
    except Exception:
        return None

    is_active = view.CropBoxActive
    is_visible = view.CropBoxVisible
    
    # Check annotation crop parameter
    anno_param = view.get_Parameter(DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
    annotation_active = bool(anno_param.AsInteger()) if anno_param else False

    can_have_shape = shape_mgr.CanHaveShape
    crop_shapes = []
    is_shaped = False
    width = 0.0
    height = 0.0

    try:
        boundary_list = shape_mgr.GetCropShape()
        if boundary_list and len(boundary_list) > 0:
            crop_shapes = list(boundary_list)
            # Check bounding box size from view.CropBox
            box = view.CropBox
            if box:
                width = abs(box.Max.X - box.Min.X) * 304.8  # mm
                height = abs(box.Max.Y - box.Min.Y) * 304.8  # mm
            # Check if shaped (non-rectangular)
            is_shaped = shape_mgr.IsCropRegionShapeValid(crop_shapes[0]) and len(list(crop_shapes[0])) != 4
    except Exception:
        pass

    return ViewCropData(
        is_active=is_active,
        is_visible=is_visible,
        annotation_active=annotation_active,
        can_have_shape=can_have_shape,
        is_shaped=is_shaped,
        curve_loops=crop_shapes,
        width=width,
        height=height,
    )


def sync_crop_to_view(doc, target_view, source_crop_data,
                      apply_shape=True, set_active=True, set_visible=True,
                      sync_annotation_crop=True):
    """Apply source crop data to a target view."""
    if not hasattr(target_view, "GetCropRegionShapeManager"):
        return False
    try:
        shape_mgr = target_view.GetCropRegionShapeManager()
    except Exception:
        return False

    success = False

    # 1. Activate crop box
    if set_active:
        target_view.CropBoxActive = True
        success = True

    if set_visible:
        target_view.CropBoxVisible = source_crop_data.is_visible
        success = True

    # 2. Annotation Crop
    if sync_annotation_crop:
        anno_param = target_view.get_Parameter(DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
        if anno_param and not anno_param.IsReadOnly:
            anno_param.Set(1 if source_crop_data.annotation_active else 0)
            success = True

    # 3. Apply Shape
    if apply_shape and source_crop_data.curve_loops and shape_mgr.CanHaveShape:
        for loop in source_crop_data.curve_loops:
            try:
                # Test validity on target view directly
                if shape_mgr.IsCropRegionShapeValid(loop):
                    shape_mgr.RemoveCropRegionShape()
                    shape_mgr.SetCropShape(loop)
                    success = True
                    break
                else:
                    # If target is on different Z plane, project loop curves to target view plane
                    curves = list(loop)
                    if curves:
                        target_box = target_view.CropBox
                        target_z = target_box.Min.Z if target_box else 0.0
                        projected_loop = DB.CurveLoop()
                        for c in curves:
                            p0 = c.GetEndPoint(0)
                            p1 = c.GetEndPoint(1)
                            pt0 = DB.XYZ(p0.X, p0.Y, target_z)
                            pt1 = DB.XYZ(p1.X, p1.Y, target_z)
                            if pt0.DistanceTo(pt1) > 0.001:
                                if isinstance(c, DB.Arc):
                                    mid = c.Evaluate(0.5, True)
                                    pt_mid = DB.XYZ(mid.X, mid.Y, target_z)
                                    projected_loop.Append(DB.Arc.Create(pt0, pt1, pt_mid))
                                else:
                                    projected_loop.Append(DB.Line.CreateBound(pt0, pt1))
                        
                        if shape_mgr.IsCropRegionShapeValid(projected_loop):
                            shape_mgr.RemoveCropRegionShape()
                            shape_mgr.SetCropShape(projected_loop)
                            success = True
                            break
            except Exception:
                continue

    return success
