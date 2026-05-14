# -*- coding: utf-8 -*-
"""
Save Grids
Save current grid head and tail positions for later restoration.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Save Grids"
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
Axis = namedtuple('Axis', ['Name', 'Start', 'End', 'StartBubble', 'EndBubble',
                            'StartBubbleVisible', 'EndBubbleVisible'])

tempfile_path = os.path.join(gettempdir(), 'GridPlacement')

cView = doc.ActiveView

if cView.ViewType in [DB.ViewType.Section, DB.ViewType.Elevation]:
    UI.TaskDialog.Show('T3Lab', 'Support for \'{}\' view type is experimental!'.format(cView.ViewType))

if cView.ViewType in [DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.Detail,
                      DB.ViewType.AreaPlan, DB.ViewType.Section, DB.ViewType.Elevation]:

    selection = [doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds()]

    n = 0
    GridLines = dict()

    for el in selection:
        if isinstance(el, DB.Grid):
            curves = el.GetCurvesInView(DB.DatumExtentType.ViewSpecific, cView)
            if len(curves) != 1:
                UI.TaskDialog.Show('T3Lab', 'Grid \'{}\' defined by {} curves, unable to proceed.'.format(
                    el.Name, len(curves)))
            else:
                cGridLine = {
                    'Name': '', 'Start': Point(0, 0, 0), 'End': Point(0, 0, 0),
                    'StartBubble': False, 'StartBubbleVisible': False,
                    'EndBubble': False, 'EndBubbleVisible': False
                }
                cCurve = curves[0]

                leader0 = el.GetLeader(DB.DatumEnds.End0, cView)
                if leader0:
                    tmp = leader0.Elbow
                    cGridLine['Leader0Elbow'] = Point(tmp.X, tmp.Y, tmp.Z)
                    tmp = leader0.End
                    cGridLine['Leader0End'] = Point(tmp.X, tmp.Y, tmp.Z)
                    tmp = leader0.Anchor
                    cGridLine['Leader0Anchor'] = Point(tmp.X, tmp.Y, tmp.Z)

                leader1 = el.GetLeader(DB.DatumEnds.End1, cView)
                if leader1:
                    tmp = leader1.Elbow
                    cGridLine['Leader1Elbow'] = Point(tmp.X, tmp.Y, tmp.Z)
                    tmp = leader1.End
                    cGridLine['Leader1End'] = Point(tmp.X, tmp.Y, tmp.Z)
                    tmp = leader1.Anchor
                    cGridLine['Leader1Anchor'] = Point(tmp.X, tmp.Y, tmp.Z)

                cGridLine['Name'] = el.Name

                tmp = cCurve.GetEndPoint(0)
                cGridLine['Start'] = Point(tmp.X, tmp.Y, tmp.Z)
                tmp = cCurve.GetEndPoint(1)
                cGridLine['End'] = Point(tmp.X, tmp.Y, tmp.Z)

                if el.HasBubbleInView(DB.DatumEnds.End0, cView):
                    cGridLine['StartBubble'] = True
                if el.HasBubbleInView(DB.DatumEnds.End1, cView):
                    cGridLine['EndBubble'] = True
                if el.IsBubbleVisibleInView(DB.DatumEnds.End0, cView):
                    cGridLine['StartBubbleVisible'] = True
                if el.IsBubbleVisibleInView(DB.DatumEnds.End1, cView):
                    cGridLine['EndBubbleVisible'] = True

                if isinstance(cCurve, DB.Arc):
                    tmp = cCurve.Center
                    cGridLine['Center'] = Point(tmp.X, tmp.Y, tmp.Z)

                GridLines[cGridLine['Name']] = cGridLine
                n += 1
        else:
            if isinstance(el, DB.MultiSegmentGrid):
                UI.TaskDialog.Show('T3Lab', 'Skipping unsupported Multi-Segment grid \'{}\''.format(el.Name))
            else:
                UI.TaskDialog.Show('T3Lab', 'Skipping non-grid element \'{}\''.format(el.Name))

    if n > 0:
        with open(tempfile_path, 'wb') as fp:
            pickle.dump(GridLines, fp)
        if n != 1:
            msg = 'Saved {} grid placements to {}'.format(n, tempfile_path)
        else:
            msg = 'Saved grid \'{}\' placement to {}'.format(cGridLine['Name'], tempfile_path)
        UI.TaskDialog.Show('T3Lab', msg)
    else:
        UI.TaskDialog.Show('T3Lab', 'Nothing to save.')

else:
    UI.TaskDialog.Show('T3Lab', 'View type \'{}\' not supported.'.format(cView.ViewType))
