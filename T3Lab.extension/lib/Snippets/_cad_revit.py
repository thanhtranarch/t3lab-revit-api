# -*- coding: utf-8 -*-
"""CAD to Elements — Revit API side.

Reads an imported/linked CAD instance into plain per-layer geometry
(`scan_cad`), collects the types each mode offers, and creates elements.
Every `create_*` function runs ONE transaction (one Ctrl+Z step), rolls back
when nothing was created, and returns a `RunResult` with counts. Revit
warnings raised while creating (overlapping walls, room not enclosed …) are
counted and dismissed so a 300-wall run does not end in a 300-row warning
dialog; errors still roll the transaction back.

What to create is decided by the pure rules in `Snippets/_cad_geometry.py`.

Revit 2022 -> 2027: Floor.Create / Ceiling.Create (2022+) are inside the
supported range, so the removed `NewFloor` fallback is gone. ElementId values
go through `Snippets._compat.eid_value`.
"""
import math

from System.Collections.Generic import List
import Autodesk.Revit.DB as DB

from Snippets import _cad_geometry as geo
from Snippets._compat import disposing, eid_value, elem_name

TOL = geo.TOL


# ═══════════════════════════════════════════════════════════════════════════
# RESULT + TRANSACTION
# ═══════════════════════════════════════════════════════════════════════════

class RunResult(object):
    """Counts of one run. `notes` are extra lines for the result dialog."""

    def __init__(self, noun):
        self.noun = noun
        self.created = 0
        self.failed = 0
        self.skipped = 0
        self.warnings = 0
        self.notes = []
        self.errors = []

    def fail(self, exc=None):
        self.failed += 1
        if exc is not None and len(self.errors) < 3:
            msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            if msg not in self.errors:
                self.errors.append(msg)


_WARNINGS = {"count": 0}


class CADToElementsWarnings(DB.IFailuresPreprocessor):
    """Dismiss Revit WARNINGS raised while creating; errors are left alone."""
    __namespace__ = "T3Lab.CADToElementsWarnings"

    def PreprocessFailures(self, failuresAccessor):
        try:
            for failure in failuresAccessor.GetFailureMessages():
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(failure)
                    _WARNINGS["count"] += 1
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def run_transaction(doc, name, body, result):
    """Run `body()` inside one transaction; commit only if something was made."""
    _WARNINGS["count"] = 0
    with disposing(DB.Transaction(doc, name)) as t:
        t.Start()
        try:
            opts = t.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(CADToElementsWarnings())
            t.SetFailureHandlingOptions(opts)
        except Exception:
            pass
        body()
        if result.created <= 0:
            t.RollBack()
            return result
        status = t.Commit()
        if status != DB.TransactionStatus.Committed:
            result.notes.append("Revit refused to commit the change ({}); nothing was kept."
                                .format(status))
            result.failed += result.created
            result.created = 0
    result.warnings = _WARNINGS["count"]
    return result


def _tick(progress, i, total):
    if progress is not None:
        try:
            progress(i, total)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# DOCUMENT LOOKUPS
# ═══════════════════════════════════════════════════════════════════════════

def get_cad_instances(doc):
    """[{element, name, id}] for every imported or linked CAD instance."""
    out = []
    seen = set()
    collector = DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance)
    for inst in collector:
        try:
            iid = eid_value(inst.Id)
            if iid in seen:
                continue
            seen.add(iid)
            name = None
            try:
                ctype = doc.GetElement(inst.GetTypeId())
                name = elem_name(ctype) if ctype is not None else None
            except Exception:
                name = None
            if not name:
                try:
                    name = inst.Category.Name
                except Exception:
                    name = "CAD {}".format(iid)
            linked = False
            try:
                linked = bool(inst.IsLinked)
            except Exception:
                pass
            out.append({"element": inst, "id": iid,
                        "name": u"{} [{}]".format(name, "Linked" if linked else "Imported")})
        except Exception:
            continue
    out.sort(key=lambda c: c["name"].lower())
    return out


def get_levels(doc):
    levels = []
    for lv in DB.FilteredElementCollector(doc).OfClass(DB.Level):
        try:
            levels.append({"element": lv, "id": lv.Id, "name": elem_name(lv),
                           "elevation": lv.Elevation})
        except Exception:
            pass
    levels.sort(key=lambda x: x["elevation"])
    return levels


def _named(elements, label=None):
    out = []
    for e in elements:
        try:
            out.append({"element": e, "id": e.Id, "name": label(e) if label else elem_name(e)})
        except Exception:
            pass
    out.sort(key=lambda x: x["name"].lower())
    return out


def get_basic_wall_types(doc):
    types = [wt for wt in DB.FilteredElementCollector(doc).OfClass(DB.WallType)
             if wt.Kind == DB.WallKind.Basic]
    return _named(types)


def get_floor_types(doc):
    def label(ft):
        try:
            return u"{} : {}".format(ft.FamilyName, elem_name(ft))
        except Exception:
            return elem_name(ft)
    return _named(DB.FilteredElementCollector(doc).OfClass(DB.FloorType), label)


def get_ceiling_types(doc):
    return _named(DB.FilteredElementCollector(doc).OfClass(DB.CeilingType))


def get_grid_types(doc):
    return _named(DB.FilteredElementCollector(doc).OfClass(DB.GridType))


def get_symbols(doc, bic):
    """FamilySymbols of a category, labelled 'Family : Type'."""
    syms = (DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
            .OfCategory(bic).ToElements())
    return _named(syms, lambda s: u"{} : {}".format(s.Family.Name, elem_name(s)))


def get_line_styles(doc):
    out = []
    try:
        cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sc in cat.SubCategories:
            try:
                gs = sc.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
                if gs is not None:
                    out.append({"element": gs, "id": gs.Id, "name": sc.Name})
            except Exception:
                pass
    except Exception:
        pass
    out.sort(key=lambda x: x["name"].lower())
    return out


def get_plan_views(doc, level_id):
    """Floor plans (not templates) of a level — room separation lines live there."""
    out = []
    target = eid_value(level_id)
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        try:
            if v.IsTemplate or v.ViewType != DB.ViewType.FloorPlan:
                continue
            if v.GenLevel is None or eid_value(v.GenLevel.Id) != target:
                continue
            out.append({"element": v, "id": v.Id, "name": v.Name})
        except Exception:
            pass
    out.sort(key=lambda x: x["name"].lower())
    return out


def get_ds_categories():
    """Categories offered for DirectShape 'parts'."""
    B = DB.BuiltInCategory
    return [
        {"name": "Generic Models", "bic": B.OST_GenericModel},
        {"name": "Walls", "bic": B.OST_Walls},
        {"name": "Floors", "bic": B.OST_Floors},
        {"name": "Ceilings", "bic": B.OST_Ceilings},
        {"name": "Structural Framing", "bic": B.OST_StructuralFraming},
        {"name": "Structural Foundations", "bic": B.OST_StructuralFoundation},
        {"name": "Roofs", "bic": B.OST_Roofs},
        {"name": "Mass", "bic": B.OST_Mass},
        {"name": "Site", "bic": B.OST_Site},
    ]


def _types_of_class(doc, cls):
    return _named(DB.FilteredElementCollector(doc).OfClass(cls))


def _systems_of_class(doc, cls):
    return _named([st for st in DB.FilteredElementCollector(doc).OfClass(DB.MEPSystemType)
                   if isinstance(st, cls)])


def get_mep_types(doc, key):
    cls = {"duct": DB.Mechanical.DuctType, "pipe": DB.Plumbing.PipeType,
           "tray": DB.Electrical.CableTrayType, "conduit": DB.Electrical.ConduitType}[key]
    return _types_of_class(doc, cls)


def get_mep_systems(doc, key):
    if key == "duct":
        return _systems_of_class(doc, DB.Mechanical.MechanicalSystemType)
    if key == "pipe":
        return _systems_of_class(doc, DB.Plumbing.PipingSystemType)
    return []


def existing_grid_lines(doc):
    out = []
    names = set()
    for gr in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
        try:
            names.add(gr.Name)
            c = gr.Curve
            if isinstance(c, DB.Line):
                a, b = c.GetEndPoint(0), c.GetEndPoint(1)
                out.append((a.X, a.Y, b.X, b.Y))
        except Exception:
            pass
    return out, names


# ═══════════════════════════════════════════════════════════════════════════
# CAD SCAN
# ═══════════════════════════════════════════════════════════════════════════

class LayerGeom(object):
    """Flattened (XY, feet) geometry of one CAD layer."""

    def __init__(self, name):
        self.name = name
        self.segments = []       # (x0,y0,x1,y1,layer): Lines + every polyline piece
        self.line_edges = []     # loose straight edges (Lines + open polylines)
        self.arc_edges = []      # ("A", a, b, mid)
        self.spline_edges = []   # tessellated ellipses / splines
        self.closed_polys = []   # closed polylines as point lists
        self.circles = []        # (cx, cy, r)
        self.n_curves = 0
        self._loops = None
        self._footprints = None

    def loops(self):
        """Closed outlines (closed polylines, circles, chained loose curves)."""
        if self._loops is None:
            cand = []
            for poly in self.closed_polys:
                lp = geo.loop_from_polygon(poly)
                if lp:
                    cand.append(lp)
            for (cx, cy, r) in self.circles:
                cand.append(geo.loop_from_circle(cx, cy, r))
            cand.extend(geo.chain_loops(self.line_edges + self.arc_edges + self.spline_edges))
            polys = [geo.loop_polygon(lp) for lp in cand]
            self._loops = [cand[i] for i in geo.dedupe_polygons(polys)]
        return self._loops

    def footprints(self):
        """Raw column candidates: rectangles from straight loops + circles."""
        if self._footprints is None:
            fps = []
            for lp in self.loops():
                if all(e[0] == "L" for e in lp):
                    fp = geo.rectangle_footprint(geo.loop_polygon(lp))
                    if fp:
                        fps.append(fp)
            for (cx, cy, r) in self.circles:
                fps.append(geo.circle_footprint(cx, cy, r))
            self._footprints = fps
        return self._footprints

    def all_edges(self):
        """Every curve as an edge — the Lines mode copies all of them."""
        edges = [("L", (s[0], s[1]), (s[2], s[3]), None) for s in self.segments]
        edges.extend(self.arc_edges)
        edges.extend(self.spline_edges)
        for (cx, cy, r) in self.circles:
            edges.extend(geo.loop_from_circle(cx, cy, r))
        return edges

    def count(self, kind):
        if kind == "lines":
            return len(self.segments)
        if kind == "loops":
            return len(self.loops())
        if kind == "shapes":
            return len(geo.filter_footprints(self.footprints()))
        return self.n_curves


def _pt(p):
    return (p.X, p.Y)


def _add_polyline(lg, coords):
    pts = [_pt(c) for c in coords]
    if len(pts) < 2:
        return
    lg.n_curves += 1
    for a, b in zip(pts[:-1], pts[1:]):
        if geo._dist(a, b) > TOL:
            lg.segments.append((a[0], a[1], b[0], b[1], lg.name))
    closed = len(pts) >= 4 and geo._dist(pts[0], pts[-1]) < TOL
    if closed:
        lg.closed_polys.append(pts[:-1])
    else:
        for a, b in zip(pts[:-1], pts[1:]):
            if geo._dist(a, b) > TOL:
                lg.line_edges.append(("L", a, b, None))


def scan_cad(doc, cad_instance):
    """{layer name: LayerGeom} for every layer of the CAD instance.

    Nested blocks are walked to any depth with GetInstanceGeometry(), which
    already returns geometry in the coordinates of its container — applying
    each instance's Transform again double-transforms rotated/offset imports
    (see the note this replaces in the old extract_lines_from_cad).
    """
    layers = {}
    style_names = {}

    def layer_of(obj):
        try:
            sid = obj.GraphicsStyleId
        except Exception:
            return None
        key = eid_value(sid)
        if key not in style_names:
            name = None
            try:
                gs = doc.GetElement(sid)
                if gs is not None and gs.GraphicsStyleCategory is not None:
                    name = gs.GraphicsStyleCategory.Name
            except Exception:
                name = None
            style_names[key] = name
        return style_names[key]

    def bucket(name):
        lg = layers.get(name)
        if lg is None:
            lg = layers[name] = LayerGeom(name)
        return lg

    def walk(geom, depth):
        for obj in geom:
            if isinstance(obj, DB.GeometryInstance):
                if depth < 32:
                    sub = obj.GetInstanceGeometry()
                    if sub is not None:
                        walk(sub, depth + 1)
                continue
            name = layer_of(obj)
            if not name:
                continue
            try:
                _add_object(bucket(name), obj)
            except Exception:
                continue

    geom = cad_instance.get_Geometry(DB.Options())
    if geom is not None:
        walk(geom, 0)
    try:
        for sc in cad_instance.Category.SubCategories:
            bucket(sc.Name)
    except Exception:
        pass
    return layers


def _add_object(lg, obj):
    if isinstance(obj, DB.PolyLine):
        _add_polyline(lg, list(obj.GetCoordinates()))
    elif isinstance(obj, DB.Line):
        a, b = _pt(obj.GetEndPoint(0)), _pt(obj.GetEndPoint(1))
        lg.n_curves += 1
        if geo._dist(a, b) > TOL:
            lg.segments.append((a[0], a[1], b[0], b[1], lg.name))
            lg.line_edges.append(("L", a, b, None))
    elif isinstance(obj, DB.Arc):
        lg.n_curves += 1
        if not obj.IsBound:
            lg.circles.append((obj.Center.X, obj.Center.Y, obj.Radius))
            return
        a, b = _pt(obj.GetEndPoint(0)), _pt(obj.GetEndPoint(1))
        if geo._dist(a, b) < TOL:
            lg.circles.append((obj.Center.X, obj.Center.Y, obj.Radius))
            return
        lg.arc_edges.append(("A", a, b, _pt(obj.Evaluate(0.5, True))))
    elif isinstance(obj, DB.Curve):
        pts = [_pt(p) for p in obj.Tessellate()]
        if len(pts) < 2:
            return
        lg.n_curves += 1
        if len(pts) >= 4 and geo._dist(pts[0], pts[-1]) < TOL:
            lg.closed_polys.append(pts[:-1])
            return
        for a, b in zip(pts[:-1], pts[1:]):
            if geo._dist(a, b) > TOL:
                lg.spline_edges.append(("L", a, b, None))


# ═══════════════════════════════════════════════════════════════════════════
# GEOMETRY -> REVIT
# ═══════════════════════════════════════════════════════════════════════════

def xyz(p, z):
    return DB.XYZ(p[0], p[1], z)


def curve_from_edge(edge, z):
    kind, a, b, mid = edge
    if kind == "A" and mid is not None:
        return DB.Arc.Create(xyz(a, z), xyz(b, z), xyz(mid, z))
    return DB.Line.CreateBound(xyz(a, z), xyz(b, z))


def curve_loop(loop, z):
    cl = DB.CurveLoop()
    for e in loop:
        cl.Append(curve_from_edge(e, z))
    return cl


def profile(outer, holes, z):
    loops = List[DB.CurveLoop]()
    loops.Add(curve_loop(geo.orient_loop(outer, True), z))
    for h in holes:
        loops.Add(curve_loop(geo.orient_loop(h, False), z))
    return loops


def _set_param(elem, bip, value):
    try:
        p = elem.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(value)
            return True
    except Exception:
        pass
    return False


def _set_named(elem, names, value_ft):
    """Set the first writable length parameter found under `names`."""
    for n in names:
        try:
            p = elem.LookupParameter(n)
        except Exception:
            p = None
        if p is not None and not p.IsReadOnly and p.StorageType == DB.StorageType.Double:
            try:
                p.Set(value_ft)
                return True
            except Exception:
                continue
    return False


def directshape_extrusion(doc, bic, prof, z_ft, height_ft, name="T3Lab Part"):
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(prof, DB.XYZ.BasisZ, height_ft)
    if solid is None or solid.Volume < 1e-6:
        return None
    if abs(z_ft) > 1e-9:
        solid = DB.SolidUtils.CreateTransformed(
            solid, DB.Transform.CreateTranslation(DB.XYZ(0, 0, z_ft)))
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(bic))
    shapes = List[DB.GeometryObject]()
    shapes.Add(solid)
    ds.SetShape(shapes)
    try:
        ds.Name = name
    except Exception:
        pass
    return ds


def _ensure_active(doc, symbol):
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()


def _duplicate_sized(doc, base_symbol, type_name, sizes, cache, family_symbols):
    """Type `type_name` in base_symbol's family with `sizes` {param names: ft}.

    Returns (symbol, size_applied). An existing type with that name is reused
    as-is. A duplicate whose size parameters cannot be found is deleted again
    and the base type is used, so a run never leaves junk types behind.
    """
    if type_name in cache:
        return cache[type_name]
    for s in family_symbols:
        if elem_name(s) == type_name:
            cache[type_name] = (s, True)
            return cache[type_name]
    try:
        dup = base_symbol.Duplicate(type_name)
    except Exception:
        cache[type_name] = (base_symbol, False)
        return cache[type_name]
    applied = all(_set_named(dup, names, value) for names, value in sizes)
    if not applied:
        try:
            doc.Delete(dup.Id)
        except Exception:
            pass
        cache[type_name] = (base_symbol, False)
    else:
        family_symbols.append(dup)
        cache[type_name] = (dup, True)
    return cache[type_name]


def _family_symbols(doc, symbol):
    try:
        return [doc.GetElement(i) for i in symbol.Family.GetFamilySymbolIds()]
    except Exception:
        return [symbol]


# ═══════════════════════════════════════════════════════════════════════════
# CREATORS — one transaction each
# ═══════════════════════════════════════════════════════════════════════════

def _wall_type_name(base_type, thickness_mm):
    base = elem_name(base_type)
    if base.lower().startswith("generic"):
        return "Generic - {}mm".format(thickness_mm)
    return "{} - {}mm".format(base, thickness_mm)


def _wall_type_for(doc, base_type, thickness_mm, cache, result):
    if thickness_mm in cache:
        return cache[thickness_mm]
    name = _wall_type_name(base_type, thickness_mm)
    for wt in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        if elem_name(wt) == name:
            cache[thickness_mm] = wt
            return wt
    wt = base_type.Duplicate(name)
    cs = wt.GetCompoundStructure()
    if cs is not None:
        layers = cs.GetLayers()
        idx = 0
        for i in range(layers.Count):
            if layers[i].Function == DB.MaterialFunctionAssignment.Structure:
                idx = i
                break
        # the core layer takes up whatever the other layers do not
        others = sum(layers[i].Width for i in range(layers.Count) if i != idx)
        cs.SetLayerWidth(idx, max(geo.mm(thickness_mm) - others, geo.mm(1)))
        wt.SetCompoundStructure(cs)
    result.notes.append(u"Wall type created: {}".format(name))
    cache[thickness_mm] = wt
    return wt


def create_walls(doc, items, level, base_type, height_ft, base_offset_ft,
                 structural, match_thickness, progress=None):
    """items: [(x0, y0, x1, y1, thickness_ft or None)]."""
    result = RunResult("walls")
    cache = {}

    def body():
        z = level.Elevation
        for i, (x0, y0, x1, y1, thk) in enumerate(items):
            _tick(progress, i, len(items))
            if math.hypot(x1 - x0, y1 - y0) < TOL:
                result.skipped += 1
                continue
            try:
                wt = base_type
                if match_thickness and thk:
                    wt = _wall_type_for(doc, base_type, geo.round_to(geo.to_mm(thk), 1),
                                        cache, result)
                line = DB.Line.CreateBound(DB.XYZ(x0, y0, z), DB.XYZ(x1, y1, z))
                wall = DB.Wall.Create(doc, line, wt.Id, level.Id, height_ft,
                                      base_offset_ft, False, bool(structural))
                if wall is None:
                    result.fail()
                else:
                    result.created += 1
            except Exception as ex:
                result.fail(ex)

    return run_transaction(doc, "T3Lab: CAD to Walls", body, result)


def create_parts(doc, profiles, bic, tx_name, progress=None):
    """profiles: [(outer_loop, [hole loops], bottom_z_ft, height_ft)] -> DirectShapes."""
    result = RunResult("parts")

    def body():
        for i, (outer, holes, z_ft, height_ft) in enumerate(profiles):
            _tick(progress, i, len(profiles))
            try:
                ds = directshape_extrusion(doc, bic, profile(outer, holes, 0.0), z_ft, height_ft)
                if ds is None:
                    result.fail()
                else:
                    result.created += 1
            except Exception as ex:
                result.fail(ex)

    return run_transaction(doc, tx_name, body, result)


def create_floors(doc, groups, floor_type_id, level, offset_ft, structural, progress=None):
    """groups: [(outer_loop, [hole loops])]."""
    result = RunResult("floors")
    holes_dropped = [0]

    def make(loops):
        return DB.Floor.Create(doc, loops, floor_type_id, level.Id, bool(structural), None, 0.0)

    def body():
        z = level.Elevation
        for i, (outer, holes) in enumerate(groups):
            _tick(progress, i, len(groups))
            try:
                try:
                    floor = make(profile(outer, holes, z))
                except Exception:
                    if not holes:
                        raise
                    floor = make(profile(outer, [], z))
                    holes_dropped[0] += len(holes)
                if floor is None:
                    result.fail()
                    continue
                if offset_ft:
                    _set_param(floor, DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM, offset_ft)
                result.created += 1
            except Exception as ex:
                result.fail(ex)

    run_transaction(doc, "T3Lab: CAD to Floors", body, result)
    if holes_dropped[0]:
        result.notes.append("Openings Revit rejected (floor made without them): {}"
                            .format(holes_dropped[0]))
    return result


def create_ceilings(doc, groups, ceiling_type_id, level, offset_ft, progress=None):
    result = RunResult("ceilings")
    holes_dropped = [0]

    def body():
        z = level.Elevation
        for i, (outer, holes) in enumerate(groups):
            _tick(progress, i, len(groups))
            try:
                try:
                    ceiling = DB.Ceiling.Create(doc, profile(outer, holes, z), ceiling_type_id, level.Id)
                except Exception:
                    if not holes:
                        raise
                    ceiling = DB.Ceiling.Create(doc, profile(outer, [], z), ceiling_type_id, level.Id)
                    holes_dropped[0] += len(holes)
                if ceiling is None:
                    result.fail()
                    continue
                _set_param(ceiling, DB.BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM, offset_ft)
                result.created += 1
            except Exception as ex:
                result.fail(ex)

    run_transaction(doc, "T3Lab: CAD to Ceilings", body, result)
    if holes_dropped[0]:
        result.notes.append("Openings Revit rejected (ceiling made without them): {}"
                            .format(holes_dropped[0]))
    return result


def create_rooms(doc, level, view, edges, points, make_lines, make_rooms,
                 room_name, skip_existing, progress=None):
    """Room separation lines along `edges` (in `view`) and rooms at `points`."""
    result = RunResult("rooms" if make_rooms else "room separation lines")
    lines_made = [0]
    stats = {"occupied": 0, "open": 0}

    def body():
        z = level.Elevation
        total = len(points) + (1 if make_lines else 0)
        if make_lines and edges:
            _tick(progress, 0, total)
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, z))
            sp = DB.SketchPlane.Create(doc, plane)
            carr = DB.CurveArray()
            short = doc.Application.ShortCurveTolerance
            for e in edges:
                try:
                    c = curve_from_edge(e, z)
                    if c.Length > short:
                        carr.Append(c)
                    else:
                        result.skipped += 1
                except Exception:
                    result.skipped += 1
            made = doc.Create.NewRoomBoundaryLines(sp, carr, view)
            lines_made[0] = made.Size if made is not None else 0
            if not make_rooms:
                result.created = lines_made[0]
            doc.Regenerate()
        if not make_rooms:
            return
        new_rooms = []
        for i, (x, y) in enumerate(points):
            _tick(progress, i + 1, total)
            try:
                if skip_existing and doc.GetRoomAtPoint(DB.XYZ(x, y, z + 1.0)) is not None:
                    stats["occupied"] += 1
                    result.skipped += 1
                    continue
                room = doc.Create.NewRoom(level, DB.UV(x, y))
                if room is None:
                    result.fail()
                    continue
                if room_name:
                    _set_param(room, DB.BuiltInParameter.ROOM_NAME, room_name)
                new_rooms.append(room)
            except Exception as ex:
                result.fail(ex)
        doc.Regenerate()
        for room in new_rooms:
            try:
                if room.Area <= 1e-6:
                    doc.Delete(room.Id)          # not enclosed: do not leave junk
                    stats["open"] += 1
                    result.skipped += 1
                else:
                    result.created += 1
            except Exception:
                result.created += 1

    run_transaction(doc, "T3Lab: CAD to Rooms", body, result)
    if make_rooms and make_lines:
        result.notes.append("Room separation lines drawn: {}".format(lines_made[0]))
    if stats["occupied"]:
        result.notes.append("Outlines that already hold a room: {}".format(stats["occupied"]))
    if stats["open"]:
        result.notes.append("Outlines that are not enclosed in Revit (room removed): {}"
                            .format(stats["open"]))
    return result


RECT_WIDTH_PARAMS = ("b", "Width", "B", "W")
RECT_DEPTH_PARAMS = ("h", "Depth", "H", "D")
ROUND_PARAMS = ("d", "Diameter", "D", "b")


def create_columns(doc, footprints, level, top_level, height_ft, rect_symbol,
                   round_symbol, structural, match_size, rotate, step_mm, progress=None):
    result = RunResult("columns")
    stype = DB.Structure.StructuralType.Column if structural \
        else DB.Structure.StructuralType.NonStructural
    caches = {}
    size_failed = [0]

    def symbol_for(fp):
        base = rect_symbol if fp["shape"] == "rect" else round_symbol
        if base is None:
            return None
        if not match_size:
            return base
        key = eid_value(base.Family.Id)
        cache = caches.setdefault(key, {})
        fam_syms = cache.setdefault("__symbols__", _family_symbols(doc, base))
        w = geo.mm(geo.round_to(geo.to_mm(fp["width"]), step_mm))
        if fp["shape"] == "round":
            sizes = [(ROUND_PARAMS, w)]
        else:
            d = geo.mm(geo.round_to(geo.to_mm(fp["depth"]), step_mm))
            sizes = [(RECT_WIDTH_PARAMS, w), (RECT_DEPTH_PARAMS, d)]
        sym, ok = _duplicate_sized(doc, base, geo.footprint_type_name(fp, step_mm),
                                   sizes, cache, fam_syms)
        if not ok:
            size_failed[0] += 1
        return sym

    def body():
        z = level.Elevation
        for i, fp in enumerate(footprints):
            _tick(progress, i, len(footprints))
            try:
                sym = symbol_for(fp)
                if sym is None:
                    result.skipped += 1
                    continue
                _ensure_active(doc, sym)
                p = DB.XYZ(fp["cx"], fp["cy"], z)
                inst = doc.Create.NewFamilyInstance(p, sym, level, stype)
                if inst is None:
                    result.fail()
                    continue
                _set_param(inst, DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM, 0.0)
                if top_level is not None:
                    _set_param(inst, DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM, top_level.Id)
                    _set_param(inst, DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM, 0.0)
                else:
                    _set_param(inst, DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM, level.Id)
                    _set_param(inst, DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM, height_ft)
                if rotate and abs(fp["angle"]) > 1e-6:
                    axis = DB.Line.CreateBound(p, p + DB.XYZ.BasisZ)
                    DB.ElementTransformUtils.RotateElement(doc, inst.Id, axis, fp["angle"])
                result.created += 1
            except Exception as ex:
                result.fail(ex)

    run_transaction(doc, "T3Lab: CAD to Columns", body, result)
    if size_failed[0]:
        result.notes.append("Placed with the chosen type because its family has no "
                            "size parameter (b/h, Width/Depth, d/Diameter): {}".format(size_failed[0]))
    return result


BEAM_WIDTH_PARAMS = ("b", "Width", "B", "W")
BEAM_HEIGHT_PARAMS = ("h", "Height", "H", "Depth", "d")


def create_beams(doc, items, level, base_symbol, z_offset_ft, match_size, progress=None):
    """items: [(x0, y0, x1, y1, width_mm, height_mm)]."""
    result = RunResult("beams")
    cache = {}
    fam_syms = _family_symbols(doc, base_symbol)
    size_failed = [0]

    def body():
        z = level.Elevation + z_offset_ft
        for i, (x0, y0, x1, y1, w_mm, h_mm) in enumerate(items):
            _tick(progress, i, len(items))
            if math.hypot(x1 - x0, y1 - y0) < geo.mm(100):
                result.skipped += 1
                continue
            try:
                sym = base_symbol
                if match_size:
                    sym, ok = _duplicate_sized(
                        doc, base_symbol, "{}x{}mm".format(int(w_mm), int(h_mm)),
                        [(BEAM_WIDTH_PARAMS, geo.mm(w_mm)), (BEAM_HEIGHT_PARAMS, geo.mm(h_mm))],
                        cache, fam_syms)
                    if not ok:
                        size_failed[0] += 1
                _ensure_active(doc, sym)
                line = DB.Line.CreateBound(DB.XYZ(x0, y0, z), DB.XYZ(x1, y1, z))
                beam = doc.Create.NewFamilyInstance(line, sym, level,
                                                    DB.Structure.StructuralType.Beam)
                if beam is None:
                    result.fail()
                else:
                    result.created += 1
            except Exception as ex:
                result.fail(ex)

    run_transaction(doc, "T3Lab: CAD to Beams", body, result)
    if size_failed[0]:
        result.notes.append("Placed with the chosen type because its family has no "
                            "b/h (Width/Height) parameter: {}".format(size_failed[0]))
    return result


def create_grids(doc, lines, names, grid_type_id, taken_names, progress=None):
    """lines: [(x0, y0, x1, y1)] already extended; names aligned (None = Revit's)."""
    result = RunResult("grids")
    renamed = [0]

    def body():
        for i, (x0, y0, x1, y1) in enumerate(lines):
            _tick(progress, i, len(lines))
            try:
                grid = DB.Grid.Create(doc, DB.Line.CreateBound(DB.XYZ(x0, y0, 0), DB.XYZ(x1, y1, 0)))
                if grid is None:
                    result.fail()
                    continue
                if grid_type_id is not None:
                    try:
                        grid.ChangeTypeId(grid_type_id)
                    except Exception:
                        pass
                name = names[i] if names else None
                if name:
                    if name in taken_names:
                        renamed[0] += 1
                    else:
                        try:
                            grid.Name = name
                            taken_names.add(name)
                        except Exception:
                            renamed[0] += 1
                result.created += 1
            except Exception as ex:
                result.fail(ex)

    run_transaction(doc, "T3Lab: CAD to Grids", body, result)
    if renamed[0]:
        result.notes.append("Grids that kept Revit's name because the planned name "
                            "is already used: {}".format(renamed[0]))
    return result


def create_lines(doc, edges, detail_view, z_ft, line_style, progress=None):
    """Model lines at z_ft, or detail lines in `detail_view` when given."""
    result = RunResult("detail lines" if detail_view is not None else "model lines")

    def body():
        short = doc.Application.ShortCurveTolerance
        sp = None
        if detail_view is None:
            sp = DB.SketchPlane.Create(
                doc, DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, z_ft)))
        for i, e in enumerate(edges):
            _tick(progress, i, len(edges))
            try:
                c = curve_from_edge(e, z_ft)
                if c.Length <= short:
                    result.skipped += 1
                    continue
                if detail_view is None:
                    el = doc.Create.NewModelCurve(c, sp)
                else:
                    el = doc.Create.NewDetailCurve(detail_view, c)
                if el is None:
                    result.fail()
                    continue
                if line_style is not None:
                    try:
                        el.LineStyle = line_style
                    except Exception:
                        pass
                result.created += 1
            except Exception as ex:
                result.fail(ex)

    tx = "T3Lab: CAD to Detail Lines" if detail_view is not None else "T3Lab: CAD to Model Lines"
    return run_transaction(doc, tx, body, result)


# ── MEP ─────────────────────────────────────────────────────────────────────

def _set_mep_size(elem, key, width_ft, height_ft):
    """Best-effort sizing; False = the type kept its default size."""
    B = DB.BuiltInParameter
    if not width_ft:
        return True
    if key == "duct":
        if _set_param(elem, B.RBS_CURVE_WIDTH_PARAM, width_ft):
            if height_ft:
                _set_param(elem, B.RBS_CURVE_HEIGHT_PARAM, height_ft)
            return True
        return _set_param(elem, B.RBS_CURVE_DIAMETER_PARAM, width_ft)
    if key == "pipe":
        return _set_param(elem, B.RBS_PIPE_DIAMETER_PARAM, width_ft)
    if key == "tray":
        ok = _set_param(elem, B.RBS_CABLETRAY_WIDTH_PARAM, width_ft)
        if height_ft:
            ok = _set_param(elem, B.RBS_CABLETRAY_HEIGHT_PARAM, height_ft) and ok
        return ok
    if key == "conduit":
        return _set_param(elem, B.RBS_CONDUIT_DIAMETER_PARAM, width_ft)
    return False


def _free_end_connector(elem, point, tol):
    best, best_d = None, None
    try:
        for c in elem.ConnectorManager.Connectors:
            try:
                if c.ConnectorType != DB.ConnectorType.End or c.IsConnected:
                    continue
                d = c.Origin.DistanceTo(point)
                if d <= tol and (best_d is None or d < best_d):
                    best, best_d = c, d
            except Exception:
                pass
    except Exception:
        pass
    return best


def connect_with_elbows(doc, created, tol=0.03):
    """Elbows where exactly two new runs meet at an angle. (placed, skipped)."""
    placed = skipped = 0
    ends = []
    for idx, (elem, p0, p1) in enumerate(created):
        if p0.DistanceTo(p1) < TOL:
            continue
        d = (p1 - p0).Normalize()
        ends.append((idx, p0, d))
        ends.append((idx, p1, d.Negate()))
    used = [False] * len(ends)
    for i in range(len(ends)):
        if used[i]:
            continue
        group = [i] + [j for j in range(i + 1, len(ends))
                       if not used[j] and ends[i][1].DistanceTo(ends[j][1]) <= tol]
        if len(group) < 2:
            continue
        for gi in group:
            used[gi] = True
        if len(group) > 2:
            skipped += 1            # T / X junction: needs a tee or cross
            continue
        a, b = ends[group[0]], ends[group[1]]
        if a[0] == b[0] or abs(a[2].DotProduct(b[2])) > 0.985:
            skipped += 1            # straight continuation: no elbow possible
            continue
        c1 = _free_end_connector(created[a[0]][0], a[1], tol)
        c2 = _free_end_connector(created[b[0]][0], b[1], tol)
        if c1 is None or c2 is None:
            skipped += 1
            continue
        try:
            doc.Create.NewElbowFitting(c1, c2)
            placed += 1
        except Exception:
            skipped += 1
    return placed, skipped


def create_mep_runs(doc, key, segments, level, offset_ft, type_id, system_id,
                    width_ft, height_ft, auto_elbow, noun, tx_name, progress=None):
    """segments: [(x0, y0, x1, y1, width_ft or None)] — a detected width wins."""
    result = RunResult(noun)
    stats = {"size": 0, "elbows": 0, "elbow_skipped": 0}

    def body():
        z = level.Elevation + offset_ft
        created = []
        for i, (x0, y0, x1, y1, w) in enumerate(segments):
            _tick(progress, i, len(segments))
            p0, p1 = DB.XYZ(x0, y0, z), DB.XYZ(x1, y1, z)
            if p0.DistanceTo(p1) < TOL:
                result.skipped += 1
                continue
            try:
                if key == "duct":
                    el = DB.Mechanical.Duct.Create(doc, system_id, type_id, level.Id, p0, p1)
                elif key == "pipe":
                    el = DB.Plumbing.Pipe.Create(doc, system_id, type_id, level.Id, p0, p1)
                elif key == "tray":
                    el = DB.Electrical.CableTray.Create(doc, type_id, p0, p1, level.Id)
                else:
                    el = DB.Electrical.Conduit.Create(doc, type_id, p0, p1, level.Id)
                if el is None:
                    result.fail()
                    continue
                if not _set_mep_size(el, key, w or width_ft, height_ft):
                    stats["size"] += 1
                created.append((el, p0, p1))
                result.created += 1
            except Exception as ex:
                result.fail(ex)
        if auto_elbow and len(created) >= 2:
            doc.Regenerate()            # connector origins follow the new sizes
            stats["elbows"], stats["elbow_skipped"] = connect_with_elbows(doc, created)

    run_transaction(doc, tx_name, body, result)
    if stats["size"]:
        result.notes.append("Size not applied (not in the type's size catalog): {}"
                            .format(stats["size"]))
    if auto_elbow:
        result.notes.append("Elbows placed: {} · junctions left to connect by hand: {}"
                            .format(stats["elbows"], stats["elbow_skipped"]))
    return result
