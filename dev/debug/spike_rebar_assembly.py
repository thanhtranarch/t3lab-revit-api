#! python3
# -*- coding: utf-8 -*-
"""Rebar & Assembly spike - probes G1..G16 of the Revit 2027 assumptions.

Run it from the pyRevit console (or as a one-off pushbutton) on the sample
model of spec section 3.11 inside Revit. It changes nothing: every probe that
needs a write runs inside a Transaction that is rolled back.

Writes one line per probe to  %APPDATA%\\T3LabAI\\spike_rebar.log :

    <timestamp> G<n> PASS|FAIL|SKIP - <detail>

and shows a summary dialog. Copy the G1..G16 lines into dev/plan/README.md
and the G10 / G12 / G14 results into the JSON / tables named in the spec.

Sample model (probes SKIP, they never guess, when a part is missing):
  * at least 2 identical column assemblies (same AssemblyType) with rebar and
    1 assembly of a different type;
  * one of them with assembly views, a sheet, a text note, a rebar tag and a
    dimension in its views.

Spec: dev/plan/rebar-tekla-implementation-spec.md section 3.11.
Standalone on purpose: imports only Snippets._compat / Snippets._host, not the
new Rebar & Assembly modules, so it can run before they are merged.
"""
__title__ = "Rebar\nSpike"
__author__ = "Tran Tien Thanh"

import os
import sys

# -- lib bootstrap ------------------------------------------------------------
_cur = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = None
while _cur:
    for _candidate in (os.path.join(_cur, "T3Lab.extension", "lib"),
                       os.path.join(_cur, "lib")):
        if os.path.isdir(os.path.join(_candidate, "Snippets")):
            LIB_DIR = _candidate
            break
    _parent = os.path.dirname(_cur)
    if LIB_DIR or _parent == _cur:
        break
    _cur = _parent
if LIB_DIR and LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass

import datetime
import json
import traceback

from pyrevit import revit      # noqa: F401  (keeps pyRevit's CPython loader happy)

MM = 304.8                      # feet -> mm
TOL_FT = 1.0 / MM               # 1 mm in feet
MAX_LINES_PER_PROBE = 40
LOG_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "T3LabAI", "spike_rebar.log")

PROBE_ORDER = ("G1", "G2", "G3", "G4", "G5", "G6", "G8", "G10", "G11", "G12",
               "G13", "G14", "G15", "G16")


class Skip(Exception):
    """Raised by a probe whose sample-model prerequisite is missing."""


# -- log ----------------------------------------------------------------------

class SpikeLog(object):
    """Append-only log file plus the counts the summary dialog shows."""

    def __init__(self, path):
        self.path = path
        self.lines = []
        self.counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        folder = os.path.dirname(path)
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except OSError:
            pass

    def write(self, probe, status, detail):
        line = u"%s %s %s — %s" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), probe, status, detail)
        self.lines.append(line)
        self.counts[status] = self.counts.get(status, 0) + 1
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + u"\n")
        except OSError:
            pass


# -- helpers ------------------------------------------------------------------

def _err(exc):
    from Snippets._compat import short_error
    return short_error(exc)


def _mm(value):
    return round(float(value) * MM, 1)


def _pt(point):
    return "(%.1f, %.1f, %.1f)" % (point.X * MM, point.Y * MM, point.Z * MM)


def _dist(a, b):
    return ((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2) ** 0.5


def _eid(element_id):
    from Snippets._compat import eid_value
    return eid_value(element_id)


def _name(element):
    from Snippets._compat import elem_name
    try:
        return elem_name(element)
    except Exception:
        return "?"


def _valid(element_id):
    return _eid(element_id) >= 0


def _rolled_back(doc, label, work):
    """Run work() in a Transaction that is always rolled back; return its result."""
    from Autodesk.Revit import DB
    from Snippets._compat import disposing
    txn = DB.Transaction(doc, u"T3Lab: spike %s" % label)
    with disposing(txn) as t:
        t.Start()
        try:
            return work()
        finally:
            try:
                if not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass


def _collect(doc, element_class):
    from Snippets._compat import disposing
    from Autodesk.Revit import DB
    with disposing(DB.FilteredElementCollector(doc)) as collector:
        return list(collector.OfClass(element_class).ToElements())


def _revit_type(name):
    from Snippets._compat import _revit_type as lookup
    return lookup(name)


def _net_ids(ids):
    from Autodesk.Revit import DB
    from Snippets._compat import net_list
    return net_list(DB.ElementId, ids)


def _views_by_assembly(doc):
    """{assembly id: [View]} (sheets included) via AssociatedAssemblyInstanceId."""
    from Autodesk.Revit import DB
    out = {}
    for view in _collect(doc, DB.View):
        try:
            if view.IsTemplate:
                continue
            owner = _eid(view.AssociatedAssemblyInstanceId)
            if owner >= 0:
                out.setdefault(owner, []).append(view)
        except Exception:
            continue
    return out


class Sample(object):
    """What the probes need to find in the model, discovered once."""

    def __init__(self, doc, uiapp, uidoc):
        from Autodesk.Revit import DB
        self.doc = doc
        self.uiapp = uiapp
        self.uidoc = uidoc
        self.assemblies = _collect(doc, DB.AssemblyInstance)
        self.by_type = {}
        for asm in self.assemblies:
            self.by_type.setdefault(_eid(asm.GetTypeId()), []).append(asm)
        self.pair = None
        for members in self.by_type.values():
            if len(members) >= 2:
                self.pair = (members[0], members[1])
                break
        self.other = None
        if self.pair is not None:
            pair_type = _eid(self.pair[0].GetTypeId())
            for asm in self.assemblies:
                if _eid(asm.GetTypeId()) != pair_type:
                    self.other = asm
                    break
        self.views = _views_by_assembly(doc)

    def missing(self):
        notes = []
        if len(self.assemblies) < 3:
            notes.append("fewer than 3 assemblies (%d found)" % len(self.assemblies))
        if self.pair is None:
            notes.append("no two assemblies share one AssemblyType")
        elif self.other is None:
            notes.append("no assembly of a different type")
        if not self.views:
            notes.append("no assembly has any view")
        return notes

    def assembly(self, asm_id):
        for asm in self.assemblies:
            if _eid(asm.Id) == asm_id:
                return asm
        return None

    def view_sources(self, element_class):
        """[(element, owner view, assembly)] of view-owned elements in assembly views."""
        out = []
        for element in _collect(self.doc, element_class):
            try:
                owner_id = element.OwnerViewId
                if not _valid(owner_id):
                    continue
                view = self.doc.GetElement(owner_id)
                asm_id = _eid(view.AssociatedAssemblyInstanceId)
                asm = self.assembly(asm_id) if asm_id >= 0 else None
                if asm is not None:
                    out.append((element, view, asm))
            except Exception:
                continue
        return out

    def destination_view(self, src_view, src_asm):
        """A view of the same ViewType in ANOTHER assembly (prefer one of the same type)."""
        src_type = _eid(src_asm.GetTypeId())
        candidates = sorted(
            (a for a in self.assemblies if _eid(a.Id) != _eid(src_asm.Id)),
            key=lambda a: 0 if _eid(a.GetTypeId()) == src_type else 1)
        for asm in candidates:
            for view in self.views.get(_eid(asm.Id), []):
                try:
                    if view.ViewType == src_view.ViewType and _eid(view.Id) != _eid(src_view.Id):
                        return view, asm
                except Exception:
                    continue
        return None, None


def _delta(src_asm, dst_asm):
    return dst_asm.GetTransform().Multiply(src_asm.GetTransform().Inverse)


# -- G1 / G13: assembly type behaviour -----------------------------------------

def probe_g1(ctx, log):
    if ctx.pair is None:
        raise Skip("needs two instances of one AssemblyType")
    first, second = ctx.pair

    def views_of(asm):
        return sorted(_eid(v.Id) for v in _views_by_assembly(ctx.doc).get(_eid(asm.Id), []))

    before = (views_of(first), views_of(second))

    def work():
        first.AssemblyTypeName = (first.AssemblyTypeName or "T") + "_SPIKE"
        return (views_of(first), views_of(second),
                _eid(first.GetTypeId()), _eid(second.GetTypeId()))

    after = _rolled_back(ctx.doc, "G1", work)
    type_split = after[2] != after[3]
    stayed = before[0] == after[0] and before[1] == after[1]
    log.write("G1", "PASS",
              "views per instance before=%d/%d after rename=%d/%d; %s; type split=%s" % (
                  len(before[0]), len(before[1]), len(after[0]), len(after[1]),
                  "views stayed with their instance" if stayed else "views MOVED on rename",
                  type_split))


def probe_g13(ctx, log):
    if ctx.pair is None:
        raise Skip("needs two instances of one AssemblyType")
    from Autodesk.Revit import DB
    first, second = ctx.pair

    def type_count():
        return (len(set(_eid(a.GetTypeId()) for a in _collect(ctx.doc, DB.AssemblyInstance))),
                len(_collect(ctx.doc, DB.AssemblyType)))

    before = type_count()

    def rename_one():
        first.AssemblyTypeName = (first.AssemblyTypeName or "T") + "_SPIKE"
        return type_count(), _eid(first.GetTypeId()) != _eid(second.GetTypeId())

    (after, split) = _rolled_back(ctx.doc, "G13", rename_one)
    log.write("G13", "PASS",
              "rename ONE of two same-type instances: types (in use, AssemblyType elements) "
              "%s -> %s; instances now have different types=%s" % (before, after, split))

    if ctx.other is not None:
        target_name = ctx.other.AssemblyTypeName

        def rename_to_existing():
            first.AssemblyTypeName = target_name
            return type_count(), _eid(first.GetTypeId()) == _eid(ctx.other.GetTypeId())

        try:
            (after, merged) = _rolled_back(ctx.doc, "G13b", rename_to_existing)
            log.write("G13", "PASS",
                      "rename to the name of another type '%s': types %s -> %s; joined that "
                      "type=%s" % (target_name, before, after, merged))
        except Exception as exc:
            log.write("G13", "FAIL", "rename to an existing type name refused: %s" % _err(exc))


# -- G2 / G3: copying view-owned elements between assembly views ---------------

def probe_g2(ctx, log):
    from Autodesk.Revit import DB
    sources = ctx.view_sources(DB.TextNote)
    if not sources:
        raise Skip("no text note owned by an assembly view")
    for note, src_view, src_asm in sources:
        dst_view, dst_asm = ctx.destination_view(src_view, src_asm)
        if dst_view is not None:
            break
    else:
        raise Skip("no second assembly with a view of the same ViewType as the note's view")

    src_local = src_asm.GetTransform().Inverse.OfPoint(note.Coord)
    verdict = []
    for mode, transform in (("delta", _delta(src_asm, dst_asm)),
                            ("identity", DB.Transform.Identity)):
        try:
            copy, count = _rolled_back(
                ctx.doc, "G2 " + mode,
                lambda tr=transform: _copy_in_open_txn(ctx, note, src_view, dst_view, tr))
            if copy is None:
                log.write("G2", "FAIL", "%s: CopyElements returned no element" % mode)
                continue
            dst_local = dst_asm.GetTransform().Inverse.OfPoint(copy.Coord)
            same_local = _dist(dst_local, src_local) < TOL_FT
            verdict.append((mode, same_local))
            log.write("G2", "PASS",
                      "%s transform: source local %s, copy local-in-target %s, model %s -> %s" % (
                          mode, _pt(src_local), _pt(dst_local), _pt(copy.Coord),
                          "SAME local position" if same_local else "different local position"))
        except Exception as exc:
            log.write("G2", "FAIL", "%s transform: CopyElements refused: %s" % (mode, _err(exc)))
    winner = [m for m, ok in verdict if ok]
    log.write("G2", "PASS" if winner else "FAIL",
              "T2_TRANSFORM_MODE = %s" % (repr(winner[0]) if winner else
                                          "undecided (neither mode kept the local position)"))


def _copy_in_open_txn(ctx, element, src_view, dst_view, transform):
    """CopyElements inside an already open transaction; returns (copy, count)."""
    from Autodesk.Revit import DB
    ids = DB.ElementTransformUtils.CopyElements(
        src_view, _net_ids([element.Id]), dst_view, transform, DB.CopyPasteOptions())
    new_ids = list(ids)
    return (ctx.doc.GetElement(new_ids[0]) if new_ids else None), len(new_ids)


def probe_g3(ctx, log):
    from Autodesk.Revit import DB
    reported = False
    for kind, element_class in (("rebar tag", DB.IndependentTag), ("dimension", DB.Dimension)):
        sources = ctx.view_sources(element_class)
        if not sources:
            log.write("G3", "SKIP", "no %s owned by an assembly view" % kind)
            continue
        for element, src_view, src_asm in sources:
            dst_view, dst_asm = ctx.destination_view(src_view, src_asm)
            if dst_view is not None:
                break
        else:
            log.write("G3", "SKIP", "%s: no target view of the same ViewType" % kind)
            continue
        reported = True
        try:
            before = _reference_summary(element)
            copy, _ = _rolled_back(
                ctx.doc, "G3 " + kind,
                lambda e=element, s=src_view, d=dst_view, a=src_asm, b=dst_asm:
                _copy_and_inspect(ctx, e, s, d, a, b))
            log.write("G3", "PASS", "%s: original %s; copy %s" % (kind, before, copy))
        except Exception as exc:
            log.write("G3", "FAIL", "%s: copy refused: %s" % (kind, _err(exc)))
    if not reported:
        raise Skip("no tag or dimension in any assembly view")


def _reference_summary(element):
    try:
        if hasattr(element, "GetTaggedLocalElementIds"):
            ids = sorted(_eid(i) for i in element.GetTaggedLocalElementIds())
            return "tags element ids %s" % ids
    except Exception:
        pass
    try:
        return "%d reference(s), %d segment(s)" % (element.References.Size, element.NumberOfSegments)
    except Exception as exc:
        return "unreadable (%s)" % _err(exc)


def _copy_and_inspect(ctx, element, src_view, dst_view, src_asm, dst_asm):
    copy, count = _copy_in_open_txn(ctx, element, src_view, dst_view, _delta(src_asm, dst_asm))
    if copy is None:
        return "no element created"
    return "%s (created %d element(s))" % (_reference_summary(copy), count)


# -- G4 / G5: assembly membership ------------------------------------------------

def probe_g4(ctx, log):
    from Autodesk.Revit import DB
    if not ctx.assemblies:
        raise Skip("no assembly in the model")
    target = ctx.assemblies[0]
    for kind in ("Rebar", "RebarInSystem", "AreaReinforcement", "FabricSheet", "RebarCoupler"):
        element_type = _revit_type(kind)
        if element_type is None:
            log.write("G4", "SKIP", "%s: class not present on this Revit release" % kind)
            continue
        loose = [e for e in _collect(ctx.doc, element_type)
                 if not _valid(e.AssemblyInstanceId)]
        if not loose:
            log.write("G4", "SKIP", "%s: no loose element of this kind in the model" % kind)
            continue
        element = loose[0]
        ids = _net_ids([element.Id])
        try:
            valid = DB.AssemblyInstance.AreElementsValidForAssembly(ctx.doc, ids, target.Id)
        except Exception as exc:
            log.write("G4", "FAIL", "%s: AreElementsValidForAssembly raised: %s" % (kind, _err(exc)))
            continue

        def add(asm=target, new_ids=ids, el=element):
            asm.AddMemberIds(new_ids)
            return asm.IsMember(el.Id)

        try:
            member = _rolled_back(ctx.doc, "G4 " + kind, add)
            log.write("G4", "PASS", "%s: AreElementsValidForAssembly=%s, AddMemberIds ok, "
                      "IsMember=%s" % (kind, valid, member))
        except Exception as exc:
            log.write("G4", "FAIL", "%s: AreElementsValidForAssembly=%s, AddMemberIds raised: %s"
                      % (kind, valid, _err(exc)))


def probe_g5(ctx, log):
    from Autodesk.Revit import DB
    rebar_type = _revit_type("Rebar")
    for rebar in _collect(ctx.doc, rebar_type):
        try:
            host = ctx.doc.GetElement(rebar.GetHostId())
            if host is not None and _valid(host.AssemblyInstanceId):
                break
        except Exception:
            continue
    else:
        raise Skip("no rebar whose host is inside an assembly")

    def work():
        ids = DB.ElementTransformUtils.CopyElements(
            ctx.doc, _net_ids([rebar.Id]), DB.XYZ(0.0, 0.0, 0.1))
        new = ctx.doc.GetElement(list(ids)[0])
        return _eid(new.AssemblyInstanceId), _eid(new.GetHostId())

    asm_id, host_id = _rolled_back(ctx.doc, "G5", work)
    log.write("G5", "PASS",
              "copy of rebar %d (host %d in assembly %d): copy host=%d, copy assembly=%d -> %s" % (
                  _eid(rebar.Id), _eid(host.Id), _eid(host.AssemblyInstanceId), host_id, asm_id,
                  "AUTO-ADDED to an assembly" if asm_id >= 0 else "NOT added to any assembly"))


# -- G6 / G7 / G9: commands and ribbon -------------------------------------------

def _postable_names():
    from Autodesk.Revit import UI
    try:
        import System
        return sorted(System.Enum.GetNames(UI.PostableCommand))
    except Exception:
        return sorted(n for n in dir(UI.PostableCommand) if not n.startswith("_"))


def _walk_items(items, trail, out, depth=0):
    if items is None or depth > 6:
        return
    try:
        iterator = list(items)
    except Exception:
        return
    for item in iterator:
        text = ""
        for member in ("Text", "Title", "Name", "Id"):
            try:
                value = getattr(item, member, None)
                if value:
                    text = u"%s" % value
                    break
            except Exception:
                continue
        if text:
            out.append(u"%s › %s" % (trail, text))
        _walk_items(getattr(item, "Items", None), trail, out, depth + 1)


def _ribbon_texts(uiapp):
    """{tab title: [ 'panel > item' ]} from AdWindows, plus API panels per tab."""
    result = {}
    try:
        import clr
        clr.AddReference("AdWindows")
        from Autodesk.Windows import ComponentManager
        for tab in ComponentManager.Ribbon.Tabs:
            texts = result.setdefault(u"%s" % tab.Title, [])
            for panel in tab.Panels:
                title = u"%s" % panel.Source.Title
                texts.append(title)
                _walk_items(panel.Source.Items, title, texts)
    except Exception as exc:
        result["(AdWindows unavailable)"] = [_err(exc)]
    for tab_name in ("Concrete Detailing", "Structure"):
        try:
            names = [u"%s" % p.Name for p in uiapp.GetRibbonPanels(tab_name)]
            result.setdefault(u"API:%s" % tab_name, []).extend(names)
        except Exception as exc:
            result.setdefault(u"API:%s" % tab_name, []).append(u"(%s)" % _err(exc))
    return result


def _matches(texts_by_tab, keywords):
    out = []
    for tab, texts in texts_by_tab.items():
        for text in texts:
            lowered = text.lower()
            if any(k in lowered for k in keywords):
                out.append(u"%s: %s" % (tab, text))
    return out


def probe_g6(ctx, log):
    names = _postable_names()
    wanted = [n for n in names if any(k in n for k in ("Rebar", "Reinforc", "Assembl", "Number", "BVBS"))]
    log.write("G6", "PASS", "PostableCommand (%d total) matching Rebar/Reinforc/Assembl/Number/BVBS: %s"
              % (len(names), ", ".join(wanted) or "none"))
    texts = _ribbon_texts(ctx.uiapp)
    log.write("G6", "PASS", "ribbon tabs seen: %s" % ", ".join(sorted(texts)))
    for code, label, words in (
            ("G6", "auto beam/column/footing reinforcement generator",
             ("beam reinforc", "column reinforc", "footing reinforc", "automatic reinforc", "auto reinforc")),
            ("G7", "assembly drawing propagation / clone",
             ("clone", "propagat", "copy drawing", "apply drawing")),
            ("G9", "BVBS / bending machine export",
             ("bvbs", "bending machine", "bar export", "export rebar"))):
        found = _matches(texts, words)
        detail = ("%s: FOUND %s" % (label, "; ".join(found[:10])) if found
                  else "%s: none found in the ribbon (the tool is still justified)" % label)
        log.write(code, "PASS", detail)


# -- G8: assembly view creation ----------------------------------------------------

def probe_g8(ctx, log):
    from Autodesk.Revit import DB
    target = None
    for asm in ctx.assemblies:
        try:
            if asm.AllowsAssemblyViewCreation():
                target = asm
                break
        except Exception:
            continue
    if target is None:
        raise Skip("no assembly that allows view creation")
    utils = DB.AssemblyViewUtils
    results = []

    def step(name, call):
        try:
            value = call()
            results.append("%s ok" % name)
            return value
        except Exception as exc:
            results.append("%s FAILED: %s" % (name, _err(exc)))
            return None

    def work():
        view3d = step("Create3DOrthographic", lambda: utils.Create3DOrthographic(ctx.doc, target.Id))
        step("CreateDetailSection(HorizontalDetail)", lambda: utils.CreateDetailSection(
            ctx.doc, target.Id, DB.AssemblyDetailViewOrientation.HorizontalDetail))
        step("CreatePartList", lambda: utils.CreatePartList(ctx.doc, target.Id))
        from Snippets._compat import disposing
        with disposing(DB.FilteredElementCollector(ctx.doc)) as collector:
            title_block = collector.OfCategory(DB.BuiltInCategory.OST_TitleBlocks) \
                .WhereElementIsElementType().FirstElementId()
        sheet = step("CreateSheet", lambda: utils.CreateSheet(ctx.doc, target.Id, title_block))
        if sheet is not None and view3d is not None:
            def place():
                if not DB.Viewport.CanAddViewToSheet(ctx.doc, sheet.Id, view3d.Id):
                    raise RuntimeError("CanAddViewToSheet is False")
                return DB.Viewport.Create(ctx.doc, sheet.Id, view3d.Id, DB.XYZ(0.5, 0.5, 0.0))
            step("Viewport.Create", place)

    _rolled_back(ctx.doc, "G8", work)
    failed = [r for r in results if "FAILED" in r]
    log.write("G8", "FAIL" if failed else "PASS",
              "assembly %d: %s" % (_eid(target.Id), "; ".join(results)))


# -- G10: postable command ids -----------------------------------------------------

def _bridge_rows():
    path = os.path.join(LIB_DIR or "", "data", "tekla_bridge.json")
    if not os.path.isfile(path):
        raise Skip("lib/data/tekla_bridge.json does not exist yet (Tekla Bridge package not merged)")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    return data


def probe_g10(ctx, log):
    from Autodesk.Revit import UI
    rows = _bridge_rows()
    resolved = 0
    unresolved = []
    for row in rows:
        candidates = row.get("postable") or []
        if not candidates:
            continue
        found = False
        for name in candidates:
            member = getattr(UI.PostableCommand, name, None)
            if member is None:
                log.write("G10", "SKIP", "%s: PostableCommand.%s does not exist on this release"
                          % (row.get("id"), name))
                continue
            try:
                command_id = UI.RevitCommandId.LookupPostableCommandId(member)
                can_post = ctx.uiapp.CanPostCommand(command_id)
                log.write("G10", "PASS", "%s: %s -> RevitCommandId.Name=%s CanPostCommand=%s"
                          % (row.get("id"), name, command_id.Name, can_post))
                found = True
                break
            except Exception as exc:
                log.write("G10", "FAIL", "%s: %s lookup failed: %s" % (row.get("id"), name, _err(exc)))
        if found:
            resolved += 1
        else:
            unresolved.append(row.get("id"))
    log.write("G10", "PASS" if not unresolved else "FAIL",
              "%d row(s) with a usable command; no command for: %s" % (
                  resolved, ", ".join(str(i) for i in unresolved) or "none"))


# -- G11: partition parameter and numbering schema --------------------------------

def probe_g11(ctx, log):
    from Autodesk.Revit import DB
    rebar_type = _revit_type("Rebar")
    rebars = _collect(ctx.doc, rebar_type)
    if not rebars:
        raise Skip("no rebar in the model")
    rebar = rebars[0]
    built_in = getattr(DB.BuiltInParameter, "NUMBER_PARTITION_PARAM", None)
    param = rebar.get_Parameter(built_in) if built_in is not None else None
    if param is None:
        param = rebar.LookupParameter("Partition")
    if param is None:
        log.write("G11", "FAIL", "rebar %d has no Partition parameter" % _eid(rebar.Id))
    else:
        value = param.AsString()

        def write_test():
            ok = param.Set("T3SPIKE")
            return ok, param.AsString()

        try:
            ok, readback = _rolled_back(ctx.doc, "G11", write_test)
            log.write("G11", "PASS", "NUMBER_PARTITION_PARAM %s: value=%r read-only=%s Set(...)=%s "
                      "read back %r" % ("present" if built_in is not None else "MISSING from enum",
                                        value, param.IsReadOnly, ok, readback))
        except Exception as exc:
            log.write("G11", "FAIL", "Partition write test raised: %s" % _err(exc))

    schema_type = _revit_type("NumberingSchema")
    if schema_type is None:
        log.write("G11", "SKIP", "NumberingSchema is not available on this Revit release")
        return
    param_type = _revit_type("NumberingParameter")
    if param_type is not None:
        log.write("G11", "PASS", "NumberingParameter members: %s"
                  % ", ".join(n for n in dir(param_type) if not n.startswith("_")))
    for schema in schema_type.GetSchemasInDocument(ctx.doc):
        try:
            scope = [_eid(getattr(c, "Id", c)) for c in schema.GetScopeDefiningCategories()]
        except Exception as exc:
            scope = ["(%s)" % _err(exc)]
        params = []
        try:
            for item in schema.GetPartitioningParameters():
                params.append({n: _safe_text(getattr(item, n, None))
                               for n in dir(item) if n in ("ParameterId", "Id", "Name", "ParameterType")})
        except Exception as exc:
            params = ["(%s)" % _err(exc)]
        log.write("G11", "PASS", "schema %r enabled=%s scope categories=%s partitioning=%s" % (
            _safe_text(getattr(schema, "Name", None)), _safe_text(getattr(schema, "Enabled", None)),
            scope, params))


def _safe_text(value):
    try:
        return u"%s" % value
    except Exception:
        return "?"


# -- G12: centre-line and shape parameters -----------------------------------------

def probe_g12(ctx, log):
    from Autodesk.Revit import DB
    rebar_type = _revit_type("Rebar")
    option_type = _revit_type("MultiplanarOption")
    option = getattr(option_type, "IncludeAllMultiplanarCurves", None) if option_type else None
    if option is None:
        raise Skip("MultiplanarOption.IncludeAllMultiplanarCurves not found")
    driven = []
    for rebar in _collect(ctx.doc, rebar_type):
        try:
            if rebar.IsRebarShapeDriven():
                driven.append(rebar)
        except Exception:
            continue
        if len(driven) == 3:
            break
    if not driven:
        raise Skip("no shape-driven rebar in the model")
    for rebar in driven:
        try:
            bar_type = ctx.doc.GetElement(rebar.GetTypeId())
            diameter = _mm(bar_type.BarNominalDiameter)
            curves = rebar.GetCenterlineCurves(False, False, True, option, 0)
            points = []
            for curve in curves:
                points.append("%s %s>%s" % (curve.GetType().Name, _pt(curve.GetEndPoint(0)),
                                            _pt(curve.GetEndPoint(1))))
            shape = ctx.doc.GetElement(rebar.GetShapeId())
            shape_values = []
            for letter in "ABCDEFGHIJKR":
                p = rebar.LookupParameter(letter)
                if p is not None and p.HasValue:
                    shape_values.append("%s=%.1f" % (letter, _mm(p.AsDouble())))
            log.write("G12", "PASS", "rebar %d shape=%s d=%.1f mm hasTransformedGetter=%s "
                      "centreline(suppressBendRadius)=%s shape params(mm)=%s" % (
                          _eid(rebar.Id), _name(shape), diameter,
                          hasattr(rebar, "GetTransformedCenterlineCurves"),
                          " | ".join(points), ", ".join(shape_values) or "none"))
        except Exception as exc:
            log.write("G12", "FAIL", "rebar %d: %s" % (_eid(rebar.Id), _err(exc)))


# -- G14: assembly view orientation -----------------------------------------------

def probe_g14(ctx, log):
    shown = 0
    for asm in ctx.assemblies:
        views = ctx.views.get(_eid(asm.Id), [])
        if not views:
            continue
        inverse = asm.GetTransform().Inverse
        for view in views:
            if shown >= MAX_LINES_PER_PROBE:
                log.write("G14", "PASS", "... more views not listed (limit %d)" % MAX_LINES_PER_PROBE)
                return
            shown += 1
            try:
                direction = _pt(inverse.OfVector(view.ViewDirection))
                origin = _pt(inverse.OfPoint(view.Origin))
            except Exception:
                direction = origin = "n/a"
            log.write("G14", "PASS", "assembly %d %r view %d %r ViewType=%s direction(local)=%s "
                      "origin(local mm)=%s template=%d" % (
                          _eid(asm.Id), _safe_text(asm.AssemblyTypeName), _eid(view.Id),
                          _name(view), view.ViewType, direction, origin,
                          _eid(view.ViewTemplateId)))
    if not shown:
        raise Skip("no assembly has views")


# -- G15: API availability and mass unit --------------------------------------------

def probe_g15(ctx, log):
    from Autodesk.Revit import DB
    from Snippets._compat import revit_year
    year = revit_year(ctx.doc)
    log.write("G15", "PASS", "Revit %s; BarTerminationsData present=%s; RebarHookOrientation present=%s; "
              "RebarTerminationOrientation present=%s" % (
                  year, _revit_type("BarTerminationsData") is not None,
                  _revit_type("RebarHookOrientation") is not None,
                  _revit_type("RebarTerminationOrientation") is not None))
    rebars = _collect(ctx.doc, _revit_type("Rebar"))
    if not rebars:
        raise Skip("no rebar in the model for the mass probe")
    rebar = rebars[0]
    bar_type = ctx.doc.GetElement(rebar.GetTypeId())
    diameter = _mm(bar_type.BarNominalDiameter)
    formula = 0.006165 * diameter * diameter
    raw_mass_unit = getattr(DB.UnitTypeId, "KilogramsPerMeter", None)
    native = getattr(bar_type, "BarMassPerUnitLength", None)
    if native is None:
        log.write("G15", "PASS", "BarMassPerUnitLength is not on RebarBarType (release before 2027); "
                  "formula 0.006165*d^2 = %.3f kg/m for d=%.1f" % (formula, diameter))
    else:
        converted = DB.UnitUtils.ConvertFromInternalUnits(native, raw_mass_unit) if raw_mass_unit else None
        log.write("G15", "PASS", "BarMassPerUnitLength raw=%s -> KilogramsPerMeter=%s; formula says "
                  "%.3f kg/m for d=%.1f (the unit that matches the formula is the right one)" % (
                      native, converted, formula, diameter))
    mass = getattr(rebar, "Mass", None)
    if mass is None:
        log.write("G15", "PASS", "Rebar.Mass is not available on this release")
    else:
        kilograms = getattr(DB.UnitTypeId, "Kilograms", None)
        converted = DB.UnitUtils.ConvertFromInternalUnits(mass, kilograms) if kilograms else None
        length = _mm(rebar.TotalLength) / 1000.0
        log.write("G15", "PASS", "rebar %d Mass raw=%s -> Kilograms=%s; TotalLength=%.3f m; "
                  "formula mass=%.3f kg" % (_eid(rebar.Id), mass, converted, length, formula * length))


# -- G16: MultiReferenceAnnotationOptions ------------------------------------------

def probe_g16(ctx, log):
    options_type = _revit_type("MultiReferenceAnnotationOptions")
    if options_type is None:
        raise Skip("MultiReferenceAnnotationOptions is not available on this release")
    members = [n for n in dir(options_type) if not n.startswith("_")]
    log.write("G16", "PASS", "MultiReferenceAnnotationOptions members: %s" % ", ".join(members))


PROBES = {
    "G1": probe_g1, "G2": probe_g2, "G3": probe_g3, "G4": probe_g4, "G5": probe_g5,
    "G6": probe_g6, "G8": probe_g8, "G10": probe_g10, "G11": probe_g11, "G12": probe_g12,
    "G13": probe_g13, "G14": probe_g14, "G15": probe_g15, "G16": probe_g16,
}


# -- run -----------------------------------------------------------------------------

def run_spike(doc, uiapp, uidoc):
    """Run every probe; returns the SpikeLog. One probe failing never stops the next."""
    log = SpikeLog(LOG_PATH)
    from Snippets._compat import revit_year
    log.write("G0", "PASS", "=== spike run: Revit %s, model %r ===" % (revit_year(doc), doc.Title))
    ctx = Sample(doc, uiapp, uidoc)
    for note in ctx.missing():
        log.write("G0", "SKIP", "sample model: %s" % note)
    for probe_id in PROBE_ORDER:
        before = len(log.lines)
        try:
            PROBES[probe_id](ctx, log)
        except Skip as skip:
            log.write(probe_id, "SKIP", str(skip))
        except Exception as exc:
            last = traceback.format_exc().strip().splitlines()[-1]
            log.write(probe_id, "FAIL", "%s (%s)" % (_err(exc), last))
        if len(log.lines) == before:
            log.write(probe_id, "SKIP", "probe wrote nothing")
    return log


def main():
    from Snippets._host import host_uiapp, resolve_doc, resolve_uidoc
    doc, doc_error = resolve_doc()
    if not doc:
        _show("Rebar spike", doc_error or "Open the sample Revit project first.", None)
        return
    if doc.IsFamilyDocument:
        _show("Rebar spike", "The spike runs on a project, not on a family document.", None)
        return
    log = run_spike(doc, host_uiapp(), resolve_uidoc())
    counts = log.counts
    summary = ("Spike finished: %d PASS, %d FAIL, %d SKIP.\n\nLog: %s\n\n"
               "Copy the G1-G16 lines into dev/plan/README.md." % (
                   counts["PASS"], counts["FAIL"], counts["SKIP"], LOG_PATH))
    details = "\n".join(line for line in log.lines if " FAIL " in line or " SKIP " in line)
    _show("Rebar spike", summary, details or None)


def _show(title, message, details):
    try:
        from GUI.T3Dialog import show_info
        show_info(message, title=title, details=details)
    except Exception:
        pass


if __name__ == "__main__":
    main()
