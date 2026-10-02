# -*- coding: utf-8 -*-
"""
Bad Geometry Check (faceter / silhouette killers)

Finds the elements whose geometry defeats Revit's faceter — the class of
geometry that makes Revit log

    Zero normal vector for ruled surface.      (RuledSurf.cpp)
    Invalid RuledSurf.                         (Face.cpp)
    Silhouette moved outside face envelope.    (Silhouettes.cpp)
    Shewchuk triangulation failed...           (Face.cpp)
    Problems during faceting
    facetFaceWithSpecifiedAlgorithm is triangulating a long, thin face

and then hard-crash the process (0xc0000005 / "Invalid array ... Arr.cpp")
during PDF or DWG export. That fatal is native and uncatchable, so the point of
this check is to find the culprit BEFORE it is handed to an exporter.

Two safeguards make the scan itself survivable:
  * every element is breadcrumbed to disk before it is probed, so if Revit does
    die mid-scan the marker names the exact element (same trick BatchOut uses
    for exports);
  * the probe is normal-evaluation, not full tessellation. DEEP_PROBE=True
    additionally calls Face.Triangulate(), which is the real crash path — only
    turn it on when a normal scan comes back clean.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__ = "Tran Tien Thanh"
__title__ = "Bad Geometry Check"

# pylint: disable=import-error,invalid-name,broad-except,superfluous-parens
import os
import json
import math
import datetime

from pyrevit import coreutils
from pyrevit import script
from pyrevit import revit, DB, forms

from pyrevit.preflight import PreflightTestCase


# ── Tuning ───────────────────────────────────────────────────────────
# The thresholds and the probing functions moved to lib/Snippets/_geometry_probe.py
# so the assistant's `check_bad_geometry` tool runs the SAME probe this preflight
# check does — two copies would drift, and this one is the ground truth for the
# PDF-export crash. Everything below is re-exported for the rest of this module.
from Snippets._geometry_probe import (        # noqa: F401
    FT_PER_MM, SLIVER_MM, TINY_AREA, ZERO_NORMAL, SINGULAR_TOL,
    SAMPLE_FRACTIONS, DOMAIN_ASPECT,
    surface_kind, face_perimeter, walk_solids,
    probe_face as _probe_face, probe_element as _probe_element,
)

# Call Face.Triangulate() too. This IS the code path that crashes Revit —
# leave it False for a first pass; the breadcrumb will name the element if it
# does bring the session down.
DEEP_PROBE = False


def probe_face(face):
    """Preflight wrapper — applies this module's DEEP_PROBE setting."""
    return _probe_face(face, deep_probe=DEEP_PROBE)


def probe_element(element, view):
    """Preflight wrapper — applies this module's DEEP_PROBE setting."""
    return _probe_element(element, view, deep_probe=DEEP_PROBE)


# ── Where the scan keeps its state ───────────────────────────────────────
# %APPDATA%\T3LabAI\diagnostics, never Documents: the marker is rewritten and
# fsync'd before EVERY probed element (and the progress checkpoint as the scan
# goes), which in a OneDrive-synced Documents folder meant one sync upload per
# element. It is per-machine state, not a user file. Older builds wrote to
# Documents\T3Lab_Diagnostics — see _diag_file for how those files are adopted.
from core.paths import user_data_path

LEGACY_DIAG_FOLDER = os.path.join(os.path.expanduser('~'), 'Documents', 'T3Lab_Diagnostics')


def _adopt_legacy_file(old, new):
    """Move a TRANSIENT file an older build left in Documents to its new home.

    Moved, not copied: marker and progress are deleted when a scan completes,
    and a copy would come back from the old folder after every delete. Never
    raises."""
    try:
        if old and os.path.isfile(old) and not os.path.exists(new):
            import shutil
            shutil.move(old, new)
    except Exception:
        pass


def _diag_file(name, transient):
    """Path of one diagnostics file under %APPDATA%\\T3LabAI\\diagnostics."""
    old = os.path.join(LEGACY_DIAG_FOLDER, name)
    if transient:
        path = user_data_path('diagnostics', name)
        _adopt_legacy_file(old, path)
        return path
    # durable: copied over once, the old file is left untouched
    return user_data_path('diagnostics', name, legacy=[old])


MARKER_FILE = _diag_file('_geometry_scan_marker.json', transient=True)

# Elements already probed in an earlier (possibly crashed) run, so a scan that
# brings Revit down can be resumed instead of restarted. Cleared when a scan
# completes normally.
PROGRESS_FILE = _diag_file('_geometry_scan_progress.json', transient=True)

# Findings are written here so BatchOut's safe-mode export can hide exactly
# these elements while exporting the sheets they poison. BatchOut reads the
# SAME path (GUI/BatchOutDialog.py bad_geometry_file(); dev/test_user_data_moves.py
# keeps the two in step).
FINDINGS_FILE = _diag_file('_bad_geometry.json', transient=False)

DIAG_FOLDER = os.path.dirname(FINDINGS_FILE)


# ── ElementId compatibility (Revit 2024+ dropped IntegerValue) ────────────
from Snippets._compat import make_eid, eid_value


def make_eid_safe(value):
    """ElementId from an int, version-safe. None when it cannot be built."""
    try:
        eid = make_eid(value)
        if eid and eid_value(eid) != -1:
            return eid
    except Exception:
        pass
    return None


def elem_name(element):
    """element.Name, IronPython-safe (some subclasses hide the getter)."""
    try:
        return element.Name
    except AttributeError:
        try:
            return DB.Element.Name.GetValue(element)
        except Exception:
            return "<unnamed>"


# ── Crash breadcrumb ──────────────────────────────────────────────────────
def write_marker(view_name, element_id, description):
    """Name the element about to be probed, durably (fsync'd), so a native
    crash still tells us which one was fatal."""
    try:
        if not os.path.exists(DIAG_FOLDER):
            os.makedirs(DIAG_FOLDER)
        payload = {
            'view': view_name,
            'element_id': element_id,
            'element': description,
            'time': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(MARKER_FILE, 'w') as fh:
            json.dump(payload, fh)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
    except Exception:
        pass


def clear_marker():
    try:
        if os.path.exists(MARKER_FILE):
            os.remove(MARKER_FILE)
    except Exception:
        pass


def read_marker():
    try:
        if os.path.exists(MARKER_FILE):
            with open(MARKER_FILE, 'r') as fh:
                return json.load(fh)
    except Exception:
        pass
    return None


# ── Resumable scan state ──────────────────────────────────────────────────
# DEEP_PROBE (and, rarely, the normal probe) can take the process down. Losing
# an hour of scanning to that is worse than the crash itself, so every probed
# element id and every finding is persisted as we go. A resumed scan skips what
# it already knows and — crucially — skips the element that killed the last
# session, so consecutive runs make progress instead of dying in the same spot.

def read_progress(doc):
    """(probed_ids set, findings list, fatal_ids list) carried over from an
    unfinished scan of THIS model. Empty when there is nothing to resume."""
    try:
        if not os.path.exists(PROGRESS_FILE):
            return set(), [], []
        with open(PROGRESS_FILE, 'r') as fh:
            data = json.load(fh)
        if data.get('doc') != doc.Title:
            return set(), [], []
        return (set(data.get('probed', [])),
                data.get('findings', []),
                data.get('fatal', []))
    except Exception:
        return set(), [], []


def write_progress(doc, probed, findings, fatal):
    """Checkpoint the scan. Findings are stored without the live ElementId
    (not JSON-serialisable) — it is rebuilt from id_value on resume."""
    try:
        if not os.path.exists(DIAG_FOLDER):
            os.makedirs(DIAG_FOLDER)
        slim = []
        for finding in findings:
            slim.append({k: v for k, v in finding.items() if k != 'id'})
        payload = {
            'doc': doc.Title,
            'updated': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'probed': sorted(probed),
            'findings': slim,
            'fatal': fatal,
        }
        with open(PROGRESS_FILE, 'w') as fh:
            json.dump(payload, fh)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
    except Exception:
        pass


def clear_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
    except Exception:
        pass


def write_findings(doc, findings):
    """Publish the suspect element ids for BatchOut's safe-mode export to hide.
    Only elements with a hard signature are published — sliver-only hits are
    reported to the user but not auto-hidden, since slivers are common and
    usually harmless."""
    try:
        if not os.path.exists(DIAG_FOLDER):
            os.makedirs(DIAG_FOLDER)
        publishable = []
        for finding in findings:
            joined = " ".join(finding['problems'])
            if ("SINGULAR POINT" in joined
                    or "DEGENERATE UV DOMAIN" in joined
                    or "ZERO NORMAL" in joined
                    or "zero-area" in joined
                    or "Triangulate() failed" in joined
                    or "ComputeNormal failed" in joined
                    or "FATAL" in joined):
                publishable.append({
                    'id': finding['id_value'],
                    'category': finding['category'],
                    'name': finding['name'],
                    'view': finding['view'],
                    'problems': finding['problems'],
                })
        payload = {
            'doc': doc.Title,
            'generated': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'elements': publishable,
        }
        with open(FINDINGS_FILE, 'w') as fh:
            json.dump(payload, fh, indent=2)
        return len(publishable)
    except Exception:
        return 0



# ── Target selection ──────────────────────────────────────────────────────
def views_to_scan(doc, output):
    """Views placed on the sheets the user picks. The crash is view-scoped, so
    scanning by view (not whole model) keeps this bounded and relevant."""
    sheets = DB.FilteredElementCollector(doc) \
               .OfClass(DB.ViewSheet) \
               .WhereElementIsNotElementType() \
               .ToElements()
    sheets = [s for s in sheets if not s.IsTemplate]
    if not sheets:
        return []

    picked = forms.SelectFromList.show(
        sorted([SheetOption(s) for s in sheets], key=lambda o: o.name),
        title="Pick the sheet(s) that crash on export",
        multiselect=True,
        button_name="Scan these sheets")
    if not picked:
        return []

    targets = []
    for option in picked:
        sheet = option.sheet
        for vid in sheet.GetAllPlacedViews():
            view = doc.GetElement(vid)
            if view is not None and not view.IsTemplate:
                targets.append((sheet.SheetNumber, view))
    return targets


def report_links(doc, view, view_label, output):
    """List the RevitLinkInstances drawing into `view`.

    Every crash journal for this model shows RVT-link graphics regenerating
    immediately before death, and `FilteredElementCollector(doc, view.Id)` does
    not return elements that live inside a link — so without this the scan can
    come back clean while the real culprit sits in a linked file. Individual
    linked elements cannot be hidden through `View.HideElements` (the ids belong
    to another document), so the mitigations that reach them are the whole-link
    rung or a category hide.
    """
    try:
        links = DB.FilteredElementCollector(doc, view.Id) \
                  .OfClass(DB.RevitLinkInstance) \
                  .ToElements()
    except Exception:
        return
    if not links:
        return
    names = []
    for link in links:
        try:
            link_doc = link.GetLinkDocument()
            names.append(link_doc.Title if link_doc is not None
                         else "{} (not loaded)".format(elem_name(link)))
        except Exception:
            names.append(elem_name(link))
    output.print_md(
        "- `{}` draws **{}** link(s) not covered by this element scan: {}".format(
            view_label, len(names), ", ".join("`{}`".format(n) for n in names)))


class SheetOption(object):
    """forms.SelectFromList wrapper so the list shows number + name."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.name = "{} - {}".format(sheet.SheetNumber, elem_name(sheet))

    def __str__(self):
        return self.name


# ── Main check ────────────────────────────────────────────────────────────
def checkModel(doc, output):
    output.print_md("# Bad Geometry Check")
    output.print_md(
        "Hunting the geometry that defeats Revit's faceter and hard-crashes "
        "PDF / DWG export.")

    # Resume state first: a scan that crashed left both a breadcrumb naming the
    # killer and a checkpoint of everything already probed.
    probed, findings, fatal_ids = read_progress(doc)
    for finding in findings:
        finding['id'] = make_eid_safe(finding.get('id_value'))

    stale = read_marker()
    if stale:
        killer_id = stale.get('element_id')
        output.print_md(
            "> ### Previous scan was killed by this element\n"
            "> `{}` (id **{}**) in view `{}` at {}.\n"
            "> It is recorded as **FATAL** and will be skipped from here on, so "
            "this run continues past it instead of dying in the same place."
            .format(stale.get('element'), killer_id,
                    stale.get('view'), stale.get('time')))
        if killer_id is not None and killer_id not in fatal_ids:
            fatal_ids.append(killer_id)
            findings.append({
                'view': stale.get('view', '<unknown>'),
                'id': make_eid_safe(killer_id),
                'id_value': killer_id,
                'category': (stale.get('element') or ':').split(':')[0].strip(),
                'name': (stale.get('element') or ': ').split(':')[-1].strip(),
                'problems': ["FATAL -- hard-crashed Revit while being probed"],
            })
        probed.add(killer_id)
        clear_marker()

    if probed:
        output.print_md(
            "Resuming: **{}** element(s) already probed, **{}** known fatal."
            .format(len(probed), len(fatal_ids)))

    targets = views_to_scan(doc, output)
    if not targets:
        output.print_md("No sheets selected - nothing scanned.")
        return

    output.print_md("Scanning **{}** views. DEEP_PROBE = `{}`.".format(
        len(targets), DEEP_PROBE))

    scanned = 0
    since_checkpoint = 0
    for sheet_number, view in targets:
        view_label = "{} / {}".format(sheet_number, elem_name(view))
        try:
            elements = DB.FilteredElementCollector(doc, view.Id) \
                         .WhereElementIsNotElementType() \
                         .ToElements()
        except Exception as ex:
            output.print_md("- Could not collect from `{}`: {}".format(view_label, ex))
            continue

        # Linked geometry renders through the host view but is NOT returned by
        # the collector above, so report the links carrying content into this
        # view — they cannot be hidden element-by-element, only as a whole
        # instance or by category.
        report_links(doc, view, view_label, output)

        for element in elements:
            element_id = eid_value(element.Id)
            if element_id in probed:
                continue

            try:
                category = element.Category.Name if element.Category else "<no category>"
            except Exception:
                category = "<no category>"

            description = "{} : {}".format(category, elem_name(element))

            write_marker(view_label, element_id, description)
            problems = probe_element(element, view)
            clear_marker()
            scanned += 1
            probed.add(element_id)
            since_checkpoint += 1

            if problems:
                findings.append({
                    'view': view_label,
                    'id': element.Id,
                    'id_value': element_id,
                    'category': category,
                    'name': elem_name(element),
                    'problems': problems,
                })

            # Checkpoint periodically so a crash costs at most 25 elements of
            # re-probing rather than the whole scan.
            if since_checkpoint >= 25:
                write_progress(doc, probed, findings, fatal_ids)
                since_checkpoint = 0

    output.print_md("---")
    output.print_md("Probed **{}** elements across **{}** views.".format(
        scanned, len(targets)))

    # The scan finished without taking Revit down — drop the resume state so
    # the next run starts clean.
    clear_progress()

    if not findings:
        output.print_md(
            "**No degenerate geometry found.** Re-run with `DEEP_PROBE = True` "
            "at the top of this file to exercise the actual tessellation path "
            "(that probe can crash Revit - the breadcrumb will name the "
            "element and the scan will resume past it).")
        return

    # Worst first. A confirmed process-killer outranks everything; after that,
    # the singular point is the exact SilSolver signature from the journal.
    def severity(finding):
        joined = " ".join(finding['problems'])
        if "FATAL" in joined:
            return 0
        if "SINGULAR POINT" in joined:
            return 1
        if "ZERO NORMAL" in joined:
            return 2
        if "Triangulate() failed" in joined or "zero-area" in joined:
            return 3
        if "DEGENERATE UV DOMAIN" in joined:
            return 4
        if "sliver" in joined:
            return 5
        return 6

    findings.sort(key=severity)

    output.print_md("## {} suspect element(s)".format(len(findings)))
    for finding in findings:
        link = (output.linkify(finding['id']) if finding.get('id') is not None
                else finding.get('id_value'))
        output.print_md("**{}** - {} (id {})".format(
            finding['category'], finding['name'], link))
        output.print_md("  - view: `{}`".format(finding['view']))
        for problem in finding['problems']:
            output.print_md("  - {}".format(problem))

    published = write_findings(doc, findings)

    output.print_md("---")
    output.print_md(
        "**Fix order:** *FATAL* first (it already killed a session), then "
        "*SINGULAR POINT* - that is literally what `SilSolver::eval` logs "
        "before the crash - then *ZERO NORMAL VECTOR*. In the family, look for "
        "a blend / swept blend whose two profiles collapse to a point, a "
        "revolve with a zero-radius edge, or a spline-driven sweep. Rebuilding "
        "that one solid usually clears the whole crash.")
    if published:
        output.print_md(
            "**{} element(s) published to** `{}` - BatchOut safe mode will hide "
            "exactly these while exporting the sheets they poison, then roll the "
            "change back.".format(published, FINDINGS_FILE))
    else:
        output.print_md(
            "Nothing met the auto-hide bar (sliver-only hits are reported but "
            "not published, since slivers are common and usually harmless). "
            "If a sheet still crashes, use BatchOut's safe-mode ladder.")


class ModelChecker(PreflightTestCase):
    """
    Find geometry that crashes Revit's faceter during PDF / DWG export.

    Probes every face of every element drawn in the selected sheets' views for
    degenerate ruled surfaces (zero normals), zero-area faces and slivers -
    the geometry behind "Problems during faceting" and the 0xc0000005 export
    crash. Breadcrumbs each element so a crash still identifies the culprit.
    """

    name = "Bad Geometry (faceter crash) Finder"
    author = "Tran Tien Thanh"

    def setUp(self, doc, output):
        pass

    def startTest(self, doc, output):
        timer = coreutils.Timer()
        checkModel(doc, output)
        endtime = timer.get_time()
        print("Scan took " + str(datetime.timedelta(seconds=endtime)))

    def tearDown(self, doc, output):
        pass

    def doCleanups(self, doc, output):
        clear_marker()
