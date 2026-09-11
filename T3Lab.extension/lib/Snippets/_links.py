# -*- coding: utf-8 -*-
"""
_links.py
=========
Revit API helpers for managing Revit links (RVT links):

* discover link types / instances in the host document
* read and apply the **worksets** of a linked model
* read and change the **host workset** a link instance sits on
* read and apply the per-view **display settings** of a link
  (By Host View / By Linked View / Custom)

Kept free of any WPF/UI reference so it can be reused by other tools.

RevitLinkGraphicsSettings and View.Get/Set/RemoveLinkOverrides only exist from
Revit 2025 onwards; every display-settings helper here degrades gracefully on
older releases (see display_api_available()).

Part of T3Lab Extension.
"""

import os

from Autodesk.Revit.DB import (
    BuiltInParameter,
    CheckoutStatus,
    Element,
    ElementId,
    ExternalResourceTypes,
    FilteredElementCollector,
    FilteredWorksetCollector,
    LinkedFileStatus,
    LinkLoadResultType,
    ModelPathUtils,
    OverrideGraphicSettings,
    RevitLinkInstance,
    RevitLinkType,
    View,
    ViewType,
    WorksetConfiguration,
    WorksetConfigurationOption,
    WorksetId,
    WorksetKind,
    WorksharingUtils,
)

import Autodesk.Revit.DB as DB

import System
from System.Collections.Generic import List as NetList


# -- VERSION SHIMS -----------------------------------------------------------

def eid_int(element_id):
    """Integer value of an ElementId, valid on Revit 2023 - 2026+."""
    if element_id is None:
        return -1
    try:
        return element_id.Value          # Revit 2024+
    except AttributeError:
        return element_id.IntegerValue   # Revit 2023


def new_element_id(value):
    """Build an ElementId from a plain int.

    A bare Python int makes the ElementId constructor ambiguous under pythonnet
    (BuiltInParameter / BuiltInCategory / Int64 all match), so the value is
    widened explicitly: Int64 on Revit 2024+, Int32 on Revit 2023.
    """
    try:
        return ElementId(System.Int64(int(value)))
    except Exception:
        pass
    try:
        return ElementId(System.Int32(int(value)))
    except Exception:
        return ElementId(int(value))   # IronPython resolves this without help


def eid_int_workset(workset_id):
    """Integer value of a WorksetId across Revit versions."""
    try:
        return workset_id.IntegerValue
    except AttributeError:
        pass
    try:
        return workset_id.Value
    except AttributeError:
        return int(str(workset_id))


def element_name(element):
    """Element name without tripping over the shadowed Name property."""
    if element is None:
        return ""
    try:
        return Element.Name.__get__(element)
    except Exception:
        pass
    try:
        return Element.Name.GetValue(element)
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        return ""


def display_api_available():
    """True when this Revit build exposes the link display-settings API (2025+)."""
    return (hasattr(DB, 'RevitLinkGraphicsSettings')
            and hasattr(DB, 'LinkVisibility')
            and hasattr(View, 'SetLinkOverrides'))


# Display-settings modes, ordered exactly as the combo boxes present them.
DISPLAY_MODES = ("By Host View", "By Linked View", "Custom")

# Aspects of a Custom display setting that the Revit API exposes.
#
#   key    - property name on RevitLinkGraphicsSettings, or the aspect name for
#            the method-backed ones (Discipline, ViewDetailLevel)
#   label  - what the UI shows
#   kind   - "prop" (plain LinkVisibility property) or a method-backed setter
#   custom - True when Revit also accepts LinkVisibility.Custom for this aspect.
#            Only View Filters does; the others raise
#            "Disallowed LinkVisibility value" (verified in Revit 2026).
CUSTOM_ASPECTS = (
    ("ObjectStyles",    "Object Styles", "prop",       False),
    ("ViewFilterType",  "View Filters",  "prop",       True),
    ("ViewRange",       "View Range",    "prop",       False),
    ("ColorFill",       "Color Fill",    "prop",       False),
    ("NestedLinks",     "Nested Links",  "prop",       False),
    ("Discipline",      "Discipline",    "discipline", False),
    ("ViewDetailLevel", "Detail Level",  "detail",     False),
)


def aspect_modes(allows_custom):
    """Mode labels a given aspect accepts."""
    return DISPLAY_MODES if allows_custom else DISPLAY_MODES[:2]


def _link_visibility(mode_index):
    """Map a DISPLAY_MODES index onto a LinkVisibility enum value."""
    lv = DB.LinkVisibility
    return (lv.ByHostView, lv.ByLinkView, lv.Custom)[max(0, min(2, int(mode_index)))]


def _mode_index(link_visibility_value):
    """Map a LinkVisibility enum value back onto a DISPLAY_MODES index."""
    name = str(link_visibility_value)
    if name == "ByLinkView":
        return 1
    if name == "Custom":
        return 2
    return 0


# -- LINK DISCOVERY ----------------------------------------------------------

class LinkRecord(object):
    """One Revit link in the host document: the type and every placed instance."""

    def __init__(self, link_type, instances=None):
        # A link type can be placed more than once. Keeping only the first
        # instance made "hide this link" / "halftone this link" silently act on
        # one copy while the others stayed as they were, which reads as the tool
        # having done nothing.
        if instances is None:
            instances = []
        elif not isinstance(instances, (list, tuple)):
            instances = [instances]
        instances = [i for i in instances if i is not None]

        instance = instances[0] if instances else None
        self.link_type = link_type
        self.instance = instance
        self.instances = instances
        self.instance_count = len(instances)
        self.type_id = link_type.Id
        self.instance_id = instance.Id if instance is not None else ElementId.InvalidElementId
        self.instance_ids = [i.Id for i in instances]
        self.name = element_name(link_type)
        self.is_nested = bool(getattr(link_type, 'IsNestedLink', False))
        self.pinned = bool(getattr(instance, 'Pinned', False)) if instance is not None else False

        try:
            self.status = str(link_type.GetLinkedFileStatus())
        except Exception:
            self.status = "Unknown"
        self.is_loaded = (self.status == str(LinkedFileStatus.Loaded))

        self.link_doc = None
        if instance is not None:
            try:
                self.link_doc = instance.GetLinkDocument()
            except Exception:
                self.link_doc = None

        self.path = _link_source_path(link_type)
        self.is_cloud = self.path.lower().startswith("autodesk docs:")

        try:
            self.path_type = str(link_type.PathType)
        except Exception:
            self.path_type = ""

        self.host_workset = ""
        if instance is not None:
            try:
                p = instance.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                if p is not None:
                    self.host_workset = p.AsValueString() or ""
            except Exception:
                pass


def _link_source_path(link_type):
    """User-visible source path of a link - disk, Revit Server or cloud."""
    try:
        ref = link_type.GetExternalResourceReference(
            ExternalResourceTypes.BuiltInExternalResourceTypes.RevitLink)
        if ref is not None:
            p = ref.InSessionPath
            if p:
                return p
    except Exception:
        pass
    try:
        ext = link_type.GetExternalFileReference()
        if ext is not None:
            mp = ext.GetAbsolutePath()
            if mp is not None:
                p = ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
                if p:
                    return p
    except Exception:
        pass
    return ""


def collect_links(doc, loaded_only=False, include_nested=False):
    """Every RVT link in doc, paired with its first placed instance."""
    if doc is None:
        return []

    instances_by_type = {}
    try:
        for inst in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
            key = eid_int(inst.GetTypeId())
            instances_by_type.setdefault(key, []).append(inst)
    except Exception:
        pass

    records = []
    try:
        for lt in FilteredElementCollector(doc).OfClass(RevitLinkType):
            if not include_nested and bool(getattr(lt, 'IsNestedLink', False)):
                continue
            rec = LinkRecord(lt, instances_by_type.get(eid_int(lt.Id), []))
            if loaded_only and not rec.is_loaded:
                continue
            records.append(rec)
    except Exception:
        pass

    records.sort(key=lambda r: r.name.lower())
    return records


def existing_link_keys(doc):
    """(normalised paths, file names) of models already linked into doc."""
    paths = set()
    names = set()
    if doc is None:
        return paths, names
    try:
        for lt in FilteredElementCollector(doc).OfClass(RevitLinkType):
            name = element_name(lt)
            if name:
                names.add(name.split(" : ")[0].strip().lower())
            p = _link_source_path(lt)
            if p:
                paths.add(os.path.normpath(p).lower())
                names.add(os.path.basename(p).lower())
    except Exception:
        pass
    return paths, names


# -- WORKSETS OF A LINKED MODEL ----------------------------------------------

class LinkWorkset(object):
    """A user workset inside a linked model."""

    def __init__(self, workset_id, name, is_open, visible_by_default):
        self.workset_id = workset_id          # int
        self.name = name
        self.is_open = bool(is_open)
        self.visible_by_default = bool(visible_by_default)


def get_link_worksets(link_doc):
    """User worksets of an open link document, with their open/closed state."""
    result = []
    if link_doc is None:
        return result
    try:
        if not link_doc.IsWorkshared:
            return result
    except Exception:
        return result
    try:
        for ws in FilteredWorksetCollector(link_doc).OfKind(WorksetKind.UserWorkset):
            result.append(LinkWorkset(eid_int_workset(ws.Id), ws.Name,
                                      ws.IsOpen, ws.IsVisibleByDefault))
    except Exception:
        pass
    result.sort(key=lambda w: w.name.lower())
    return result


def split_workset_ids(worksets, open_names, closed_names):
    """Split a link's worksets into the ids to open and the ids to close.

    Matching is by workset **name**, so one edited list can drive many links.
    A workset whose name appears in neither set keeps its current state.
    Returns ``(open_ids, close_ids)``.
    """
    open_ids = []
    close_ids = []
    for ws in worksets or []:
        if ws.name in open_names:
            open_ids.append(ws.workset_id)
        elif ws.name in closed_names:
            close_ids.append(ws.workset_id)
    return open_ids, close_ids


def workset_config_ids(worksets, open_names, closed_names):
    """Open/close ids covering **every** workset of one link.

    ``split_workset_ids`` only classifies the worksets whose name the user
    actually saw in the grid. ``apply_link_worksets`` then starts its
    WorksetConfiguration from OpenAllWorksets, so anything left unclassified was
    silently **opened** -- which is what happens when the workset list of the
    focused link does not cover every link being reloaded.

    This classifies the leftovers explicitly by their current state instead, so
    a workset nobody ticked comes back exactly as it was.
    Returns ``(open_ids, close_ids)``.
    """
    open_ids = []
    close_ids = []
    for ws in worksets or []:
        if ws.name in open_names:
            open_ids.append(ws.workset_id)
        elif ws.name in closed_names:
            close_ids.append(ws.workset_id)
        elif getattr(ws, 'is_open', True):
            open_ids.append(ws.workset_id)
        else:
            close_ids.append(ws.workset_id)
    return open_ids, close_ids


def apply_link_worksets(link_type, open_ids, close_ids):
    """Reload link_type with the given worksets opened / closed.

    Must run with **no transaction open** - Revit reloads links outside the
    transaction model. Returns (ok, message).
    """
    if link_type is None:
        return False, "Link no longer exists"

    try:
        config = WorksetConfiguration(WorksetConfigurationOption.OpenAllWorksets)
        if close_ids:
            closing = NetList[WorksetId]()
            for wid in close_ids:
                closing.Add(WorksetId(int(wid)))
            config.Close(closing)
        if open_ids:
            opening = NetList[WorksetId]()
            for wid in open_ids:
                opening.Add(WorksetId(int(wid)))
            config.Open(opening)
    except Exception as ex:
        return False, "Workset configuration failed: {}".format(str(ex).split("\n")[0])

    try:
        ref = None
        try:
            ref = link_type.GetExternalResourceReference(
                ExternalResourceTypes.BuiltInExternalResourceTypes.RevitLink)
        except Exception:
            ref = None

        if ref is not None:
            load_result = link_type.LoadFrom(ref, config)
        else:
            ext = link_type.GetExternalFileReference()
            model_path = ext.GetAbsolutePath()
            load_result = link_type.LoadFrom(model_path, config)
    except Exception as ex:
        return False, str(ex).split("\n")[0]

    if load_result is None:
        return False, "Reload returned no result"

    res = load_result.LoadResult
    ok_results = [LinkLoadResultType.LinkLoaded]
    for extra in ('LinkAlreadyLoaded', 'UsedExisting'):
        if hasattr(LinkLoadResultType, extra):
            ok_results.append(getattr(LinkLoadResultType, extra))
    if res in ok_results:
        return True, "Reloaded"
    return False, str(res)


# -- WORKSET OF THE LINK IN THE HOST MODEL -----------------------------------
# Khác hẳn phần trên: ở đây là workset của CHÍNH host document mà link instance
# đang nằm trên, không phải workset bên trong file link. Đổi workset chỉ là ghi
# ELEM_PARTITION_PARAM trong một transaction — không reload link.

class HostWorkset(object):
    """A user workset of the host document."""

    def __init__(self, workset_id, name, is_open, is_editable, owner):
        self.workset_id = workset_id          # int
        self.name = name
        self.is_open = bool(is_open)
        self.is_editable = bool(is_editable)
        self.owner = owner or ""


def is_workshared(doc):
    """True when doc has worksharing enabled (worksets exist at all)."""
    try:
        return bool(doc is not None and doc.IsWorkshared)
    except Exception:
        return False


def get_host_worksets(doc):
    """User worksets of the host document, sorted by name."""
    result = []
    if not is_workshared(doc):
        return result
    try:
        for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            result.append(HostWorkset(eid_int_workset(ws.Id), ws.Name,
                                      ws.IsOpen, ws.IsEditable, ws.Owner))
    except Exception:
        pass
    result.sort(key=lambda w: w.name.lower())
    return result


def active_workset_id(doc):
    """Id of the workset new elements land on, or -1."""
    if not is_workshared(doc):
        return -1
    try:
        return eid_int_workset(doc.GetWorksetTable().GetActiveWorksetId())
    except Exception:
        return -1


def _workset_param(element):
    if element is None:
        return None
    try:
        return element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    except Exception:
        return None


def element_workset_name(element):
    """Name of the workset an element sits on ("" when it has none)."""
    p = _workset_param(element)
    if p is None:
        return ""
    try:
        return p.AsValueString() or ""
    except Exception:
        return ""


def element_workset_id(element):
    """Id of the workset an element sits on, or -1."""
    p = _workset_param(element)
    if p is None:
        return -1
    try:
        return int(p.AsInteger())
    except Exception:
        return -1


def element_owner(doc, element_id):
    """Username holding an element, "" when nobody does."""
    try:
        info = WorksharingUtils.GetWorksharingTooltipInfo(doc, element_id)
        return info.Owner or ""
    except Exception:
        return ""


def owned_by_other(doc, element_id):
    """True when another user has the element checked out."""
    try:
        return (WorksharingUtils.GetCheckoutStatus(doc, element_id)
                == CheckoutStatus.OwnedByOtherUser)
    except Exception:
        return False


def link_host_workset(link_record):
    """Workset label of a link's placed instances in the host document.

    A link placed twice can sit on two different worksets; saying so is the
    point - "Mixed" tells the user why one apply will change two things.
    Returns (label, workset_id) with workset_id -1 when there is nothing to
    move or the instances disagree.
    """
    instances = list(getattr(link_record, 'instances', None) or [])
    if not instances:
        return "Not placed", -1

    names = []
    ids = []
    for inst in instances:
        name = element_workset_name(inst)
        names.append(name or "—")
        ids.append(element_workset_id(inst))

    if len(set(ids)) == 1:
        return names[0], ids[0]
    return "Mixed ({} instances)".format(len(instances)), -1


def set_element_workset(doc, element, workset_id):
    """Move one element onto workset_id. Needs an OPEN transaction.

    Returns (ok, message); ok is True when the element already sat there.
    """
    if element is None:
        return False, "Element no longer exists"
    p = _workset_param(element)
    if p is None:
        return False, "No workset parameter"

    try:
        if int(p.AsInteger()) == int(workset_id):
            return True, "Already there"
    except Exception:
        pass

    try:
        if p.IsReadOnly:
            owner = element_owner(doc, element.Id)
            return False, ("Owned by %s" % owner) if owner else "Workset is read-only"
    except Exception:
        pass

    if owned_by_other(doc, element.Id):
        owner = element_owner(doc, element.Id)
        return False, ("Owned by %s" % owner) if owner else "Owned by another user"

    try:
        p.Set(int(workset_id))
        return True, "Moved"
    except Exception as ex:
        owner = element_owner(doc, element.Id)
        if owner:
            return False, "Owned by %s" % owner
        return False, str(ex).split("\n")[0]


def set_link_workset(doc, link_record, workset_id, include_type=True):
    """Put every placed instance of a link - and optionally its type - on one
    workset of the HOST document. Needs an OPEN transaction.

    Returns (ok, message). The first instance that refuses stops this link and
    its reason is what comes back; instances already written stay written inside
    the caller's transaction, so undoing the whole apply is one Ctrl+Z.
    """
    if link_record is None:
        return False, "Link no longer exists"
    instances = list(getattr(link_record, 'instances', None) or [])
    if not instances:
        return False, "Not placed in this model"

    moved = 0
    for inst in instances:
        ok, message = set_element_workset(doc, inst, workset_id)
        if not ok:
            return False, message
        if message == "Moved":
            moved += 1

    if include_type:
        ok, type_message = set_element_workset(doc, link_record.link_type, workset_id)
        if not ok:
            return False, "Type: %s" % type_message
        if type_message == "Moved":
            moved += 1

    if moved == 0:
        return True, "Already there"
    return True, "Moved"


# -- VIEWS INSIDE A LINKED MODEL ---------------------------------------------

_LINKED_VIEW_TYPES = (
    ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
    ViewType.AreaPlan, ViewType.Section, ViewType.Elevation,
    ViewType.Detail, ViewType.ThreeD, ViewType.DraftingView,
)


def get_link_views(link_doc, view_type=None):
    """Placeable views of a link document as [(id_int, label), ...].

    view_type narrows the list to views of the same ViewType as the host view,
    which is what the Revit UI offers for "By Linked View".
    """
    views = []
    if link_doc is None:
        return views
    try:
        for v in FilteredElementCollector(link_doc).OfClass(View):
            try:
                if v.IsTemplate:
                    continue
                vt = v.ViewType
                if view_type is not None:
                    if vt != view_type:
                        continue
                elif vt not in _LINKED_VIEW_TYPES:
                    continue
                views.append((eid_int(v.Id), "{} - {}".format(str(vt), element_name(v))))
            except Exception:
                continue
    except Exception:
        pass
    views.sort(key=lambda x: x[1].lower())
    return views


# -- PER-VIEW DISPLAY SETTINGS -----------------------------------------------

class LinkDisplayState(object):
    """The display settings of one link in one view."""

    def __init__(self):
        self.mode_index = 0            # index into DISPLAY_MODES
        self.linked_view_id = -1
        self.visible = True
        self.halftone = False
        self.aspects = {}              # aspect name -> DISPLAY_MODES index


def get_link_display(view, link_record):
    """Read the display settings of link_record in view."""
    state = LinkDisplayState()
    if view is None or link_record is None:
        return state

    inst_id = link_record.instance_id
    if inst_id is not None and inst_id != ElementId.InvalidElementId:
        try:
            element = view.Document.GetElement(inst_id)
            if element is not None:
                state.visible = not element.IsHidden(view)
        except Exception:
            pass
        try:
            ogs = view.GetElementOverrides(inst_id)
            state.halftone = bool(ogs.Halftone) if ogs is not None else False
        except Exception:
            pass

    if not display_api_available():
        return state

    settings = None
    for target in (inst_id, link_record.type_id):
        if target is None or target == ElementId.InvalidElementId:
            continue
        try:
            settings = view.GetLinkOverrides(target)
        except Exception:
            settings = None
        if settings is not None:
            break

    if settings is None:
        return state

    try:
        state.mode_index = _mode_index(settings.LinkVisibilityType)
    except Exception:
        pass
    try:
        state.linked_view_id = eid_int(settings.LinkedViewId)
    except Exception:
        pass
    for key, _label, kind, _custom in CUSTOM_ASPECTS:
        try:
            if kind == "prop":
                state.aspects[key] = _mode_index(getattr(settings, key))
            elif kind == "discipline":
                state.aspects[key] = _mode_index(settings.GetDisciplineType())
            elif kind == "detail":
                state.aspects[key] = _mode_index(settings.GetViewDetailLevelType())
        except Exception:
            state.aspects[key] = 0
    return state


def set_link_display(view, link_record, mode_index, linked_view_id=-1, aspects=None):
    """Write the display settings of link_record in view.

    Caller must have an open transaction. Returns (ok, message).
    """
    if not display_api_available():
        return False, "Requires Revit 2025 or newer"
    if view is None or link_record is None:
        return False, "No view or link"

    targets = _placed_instance_ids(link_record) or [link_record.type_id]

    if int(mode_index) == 0:
        for target in targets:
            try:
                view.RemoveLinkOverrides(target)
            except Exception as ex:
                return False, str(ex).split("\n")[0]
        return True, "By Host View"

    mode_index = int(mode_index)
    has_view = linked_view_id is not None and int(linked_view_id) > 0

    if mode_index == 1 and not has_view:
        return False, "Pick a view inside the link first"

    if mode_index == 2 and aspects and not has_view:
        # Any aspect that defers to the link needs a linked view to defer to.
        if any(int(v) >= 1 for v in aspects.values()):
            return False, "Custom aspects set to By Linked View need a linked view"

    try:
        settings = DB.RevitLinkGraphicsSettings()
        settings.LinkVisibilityType = _link_visibility(mode_index)
        if has_view:
            settings.LinkedViewId = new_element_id(linked_view_id)

        if mode_index == 2 and aspects:
            for key, _label, kind, allows_custom in CUSTOM_ASPECTS:
                if key not in aspects:
                    continue
                value = int(aspects[key])
                if value == 2 and not allows_custom:
                    value = 1
                vis = _link_visibility(value)
                try:
                    if kind == "prop":
                        setattr(settings, key, vis)
                    elif kind == "discipline":
                        settings.SetDiscipline(vis, DB.ViewDiscipline.Architectural)
                    elif kind == "detail":
                        settings.SetViewDetailLevel(vis, DB.ViewDetailLevel.Undefined)
                except Exception:
                    pass

        for target in targets:
            view.SetLinkOverrides(target, settings)
        return True, DISPLAY_MODES[mode_index]
    except Exception as ex:
        return False, str(ex).split("\n")[0]


def _placed_instance_ids(link_record):
    """Every placed instance of a link; falls back to the legacy single id."""
    if link_record is None:
        return []
    ids = [i for i in (getattr(link_record, 'instance_ids', None) or [])
           if i is not None and i != ElementId.InvalidElementId]
    if ids:
        return ids
    single = getattr(link_record, 'instance_id', None)
    if single is not None and single != ElementId.InvalidElementId:
        return [single]
    return []


def set_link_visibility(view, link_record, visible):
    """Show or hide every placed instance of a link in view.

    A link type placed more than once used to have only its first instance
    hidden, which reads on screen as the tool having done nothing.
    Caller owns the transaction.
    """
    inst_ids = _placed_instance_ids(link_record)
    if view is None or not inst_ids:
        return False, "Link has no placed instance"
    try:
        ids = NetList[ElementId]()
        for inst_id in inst_ids:
            ids.Add(inst_id)
        if visible:
            view.UnhideElements(ids)
        else:
            view.HideElements(ids)
        return True, "Visible" if visible else "Hidden"
    except Exception as ex:
        return False, str(ex).split("\n")[0]


def set_link_halftone(view, link_record, halftone):
    """Halftone every placed instance of a link in view.

    Caller owns the transaction.
    """
    inst_ids = _placed_instance_ids(link_record)
    if view is None or not inst_ids:
        return False, "Link has no placed instance"
    try:
        for inst_id in inst_ids:
            ogs = view.GetElementOverrides(inst_id)
            if ogs is None:
                ogs = OverrideGraphicSettings()
            ogs.SetHalftone(bool(halftone))
            view.SetElementOverrides(inst_id, ogs)
        return True, "Halftone on" if halftone else "Halftone off"
    except Exception as ex:
        return False, str(ex).split("\n")[0]
