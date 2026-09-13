# -*- coding: utf-8 -*-
"""WorksetService — Headless service for inspecting, creating, and managing Revit worksets."""

import os
import sys

from pyrevit import revit, script
from Snippets._compat import eid_value
from Snippets._host import get_revit_version

try:
    import clr
    for _r in ('System', 'PresentationFramework', 'PresentationCore', 'WindowsBase'):
        try:
            clr.AddReference(_r)
        except Exception:
            pass
except Exception:
    clr = None

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        FilteredWorksetCollector,
        Workset,
        WorksetKind,
        Transaction,
        TransactionStatus,
        WorksetTable,
        DeleteWorksetSettings,
        DeleteWorksetOption,
        View3D,
        ViewFamilyType,
        ViewFamily,
        WorksetVisibility,
    )
except Exception:
    FilteredElementCollector = FilteredWorksetCollector = Workset = WorksetKind = None
    Transaction = TransactionStatus = WorksetTable = DeleteWorksetSettings = DeleteWorksetOption = None
    View3D = ViewFamilyType = ViewFamily = WorksetVisibility = None

logger = script.get_logger()
REVIT_VERSION = get_revit_version()

DEFAULT_LIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'GUI', 'workset_list.txt')

DEFAULT_WORKSET_LIST = [
    "01_Shared Levels and Grids_CORE_OFF",
    "01_Shared Levels and Grids_PH_OFF",
    "01_Shared Levels and Grids_RA_OFF",
    "01_Shared Levels and Grids_SA_OFF",
    "01_Shared Levels and Grids_ROOF_OFF",
    "01_Shared Levels and Grids_for Coordination",
    "02_Link Architecture Models_OFF",
    "02_Link Architecture Models_Attachment",
    "03_Link Structural Models_OFF",
    "04_Link Interior Models_OFF",
    "05_Link Facade Models_OFF",
    "06_Link Site Models_OFF",
    "07_Link Landscape Models_OFF",
    "08_Link Other 3D Data_OFF",
    "09_Link MEP Models_OFF",
    "10_Do not use_OFF",
    "11_Link Cad Consultant_OFF",
    "11_Link Cad Internal_OFF",
    "11_Link Cad Subcon_OFF",
    "12_Link PBU Models",
    "ARC_3DLine-3DText",
    "ARC_3DRoomTag",
    "ARC_Ancillary",
    "ARC_AreaRoomSpace",
    "ARC_BMU",
    "ARC_Ceiling",
    "ARC_DoorAndWindow",
    "ARC_ExteriallWallAndFacade",
    "ARC_ExteriorRoofAndCanopy",
    "ARC_FireProvision",
    "ARC_FloorFinish",
    "ARC_FloorStructural_OFF",
    "ARC_Floor",
    "ARC_Furniture",
    "ARC_Matchline",
    "ARC_Misc",
    "ARC_NonPBU",
    "ARC_NonStructureWall",
    "ARC_ParkingLots",
    "ARC_PlantingSoil",
    "ARC_Railing",
    "ARC_Ramp",
    "ARC_RoadAndPavement",
    "ARC_SanitaryAndDrainage",
    "ARC_Signage",
    "ARC_StructuralCore_OFF",
    "ARC_StructuralColumn_OFF",
    "ARC_StructuralSlabElement_OFF",
    "ARC_StructureWall_OFF",
    "ARC_Temporary_OFF",
    "ARC_Tile Line (Model)",
    "ARC_Toilets",
    "ARC_WallExterior",
    "ARC_WallFinish",
    "ARC_WallInterior",
    "Workset1",
]


def load_workset_list(list_file=None):
    filepath = list_file or DEFAULT_LIST_FILE
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r") as f:
                names = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            if names:
                return names
        except Exception:
            pass
    return list(DEFAULT_WORKSET_LIST)


def save_workset_list(names, list_file=None):
    filepath = list_file or DEFAULT_LIST_FILE
    try:
        with open(filepath, "w") as f:
            f.write("# Workset List for T3Lab\n")
            f.write("# One workset name per line. Lines starting with '#' are comments.\n\n")
            for name in names:
                f.write(name + "\n")
    except Exception as ex:
        logger.warning("Could not save workset list: {}".format(ex))


def get_user_worksets(doc):
    """Return all UserWorkset instances in the document."""
    if not doc or not doc.IsWorkshared:
        return []
    return list(
        FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset).ToWorksets()
    )


def get_workset_names(doc):
    """Return list of names of all user worksets."""
    return [ws.Name for ws in get_user_worksets(doc)]


def get_active_workset_id(doc):
    """Return the ElementId of the active workset."""
    try:
        return doc.GetWorksetTable().GetActiveWorksetId()
    except Exception:
        return None


def enable_worksharing(doc):
    """Enable worksharing with default workset names."""
    if not doc:
        return False
    try:
        doc.EnableWorksharing("_SHARED LEVELS & GRIDS", "_ARCHITECT")
        return True
    except Exception as e:
        logger.error("Failed to enable worksharing: {}".format(e))
        return False


def _commit_transaction(transaction):
    """A commit may return RolledBack without raising an exception."""
    options = transaction.GetFailureHandlingOptions()
    options.SetForcedModalHandling(True)
    transaction.SetFailureHandlingOptions(options)
    status = transaction.Commit()
    if status != TransactionStatus.Committed:
        raise RuntimeError("Transaction was not committed: {}".format(status))


def _rollback_started(transaction):
    """Do not roll back a transaction that failed to start or already ended."""
    if transaction is not None and transaction.GetStatus() == TransactionStatus.Started:
        transaction.RollBack()


def create_worksets(doc, workset_names, existing_names=None):
    """Create worksets not already present; returns list of created names."""
    if not doc:
        return []
    if existing_names is None:
        existing_names = set(get_workset_names(doc))
    elif not isinstance(existing_names, set):
        existing_names = set(existing_names)

    created = []
    for name in workset_names:
        if name not in existing_names:
            t = Transaction(doc, "T3Lab: Create Workset: {}".format(name))
            try:
                t.Start()
                Workset.Create(doc, name)
                _commit_transaction(t)
                created.append(name)
                existing_names.add(name)
            except Exception as e:
                _rollback_started(t)
                if t.GetStatus() == TransactionStatus.Pending:
                    # Revit must finish failure processing before another transaction.
                    raise
                logger.warning("Failed to create workset '{}': {}".format(name, e))
    return created


def _get_3d_view_type_id(doc):
    viewtypes = FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements()
    return next(
        (vt.Id for vt in viewtypes if vt.ViewFamily == ViewFamily.ThreeDimensional),
        None
    )


def create_workset_views(doc):
    """Create one 3D isometric view per user workset, isolating visibility to that workset."""
    if not doc or not doc.IsWorkshared:
        return None, None, "Document is not workshared."

    type_id = _get_3d_view_type_id(doc)
    if type_id is None:
        return None, None, "No 3D view family type found in document."

    worksets = get_user_worksets(doc)
    if not worksets:
        return None, None, "No user worksets found."

    existing = set(v.Name for v in FilteredElementCollector(doc).OfClass(View3D).ToElements())
    created, skipped = [], []

    t = Transaction(doc, "T3Lab: Create Workset Views")
    try:
        t.Start()
        for ws in worksets:
            if ws.Name in existing:
                skipped.append(ws.Name)
                continue
            view3d = View3D.CreateIsometric(doc, type_id)
            view3d.Name = ws.Name
            for other in worksets:
                vis = (WorksetVisibility.Visible
                       if eid_value(other.Id) == eid_value(ws.Id)
                       else WorksetVisibility.Hidden)
                view3d.SetWorksetVisibility(other.Id, vis)
            created.append(ws.Name)
        _commit_transaction(t)
    except Exception as e:
        _rollback_started(t)
        return None, None, str(e)

    return created, skipped, None


def delete_workset(doc, ws_to_delete, ws_to_reassign=None):
    """Delete a user workset, moving its elements into another workset or deleting them."""
    t = None
    try:
        ws_all = get_user_worksets(doc)
        ws_del = next((w for w in ws_all if w.Name == ws_to_delete), None)
        if not ws_del:
            return False, "Workset to delete not found: {}".format(ws_to_delete)

        if ws_to_reassign:
            ws_keep = next((w for w in ws_all if w.Name == ws_to_reassign), None)
            if not ws_keep:
                return False, "Workset to reassign into not found: {}".format(ws_to_reassign)
            settings = DeleteWorksetSettings(DeleteWorksetOption.MoveElementsToWorkset, ws_keep.Id)
        else:
            settings = DeleteWorksetSettings(DeleteWorksetOption.DeleteElements)

        t = Transaction(doc, "T3Lab: Delete Workset: {}".format(ws_to_delete))
        t.Start()
        WorksetTable.DeleteWorkset(doc, ws_del.Id, settings)
        _commit_transaction(t)
        return True, None
    except Exception as e:
        _rollback_started(t)
        if t is not None and t.GetStatus() == TransactionStatus.Pending:
            # Callers may delete several worksets; abort that batch until Revit
            # finishes failure processing rather than returning a recoverable failure.
            raise
        return False, str(e)
