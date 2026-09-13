# -*- coding: utf-8 -*-
"""BCFReaderDialog — WPF Window and engine for viewing and navigating BCF issues."""

import os
import sys
import re
import clr
import traceback

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System")
clr.AddReference("System.Xml")
clr.AddReference("System.IO.Compression")
clr.AddReference("System.IO.Compression.FileSystem")

import System
from System.IO import MemoryStream, File, Path as IOPath, StreamReader
from System.IO.Compression import ZipFile
from System.Text import Encoding
from System.Windows import (
    Thickness, HorizontalAlignment, VerticalAlignment,
    TextWrapping, FontStyles, FontWeights, CornerRadius,
    GridLength, GridUnitType, WindowState, Visibility
)
from System.Windows.Media import BrushConverter, Stretch
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Windows.Controls import (
    Button, TextBlock, StackPanel, Border, ScrollViewer,
    Image, Orientation, ColumnDefinition, RowDefinition,
    ScrollBarVisibility
)
from System.Windows.Controls import Grid as WPFGrid
from System.Windows.Input import Cursors
from System.Collections.Generic import List
from System.Xml import XmlDocument
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog

# ---------------------------------------------------------------------------
# Revit API
# ---------------------------------------------------------------------------
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    ElementId, Transaction, FilteredElementCollector,
    RevitLinkInstance, IFailuresPreprocessor, FailureProcessingResult,
    XYZ, BoundingBoxXYZ
)
from Autodesk.Revit.UI import TaskDialog, IExternalEventHandler, ExternalEvent

# ---------------------------------------------------------------------------
# pyRevit
# ---------------------------------------------------------------------------
from pyrevit import revit, script, forms
from GUI.WPF_Base import T3WPFWindow, setup_window_logo
from GUI.forms import WPFWindow

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTPUT_DIR = r"C:\Temp\DQT_Purge"

COLOR_HEADER_BG   = "#18181B"
COLOR_HEADER_FG   = "#FFFFFF"
COLOR_BG          = "#F4F4F6"
COLOR_FOOTER_BG   = "#F4F4F6"
COLOR_CARD_BORDER = "#DCDCE0"
COLOR_CARD_SEL    = "#18181B"
COLOR_REMOVED     = "#D23B3B"
COLOR_ADDED       = "#22A85C"
COLOR_MODIFIED    = "#F5CE5A"
COLOR_OTHER       = "#71717A"
COLOR_TEXT        = "#27272A"
COLOR_TEXT_MUTED  = "#71717A"

LABEL_COLORS = {
    "REMOVED": COLOR_REMOVED,
    "ADDED": COLOR_ADDED,
    "MODIFIED": COLOR_MODIFIED,
}

# BCF stores coordinates in meters; Revit internal units are feet.
METERS_TO_FEET = 3.2808398950131233


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _eid_int(eid):
    """Revit 2026+ compatibility."""
    if eid is None:
        return -1
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


def _make_eid(int_value):
    try:
        return ElementId(System.Int64(int_value))
    except Exception:
        return ElementId(int(int_value))


def _ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        try:
            os.makedirs(OUTPUT_DIR)
        except Exception:
            pass


def _brush(hex_color):
    return BrushConverter().ConvertFromString(hex_color)


def _truncate(text, n):
    if text is None:
        return ""
    t = text.strip()
    if len(t) <= n:
        return t
    return t[:n-3] + "..."


def _csv_esc(s):
    if s is None:
        return ""
    s = str(s).replace('"', '""')
    if "," in s or '"' in s or "\n" in s or "\r" in s:
        return '"' + s + '"'
    return s


def _get_revit_author():
    """Get current user identifier from Revit Application.Username.
    Falls back to Windows username if Revit not logged in."""
    try:
        name = uiapp.Application.Username
        if name:
            return name
    except Exception:
        pass
    try:
        return System.Environment.UserName
    except Exception:
        return "unknown"


import uuid
# Static namespace for lib

class WarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.BCFReaderWarningSwallower"
    def PreprocessFailures(self, failuresAccessor):
        failuresAccessor.DeleteAllWarnings()
        return FailureProcessingResult.Continue


class ActionHandler(IExternalEventHandler):
    __namespace__ = "T3Lab.BCFReaderActionHandler"
    """Queues a callable to run on Revit API thread. Required for modeless
    WPF windows since WPF event handlers are outside Revit API context."""
    def __init__(self):
        self._action = None

    def set_action(self, action):
        self._action = action

    def Execute(self, uiapp):
        try:
            if self._action is not None:
                self._action()
        except Exception as ex:
            try:
                TaskDialog.Show("BCF Reader",
                    "Action error:\n" + str(ex) + "\n\n" + traceback.format_exc())
            except Exception:
                pass
        finally:
            self._action = None

    def GetName(self):
        return "BCF Action"


# ---------------------------------------------------------------------------
# BCF data model
# ---------------------------------------------------------------------------
class BCFComment(object):
    """A single comment on a BCF topic (BCF spec compliant)."""
    def __init__(self):
        self.guid = ""
        self.date = ""           # ISO 8601
        self.author = ""
        self.text = ""
        self.viewpoint_guid = "" # link to a viewpoint (optional)
        self.modified_date = ""
        self.modified_author = ""
        # Internal: dirty flag for new/modified comments not yet saved
        self.is_new = False


class BCFIssue(object):
    """Holds parsed data for a single BCF topic."""
    def __init__(self):
        self.guid = ""
        self.title = ""
        self.description = ""
        self.creation_date = ""
        self.label = "OTHER"
        self.resolved = False     # User mark - will be written to exported BCF
        self.element_id = None    # Primary element ID (first one)
        self.element_ids = []     # ALL element IDs (for clashes: 2+ elements)
        self.ifc_guid = ""
        self.position = None
        self.snapshot_bytes = None
        self.snapshot_image = None   # cached frozen BitmapImage
        self.camera_type = ""
        self.cam_viewpoint = None
        self.cam_direction = None
        self.cam_up = None
        self.clipping_planes = []
        self.components = []
        self.comments = []        # list of BCFComment
        self.index = 0

    def add_comment(self, text, author, viewpoint_guid=""):
        """Create a new BCFComment with current timestamp. Returns the comment."""
        from datetime import datetime
        c = BCFComment()
        c.guid = str(System.Guid.NewGuid())
        c.date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        c.author = author or "unknown"
        c.text = text or ""
        c.viewpoint_guid = viewpoint_guid or ""
        c.is_new = True
        self.comments.append(c)
        return c

    def build_snapshot_image(self):
        """Build and cache a full-resolution frozen BitmapImage.
        Only call from the WPF UI thread (e.g. detail panel display)."""
        if self.snapshot_image is not None:
            return self.snapshot_image
        if self.snapshot_bytes is None:
            return None
        try:
            ms = MemoryStream(self.snapshot_bytes)
            try:
                bi = BitmapImage()
                bi.BeginInit()
                bi.CacheOption = BitmapCacheOption.OnLoad
                bi.StreamSource = ms
                bi.EndInit()
                bi.Freeze()
            finally:
                try:
                    ms.Close()
                    ms.Dispose()
                except Exception:
                    pass
            self.snapshot_image = bi
            return bi
        except Exception:
            self.snapshot_image = None
            return None

    def build_thumbnail(self, max_width=220):
        """Build a small thumbnail BitmapImage (not cached) for card grid display.
        Uses DecodePixelWidth to avoid decoding the full-resolution PNG into RAM.
        Safe to call repeatedly — each call returns a new frozen BitmapImage."""
        if self.snapshot_bytes is None:
            return None
        try:
            ms = MemoryStream(self.snapshot_bytes)
            try:
                bi = BitmapImage()
                bi.BeginInit()
                bi.CacheOption = BitmapCacheOption.OnLoad
                bi.StreamSource = ms
                bi.DecodePixelWidth = max_width
                bi.EndInit()
                bi.Freeze()
            finally:
                try:
                    ms.Close()
                    ms.Dispose()
                except Exception:
                    pass
            return bi
        except Exception:
            return None


class BCFReader(object):
    """Parses a .bcf / .bcfzip archive."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.project_name = ""
        self.project_guid = ""
        self.issues = []

    def parse(self):
        if not File.Exists(self.filepath):
            raise IOError("BCF file not found: " + self.filepath)

        archive = ZipFile.OpenRead(self.filepath)
        try:
            # Project info
            for entry in archive.Entries:
                name_lower = entry.FullName.lower()
                if name_lower.endswith("project.bcfp"):
                    try:
                        self._parse_project(self._read_entry_text(entry))
                    except Exception:
                        pass

            # Group entries by topic folder
            folder_map = {}
            for entry in archive.Entries:
                full = entry.FullName.replace("\\", "/")
                parts = full.split("/")
                if len(parts) < 2:
                    continue
                folder = parts[0]
                filename = parts[-1].lower()
                if not folder:
                    continue
                d = folder_map.setdefault(folder, {})
                if filename == "markup.bcf":
                    d["markup"] = entry
                elif filename == "viewpoint.bcfv":
                    d["viewpoint"] = entry
                elif filename == "snapshot.png":
                    d["snapshot"] = entry

            idx = 0
            for folder in sorted(folder_map.keys()):
                parts = folder_map[folder]
                if "markup" not in parts:
                    continue
                idx += 1
                issue = BCFIssue()
                issue.guid = folder
                issue.index = idx

                try:
                    self._parse_markup(self._read_entry_text(parts["markup"]), issue)
                except Exception as ex:
                    issue.title = "[parse error] " + folder
                    issue.description = "Failed: " + str(ex)

                if "viewpoint" in parts:
                    try:
                        self._parse_viewpoint(self._read_entry_text(parts["viewpoint"]), issue)
                    except Exception:
                        pass

                if "snapshot" in parts:
                    try:
                        issue.snapshot_bytes = self._read_entry_bytes(parts["snapshot"])
                        # Image decoded lazily on the UI thread via build_thumbnail() /
                        # build_snapshot_image() — never during parse() to avoid OOM.
                    except Exception:
                        pass

                self._extract_from_description(issue)

                # Collect ALL element ids from components (clashes have 2+ elements)
                all_ids = []
                for c in issue.components:
                    aid = c.get("AuthoringToolId") or c.get("ElementId")
                    if aid:
                        try:
                            eid = int(aid)
                            if eid not in all_ids:
                                all_ids.append(eid)
                        except Exception:
                            pass
                    if not issue.ifc_guid and c.get("IfcGuid"):
                        issue.ifc_guid = c.get("IfcGuid")

                # Also extract additional IDs from description (e.g. "Source: ... :1372474 ... Target: ... :792679")
                if issue.description:
                    found_in_desc = re.findall(r":(\d{4,})", issue.description)
                    for s in found_in_desc:
                        try:
                            eid = int(s)
                            if eid not in all_ids and eid > 0:
                                all_ids.append(eid)
                        except Exception:
                            pass

                # If we still have nothing, try the regex-extracted single ID
                if not all_ids and issue.element_id is not None:
                    all_ids.append(issue.element_id)

                issue.element_ids = all_ids
                if issue.element_id is None and all_ids:
                    issue.element_id = all_ids[0]

                self.issues.append(issue)
        finally:
            archive.Dispose()

    def _read_entry_text(self, entry):
        stream = entry.Open()
        try:
            reader = StreamReader(stream, Encoding.UTF8)
            try:
                return reader.ReadToEnd()
            finally:
                reader.Dispose()
        finally:
            stream.Dispose()

    def _read_entry_bytes(self, entry):
        stream = entry.Open()
        try:
            ms = MemoryStream()
            try:
                stream.CopyTo(ms)
                return ms.ToArray()
            finally:
                ms.Dispose()
        finally:
            stream.Dispose()

    def _xml_doc(self, xml_text):
        xd = XmlDocument()
        xd.LoadXml(xml_text)
        return xd

    def _first_child_text(self, node, names):
        if node is None:
            return ""
        names_lower = [n.lower() for n in names]
        for child in node.ChildNodes:
            if child.NodeType != System.Xml.XmlNodeType.Element:
                continue
            if child.LocalName.lower() in names_lower:
                return child.InnerText or ""
        return ""

    def _find_element_recursive(self, node, local_name):
        if node is None:
            return None
        target = local_name.lower()
        for child in node.ChildNodes:
            if child.NodeType != System.Xml.XmlNodeType.Element:
                continue
            if child.LocalName.lower() == target:
                return child
            found = self._find_element_recursive(child, local_name)
            if found is not None:
                return found
        return None

    def _find_all_recursive(self, node, local_name, out_list):
        if node is None:
            return
        target = local_name.lower()
        for child in node.ChildNodes:
            if child.NodeType != System.Xml.XmlNodeType.Element:
                continue
            if child.LocalName.lower() == target:
                out_list.append(child)
            self._find_all_recursive(child, local_name, out_list)

    def _parse_xyz(self, node):
        if node is None:
            return None
        try:
            xs = self._first_child_text(node, ["X"])
            ys = self._first_child_text(node, ["Y"])
            zs = self._first_child_text(node, ["Z"])
            if xs == "" or ys == "" or zs == "":
                return None
            return (float(xs), float(ys), float(zs))
        except Exception:
            return None

    def _parse_project(self, xml_text):
        try:
            xd = self._xml_doc(xml_text)
            proj = self._find_element_recursive(xd, "Project")
            if proj is not None:
                self.project_guid = proj.GetAttribute("ProjectId") or ""
                self.project_name = self._first_child_text(proj, ["Name"])
        except Exception:
            pass

    def _parse_markup(self, xml_text, issue):
        xd = self._xml_doc(xml_text)
        topic = self._find_element_recursive(xd, "Topic")
        if topic is None:
            return
        issue.guid = topic.GetAttribute("Guid") or issue.guid
        issue.title = self._first_child_text(topic, ["Title"])
        issue.description = self._first_child_text(topic, ["Description"])
        issue.creation_date = self._first_child_text(topic, ["CreationDate"])

        labels_nodes = []
        self._find_all_recursive(topic, "Labels", labels_nodes)
        label_texts = []
        for ln in labels_nodes:
            t = (ln.InnerText or "").strip()
            if t:
                label_texts.append(t.upper())
        joined = ",".join(label_texts)
        detected = "OTHER"
        for key in ("REMOVED", "ADDED", "MODIFIED"):
            if key in joined:
                detected = key
                break
        # Resolved flag (user-marked via DQT)
        if "RESOLVED" in joined or "DQT_RESOLVED" in joined:
            issue.resolved = True

        if detected == "OTHER":
            desc_up = (issue.description or "").upper()
            title_up = (issue.title or "").upper()
            for key in ("REMOVED", "ADDED", "MODIFIED"):
                if desc_up.startswith(key) or ("| " + key in desc_up) or \
                   key in desc_up[:50] or key in title_up[:50]:
                    detected = key
                    break

        if detected == "OTHER":
            ttype = (topic.GetAttribute("TopicType") or "").upper()
            for key in ("REMOVED", "ADDED", "MODIFIED"):
                if key in ttype:
                    detected = key
                    break

        issue.label = detected

        # Parse <Comment> nodes (siblings of <Topic> in <Markup>)
        markup_root = topic.ParentNode
        if markup_root is None:
            markup_root = xd.DocumentElement
        comment_nodes = []
        self._find_all_recursive(markup_root, "Comment", comment_nodes)
        for cn in comment_nodes:
            c = BCFComment()
            c.guid = cn.GetAttribute("Guid") or ""
            c.date = self._first_child_text(cn, ["Date"])
            c.author = self._first_child_text(cn, ["Author"])
            c.text = self._first_child_text(cn, ["Comment"])
            c.modified_date = self._first_child_text(cn, ["ModifiedDate"])
            c.modified_author = self._first_child_text(cn, ["ModifiedAuthor"])
            # ViewpointGuid can be child element or attribute on <Viewpoint Guid="..."/>
            vp_node = self._find_element_recursive(cn, "Viewpoint")
            if vp_node is not None:
                c.viewpoint_guid = vp_node.GetAttribute("Guid") or ""
            if not c.viewpoint_guid:
                c.viewpoint_guid = self._first_child_text(cn, ["ViewpointGuid"])
            issue.comments.append(c)

    def _parse_viewpoint(self, xml_text, issue):
        xd = self._xml_doc(xml_text)
        vis = self._find_element_recursive(xd, "VisualizationInfo")
        if vis is None:
            vis = xd.DocumentElement

        comps = []
        self._find_all_recursive(vis, "Component", comps)
        for c in comps:
            d = {
                "IfcGuid": c.GetAttribute("IfcGuid") or "",
                "ElementId": "",
                "AuthoringToolId": c.GetAttribute("AuthoringToolId") or "",
            }
            ati_child = self._find_element_recursive(c, "AuthoringToolId")
            if ati_child is not None and not d["AuthoringToolId"]:
                d["AuthoringToolId"] = (ati_child.InnerText or "").strip()
            eid_child = self._find_element_recursive(c, "ElementId")
            if eid_child is not None:
                d["ElementId"] = (eid_child.InnerText or "").strip()
            if not d["ElementId"]:
                d["ElementId"] = c.GetAttribute("ElementId") or ""
            issue.components.append(d)

        ortho = self._find_element_recursive(vis, "OrthogonalCamera")
        persp = self._find_element_recursive(vis, "PerspectiveCamera")
        cam = ortho if ortho is not None else persp
        if ortho is not None:
            issue.camera_type = "Orthogonal"
        elif persp is not None:
            issue.camera_type = "Perspective"
        if cam is not None:
            issue.cam_viewpoint = self._parse_xyz(self._find_element_recursive(cam, "CameraViewPoint"))
            issue.cam_direction = self._parse_xyz(self._find_element_recursive(cam, "CameraDirection"))
            issue.cam_up = self._parse_xyz(self._find_element_recursive(cam, "CameraUpVector"))

        planes_container = self._find_element_recursive(vis, "ClippingPlanes")
        if planes_container is not None:
            plane_nodes = []
            self._find_all_recursive(planes_container, "ClippingPlane", plane_nodes)
            for pn in plane_nodes:
                loc = self._parse_xyz(self._find_element_recursive(pn, "Location"))
                dir_ = self._parse_xyz(self._find_element_recursive(pn, "Direction"))
                if loc is not None and dir_ is not None:
                    issue.clipping_planes.append((loc, dir_))

    def _extract_from_description(self, issue):
        desc = issue.description or ""
        if issue.element_id is None:
            m = re.search(r"Element\s*ID\s*:\s*(\d+)", desc, re.IGNORECASE)
            if m is None:
                m = re.search(r"ElementId[^0-9]*(\d+)", desc, re.IGNORECASE)
            if m:
                try:
                    issue.element_id = int(m.group(1))
                except Exception:
                    pass
        if issue.position is None:
            m = re.search(
                r"Position\s*:\s*\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)",
                desc, re.IGNORECASE)
            if m:
                try:
                    issue.position = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main WPF window (pyRevit forms.WPFWindow)
# ---------------------------------------------------------------------------
class BCFManagerWindow(WPFWindow):
    """Inherits pyRevit's WPFWindow which handles modeless + cross-runtime
    safely in Revit 2024/2025/2026."""

    def __init__(self, xaml_file):
        WPFWindow.__init__(self, xaml_file)
        setup_window_logo(self)

        # Bind helpers as instance attributes so WPF event closures
        # don't lose them via IronPython 2.7 module-globals lookup
        # (this is the root cause of the "_brush is not defined" error
        #  observed in Revit 2024 + pyRevit IronPython engine).
        self._brush = _brush
        self._truncate = _truncate
        self._csv_esc = _csv_esc
        self._make_eid = _make_eid
        self._ensure_output_dir = _ensure_output_dir
        # Color/constant references too (closures lose them on Revit 2024)
        self._LABEL_COLORS = LABEL_COLORS
        self._COLOR_ADDED = COLOR_ADDED
        self._COLOR_OTHER = COLOR_OTHER
        self._COLOR_TEXT = COLOR_TEXT
        self._COLOR_CARD_BORDER = COLOR_CARD_BORDER
        self._COLOR_HEADER_BG = COLOR_HEADER_BG

        # Dirty flag (any unsaved comment / resolved change)
        self._dirty = False

        # State
        self.reader = None
        self.issues = []
        self.selected_issue = None
        self.card_borders = {}
        self.active_filter = "ALL"

        # ExternalEvent for Revit API calls from modeless WPF handlers
        self._action_handler = ActionHandler()
        self._action_event = ExternalEvent.Create(self._action_handler)

        # Wire events (control names are exposed as attributes by WPFWindow)
        self.btnOpen.Click += self.on_open_click
        self.btnZoom.Click += self.on_zoom_click
        self.btnExport.Click += self.on_export_click
        self.btnAll.Click += lambda s, e: self.set_filter("ALL")
        self.btnRemoved.Click += lambda s, e: self.set_filter("REMOVED")
        self.btnAdded.Click += lambda s, e: self.set_filter("ADDED")
        self.btnModified.Click += lambda s, e: self.set_filter("MODIFIED")
        # Optional buttons (added later in XAML)
        try:
            self.btnExportBCF.Click += self.on_export_bcf_click
        except AttributeError:
            pass
        try:
            self.btnExportPDF.Click += self.on_export_pdf_click
        except AttributeError:
            pass
        try:
            self.btnResolved.Click += lambda s, e: self.set_filter("RESOLVED")
        except AttributeError:
            pass
        try:
            self.btnUnresolved.Click += lambda s, e: self.set_filter("UNRESOLVED")
        except AttributeError:
            pass
        try:
            self.txtSearch.TextChanged += self.on_search_changed
        except AttributeError:
            pass
        try:
            self.btnReload.Click += self.on_reload_click
        except AttributeError:
            pass
        try:
            self.btnWebLink.Click += self.on_weblink_click
        except AttributeError:
            pass

        self.populate_cards()
        self._init_ai_mode()

    # ── AI Mode Support ──────────────────────────────────────────────────

    def _init_ai_mode(self):
        try:
            if hasattr(self, 'is_ai_mode_active') and self.is_ai_mode_active():
                if hasattr(self, 'ai_mode_badge') and self.ai_mode_badge:
                    self.ai_mode_badge.Visibility = Visibility.Visible
                if hasattr(self, 'txt_ai_status') and self.txt_ai_status:
                    info = self.get_ai_status_info()
                    self.txt_ai_status.Text = "AI Mode: {}".format(info.get('model', 'Ready'))
        except Exception:
            pass

    def ai_triage_clicked(self, sender, e):
        """AI Triage: diagnose clash cause, assign discipline (Arch/Struct/MEP), and recommend Revit resolution."""
        btn = getattr(self, 'btn_ai_triage', None)
        orig_content = "✨ AI Triage"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        try:
            if not self.selected_issue:
                forms.alert("Please select an issue to triage.", title="AI Triage")
                return

            issue = self.selected_issue
            title = issue.title or "Untitled Issue"
            desc = issue.description or "No description"
            elem_id = str(issue.element_id) if issue.element_id is not None else "Unknown"

            def _apply_classic():
                txt = (title + " " + desc).lower()
                disc = "General"
                if any(k in txt for k in ["pipe", "duct", "mep", "cable", "conduit", "hvac"]):
                    disc = "MEP"
                elif any(k in txt for k in ["column", "beam", "framing", "slab", "foundation", "rebar"]):
                    disc = "Structural"
                elif any(k in txt for k in ["wall", "door", "window", "room", "ceiling", "finish"]):
                    disc = "Architectural"
                forms.alert(
                    "Rule-based Triage for #{}:\n- Responsible Discipline: {}\n- Target Element: {}\n- Recommendation: Verify model alignment in 3D view.".format(
                        issue.index, disc, elem_id),
                    title="BCF Issue Triage"
                )
                _restore_btn()

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_classic()
                return

            if btn:
                btn.Content = "⏳ Triaging..."
                btn.IsEnabled = False

            self.set_status("AI triaging issue #{}...".format(issue.index))

            prompt = (
                "You are an expert BIM Coordinator specializing in Clash Detection and BCF Issue Resolution.\n"
                "Analyze this BCF issue from Autodesk Revit / Solibri / Navisworks:\n\n"
                "Issue #{}: {}\n"
                "Description: {}\n"
                "Element ID: {}\n"
                "Camera / Viewpoint: {}\n\n"
                "Provide a concise 3-part triage in Vietnamese:\n"
                "1. PHÂN LUỒNG BỘ MÔN (Discipline & Subcontractor: Kiến trúc / Kết cấu / Cơ điện MEP)\n"
                "2. NGUYÊN NHÂN VA CHẠM / LỖI (Root Cause Diagnosis)\n"
                "3. GIẢI PHÁP KHẮC PHỤC TRONG REVIT (Recommended Action & Correction Steps)"
            ).format(issue.index, title, desc, elem_id, issue.camera_type or "N/A")

            def _worker():
                return self.ai_bridge.ask(prompt, max_tokens=1000)

            def _callback(res, err):
                try:
                    if err or not res:
                        _apply_classic()
                        return
                    forms.alert(res, title="✨ AI Triage: Issue #" + str(issue.index))
                    self.set_status("AI Triage completed for issue #{}.".format(issue.index))
                finally:
                    _restore_btn()

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            _restore_btn()
            self.set_status("AI Triage error: {}".format(ex))

    # ------------------------------------------------------------------
    # Window chrome handlers
    # ------------------------------------------------------------------
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            try:
                self.btn_maximize.ToolTip = "Maximize"
            except AttributeError:
                pass
        else:
            self.WindowState = WindowState.Maximized
            try:
                self.btn_maximize.ToolTip = "Restore"
            except AttributeError:
                pass

    def close_button_clicked(self, sender, e):
        self.Close()

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------
    def prompt_open(self):
        dlg = OpenFileDialog()
        dlg.Title = "Select BCF file"
        dlg.Filter = "BCF files (*.bcf;*.bcfzip)|*.bcf;*.bcfzip|All files (*.*)|*.*"
        if dlg.ShowDialog() == True:
            self.load_bcf(dlg.FileName)
            return True
        return False

    def load_bcf(self, filepath):
        """Load a BCF file. Parsing runs on a background thread so the Revit
        UI thread is never blocked; UI updates are marshalled back via Dispatcher."""
        from System.Threading import Thread, ThreadStart, ApartmentState
        from System import Action as _SysAction

        self.set_status("Loading: " + IOPath.GetFileName(filepath) + "...")
        try:
            self.btnOpen.IsEnabled = False
        except Exception:
            pass

        # Use a list as a mutable closure cell (Python 2 workaround)
        _result = [None]   # [BCFReader on success]
        _error  = [None]   # [error string on failure]

        def _parse_worker():
            try:
                r = BCFReader(filepath)
                r.parse()
                _result[0] = r
            except Exception as ex:
                _error[0] = str(ex) + "\n\n" + traceback.format_exc()

            # Marshal UI work back to the WPF dispatcher
            self.Dispatcher.BeginInvoke(_SysAction(_apply_result))

        def _apply_result():
            try:
                self.btnOpen.IsEnabled = True
            except Exception:
                pass

            if _error[0]:
                from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage
                MessageBox.Show(
                    "Failed to load BCF file:\n\n" + _error[0],
                    "BCF Reader",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error)
                self.set_status("Error loading BCF")
                return

            reader = _result[0]
            self.reader = reader
            self.issues = reader.issues
            fname = IOPath.GetFileName(filepath)
            subtitle = "{} - {} issues".format(fname, len(self.issues))
            if reader.project_name:
                subtitle = "{} | {}".format(reader.project_name, subtitle)
            self.lblSubtitle.Text = subtitle

            n_added    = sum(1 for i in self.issues if i.label == "ADDED")
            n_removed  = sum(1 for i in self.issues if i.label == "REMOVED")
            n_modified = sum(1 for i in self.issues if i.label == "MODIFIED")
            n_other    = sum(1 for i in self.issues if i.label == "OTHER")
            self.txtTotal.Text    = str(len(self.issues))
            self.txtAdded.Text    = str(n_added)
            self.txtRemoved.Text  = str(n_removed)
            self.txtModified.Text = str(n_modified)
            try:
                self.txtResolved.Text   = str(sum(1 for i in self.issues if i.resolved))
                self.txtUnresolved.Text = str(sum(1 for i in self.issues if not i.resolved))
            except AttributeError:
                pass

            self.set_filter("ALL", repopulate=False)
            self.populate_cards()

            msg = "Loaded {} issues (Added: {}, Removed: {}, Modified: {}".format(
                len(self.issues), n_added, n_removed, n_modified)
            if n_other > 0:
                msg += ", Unclassified: {}".format(n_other)
            msg += ")"
            self.set_status(msg)

        t = Thread(ThreadStart(_parse_worker))
        t.IsBackground = True
        t.SetApartmentState(ApartmentState.STA)
        t.Start()

    # ------------------------------------------------------------------
    # Cards
    # ------------------------------------------------------------------
    def populate_cards(self):
        self.panelCards.Children.Clear()
        self.card_borders = {}
        
        if self.reader is None:
            sp = StackPanel()
            sp.Margin = Thickness(24)
            sp.HorizontalAlignment = HorizontalAlignment.Center
            sp.VerticalAlignment = VerticalAlignment.Center
            
            title = TextBlock()
            title.Text = "Welcome to BCF Reader"
            title.FontSize = 16
            title.FontWeight = FontWeights.Bold
            title.Foreground = self._brush(COLOR_TEXT)
            title.Margin = Thickness(0, 0, 0, 8)
            title.HorizontalAlignment = HorizontalAlignment.Center
            sp.Children.Add(title)
            
            desc = TextBlock()
            desc.Text = "To get started, click 'Load BCF' below to open a BCF file,\nor click the Globe icon on the left sidebar to extract it from the web."
            desc.FontSize = 13
            desc.Foreground = self._brush(COLOR_TEXT_MUTED)
            desc.TextWrapping = TextWrapping.Wrap
            desc.HorizontalAlignment = HorizontalAlignment.Center
            desc.Margin = Thickness(0, 0, 0, 16)
            sp.Children.Add(desc)
            
            self.panelCards.Children.Add(sp)
            self._clear_detail()
            return

        visible_count = 0
        search_txt = ""
        try:
            search_txt = self.txtSearch.Text.lower().strip()
        except Exception:
            pass

        for issue in self.issues:
            if self.active_filter == "RESOLVED":
                if not issue.resolved:
                    continue
            elif self.active_filter == "UNRESOLVED":
                if issue.resolved:
                    continue
            elif self.active_filter != "ALL" and issue.label != self.active_filter:
                continue

            if search_txt:
                t_match = search_txt in (issue.title or "").lower()
                d_match = search_txt in (issue.description or "").lower()
                g_match = search_txt in (issue.guid or "").lower()
                e_match = False
                if issue.element_id is not None:
                    e_match = search_txt in str(issue.element_id)
                if not (t_match or d_match or g_match or e_match):
                    continue

            card = self._build_card(issue)
            self.panelCards.Children.Add(card)
            visible_count += 1
        if visible_count == 0:
            msg = TextBlock()
            msg.Text = "No issues to display."
            msg.Foreground = self._brush(COLOR_TEXT_MUTED)
            msg.Margin = Thickness(20)
            msg.FontStyle = FontStyles.Italic
            self.panelCards.Children.Add(msg)
        self._clear_detail()
        self._update_resolved_count()

    def on_search_changed(self, sender, e):
        try:
            if self.txtSearch.Text:
                self.lblSearchPlaceholder.Visibility = Visibility.Collapsed
            else:
                self.lblSearchPlaceholder.Visibility = Visibility.Visible
        except Exception:
            pass
        self.populate_cards()

    def _update_resolved_count(self):
        try:
            n_resolved = sum(1 for i in self.issues if i.resolved)
            n_total = len(self.issues)
            base = self.lblSubtitle.Text.split(" | Resolved:")[0]
            base = base.split(" *unsaved")[0]
            if n_total > 0:
                self.lblSubtitle.Text = "{} | Resolved: {}/{}".format(
                    base, n_resolved, n_total)
            else:
                self.lblSubtitle.Text = base
            # Refresh stat cards
            try:
                self.txtResolved.Text = str(n_resolved)
                self.txtUnresolved.Text = str(n_total - n_resolved)
            except AttributeError:
                pass
            # Re-mark dirty if applicable
            if self._dirty:
                try:
                    self.lblSubtitle.Text = self.lblSubtitle.Text + " *unsaved changes*"
                except Exception:
                    pass
        except Exception:
            pass

    def _build_card(self, issue):
        outer = Border()
        outer.Width = 240
        outer.Height = 260
        outer.Margin = Thickness(6)
        outer.BorderBrush = self._brush(COLOR_CARD_BORDER)
        outer.BorderThickness = Thickness(1)
        outer.CornerRadius = CornerRadius(8)
        outer.Background = self._brush("#FFFFFF")
        outer.Cursor = Cursors.Hand
        outer.Tag = issue

        outer_grid = WPFGrid()
        col_accent = ColumnDefinition(); col_accent.Width = GridLength(5)
        col_body = ColumnDefinition(); col_body.Width = GridLength(1, GridUnitType.Star)
        outer_grid.ColumnDefinitions.Add(col_accent)
        outer_grid.ColumnDefinitions.Add(col_body)

        accent = Border()
        accent_color = COLOR_ADDED if issue.resolved else LABEL_COLORS.get(issue.label, COLOR_OTHER)
        accent.Background = self._brush(accent_color)
        accent.CornerRadius = CornerRadius(8, 0, 0, 8)
        WPFGrid.SetColumn(accent, 0)
        outer_grid.Children.Add(accent)

        body_border = Border()
        body_border.Background = self._brush("#FFFFFF")
        body_border.BorderThickness = Thickness(0)
        WPFGrid.SetColumn(body_border, 1)

        grid = WPFGrid()
        row1 = RowDefinition(); row1.Height = GridLength(140)
        row2 = RowDefinition(); row2.Height = GridLength(1, GridUnitType.Star)
        grid.RowDefinitions.Add(row1)
        grid.RowDefinitions.Add(row2)

        thumb_border = Border()
        thumb_border.Background = self._brush(COLOR_BG)
        thumb_border.BorderThickness = Thickness(0, 0, 0, 1)
        thumb_border.BorderBrush = self._brush(COLOR_CARD_BORDER)
        thumb_border.CornerRadius = CornerRadius(0, 8, 0, 0)
        WPFGrid.SetRow(thumb_border, 0)

        thumb_grid = WPFGrid()
        if issue.snapshot_bytes is not None:
            try:
                thumb = issue.build_thumbnail(220)
                if thumb is not None:
                    img = Image()
                    img.Source = thumb
                    img.Stretch = Stretch.UniformToFill
                    thumb_grid.Children.Add(img)
                else:
                    thumb_grid.Children.Add(self._placeholder_thumb())
            except Exception:
                thumb_grid.Children.Add(self._placeholder_thumb())
        else:
            thumb_grid.Children.Add(self._placeholder_thumb())

        # Badge: ● Label
        badge = Border()
        badge.Background = self._brush("#FFFFFF")
        badge.BorderBrush = self._brush(COLOR_CARD_BORDER)
        badge.BorderThickness = Thickness(1)
        badge.CornerRadius = CornerRadius(2)
        badge.Padding = Thickness(7, 2, 8, 2)
        badge.Margin = Thickness(6)
        badge.HorizontalAlignment = HorizontalAlignment.Left
        badge.VerticalAlignment = VerticalAlignment.Top
        badge_sp = StackPanel()
        badge_sp.Orientation = Orientation.Horizontal
        dot = TextBlock()
        dot.Text = u"\u25CF"
        dot.Foreground = self._brush(LABEL_COLORS.get(issue.label, COLOR_OTHER))
        dot.FontSize = 12
        dot.Margin = Thickness(0, 0, 4, 0)
        dot.VerticalAlignment = VerticalAlignment.Center
        badge_sp.Children.Add(dot)
        badge_tb = TextBlock()
        badge_tb.Text = issue.label.title() if issue.label != "OTHER" else "Other"
        badge_tb.Foreground = self._brush(COLOR_TEXT)
        badge_tb.FontSize = 10
        badge_tb.FontWeight = FontWeights.SemiBold
        badge_tb.VerticalAlignment = VerticalAlignment.Center
        badge_sp.Children.Add(badge_tb)
        badge.Child = badge_sp
        thumb_grid.Children.Add(badge)

        # Issue number
        num = Border()
        num.Background = self._brush(COLOR_HEADER_BG)
        num.CornerRadius = CornerRadius(2)
        num.Padding = Thickness(6, 2, 6, 2)
        num.Margin = Thickness(6)
        num.HorizontalAlignment = HorizontalAlignment.Right
        num.VerticalAlignment = VerticalAlignment.Top
        num_tb = TextBlock()
        num_tb.Text = "#" + str(issue.index)
        num_tb.Foreground = self._brush(COLOR_HEADER_FG)
        num_tb.FontSize = 10
        num_tb.FontWeight = FontWeights.Bold
        num.Child = num_tb
        thumb_grid.Children.Add(num)

        thumb_border.Child = thumb_grid
        grid.Children.Add(thumb_border)

        text_sp = StackPanel()
        text_sp.Margin = Thickness(8)
        WPFGrid.SetRow(text_sp, 1)

        title_tb = TextBlock()
        title_tb.Text = self._truncate(issue.title, 80)
        title_tb.FontWeight = FontWeights.SemiBold
        title_tb.Foreground = self._brush(COLOR_TEXT)
        title_tb.FontSize = 12
        title_tb.TextWrapping = TextWrapping.Wrap
        title_tb.MaxHeight = 54
        text_sp.Children.Add(title_tb)

        if issue.element_id is not None:
            eid_tb = TextBlock()
            eid_tb.Text = "Element ID: " + str(issue.element_id)
            eid_tb.Foreground = self._brush(COLOR_TEXT_MUTED)
            eid_tb.FontSize = 10
            eid_tb.Margin = Thickness(0, 4, 0, 0)
            text_sp.Children.Add(eid_tb)

        if issue.position is not None:
            px = str(round(issue.position[0], 2))
            py = str(round(issue.position[1], 2))
            pz = str(round(issue.position[2], 2))
            pos_tb = TextBlock()
            pos_tb.Text = "Pos: ({}, {}, {})".format(px, py, pz)
            pos_tb.Foreground = self._brush(COLOR_TEXT_MUTED)
            pos_tb.FontSize = 10
            text_sp.Children.Add(pos_tb)

        grid.Children.Add(text_sp)
        body_border.Child = grid
        outer_grid.Children.Add(body_border)
        outer.Child = outer_grid

        outer.MouseLeftButtonDown += self._make_card_click_handler(issue, outer)
        self.card_borders[issue.guid] = outer
        return outer

    def _placeholder_thumb(self):
        tb = TextBlock()
        tb.Text = "[ no image ]"
        tb.FontSize = 12
        tb.Foreground = self._brush(COLOR_TEXT_MUTED)
        tb.HorizontalAlignment = HorizontalAlignment.Center
        tb.VerticalAlignment = VerticalAlignment.Center
        tb.Opacity = 0.6
        return tb

    def _make_card_click_handler(self, issue, border):
        def handler(sender, e):
            try:
                e.Handled = True
                self._select_issue(issue, border)
                if e.ClickCount >= 2:
                    self.zoom_to_issue(issue)
            except Exception as ex:
                try:
                    self.set_status("Card click error: " + str(ex))
                except Exception:
                    pass
        return handler

    def _select_issue(self, issue, border):
        try:
            for b in self.card_borders.values():
                try:
                    b.BorderBrush = self._brush(self._COLOR_CARD_BORDER)
                    b.BorderThickness = Thickness(1)
                except Exception:
                    pass
            border.BorderBrush = self._brush(COLOR_CARD_SEL)
            border.BorderThickness = Thickness(2)
            self.selected_issue = issue
            self._populate_detail(issue)
        except Exception as ex:
            try:
                self.set_status("Select error: " + str(ex))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------
    def _clear_detail(self):
        self.panelDetailHeader.Children.Clear()
        self.panelDetailBody.Children.Clear()
        tb = TextBlock()
        tb.Text = "Select an issue to view details."
        tb.Foreground = self._brush(COLOR_TEXT_MUTED)
        tb.FontStyle = FontStyles.Italic
        tb.Margin = Thickness(14, 20, 14, 0)
        self.panelDetailBody.Children.Add(tb)
        self.selected_issue = None

    def _populate_detail(self, issue):
        self.panelDetailHeader.Children.Clear()
        self.panelDetailBody.Children.Clear()

        header_sp = StackPanel()
        header_sp.Orientation = Orientation.Horizontal
        header_sp.Margin = Thickness(0, 0, 0, 8)

        badge = Border()
        badge.Background = self._brush(LABEL_COLORS.get(issue.label, COLOR_OTHER))
        badge.CornerRadius = CornerRadius(2)
        badge.Padding = Thickness(8, 3, 8, 3)
        badge.Margin = Thickness(0, 0, 8, 0)
        btb = TextBlock()
        btb.Text = issue.label
        btb.Foreground = self._brush("#FFFFFF")
        btb.FontSize = 11
        btb.FontWeight = FontWeights.Bold
        badge.Child = btb
        header_sp.Children.Add(badge)

        idx_tb = TextBlock()
        idx_tb.Text = "Issue #" + str(issue.index)
        idx_tb.FontWeight = FontWeights.Bold
        idx_tb.FontSize = 13
        idx_tb.Foreground = self._brush(COLOR_HEADER_BG)
        idx_tb.VerticalAlignment = VerticalAlignment.Center
        header_sp.Children.Add(idx_tb)
        self.panelDetailHeader.Children.Add(header_sp)

        title_tb = TextBlock()
        title_tb.Text = issue.title or "(no title)"
        title_tb.TextWrapping = TextWrapping.Wrap
        title_tb.FontWeight = FontWeights.SemiBold
        title_tb.FontSize = 14
        title_tb.Foreground = self._brush(COLOR_TEXT)
        self.panelDetailHeader.Children.Add(title_tb)

        body = self.panelDetailBody

        if issue.snapshot_bytes is not None:
            try:
                img_src = issue.build_snapshot_image()
                if img_src is not None:
                    img = Image()
                    img.Source = img_src
                    img.Stretch = Stretch.Uniform
                    img.MaxHeight = 220
                    img.Margin = Thickness(0, 4, 0, 10)
                    body.Children.Add(img)
            except Exception:
                pass

        self._add_detail_row(body, "Element ID",
            str(issue.element_id) if issue.element_id is not None else "(not specified)")
        if issue.ifc_guid:
            self._add_detail_row(body, "IFC GUID", issue.ifc_guid)
        if issue.position is not None:
            px = str(round(issue.position[0], 3))
            py = str(round(issue.position[1], 3))
            pz = str(round(issue.position[2], 3))
            self._add_detail_row(body, "Position", "({}, {}, {})".format(px, py, pz))
        if issue.creation_date:
            self._add_detail_row(body, "Created", issue.creation_date)
        if issue.camera_type:
            self._add_detail_row(body, "Camera", issue.camera_type)
        if issue.clipping_planes:
            self._add_detail_row(body, "Clipping Planes", str(len(issue.clipping_planes)))
        if issue.components:
            self._add_detail_row(body, "Components", str(len(issue.components)))

        desc_label = TextBlock()
        desc_label.Text = "Description"
        desc_label.FontWeight = FontWeights.Bold
        desc_label.Foreground = self._brush(COLOR_HEADER_BG)
        desc_label.Margin = Thickness(0, 12, 0, 4)
        body.Children.Add(desc_label)

        desc_border = Border()
        desc_border.Background = self._brush(COLOR_BG)
        desc_border.BorderBrush = self._brush(COLOR_CARD_BORDER)
        desc_border.BorderThickness = Thickness(1)
        desc_border.CornerRadius = CornerRadius(4)
        desc_border.Padding = Thickness(10)
        desc_tb = TextBlock()
        desc_tb.Text = issue.description or "(no description)"
        desc_tb.TextWrapping = TextWrapping.Wrap
        desc_tb.Foreground = self._brush(COLOR_TEXT)
        desc_tb.FontSize = 12
        desc_border.Child = desc_tb
        body.Children.Add(desc_border)

        # Action buttons
        actions_sp = StackPanel()
        actions_sp.Orientation = Orientation.Horizontal
        actions_sp.Margin = Thickness(0, 12, 0, 0)

        def _make_action_btn(text, handler, primary=False):
            btn = Button()
            btn.Content = text
            btn.Padding = Thickness(10, 5, 10, 5)
            btn.Margin = Thickness(0, 0, 6, 0)
            if primary:
                btn.Background = self._brush(COLOR_HEADER_FG)
                btn.Foreground = self._brush(COLOR_HEADER_BG)
                btn.FontWeight = FontWeights.SemiBold
            else:
                btn.Background = self._brush("#FFFFFF")
                btn.Foreground = self._brush(COLOR_TEXT)
            btn.BorderBrush = self._brush(COLOR_CARD_BORDER)
            btn.BorderThickness = Thickness(1)
            btn.Cursor = Cursors.Hand
            btn.FontSize = 11
            btn.Click += handler
            return btn

        actions_sp.Children.Add(_make_action_btn("Zoom to Element",
            lambda s, e: self.zoom_to_issue(issue), primary=True))

        if issue.element_id is not None:
            actions_sp.Children.Add(_make_action_btn("Select",
                lambda s, e: self.select_only(issue)))

        if issue.clipping_planes or issue.element_id is not None:
            actions_sp.Children.Add(_make_action_btn("Section Box",
                lambda s, e: self.apply_section_box(issue)))

        if issue.element_id is not None:
            actions_sp.Children.Add(_make_action_btn("Isolate",
                lambda s, e: self.isolate_element(issue)))

        body.Children.Add(actions_sp)

        # Resolved checkbox row
        from System.Windows.Controls import CheckBox
        resolved_sp = StackPanel()
        resolved_sp.Orientation = Orientation.Horizontal
        resolved_sp.Margin = Thickness(0, 12, 0, 0)
        chk = CheckBox()
        chk.Content = "Mark as Resolved"
        chk.FontSize = 12
        chk.FontWeight = FontWeights.SemiBold
        chk.Foreground = self._brush(self._COLOR_ADDED if issue.resolved else self._COLOR_TEXT)
        chk.IsChecked = System.Nullable[System.Boolean](bool(issue.resolved))
        chk.VerticalAlignment = VerticalAlignment.Center
        def _on_resolved_toggle(s, e):
            try:
                issue.resolved = bool(chk.IsChecked)
                chk.Foreground = self._brush(self._COLOR_ADDED if issue.resolved else self._COLOR_TEXT)
                # Update card accent (show green stripe when resolved)
                card = self.card_borders.get(issue.guid)
                if card is not None:
                    try:
                        grid = card.Child
                        if grid is not None and grid.Children.Count > 0:
                            accent = grid.Children[0]
                            if issue.resolved:
                                accent.Background = self._brush(self._COLOR_ADDED)
                            else:
                                accent.Background = self._brush(
                                    self._LABEL_COLORS.get(issue.label, self._COLOR_OTHER))
                    except Exception:
                        pass
                self._update_resolved_count()
                self._mark_dirty()
            except Exception:
                pass
        chk.Checked += _on_resolved_toggle
        chk.Unchecked += _on_resolved_toggle
        resolved_sp.Children.Add(chk)
        body.Children.Add(resolved_sp)

        # Comments section
        self._build_comments_section(issue, body)

    def _build_comments_section(self, issue, body):
        from System.Windows.Controls import TextBox
        # Header
        header = TextBlock()
        header.Text = "Comments ({})".format(len(issue.comments))
        header.FontWeight = FontWeights.Bold
        header.Foreground = self._brush(self._COLOR_HEADER_BG if hasattr(self, '_COLOR_HEADER_BG') else COLOR_HEADER_BG)
        header.Margin = Thickness(0, 16, 0, 6)
        header.FontSize = 12
        body.Children.Add(header)

        # List existing comments
        for c in issue.comments:
            body.Children.Add(self._build_comment_card(c))

        # Add comment input
        add_panel = Border()
        add_panel.Background = self._brush(COLOR_BG)
        add_panel.BorderBrush = self._brush(COLOR_CARD_BORDER)
        add_panel.BorderThickness = Thickness(1)
        add_panel.CornerRadius = CornerRadius(4)
        add_panel.Padding = Thickness(8)
        add_panel.Margin = Thickness(0, 8, 0, 0)

        add_sp = StackPanel()
        # Author hint
        author = _get_revit_author()
        author_lbl = TextBlock()
        author_lbl.Text = "Posting as: " + author
        author_lbl.Foreground = self._brush(COLOR_TEXT_MUTED)
        author_lbl.FontSize = 10
        author_lbl.FontStyle = FontStyles.Italic
        author_lbl.Margin = Thickness(0, 0, 0, 4)
        add_sp.Children.Add(author_lbl)

        # Textbox
        tb = TextBox()
        tb.AcceptsReturn = True
        tb.TextWrapping = TextWrapping.Wrap
        tb.MinHeight = 50
        tb.MaxHeight = 100
        tb.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        tb.FontSize = 11
        tb.BorderBrush = self._brush(COLOR_CARD_BORDER)
        tb.BorderThickness = Thickness(1)
        tb.Padding = Thickness(6)
        add_sp.Children.Add(tb)

        # Add button
        btn_sp = StackPanel()
        btn_sp.Orientation = Orientation.Horizontal
        btn_sp.Margin = Thickness(0, 6, 0, 0)
        btn_sp.HorizontalAlignment = HorizontalAlignment.Right

        btn_add = Button()
        btn_add.Content = "Add Comment"
        btn_add.Padding = Thickness(10, 4, 10, 4)
        btn_add.Background = self._brush(COLOR_HEADER_FG)
        btn_add.Foreground = self._brush(COLOR_HEADER_BG)
        btn_add.BorderBrush = self._brush(COLOR_CARD_BORDER)
        btn_add.BorderThickness = Thickness(1)
        btn_add.FontWeight = FontWeights.SemiBold
        btn_add.FontSize = 11
        btn_add.Cursor = Cursors.Hand

        def on_add_comment(s, e):
            try:
                txt = (tb.Text or "").strip()
                if not txt:
                    return
                issue.add_comment(txt, _get_revit_author())
                self._mark_dirty()
                # Repopulate detail to show new comment
                self._populate_detail(issue)
            except Exception as ex:
                self.set_status("Add comment error: " + str(ex))
        btn_add.Click += on_add_comment

        btn_sp.Children.Add(btn_add)
        add_sp.Children.Add(btn_sp)
        add_panel.Child = add_sp
        body.Children.Add(add_panel)

    def _build_comment_card(self, comment):
        """Render a single existing comment as a card."""
        b = Border()
        b.Background = self._brush("#FFFFFF")
        b.BorderBrush = self._brush(COLOR_CARD_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = CornerRadius(4)
        b.Padding = Thickness(8)
        b.Margin = Thickness(0, 0, 0, 6)

        sp = StackPanel()

        # Author + date row
        head = StackPanel()
        head.Orientation = Orientation.Horizontal
        head.Margin = Thickness(0, 0, 0, 4)

        author_tb = TextBlock()
        author_tb.Text = comment.author or "(unknown)"
        author_tb.FontWeight = FontWeights.SemiBold
        author_tb.Foreground = self._brush(COLOR_HEADER_BG)
        author_tb.FontSize = 11
        head.Children.Add(author_tb)

        # Date
        date_str = self._format_comment_date(comment.date)
        date_tb = TextBlock()
        date_tb.Text = "  " + date_str
        date_tb.Foreground = self._brush(COLOR_TEXT_MUTED)
        date_tb.FontSize = 10
        date_tb.VerticalAlignment = VerticalAlignment.Center
        head.Children.Add(date_tb)

        # NEW badge for unsaved comments
        if comment.is_new:
            new_badge = Border()
            new_badge.Background = self._brush(COLOR_ADDED)
            new_badge.CornerRadius = CornerRadius(2)
            new_badge.Padding = Thickness(4, 1, 4, 1)
            new_badge.Margin = Thickness(6, 0, 0, 0)
            new_badge.VerticalAlignment = VerticalAlignment.Center
            nb_tb = TextBlock()
            nb_tb.Text = "NEW"
            nb_tb.Foreground = self._brush("#FFFFFF")
            nb_tb.FontSize = 8
            nb_tb.FontWeight = FontWeights.Bold
            new_badge.Child = nb_tb
            head.Children.Add(new_badge)

        sp.Children.Add(head)

        # Comment text
        text_tb = TextBlock()
        text_tb.Text = comment.text or ""
        text_tb.TextWrapping = TextWrapping.Wrap
        text_tb.Foreground = self._brush(COLOR_TEXT)
        text_tb.FontSize = 11
        sp.Children.Add(text_tb)

        b.Child = sp
        return b

    def _format_comment_date(self, iso_date):
        """Convert ISO 8601 to friendly format."""
        if not iso_date:
            return ""
        try:
            # Truncate to YYYY-MM-DD HH:MM
            return iso_date.replace("T", " ")[:16]
        except Exception:
            return iso_date

    def _mark_dirty(self):
        """Mark BCF as having unsaved changes."""
        self._dirty = True
        try:
            base = self.lblSubtitle.Text.split(" *")[0]
            self.lblSubtitle.Text = base + " *unsaved changes*"
        except Exception:
            pass

    def _add_detail_row(self, parent, label, value):
        sp = StackPanel()
        sp.Orientation = Orientation.Horizontal
        sp.Margin = Thickness(0, 2, 0, 2)
        l = TextBlock()
        l.Text = label + ": "
        l.FontWeight = FontWeights.SemiBold
        l.Foreground = self._brush(COLOR_HEADER_BG)
        l.Width = 110
        l.FontSize = 12
        sp.Children.Add(l)
        v = TextBlock()
        v.Text = value
        v.Foreground = self._brush(COLOR_TEXT)
        v.FontSize = 12
        v.TextWrapping = TextWrapping.Wrap
        sp.Children.Add(v)
        parent.Children.Add(sp)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------
    def set_filter(self, flt, repopulate=True):
        self.active_filter = flt
        buttons = {
            "ALL": self.btnAll,
            "REMOVED": self.btnRemoved,
            "ADDED": self.btnAdded,
            "MODIFIED": self.btnModified,
        }
        # Optional buttons
        for attr, key in [("btnResolved", "RESOLVED"), ("btnUnresolved", "UNRESOLVED")]:
            try:
                buttons[key] = getattr(self, attr)
            except AttributeError:
                pass
        for key, btn in buttons.items():
            if key == flt:
                btn.Background = self._brush(COLOR_HEADER_FG)
                btn.Foreground = self._brush(COLOR_HEADER_BG)
                btn.FontWeight = FontWeights.SemiBold
            else:
                btn.Background = self._brush("#FFFFFF")
                btn.Foreground = self._brush(COLOR_TEXT)
                btn.FontWeight = FontWeights.Normal
        if repopulate and self.issues:
            self.populate_cards()

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def on_open_click(self, sender, e):
        try:
            self.prompt_open()
        except Exception as ex:
            TaskDialog.Show("BCF Reader", "Open failed:\n" + str(ex))

    def on_reload_click(self, sender, e):
        if self.reader and self.reader.filepath:
            self.load_bcf(self.reader.filepath)
        else:
            from System.Windows import MessageBox
            MessageBox.Show("No BCF file is currently loaded to reload.", "BCF Reader")

    def on_weblink_click(self, sender, e):
        try:
            import System.Diagnostics
            psi = System.Diagnostics.ProcessStartInfo("https://ifc.t3lab.space/")
            psi.UseShellExecute = True
            System.Diagnostics.Process.Start(psi)
            self.set_status("Opening web extraction link...")
        except Exception as ex:
            TaskDialog.Show("BCF Reader", "Failed to open web link:\n" + str(ex))

    def on_zoom_click(self, sender, e):
        if self.selected_issue is None:
            TaskDialog.Show("BCF Reader", "Please select an issue first.")
            return
        self.zoom_to_issue(self.selected_issue)
    def on_export_click(self, sender, e):
        if not self.issues:
            TaskDialog.Show("BCF Reader", "No issues to export.")
            return
        try:
            self.export_summary_csv()
        except Exception as ex:
            TaskDialog.Show("BCF Reader", "Export failed:\n" + str(ex))

    # ------------------------------------------------------------------
    # Dispatch helper
    # ------------------------------------------------------------------
    def _dispatch(self, action):
        """Queue a callable for Revit API thread via ExternalEvent."""
        try:
            self._action_handler.set_action(action)
            self._action_event.Raise()
        except Exception as ex:
            TaskDialog.Show("BCF Reader", "Dispatch failed:\n" + str(ex))

    # ------------------------------------------------------------------
    # Revit operations (via ExternalEvent)
    # ------------------------------------------------------------------
    def zoom_to_issue(self, issue):
        self._dispatch(lambda: self._zoom_to_issue_impl(issue))

    def select_only(self, issue):
        """Select element without zooming."""
        self._dispatch(lambda: self._select_only_impl(issue))

    def apply_section_box(self, issue):
        self._dispatch(lambda: self._apply_section_box_impl(issue))

    def isolate_element(self, issue):
        self._dispatch(lambda: self._isolate_element_impl(issue))

    def _select_only_impl(self, issue):
        ids_to_select = self._collect_element_ids(issue)
        if not ids_to_select:
            TaskDialog.Show("BCF Reader", "No Element ID for this issue.")
            return

        host_ids = []
        link_refs = []   # list of (link_inst, link_elem)
        not_found = []
        for eid_int in ids_to_select:
            ok, elem = self._find_in_host(eid_int)
            if ok:
                host_ids.append(elem.Id)
                continue
            link_inst, link_elem = self._find_in_links(eid_int)
            if link_elem is not None:
                link_refs.append((link_inst, link_elem))
            else:
                not_found.append(eid_int)

        # Strategy: if all in host -> SetElementIds; if mixed -> SetReferences (host + link refs)
        try:
            if link_refs and not host_ids:
                refs = List[DB.Reference]()
                for li, le in link_refs:
                    try:
                        refs.Add(DB.Reference(le).CreateLinkReference(li))
                    except Exception:
                        pass
                if refs.Count > 0:
                    uidoc.Selection.SetReferences(refs)
            elif link_refs and host_ids:
                # Mixed: try References API which supports both
                refs = List[DB.Reference]()
                for hid in host_ids:
                    try:
                        el = doc.GetElement(hid)
                        if el is not None:
                            refs.Add(DB.Reference(el))
                    except Exception:
                        pass
                for li, le in link_refs:
                    try:
                        refs.Add(DB.Reference(le).CreateLinkReference(li))
                    except Exception:
                        pass
                try:
                    uidoc.Selection.SetReferences(refs)
                except Exception:
                    # Fallback: just select host ids
                    eids = List[ElementId]()
                    for h in host_ids:
                        eids.Add(h)
                    uidoc.Selection.SetElementIds(eids)
            else:
                # Pure host
                eids = List[ElementId]()
                for h in host_ids:
                    eids.Add(h)
                uidoc.Selection.SetElementIds(eids)
        except Exception as ex:
            TaskDialog.Show("BCF Reader", "Selection failed:\n" + str(ex))
            return

        msg = "Selected {} element(s)".format(len(host_ids) + len(link_refs))
        if not_found:
            msg += " ({} not found: {})".format(
                len(not_found), ", ".join(str(x) for x in not_found[:5]))
        self.set_status(msg)

    def _collect_element_ids(self, issue):
        """Return list of integer element IDs to operate on (clash = 2+)."""
        if issue.element_ids:
            return list(issue.element_ids)
        if issue.element_id is not None:
            return [int(issue.element_id)]
        return []

    def _zoom_to_issue_impl(self, issue):
        if issue.element_id is None and issue.cam_viewpoint is None and issue.position is None:
            TaskDialog.Show("BCF Reader",
                "This issue has no Element ID, position, or camera viewpoint.")
            return

        eid_int = int(issue.element_id) if issue.element_id is not None else None

        found_host_elem = None
        found_link_inst = None
        found_linked_elem = None
        if eid_int is not None:
            ok_host, host_elem = self._find_in_host(eid_int)
            if ok_host:
                found_host_elem = host_elem
            else:
                link_inst, link_elem = self._find_in_links(eid_int)
                if link_elem is not None:
                    found_link_inst = link_inst
                    found_linked_elem = link_elem

        if found_host_elem is not None:
            self._select_in_host(found_host_elem)
            self.set_status("Selected element {} in active model.".format(eid_int))
        elif found_linked_elem is not None:
            self._select_link_element(found_link_inst, found_linked_elem)
            self.set_status("Element {} found in linked model '{}'.".format(
                eid_int, found_link_inst.Name if found_link_inst else "?"))
        else:
            if eid_int is not None:
                self.set_status("Element {} not found - using BCF viewpoint.".format(eid_int))
            if issue.position is None and issue.cam_viewpoint is None:
                TaskDialog.Show("BCF Reader",
                    "Element ID {} not found and no viewpoint stored.".format(eid_int))
                return

        self._apply_zoom_from_issue(issue, found_host_elem, found_link_inst, found_linked_elem)

    def _apply_zoom_from_issue(self, issue, host_elem, link_inst, linked_elem):
        """Zoom active view. For REMOVED issues, host_elem and linked_elem will
        be None - we must use issue.position with smart unit detection."""
        try:
            uiview = None
            for v in uidoc.GetOpenUIViews():
                if v.ViewId == doc.ActiveView.Id:
                    uiview = v
                    break
            if uiview is None:
                return

            bbox = None
            # Case 1: element found -> use its bbox (most accurate)
            if host_elem is not None:
                try:
                    bb = host_elem.get_BoundingBox(doc.ActiveView)
                    if bb is None:
                        bb = host_elem.get_BoundingBox(None)
                    if bb is not None:
                        bbox = (bb.Min, bb.Max)
                except Exception:
                    pass
            elif linked_elem is not None and link_inst is not None:
                try:
                    bb = linked_elem.get_BoundingBox(None)
                    if bb is not None:
                        xform = link_inst.GetTotalTransform()
                        tmin = xform.OfPoint(bb.Min)
                        tmax = xform.OfPoint(bb.Max)
                        mn = XYZ(min(tmin.X, tmax.X), min(tmin.Y, tmax.Y), min(tmin.Z, tmax.Z))
                        mx = XYZ(max(tmin.X, tmax.X), max(tmin.Y, tmax.Y), max(tmin.Z, tmax.Z))
                        bbox = (mn, mx)
                except Exception:
                    pass

            # Case 2: no element (e.g. REMOVED) -> use position from BCF
            # Smart unit detection: try both meters and feet, pick one inside model extent
            if bbox is None:
                pos = issue.position or issue.cam_viewpoint
                if pos is not None:
                    best_pos = self._detect_position_unit(pos)
                    pad = 10.0
                    bbox = (XYZ(best_pos[0] - pad, best_pos[1] - pad, best_pos[2] - pad),
                            XYZ(best_pos[0] + pad, best_pos[1] + pad, best_pos[2] + pad))

            if bbox is not None:
                uiview.ZoomAndCenterRectangle(bbox[0], bbox[1])
        except Exception:
            pass

    def _detect_position_unit(self, pos):
        """Returns (x,y,z) in feet. Auto-detects whether BCF position is in
        meters or feet by comparing with project extent."""
        # Get project extent from any element (cheap sample)
        try:
            coll = FilteredElementCollector(doc).WhereElementIsNotElementType()
            xs = []; ys = []; zs = []
            count = 0
            for el in coll:
                if count > 50:
                    break
                try:
                    bb = el.get_BoundingBox(None)
                    if bb is not None:
                        xs.append(bb.Min.X); xs.append(bb.Max.X)
                        ys.append(bb.Min.Y); ys.append(bb.Max.Y)
                        zs.append(bb.Min.Z); zs.append(bb.Max.Z)
                        count += 1
                except Exception:
                    pass
            if len(xs) > 1:
                proj_min = (min(xs), min(ys), min(zs))
                proj_max = (max(xs), max(ys), max(zs))
                # Try meters
                pm = (pos[0]*METERS_TO_FEET, pos[1]*METERS_TO_FEET, pos[2]*METERS_TO_FEET)
                # Try feet
                pf = (pos[0], pos[1], pos[2])
                # Pick whichever is closer to project center
                cx = (proj_min[0]+proj_max[0])*0.5
                cy = (proj_min[1]+proj_max[1])*0.5
                cz = (proj_min[2]+proj_max[2])*0.5
                dm = (pm[0]-cx)**2 + (pm[1]-cy)**2 + (pm[2]-cz)**2
                df = (pf[0]-cx)**2 + (pf[1]-cy)**2 + (pf[2]-cz)**2
                if dm < df:
                    return pm
                return pf
        except Exception:
            pass
        # Default: assume meters per BCF spec
        return (pos[0]*METERS_TO_FEET, pos[1]*METERS_TO_FEET, pos[2]*METERS_TO_FEET)

    def _find_in_host(self, eid_int):
        try:
            el = doc.GetElement(self._make_eid(eid_int))
            if el is not None:
                return True, el
        except Exception:
            pass
        return False, None

    def _find_in_links(self, eid_int):
        collector = FilteredElementCollector(doc).OfClass(RevitLinkInstance)
        for link_inst in collector:
            try:
                link_doc = link_inst.GetLinkDocument()
            except Exception:
                link_doc = None
            if link_doc is None:
                continue
            try:
                elem = link_doc.GetElement(self._make_eid(eid_int))
                if elem is not None:
                    return link_inst, elem
            except Exception:
                continue
        return None, None

    def _select_in_host(self, element):
        tx = Transaction(doc, "DQT - BCF Select Element")
        try:
            tx.Start()
            opts = tx.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(WarningSwallower())
            tx.SetFailureHandlingOptions(opts)
            ids = List[ElementId]()
            ids.Add(element.Id)
            uidoc.Selection.SetElementIds(ids)
            tx.Commit()
        except Exception:
            if tx.HasStarted() and not tx.HasEnded():
                tx.RollBack()
        try:
            uidoc.ShowElements(element.Id)
        except Exception:
            pass

    def _select_link_element(self, link_inst, linked_elem):
        try:
            ref = DB.Reference(linked_elem).CreateLinkReference(link_inst)
            ids = List[DB.Reference]()
            ids.Add(ref)
            try:
                uidoc.Selection.SetReferences(ids)
            except Exception:
                host_ids = List[ElementId]()
                host_ids.Add(link_inst.Id)
                uidoc.Selection.SetElementIds(host_ids)
        except Exception:
            host_ids = List[ElementId]()
            host_ids.Add(link_inst.Id)
            uidoc.Selection.SetElementIds(host_ids)

    def _get_element_world_bbox(self, eid_int):
        """Return ((minX, minY, minZ), (maxX, maxY, maxZ)) in world coords,
        handling both host and linked elements. None if not found."""
        # Try host
        try:
            el = doc.GetElement(self._make_eid(eid_int))
            if el is not None:
                bb = el.get_BoundingBox(None)
                if bb is not None:
                    return ((bb.Min.X, bb.Min.Y, bb.Min.Z),
                            (bb.Max.X, bb.Max.Y, bb.Max.Z))
        except Exception:
            pass
        # Try linked
        try:
            collector = FilteredElementCollector(doc).OfClass(RevitLinkInstance)
            for link_inst in collector:
                ldoc = link_inst.GetLinkDocument()
                if ldoc is None:
                    continue
                el = ldoc.GetElement(self._make_eid(eid_int))
                if el is None:
                    continue
                bb = el.get_BoundingBox(None)
                if bb is None:
                    continue
                xform = link_inst.GetTotalTransform()
                # 8 corners of source bbox transformed
                corners = [
                    XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z),
                    XYZ(bb.Max.X, bb.Min.Y, bb.Min.Z),
                    XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z),
                    XYZ(bb.Max.X, bb.Max.Y, bb.Min.Z),
                    XYZ(bb.Min.X, bb.Min.Y, bb.Max.Z),
                    XYZ(bb.Max.X, bb.Min.Y, bb.Max.Z),
                    XYZ(bb.Min.X, bb.Max.Y, bb.Max.Z),
                    XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z),
                ]
                xs = []; ys = []; zs = []
                for c in corners:
                    p = xform.OfPoint(c)
                    xs.append(p.X); ys.append(p.Y); zs.append(p.Z)
                return ((min(xs), min(ys), min(zs)),
                        (max(xs), max(ys), max(zs)))
        except Exception:
            pass
        return None

    def _intersect_bboxes(self, bbox_list):
        """Compute axis-aligned intersection of multiple bboxes.
        Returns ((minX,minY,minZ),(maxX,maxY,maxZ)) or None if no overlap."""
        if not bbox_list:
            return None
        mn_x = max(b[0][0] for b in bbox_list)
        mn_y = max(b[0][1] for b in bbox_list)
        mn_z = max(b[0][2] for b in bbox_list)
        mx_x = min(b[1][0] for b in bbox_list)
        mx_y = min(b[1][1] for b in bbox_list)
        mx_z = min(b[1][2] for b in bbox_list)
        # Check if intersection is valid (all maxes >= mins)
        if mx_x < mn_x or mx_y < mn_y or mx_z < mn_z:
            return None
        return ((mn_x, mn_y, mn_z), (mx_x, mx_y, mx_z))

    def _union_bboxes(self, bbox_list):
        if not bbox_list:
            return None
        mn_x = min(b[0][0] for b in bbox_list)
        mn_y = min(b[0][1] for b in bbox_list)
        mn_z = min(b[0][2] for b in bbox_list)
        mx_x = max(b[1][0] for b in bbox_list)
        mx_y = max(b[1][1] for b in bbox_list)
        mx_z = max(b[1][2] for b in bbox_list)
        return ((mn_x, mn_y, mn_z), (mx_x, mx_y, mx_z))

    def _apply_section_box_impl(self, issue):
        if not isinstance(doc.ActiveView, DB.View3D):
            TaskDialog.Show("BCF Reader",
                "Section Box requires an active 3D view.")
            return

        bbox = None
        source = ""

        # Priority 1: collect bboxes of ALL clash elements, then INTERSECT them
        # (clash region = where elements overlap)
        ids_to_use = self._collect_element_ids(issue)
        if ids_to_use:
            element_bboxes = []  # list of (min_xyz_tuple, max_xyz_tuple)
            for eid_int in ids_to_use:
                bb = self._get_element_world_bbox(eid_int)
                if bb is not None:
                    element_bboxes.append(bb)

            if len(element_bboxes) >= 2:
                # INTERSECTION of multiple bboxes (clash zone)
                inter = self._intersect_bboxes(element_bboxes)
                if inter is not None:
                    pad = 2.0  # small padding around clash zone
                    bbox = BoundingBoxXYZ()
                    bbox.Min = XYZ(inter[0][0] - pad, inter[0][1] - pad, inter[0][2] - pad)
                    bbox.Max = XYZ(inter[1][0] + pad, inter[1][1] + pad, inter[1][2] + pad)
                    source = "clash intersection ({} elements)".format(len(element_bboxes))
                else:
                    # No overlap -> union with smaller padding
                    union = self._union_bboxes(element_bboxes)
                    pad = 3.0
                    bbox = BoundingBoxXYZ()
                    bbox.Min = XYZ(union[0][0] - pad, union[0][1] - pad, union[0][2] - pad)
                    bbox.Max = XYZ(union[1][0] + pad, union[1][1] + pad, union[1][2] + pad)
                    source = "elements union (no overlap)"
            elif len(element_bboxes) == 1:
                pad = 5.0
                bb = element_bboxes[0]
                bbox = BoundingBoxXYZ()
                bbox.Min = XYZ(bb[0][0] - pad, bb[0][1] - pad, bb[0][2] - pad)
                bbox.Max = XYZ(bb[1][0] + pad, bb[1][1] + pad, bb[1][2] + pad)
                source = "single element bbox"

        # Priority 2: clipping planes
        if bbox is None:
            bbox = self._build_bbox_from_clipping_planes(issue)
            if bbox is not None:
                source = "clipping planes"

        # Priority 3: position cube
        if bbox is None:
            pos = issue.position or issue.cam_viewpoint
            if pos is None:
                TaskDialog.Show("BCF Reader",
                    "Cannot build section box: no element, clipping planes, or position.")
                return
            cx = pos[0] * METERS_TO_FEET
            cy = pos[1] * METERS_TO_FEET
            cz = pos[2] * METERS_TO_FEET
            pad = 15.0
            bbox = BoundingBoxXYZ()
            bbox.Min = XYZ(cx - pad, cy - pad, cz - pad)
            bbox.Max = XYZ(cx + pad, cy + pad, cz + pad)
            source = "position cube"

        tx = Transaction(doc, "DQT - BCF Apply Section Box")
        try:
            tx.Start()
            opts = tx.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(WarningSwallower())
            tx.SetFailureHandlingOptions(opts)
            view3d = doc.ActiveView
            view3d.SetSectionBox(bbox)
            view3d.IsSectionBoxActive = True
            tx.Commit()
            try:
                uiview = None
                for v in uidoc.GetOpenUIViews():
                    if v.ViewId == doc.ActiveView.Id:
                        uiview = v
                        break
                if uiview is not None:
                    uiview.ZoomAndCenterRectangle(bbox.Min, bbox.Max)
            except Exception:
                pass
            self.set_status("Applied section box for #{} ({}).".format(issue.index, source))
        except Exception as ex:
            if tx.HasStarted() and not tx.HasEnded():
                tx.RollBack()
            TaskDialog.Show("BCF Reader", "Failed to apply section box:\n" + str(ex))

    def _build_bbox_from_clipping_planes(self, issue):
        if not issue.clipping_planes:
            return None
        xs_raw = []; ys_raw = []; zs_raw = []
        for (loc, _) in issue.clipping_planes:
            xs_raw.append(loc[0]); ys_raw.append(loc[1]); zs_raw.append(loc[2])
        if len(xs_raw) < 2:
            return None

        raw_min = (min(xs_raw), min(ys_raw), min(zs_raw))
        raw_max = (max(xs_raw), max(ys_raw), max(zs_raw))

        # Get view extent
        view_bbox = None
        try:
            av = doc.ActiveView
            if isinstance(av, DB.View3D) and av.IsSectionBoxActive:
                view_bbox = av.GetSectionBox()
            else:
                coll = FilteredElementCollector(doc, av.Id).WhereElementIsNotElementType()
                xs_e = []; ys_e = []; zs_e = []
                count = 0
                for el in coll:
                    if count > 200:
                        break
                    try:
                        bb = el.get_BoundingBox(av)
                        if bb is not None:
                            xs_e.append(bb.Min.X); xs_e.append(bb.Max.X)
                            ys_e.append(bb.Min.Y); ys_e.append(bb.Max.Y)
                            zs_e.append(bb.Min.Z); zs_e.append(bb.Max.Z)
                            count += 1
                    except Exception:
                        pass
                if len(xs_e) > 1:
                    view_bbox = BoundingBoxXYZ()
                    view_bbox.Min = XYZ(min(xs_e), min(ys_e), min(zs_e))
                    view_bbox.Max = XYZ(max(xs_e), max(ys_e), max(zs_e))
        except Exception:
            pass

        candidates = [("meters", METERS_TO_FEET), ("feet", 1.0)]
        best = None
        for name, scale in candidates:
            mn = (raw_min[0]*scale, raw_min[1]*scale, raw_min[2]*scale)
            mx = (raw_max[0]*scale, raw_max[1]*scale, raw_max[2]*scale)
            cx = (mn[0]+mx[0])*0.5
            cy = (mn[1]+mx[1])*0.5
            cz = (mn[2]+mx[2])*0.5
            size = max(mx[0]-mn[0], mx[1]-mn[1], mx[2]-mn[2])
            score = 0
            if view_bbox is not None:
                vmn = view_bbox.Min; vmx = view_bbox.Max
                pad_x = (vmx.X-vmn.X)*0.5
                pad_y = (vmx.Y-vmn.Y)*0.5
                pad_z = (vmx.Z-vmn.Z)*0.5
                if (vmn.X-pad_x) <= cx <= (vmx.X+pad_x) and \
                   (vmn.Y-pad_y) <= cy <= (vmx.Y+pad_y) and \
                   (vmn.Z-pad_z) <= cz <= (vmx.Z+pad_z):
                    score += 10
            if 1.0 <= size <= 2000.0:
                score += 5
            elif size < 1.0:
                score -= 5
            if best is None or score > best[0]:
                best = (score, name, scale, mn, mx)

        if best is None:
            return None
        _, _, _, mn, mx = best

        pad = 1.0
        mn_x, mn_y, mn_z = mn
        mx_x, mx_y, mx_z = mx
        if abs(mx_x - mn_x) < 0.1: mn_x -= pad; mx_x += pad
        if abs(mx_y - mn_y) < 0.1: mn_y -= pad; mx_y += pad
        if abs(mx_z - mn_z) < 0.1: mn_z -= pad; mx_z += pad

        bbox = BoundingBoxXYZ()
        bbox.Min = XYZ(mn_x, mn_y, mn_z)
        bbox.Max = XYZ(mx_x, mx_y, mx_z)
        return bbox

    def _isolate_element_impl(self, issue):
        if issue.element_id is None:
            TaskDialog.Show("BCF Reader", "No Element ID for this issue.")
            return
        eid_int = int(issue.element_id)
        ok, elem = self._find_in_host(eid_int)
        target_id = None
        if ok:
            target_id = elem.Id
        else:
            link_inst, link_elem = self._find_in_links(eid_int)
            if link_inst is not None:
                target_id = link_inst.Id
        if target_id is None:
            TaskDialog.Show("BCF Reader",
                "Element ID {} not found.".format(eid_int))
            return

        tx = Transaction(doc, "DQT - BCF Isolate Element")
        try:
            tx.Start()
            opts = tx.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(WarningSwallower())
            tx.SetFailureHandlingOptions(opts)
            ids = List[ElementId]()
            ids.Add(target_id)
            doc.ActiveView.IsolateElementsTemporary(ids)
            tx.Commit()
            self.set_status("Isolated element {} (temporary).".format(eid_int))
        except Exception as ex:
            if tx.HasStarted() and not tx.HasEnded():
                tx.RollBack()
            TaskDialog.Show("BCF Reader", "Failed to isolate:\n" + str(ex))

    # ------------------------------------------------------------------
    # Export PDF (via HTML + Word interop, fallback browser)
    # ------------------------------------------------------------------
    def on_export_pdf_click(self, sender, e):
        if not self.issues:
            TaskDialog.Show("BCF Reader", "No issues to export.")
            return
        try:
            self.export_pdf()
        except Exception as ex:
            TaskDialog.Show("BCF Reader",
                "PDF export failed:\n" + str(ex) + "\n\n" + traceback.format_exc())

    def export_pdf(self):
        # Suggest filename
        suggested = "BCF_Report.pdf"
        if self.reader and self.reader.filepath:
            suggested = "BCF_Report_" + IOPath.GetFileNameWithoutExtension(
                self.reader.filepath) + ".pdf"

        # Let user choose location
        dlg = SaveFileDialog()
        dlg.Title = "Save PDF Report"
        dlg.Filter = "PDF files (*.pdf)|*.pdf|All files (*.*)|*.*"
        dlg.FileName = suggested
        dlg.DefaultExt = ".pdf"
        if os.path.exists(OUTPUT_DIR):
            dlg.InitialDirectory = OUTPUT_DIR
        if dlg.ShowDialog() != True:
            self.set_status("PDF export cancelled.")
            return

        pdf_path = dlg.FileName
        # Derive companion paths in same folder as user-chosen pdf
        out_folder = IOPath.GetDirectoryName(pdf_path)
        base = IOPath.GetFileNameWithoutExtension(pdf_path)
        if not out_folder:
            out_folder = OUTPUT_DIR
            self._ensure_output_dir()

        html_path = os.path.join(out_folder, base + ".html")
        img_dir = os.path.join(out_folder, base + "_images")
        if not os.path.exists(img_dir):
            try:
                os.makedirs(img_dir)
            except Exception:
                pass

        # Save snapshot bytes to disk so HTML <img> can reference them
        for issue in self.issues:
            if issue.snapshot_bytes is not None:
                img_path = os.path.join(img_dir, issue.guid + ".png")
                if not os.path.exists(img_path):
                    try:
                        File.WriteAllBytes(img_path, issue.snapshot_bytes)
                    except Exception:
                        pass

        html = self._build_html_report(img_dir)
        from System.IO import File as IOFile
        IOFile.WriteAllText(html_path, html, Encoding.UTF8)

        # Try multiple PDF conversion methods in order of reliability
        pdf_ok = False
        method_used = ""

        # Method 1: Microsoft Edge headless (built-in on Windows 10/11)
        if not pdf_ok:
            if self._try_html_to_pdf_edge(html_path, pdf_path):
                pdf_ok = True
                method_used = "Microsoft Edge"

        # Method 2: Word interop (if Office installed)
        if not pdf_ok:
            if self._try_html_to_pdf_word(html_path, pdf_path):
                pdf_ok = True
                method_used = "Microsoft Word"

        if pdf_ok:
            self.set_status("Exported PDF via {}: {}".format(method_used, pdf_path))
            TaskDialog.Show("BCF Reader",
                "PDF exported via {}:\n{}".format(method_used, pdf_path))
            try:
                System.Diagnostics.Process.Start(pdf_path)
            except Exception:
                pass
        else:
            # Fallback: open HTML in default browser; user prints to PDF
            self.set_status("Saved HTML report (open and print to PDF): " + html_path)
            td = TaskDialog("BCF Reader")
            td.MainInstruction = "PDF conversion unavailable"
            td.MainContent = ("Microsoft Word interop not found.\n\n"
                "HTML report saved to:\n" + html_path + "\n\n"
                "Opening in browser - press Ctrl+P then 'Save as PDF'.")
            td.Show()
            try:
                System.Diagnostics.Process.Start(html_path)
            except Exception:
                pass

    def _try_html_to_pdf_edge(self, html_path, pdf_path):
        """Convert HTML to PDF using Microsoft Edge headless mode.
        Edge is built into Windows 10/11 - no installation needed.
        Returns True on success."""
        # Common Edge executable paths
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        ]
        edge_exe = None
        for p in edge_paths:
            if p and os.path.exists(p):
                edge_exe = p
                break
        if edge_exe is None:
            # Try Chrome as alternative
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
            for p in chrome_paths:
                if os.path.exists(p):
                    edge_exe = p
                    break
        if edge_exe is None:
            return False

        # Delete existing pdf so we can verify creation
        if File.Exists(pdf_path):
            try:
                File.Delete(pdf_path)
            except Exception:
                pass

        try:
            from System.Diagnostics import Process, ProcessStartInfo
            psi = ProcessStartInfo()
            psi.FileName = edge_exe
            # File URL for HTML input
            html_uri = "file:///" + html_path.replace("\\", "/")
            psi.Arguments = (
                '--headless=new --disable-gpu --no-pdf-header-footer '
                '--print-to-pdf="{}" "{}"'.format(pdf_path, html_uri))
            psi.UseShellExecute = False
            psi.CreateNoWindow = True
            psi.RedirectStandardOutput = True
            psi.RedirectStandardError = True
            proc = Process.Start(psi)
            # Wait up to 30 seconds
            if not proc.WaitForExit(30000):
                try:
                    proc.Kill()
                except Exception:
                    pass
                return False
            return File.Exists(pdf_path)
        except Exception:
            return False

    def _try_html_to_pdf_word(self, html_path, pdf_path):
        """Convert HTML to PDF using late-bound COM to Word.Application.
        This avoids needing Microsoft.Office.Interop.Word PIA - works on any
        machine that has Word installed (including Office 365 click-to-run).
        Returns True on success."""
        word_app = None
        doc_obj = None
        try:
            # Late binding via COM ProgID - no PIA needed
            from System import Type, Activator
            word_type = Type.GetTypeFromProgID("Word.Application")
            if word_type is None:
                return False
            word_app = Activator.CreateInstance(word_type)

            # Word Application properties via COM late binding
            # Visible = False
            try:
                word_type.InvokeMember(
                    "Visible",
                    System.Reflection.BindingFlags.SetProperty,
                    None, word_app, System.Array[System.Object]([False]))
            except Exception:
                pass

            # Get Documents collection
            documents = word_type.InvokeMember(
                "Documents",
                System.Reflection.BindingFlags.GetProperty,
                None, word_app, None)

            # Documents.Open(html_path, False, True) -> ConfirmConversions=False, ReadOnly=True
            doc_obj = documents.GetType().InvokeMember(
                "Open",
                System.Reflection.BindingFlags.InvokeMethod,
                None, documents,
                System.Array[System.Object]([html_path, False, True]))

            # ExportAsFixedFormat: pdf format=17, range=0 (all), item=0 (content), bookmarks=0
            # Signature: ExportAsFixedFormat(OutputFileName, ExportFormat, OpenAfterExport,
            #   OptimizeFor, Range, From, To, Item, IncludeDocProps, KeepIRM,
            #   CreateBookmarks, DocStructureTags, BitmapMissingFonts, UseISO19005_1)
            args = System.Array[System.Object]([
                pdf_path,    # OutputFileName
                17,          # ExportFormat = wdExportFormatPDF
                False,       # OpenAfterExport
                0,           # OptimizeFor = wdExportOptimizeForPrint
                0,           # Range = wdExportAllDocument
                0, 0,        # From, To
                0,           # Item = wdExportDocumentContent
                True,        # IncludeDocProps
                True,        # KeepIRM
                0,           # CreateBookmarks = wdExportCreateNoBookmarks
                True, True, False  # DocStructureTags, BitmapMissingFonts, UseISO19005_1
            ])
            doc_obj.GetType().InvokeMember(
                "ExportAsFixedFormat",
                System.Reflection.BindingFlags.InvokeMethod,
                None, doc_obj, args)

            return File.Exists(pdf_path)
        except Exception:
            return False
        finally:
            try:
                if doc_obj is not None:
                    doc_obj.GetType().InvokeMember(
                        "Close",
                        System.Reflection.BindingFlags.InvokeMethod,
                        None, doc_obj,
                        System.Array[System.Object]([False]))
            except Exception:
                pass
            try:
                if word_app is not None:
                    word_app.GetType().InvokeMember(
                        "Quit",
                        System.Reflection.BindingFlags.InvokeMethod,
                        None, word_app,
                        System.Array[System.Object]([False]))
            except Exception:
                pass

    def _build_html_report(self, img_dir):
        """Generate a self-contained HTML report from issues."""
        n_total = len(self.issues)
        n_added = sum(1 for i in self.issues if i.label == "ADDED")
        n_removed = sum(1 for i in self.issues if i.label == "REMOVED")
        n_modified = sum(1 for i in self.issues if i.label == "MODIFIED")
        n_resolved = sum(1 for i in self.issues if i.resolved)

        proj_name = ""
        file_name = ""
        if self.reader:
            proj_name = self.reader.project_name or ""
            if self.reader.filepath:
                file_name = IOPath.GetFileName(self.reader.filepath)

        from datetime import datetime
        gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        css = """
        @page {
            size: A4;
            margin: 12mm 14mm;
        }
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            margin: 0;
            color: #333;
            background: #fff;
            font-size: 11pt;
        }
        h1 {
            color: #5D4E37;
            border-bottom: 3px solid #0F172A;
            padding-bottom: 6px;
            margin: 0 0 8px 0;
            font-size: 20pt;
        }
        .meta { color: #7B6F5A; font-size: 10pt; margin-bottom: 16px; }
        .stats { width: 100%; margin-bottom: 18px; border-collapse: separate; border-spacing: 6px; }
        .stats td {
            border: 1px solid #CBD5E1;
            border-radius: 5px;
            padding: 8px;
            text-align: center;
            background: #F8FAFC;
            width: 20%;
        }
        .stats .num { font-size: 18pt; font-weight: bold; color: #5D4E37; display: block; }
        .stats .lbl { font-size: 9pt; color: #7B6F5A; text-transform: uppercase; }
        .stats td.added { background: #E8F5E9; }
        .stats td.added .num { color: #27AE60; }
        .stats td.removed { background: #FDEDEC; }
        .stats td.removed .num { color: #E74C3C; }
        .stats td.modified { background: #FEF5E7; }
        .stats td.modified .num { color: #F39C12; }
        .stats td.resolved { background: #E8F5E9; }
        .stats td.resolved .num { color: #27AE60; }
        .issue {
            border: 1px solid #CBD5E1;
            border-radius: 5px;
            margin-bottom: 14px;
            overflow: hidden;
            page-break-inside: avoid;
            break-inside: avoid;
        }
        .issue-header {
            background: #0F172A;
            color: #5D4E37;
            padding: 8px 12px;
            font-weight: bold;
            font-size: 11pt;
        }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 9pt;
            font-weight: bold;
            color: #fff;
            margin-right: 6px;
        }
        .badge.ADDED { background: #27AE60; }
        .badge.REMOVED { background: #E74C3C; }
        .badge.MODIFIED { background: #F39C12; }
        .badge.OTHER { background: #95A5A6; }
        .badge.RESOLVED { background: #27AE60; border: 1px solid #1e7e34; }
        .issue-body {
            padding: 10px;
        }
        .issue-row {
            width: 100%;
            border-collapse: collapse;
        }
        .issue-row td {
            vertical-align: top;
            padding: 0;
        }
        .issue-row td.imgcol {
            width: 240px;
            padding-right: 12px;
        }
        .issue-row td.infocol {
            width: auto;
            font-size: 10pt;
        }
        .issue-img-wrap {
            width: 240px;
            height: 180px;
            overflow: hidden;
            border: 1px solid #ddd;
            border-radius: 3px;
            background: #f8f8f8;
            text-align: center;
            line-height: 180px;
        }
        .issue-img-wrap img {
            max-width: 240px;
            max-height: 180px;
            width: auto;
            height: auto;
            vertical-align: middle;
            object-fit: contain;
        }
        .info-table { border-collapse: collapse; width: 100%; }
        .info-table td {
            padding: 3px 6px;
            border-bottom: 1px solid #f0e8d8;
            vertical-align: top;
            font-size: 10pt;
        }
        .info-table td.k { font-weight: bold; color: #5D4E37; width: 100px; white-space: nowrap; }
        .desc {
            margin-top: 8px;
            padding: 8px;
            background: #F8FAFC;
            border-left: 3px solid #0F172A;
            border-radius: 3px;
            font-size: 9pt;
            line-height: 1.4;
            page-break-inside: avoid;
        }
        .footer {
            margin-top: 20px;
            padding-top: 8px;
            border-top: 1px solid #CBD5E1;
            color: #7B6F5A;
            font-size: 9pt;
            text-align: center;
        }
        """
        out = []
        out.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
        out.append("<title>BCF Report - " + self._html_esc(file_name) + "</title>")
        out.append("<style>" + css + "</style></head><body>")
        out.append("<h1>DQT BCF Reader - Issue Report</h1>")
        out.append("<div class='meta'>")
        if proj_name:
            out.append("<b>Project:</b> " + self._html_esc(proj_name) + " &nbsp;|&nbsp; ")
        out.append("<b>File:</b> " + self._html_esc(file_name))
        out.append(" &nbsp;|&nbsp; <b>Generated:</b> " + gen_time)
        out.append("</div>")

        # Stats as table (more print-friendly than flexbox)
        out.append("<table class='stats'><tr>")
        out.append("<td><span class='num'>{}</span><span class='lbl'>Total</span></td>".format(n_total))
        out.append("<td class='added'><span class='num'>{}</span><span class='lbl'>Added</span></td>".format(n_added))
        out.append("<td class='removed'><span class='num'>{}</span><span class='lbl'>Removed</span></td>".format(n_removed))
        out.append("<td class='modified'><span class='num'>{}</span><span class='lbl'>Modified</span></td>".format(n_modified))
        out.append("<td class='resolved'><span class='num'>{}/{}</span><span class='lbl'>Resolved</span></td>".format(n_resolved, n_total))
        out.append("</tr></table>")

        # Issues
        for issue in self.issues:
            out.append("<div class='issue'>")
            badge_html = "<span class='badge {}'>{}</span>".format(
                issue.label, issue.label)
            if issue.resolved:
                badge_html += "<span class='badge RESOLVED'>RESOLVED</span>"
            out.append("<div class='issue-header'>" + badge_html
                       + "Issue #" + str(issue.index) + " - "
                       + self._html_esc(self._truncate(issue.title, 100))
                       + "</div>")
            out.append("<div class='issue-body'>")
            # Use TABLE layout instead of flexbox for predictable PDF rendering
            out.append("<table class='issue-row'><tr>")
            if issue.snapshot_bytes is not None:
                img_rel = os.path.basename(img_dir) + "/" + issue.guid + ".png"
                out.append("<td class='imgcol'>")
                out.append("<div class='issue-img-wrap'><img src='" + img_rel + "' /></div>")
                out.append("</td>")
            out.append("<td class='infocol'>")
            out.append("<table class='info-table'>")
            if issue.element_ids:
                ids_str = ", ".join(str(x) for x in issue.element_ids)
                out.append("<tr><td class='k'>Element IDs</td><td>" + ids_str + "</td></tr>")
            elif issue.element_id is not None:
                out.append("<tr><td class='k'>Element ID</td><td>" + str(issue.element_id) + "</td></tr>")
            if issue.position:
                out.append("<tr><td class='k'>Position</td><td>({}, {}, {})</td></tr>".format(
                    str(round(issue.position[0], 3)),
                    str(round(issue.position[1], 3)),
                    str(round(issue.position[2], 3))))
            if issue.creation_date:
                out.append("<tr><td class='k'>Created</td><td>" + self._html_esc(issue.creation_date) + "</td></tr>")
            if issue.camera_type:
                out.append("<tr><td class='k'>Camera</td><td>" + self._html_esc(issue.camera_type) + "</td></tr>")
            if issue.components:
                out.append("<tr><td class='k'>Components</td><td>" + str(len(issue.components)) + "</td></tr>")
            if issue.ifc_guid:
                out.append("<tr><td class='k'>IFC GUID</td><td style='font-family:monospace;font-size:8pt;'>" + self._html_esc(issue.ifc_guid) + "</td></tr>")
            out.append("</table>")
            out.append("</td></tr></table>")
            if issue.description:
                out.append("<div class='desc'>" + self._html_esc(issue.description) + "</div>")
            out.append("</div></div>")

        out.append("<div class='footer'>DQT BCF Reader - Dang Quoc Truong &copy; 2025</div>")
        out.append("</body></html>")
        return "\n".join(out)

    def _html_esc(self, s):
        if s is None:
            return ""
        return (str(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    # ------------------------------------------------------------------
    # Export BCF (with resolved flags embedded as Labels)
    # ------------------------------------------------------------------
    def on_export_bcf_click(self, sender, e):
        if not self.issues:
            TaskDialog.Show("BCF Reader", "No issues loaded.")
            return
        if self.reader is None or not self.reader.filepath:
            TaskDialog.Show("BCF Reader", "Original BCF file path unknown.")
            return

        # Suggest filename based on source
        orig = self.reader.filepath
        base = IOPath.GetFileNameWithoutExtension(orig)
        ext = IOPath.GetExtension(orig) or ".bcfzip"
        suggested = base + "_resolved" + ext

        # Let user choose location
        dlg = SaveFileDialog()
        dlg.Title = "Save BCF (with Resolved flags)"
        dlg.Filter = "BCF files (*.bcfzip;*.bcf)|*.bcfzip;*.bcf|All files (*.*)|*.*"
        dlg.FileName = suggested
        dlg.DefaultExt = ext
        # Default folder: same as original BCF if exists, else OUTPUT_DIR
        try:
            orig_dir = IOPath.GetDirectoryName(orig)
            if orig_dir and os.path.exists(orig_dir):
                dlg.InitialDirectory = orig_dir
            elif os.path.exists(OUTPUT_DIR):
                dlg.InitialDirectory = OUTPUT_DIR
        except Exception:
            pass
        if dlg.ShowDialog() != True:
            self.set_status("BCF export cancelled.")
            return
        out_path = dlg.FileName

        try:
            self.export_bcf(out_path)
            n_resolved = sum(1 for i in self.issues if i.resolved)
            TaskDialog.Show("BCF Reader",
                "Exported BCF with {} resolved / {} total issues.\n\nSaved to:\n{}".format(
                    n_resolved, len(self.issues), out_path))
            self.set_status("Exported BCF: " + out_path)
        except Exception as ex:
            TaskDialog.Show("BCF Reader",
                "Export BCF failed:\n" + str(ex) + "\n\n" + traceback.format_exc())

    def export_bcf(self, out_path):
        """Re-zip the original BCF but rewrite markup.bcf of each issue to
        add/remove 'RESOLVED' label according to issue.resolved flag."""
        from System.IO.Compression import ZipArchiveMode
        from System.IO import FileMode, FileStream, FileAccess, FileShare

        self._ensure_output_dir()
        if File.Exists(out_path):
            File.Delete(out_path)

        # Map guid -> resolved
        resolved_map = {}
        for it in self.issues:
            resolved_map[it.guid] = it.resolved

        src_archive = ZipFile.OpenRead(self.reader.filepath)
        try:
            # FileShare.None clashes with Python keyword - use getattr
            fs_none = getattr(FileShare, "None")
            out_stream = FileStream(out_path, FileMode.Create, FileAccess.ReadWrite, fs_none)
            try:
                out_archive = System.IO.Compression.ZipArchive(
                    out_stream, ZipArchiveMode.Create)
                try:
                    for entry in src_archive.Entries:
                        full = entry.FullName.replace("\\", "/")
                        parts = full.split("/")
                        filename = parts[-1].lower() if parts else ""
                        folder = parts[0] if len(parts) >= 2 else ""

                        new_entry = out_archive.CreateEntry(entry.FullName)
                        entry_stream = new_entry.Open()
                        try:
                            if filename == "markup.bcf" and folder in resolved_map:
                                xml_text = self._read_entry_text_src(entry)
                                # Find the issue object for this folder
                                target_issue = None
                                for it in self.issues:
                                    if it.guid == folder:
                                        target_issue = it
                                        break
                                if target_issue is not None:
                                    new_xml = self._rewrite_markup_full(
                                        xml_text, target_issue)
                                else:
                                    new_xml = self._rewrite_markup_labels(
                                        xml_text, resolved_map[folder])
                                data = Encoding.UTF8.GetBytes(new_xml)
                                entry_stream.Write(data, 0, data.Length)
                            else:
                                src_stream = entry.Open()
                                try:
                                    src_stream.CopyTo(entry_stream)
                                finally:
                                    src_stream.Dispose()
                        finally:
                            entry_stream.Dispose()
                finally:
                    out_archive.Dispose()
            finally:
                out_stream.Dispose()
        finally:
            src_archive.Dispose()

    def _read_entry_text_src(self, entry):
        stream = entry.Open()
        try:
            reader = StreamReader(stream, Encoding.UTF8)
            try:
                return reader.ReadToEnd()
            finally:
                reader.Dispose()
        finally:
            stream.Dispose()

    def _rewrite_markup_labels(self, xml_text, resolved):
        """Add or remove <Labels>DQT_RESOLVED</Labels> in markup.bcf XML."""
        # Remove any existing DQT_RESOLVED labels
        xml_text = re.sub(
            r'\s*<Labels>\s*DQT_RESOLVED\s*</Labels>', '', xml_text,
            flags=re.IGNORECASE)
        xml_text = re.sub(
            r'\s*<Labels>\s*RESOLVED\s*</Labels>', '', xml_text,
            flags=re.IGNORECASE)

        if resolved:
            # Insert <Labels>DQT_RESOLVED</Labels> just before </Topic>
            m = re.search(r'</Topic>', xml_text)
            if m:
                insert_pos = m.start()
                xml_text = (xml_text[:insert_pos]
                    + "  <Labels>DQT_RESOLVED</Labels>\n"
                    + xml_text[insert_pos:])
        return xml_text

    def _rewrite_markup_full(self, xml_text, issue):
        """Rewrite markup.bcf using XmlDocument (NOT regex - regex breaks on
        nested <Comment><Comment>text</Comment></Comment> structure).
        Updates: Labels (resolved flag) + Comments (writes all from issue.comments)."""
        try:
            xd = XmlDocument()
            xd.PreserveWhitespace = False
            xd.LoadXml(xml_text)
        except Exception:
            # If parsing fails, return original (don't corrupt further)
            return xml_text

        root = xd.DocumentElement
        if root is None:
            return xml_text

        # Find <Topic> element (could be at any depth, with or without namespace)
        topic_node = self._xml_find(root, "Topic")

        # ---- Step 1: Update Labels (resolved flag) ----
        if topic_node is not None:
            # Remove any existing DQT_RESOLVED / RESOLVED label nodes
            labels_to_remove = []
            for child in topic_node.ChildNodes:
                if child.NodeType != System.Xml.XmlNodeType.Element:
                    continue
                if child.LocalName.lower() == "labels":
                    txt = (child.InnerText or "").strip().upper()
                    if txt in ("DQT_RESOLVED", "RESOLVED"):
                        labels_to_remove.append(child)
            for lbl in labels_to_remove:
                topic_node.RemoveChild(lbl)

            # Add fresh resolved label if needed
            if issue.resolved:
                ns = topic_node.NamespaceURI or ""
                if ns:
                    new_label = xd.CreateElement(topic_node.Prefix or "",
                                                 "Labels", ns)
                else:
                    new_label = xd.CreateElement("Labels")
                new_label.InnerText = "DQT_RESOLVED"
                topic_node.AppendChild(new_label)

        # ---- Step 2: Rewrite Comments ----
        # BCF schema: <Comment> elements are SIBLINGS of <Topic> (children of root <Markup>).
        # Find existing <Comment> nodes that are direct children of root and remove them.
        comments_to_remove = []
        for child in root.ChildNodes:
            if child.NodeType != System.Xml.XmlNodeType.Element:
                continue
            if child.LocalName.lower() == "comment":
                comments_to_remove.append(child)
        for cn in comments_to_remove:
            root.RemoveChild(cn)

        # Append fresh Comment nodes from issue.comments
        for c in issue.comments:
            comment_el = self._build_comment_element(xd, root, c)
            root.AppendChild(comment_el)

        # Serialize back to string
        try:
            from System.IO import StringWriter
            from System.Xml import XmlWriter, XmlWriterSettings
            settings = XmlWriterSettings()
            settings.Indent = True
            settings.IndentChars = "  "
            settings.OmitXmlDeclaration = False
            settings.Encoding = Encoding.UTF8

            sw = StringWriter()
            try:
                xw = XmlWriter.Create(sw, settings)
                try:
                    xd.Save(xw)
                finally:
                    xw.Close()
                result = sw.ToString()
            finally:
                sw.Dispose()
            return result
        except Exception:
            # Fallback to OuterXml if XmlWriter fails
            return xd.OuterXml

    def _xml_find(self, parent, local_name):
        """Find first child element with matching local name (namespace-agnostic)."""
        if parent is None:
            return None
        target = local_name.lower()
        for child in parent.ChildNodes:
            if child.NodeType != System.Xml.XmlNodeType.Element:
                continue
            if child.LocalName.lower() == target:
                return child
        return None

    def _build_comment_element(self, xd, root_for_ns, comment):
        """Build a <Comment> XmlElement matching the namespace of root."""
        ns = root_for_ns.NamespaceURI or ""
        prefix = root_for_ns.Prefix or ""

        def _make(tag):
            if ns:
                return xd.CreateElement(prefix, tag, ns)
            return xd.CreateElement(tag)

        comment_el = _make("Comment")
        comment_el.SetAttribute("Guid", comment.guid or str(System.Guid.NewGuid()))

        date_el = _make("Date")
        date_el.InnerText = comment.date or ""
        comment_el.AppendChild(date_el)

        author_el = _make("Author")
        author_el.InnerText = comment.author or ""
        comment_el.AppendChild(author_el)

        text_el = _make("Comment")
        text_el.InnerText = comment.text or ""
        comment_el.AppendChild(text_el)

        if comment.modified_date:
            md_el = _make("ModifiedDate")
            md_el.InnerText = comment.modified_date
            comment_el.AppendChild(md_el)

        if comment.modified_author:
            ma_el = _make("ModifiedAuthor")
            ma_el.InnerText = comment.modified_author
            comment_el.AppendChild(ma_el)

        if comment.viewpoint_guid:
            # Schema: <Viewpoint Guid="..." />
            vp_el = _make("Viewpoint")
            vp_el.SetAttribute("Guid", comment.viewpoint_guid)
            comment_el.AppendChild(vp_el)

        return comment_el

    def _xml_text_esc(self, s):
        if s is None:
            return ""
        return (str(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    def _xml_attr_esc(self, s):
        if s is None:
            return ""
        return (str(s)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    # ------------------------------------------------------------------
    # Export CSV
    # ------------------------------------------------------------------
    def export_summary_csv(self):
        # Suggest filename based on source BCF
        suggested = "BCF_Summary.csv"
        if self.reader and self.reader.filepath:
            base = IOPath.GetFileNameWithoutExtension(self.reader.filepath)
            suggested = "BCF_Summary_" + base + ".csv"

        # Let user choose location
        dlg = SaveFileDialog()
        dlg.Title = "Save CSV Summary"
        dlg.Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*"
        dlg.FileName = suggested
        dlg.DefaultExt = ".csv"
        # Default folder: OUTPUT_DIR if exists, else Documents
        if os.path.exists(OUTPUT_DIR):
            dlg.InitialDirectory = OUTPUT_DIR
        if dlg.ShowDialog() != True:
            self.set_status("CSV export cancelled.")
            return
        out_path = dlg.FileName

        lines = []
        lines.append(",".join([
            "Index", "GUID", "Label", "Resolved", "Title", "ElementId",
            "IfcGuid", "X", "Y", "Z", "CameraType", "CreationDate"
        ]))
        for it in self.issues:
            pos = ("", "", "")
            if it.position is not None:
                pos = (str(round(it.position[0], 4)),
                       str(round(it.position[1], 4)),
                       str(round(it.position[2], 4)))
            row = [
                str(it.index),
                self._csv_esc(it.guid),
                self._csv_esc(it.label),
                "Yes" if it.resolved else "No",
                self._csv_esc(it.title),
                str(it.element_id) if it.element_id is not None else "",
                self._csv_esc(it.ifc_guid),
                pos[0], pos[1], pos[2],
                self._csv_esc(it.camera_type),
                self._csv_esc(it.creation_date),
            ]
            lines.append(",".join(row))

        from System.IO import File as IOFile
        IOFile.WriteAllText(out_path, "\n".join(lines), Encoding.UTF8)
        self.set_status("Exported {} issues to {}".format(len(self.issues), out_path))
        TaskDialog.Show("BCF Reader",
            "Exported {} issues to:\n{}".format(len(self.issues), out_path))

    # ------------------------------------------------------------------
    def set_status(self, text):
        try:
            self.lblStatus.Text = text
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'BCFReader.xaml')


def show_bcf_reader_dialog(modal=False):
    """Show BCF Reader dialog window."""
    _ensure_output_dir()
    if not os.path.exists(XAML_FILE):
        TaskDialog.Show(
            "BCF Reader",
            "BCFReader.xaml not found in central Tools folder.\nExpected: " + XAML_FILE
        )
        return None

    win = BCFManagerWindow(XAML_FILE)
    if modal:
        win.show_dialog()
    else:
        win.show()
    return win
