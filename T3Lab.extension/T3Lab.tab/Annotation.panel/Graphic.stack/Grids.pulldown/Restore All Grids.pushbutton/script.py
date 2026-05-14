# -*- coding: utf-8 -*-
"""
Restore All Grids
Restore all grid heads and tails to their saved positions.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Restore All Grids"
__version__ = "1.0.0"

import os
import pickle
from tempfile import gettempdir
from collections import namedtuple

from pyrevit import revit, DB, UI, script

logger = script.get_logger()
doc = revit.doc
uidoc = revit.uidoc

Point = namedtuple('Point', ['X', 'Y', 'Z'])

tempfile_path = os.path.join(gettempdir(), 'GridPlacement')

cView = doc.ActiveView
Axes = [doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds()]

if cView.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation]:
    UI.TaskDialog.Show('T3Lab', 'Support for \'{}\' view type is experimental!'.format(cView.ViewType))

if cView.ViewType in [DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.Detail,
                      DB.ViewType.AreaPlan, DB.ViewType.Section, DB.ViewType.Elevation]:

    if len(Axes) < 1:
        Axes = list(DB.FilteredElementCollector(doc, cView.Id).OfClass(DB.Grid).ToElements())

    try:
        with open(tempfile_path, 'rb') as fp:
            GridLines = pickle.load(fp)
    except IOError:
        UI.TaskDialog.Show('T3Lab', 'Could not find saved placement of the grid.\nSave placement first.')
        GridLines = {}

    n = 0

    for cAxis in Axes:
        if not isinstance(cAxis, DB.Grid):
            continue
        if cAxis.Name not in GridLines:
            continue

        curves = cAxis.GetCurvesInView(DB.DatumExtentType.ViewSpecific, cView)
        if len(curves) != 1:
            UI.TaskDialog.Show('T3Lab', 'Grid \'{}\' defined by {} curves, unable to proceed.'.format(
                cAxis.Name, len(curves)))
            continue

        cCurve = curves[0]
        cGridData = GridLines[cAxis.Name]

        tmp = cCurve.GetEndPoint(0)
        if cView.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation]:
            pt0 = DB.XYZ(tmp.X, tmp.Y, cGridData['Start'].Z)
        else:
            pt0 = DB.XYZ(cGridData['Start'].X, cGridData['Start'].Y, tmp.Z)

        tmp1 = cCurve.GetEndPoint(1)
        if cView.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation]:
            pt1 = DB.XYZ(tmp.X, tmp.Y, cGridData['End'].Z)
        else:
            pt1 = DB.XYZ(cGridData['End'].X, cGridData['End'].Y, tmp1.Z)

        if isinstance(cCurve, DB.Arc):
            ptRef = cCurve.Evaluate(0.5, True)
            gridline = DB.Arc.Create(pt0, pt1, ptRef)
        else:
            gridline = DB.Line.CreateBound(pt0, pt1)

        if cAxis.IsCurveValidInView(DB.DatumExtentType.ViewSpecific, cView, gridline):
            with revit.Transaction('Restore grid curve \'{}\''.format(cAxis.Name)):
                cAxis.SetCurveInView(DB.DatumExtentType.ViewSpecific, cView, gridline)

        with revit.Transaction('Restore grid placement \'{}\''.format(cAxis.Name)):
            if cGridData['StartBubble'] and cGridData['StartBubbleVisible']:
                cAxis.ShowBubbleInView(DB.DatumEnds.End0, cView)
                if 'Leader0Anchor' in cGridData:
                    if not cAxis.GetLeader(DB.DatumEnds.End0, cView):
                        cAxis.AddLeader(DB.DatumEnds.End0, cView)
            else:
                cAxis.HideBubbleInView(DB.DatumEnds.End0, cView)

            if cGridData['EndBubble'] and cGridData['EndBubbleVisible']:
                cAxis.ShowBubbleInView(DB.DatumEnds.End1, cView)
                if 'Leader1Anchor' in cGridData:
                    if not cAxis.GetLeader(DB.DatumEnds.End1, cView):
                        cAxis.AddLeader(DB.DatumEnds.End1, cView)
            else:
                cAxis.HideBubbleInView(DB.DatumEnds.End1, cView)
        n += 1

    if n != 1:
        msg = 'Restored placement for {} grids'.format(n)
    else:
        msg = 'Restored placement of the grid \'{}\''.format(cAxis.Name)
    UI.TaskDialog.Show('T3Lab', msg)

else:
    UI.TaskDialog.Show('T3Lab', 'View type \'{}\' not supported.'.format(cView.ViewType))
