# -*- coding: utf-8 -*-
"""Per-user state leaves the extension folder and OneDrive (2026-10-02).

  - ManaLoca: session.json was written into ManaLoca.pushbutton (inside the
    shared, git-tracked clone) → %APPDATA%\\T3LabAI\\manaloca, old file copied once.
  - ManaFami: the thumbnail cache grew in ~/.t3lab/thumbnails forever →
    %APPDATA%\\T3LabAI\\famithumbs, capped (500 files / 200 MB, LRU by mtime).
  - BatchOut: _export_crash_marker.json (written + fsync'd before EVERY export
    call) and _export_crash_history.json lived in the OneDrive-synced
    Documents\\T3Lab_BatchOut_Profiles → %APPDATA%\\T3LabAI\\batchout. Profiles stay.
  - Bad Geometry check ↔ BatchOut safe mode: Documents\\T3Lab_Diagnostics →
    %APPDATA%\\T3LabAI\\diagnostics, and both sides agree on the findings path.

The shipped functions are lifted out of the source with ast and run against a
temporary APPDATA / HOME, so no Revit or .NET is needed.

Run: python3 dev/test_user_data_moves.py
"""
import ast
import os
import re
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, "T3Lab.extension")
LIB = os.path.join(EXT, "lib")
GUI = os.path.join(LIB, "GUI")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from core.paths import user_data_path   # noqa: E402  (pure Python)
from core.extension_paths import find_bundle   # noqa: E402  (pure Python)

MANALOCA = os.path.join(GUI, "ManaLocaDialog.py")
MANAFAMI = os.path.join(GUI, "ManaFamiDialog.py")
BATCHOUT = os.path.join(GUI, "BatchOutDialog.py")
BADGEOM = os.path.join(EXT, "checks", "badgeometry_check.py")


def _source(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _lift(path, names, scope):
    """Exec the module-level defs / assignments called `names` (source order)."""
    tree = ast.parse(_source(path))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            body.append(node)
    missing = set(names) - {getattr(n, "name", None) for n in body} - {
        t.id for n in body if isinstance(n, ast.Assign) for t in n.targets
        if isinstance(t, ast.Name)}
    assert not missing, "%s: not found %s" % (os.path.basename(path), sorted(missing))
    exec(compile(ast.Module(body=body, type_ignores=[]), path, "exec"), scope)
    return scope


class _TempHome(unittest.TestCase):
    """APPDATA and HOME point into a temp dir for the whole test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t3lab_moves_")
        self.appdata = os.path.join(self.tmp, "AppData")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.appdata)
        os.makedirs(os.path.join(self.home, "Documents"))
        self._env = {k: os.environ.get(k) for k in ("APPDATA", "HOME", "USERPROFILE")}
        os.environ["APPDATA"] = self.appdata
        os.environ["HOME"] = self.home
        os.environ["USERPROFILE"] = self.home

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def user(self, *parts):
        return os.path.join(self.appdata, "T3LabAI", *parts)

    @staticmethod
    def write(path, text="{}"):
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "w") as fh:
            fh.write(text)


# ── ManaLoca ─────────────────────────────────────────────────────────────────

class ManaLocaSession(_TempHome):
    def _settings(self, ext_dir):
        scope = {"os": os, "user_data_path": user_data_path, "EXT_DIR": ext_dir,
                 "find_bundle": find_bundle,   # the real lookup, on a temp extension
                 "__file__": os.path.join(ext_dir, "lib", "GUI", "ManaLocaDialog.py")}
        return _lift(MANALOCA, ["_legacy_settings_files", "SETTINGS_FILE"], scope)

    def test_session_lives_in_appdata(self):
        ext = os.path.join(self.tmp, "ext")
        scope = self._settings(ext)
        self.assertEqual(scope["SETTINGS_FILE"], self.user("manaloca", "session.json"))
        self.assertNotIn("pushbutton", scope["SETTINGS_FILE"])

    def test_old_pushbutton_file_is_copied_once_and_left_alone(self):
        ext = os.path.join(self.tmp, "ext")
        old = os.path.join(ext, "Whatever.tab", "Standards & Settings.panel",
                           "ManaLoca.pushbutton", "session.json")
        self.write(old, '{"unchecked_categories": ["Grids"]}')
        scope = self._settings(ext)
        with open(scope["SETTINGS_FILE"]) as fh:
            self.assertIn("Grids", fh.read())
        self.assertTrue(os.path.isfile(old))
        # the user changes it; the next session must not copy the old one back
        self.write(scope["SETTINGS_FILE"], '{"unchecked_categories": []}')
        scope = self._settings(ext)
        with open(scope["SETTINGS_FILE"]) as fh:
            self.assertNotIn("Grids", fh.read())

    def test_module_fallback_file_is_a_legacy_source_too(self):
        ext = os.path.join(self.tmp, "ext")
        old = os.path.join(ext, "lib", "GUI", "ManaLoca_session.json")
        self.write(old, '{"unchecked_categories": ["Floors"]}')
        scope = self._settings(ext)
        with open(scope["SETTINGS_FILE"]) as fh:
            self.assertIn("Floors", fh.read())

    def test_no_writer_points_into_the_extension(self):
        src = _source(MANALOCA)
        self.assertIn("user_data_path('manaloca', 'session.json'", src)
        self.assertNotIn("SETTINGS_FILE = os.path.join(", src)


# ── ManaFami thumbnails ──────────────────────────────────────────────────────

class ManaFamiThumbnails(_TempHome):
    NAMES = ["THUMBNAIL_CACHE_DIR", "THUMBNAIL_CACHE_MAX_FILES",
             "THUMBNAIL_CACHE_MAX_BYTES", "THUMBNAIL_PRUNE_EVERY",
             "prune_thumbnail_cache", "_touch"]

    def setUp(self):
        _TempHome.setUp(self)
        self.ns = _lift(MANAFAMI, self.NAMES, {"os": os, "user_data_path": user_data_path})
        self.cache = self.ns["THUMBNAIL_CACHE_DIR"]
        os.makedirs(self.cache)

    def _jpg(self, name, size, age):
        path = os.path.join(self.cache, name)
        with open(path, "wb") as fh:
            fh.write(b"\xff" * size)
        t = 1700000000 - age
        os.utime(path, (t, t))
        return path

    def test_cache_moved_to_appdata(self):
        self.assertEqual(self.cache, self.user("famithumbs"))
        self.assertNotIn(".t3lab", _source(MANAFAMI).split("THUMBNAIL_CACHE_DIR =")[1][:80])

    def test_caps_match_the_brief(self):
        self.assertEqual(self.ns["THUMBNAIL_CACHE_MAX_FILES"], 500)
        self.assertEqual(self.ns["THUMBNAIL_CACHE_MAX_BYTES"], 200 * 1024 * 1024)

    def test_oldest_beyond_the_file_cap_go(self):
        paths = [self._jpg("f%d.jpg" % i, 10, age=i) for i in range(8)]  # f0 newest
        removed = self.ns["prune_thumbnail_cache"](self.cache, max_files=5, max_bytes=10 ** 9)
        self.assertEqual(removed, 3)
        self.assertEqual([os.path.exists(p) for p in paths],
                         [True] * 5 + [False] * 3)

    def test_oldest_beyond_the_byte_cap_go(self):
        paths = [self._jpg("b%d.jpg" % i, 100, age=i) for i in range(5)]
        removed = self.ns["prune_thumbnail_cache"](self.cache, max_files=99, max_bytes=250)
        self.assertEqual(removed, 3)
        self.assertEqual([os.path.exists(p) for p in paths], [True, True, False, False, False])

    def test_only_cache_jpgs_are_touched(self):
        other = os.path.join(self.cache, "notes.txt")
        self.write(other, "keep me")
        for i in range(3):
            self._jpg("x%d.jpg" % i, 10, age=i)
        self.ns["prune_thumbnail_cache"](self.cache, max_files=0, max_bytes=0)
        self.assertEqual(os.listdir(self.cache), ["notes.txt"])

    def test_missing_folder_never_raises(self):
        self.assertEqual(self.ns["prune_thumbnail_cache"](os.path.join(self.tmp, "nope")), 0)

    def test_a_cache_hit_counts_as_recent(self):
        old = self._jpg("old.jpg", 10, age=1000)
        new = self._jpg("new.jpg", 10, age=1)
        self.ns["_touch"](old)                       # shown just now
        self.ns["prune_thumbnail_cache"](self.cache, max_files=1, max_bytes=10 ** 9)
        self.assertTrue(os.path.exists(old))
        self.assertFalse(os.path.exists(new))

    def test_worker_prunes_while_writing_and_at_the_end(self):
        tree = ast.parse(_source(MANAFAMI))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ManaFamiWindow")
        worker = next(n for n in cls.body
                      if isinstance(n, ast.FunctionDef) and n.name == "_thumbnail_worker")
        calls = [n for n in ast.walk(worker) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "prune_thumbnail_cache"]
        self.assertEqual(len(calls), 2)
        touched = [n for n in ast.walk(worker) if isinstance(n, ast.Call)
                   and getattr(n.func, "id", "") == "_touch"]
        self.assertEqual(len(touched), 1)


# ── BatchOut crash breadcrumbs + diagnostics ─────────────────────────────────

BATCHOUT_NAMES = ["CRASH_MARKER_NAME", "CRASH_HISTORY_NAME", "LEGACY_DIAG_FOLDER",
                  "BAD_GEOMETRY_NAME", "_adopt_legacy_file", "crash_state_files",
                  "bad_geometry_file"]
BADGEOM_NAMES = ["LEGACY_DIAG_FOLDER", "_adopt_legacy_file", "_diag_file",
                 "MARKER_FILE", "PROGRESS_FILE", "FINDINGS_FILE", "DIAG_FOLDER"]


class BatchOutCrashFiles(_TempHome):
    def setUp(self):
        _TempHome.setUp(self)
        self.ns = _lift(BATCHOUT, BATCHOUT_NAMES, {"os": os, "user_data_path": user_data_path})
        self.profiles = os.path.join(self.home, "Documents", "T3Lab_BatchOut_Profiles")

    def test_breadcrumbs_live_in_appdata_not_in_profiles(self):
        marker, history = self.ns["crash_state_files"](self.profiles)
        self.assertEqual(marker, self.user("batchout", "_export_crash_marker.json"))
        self.assertEqual(history, self.user("batchout", "_export_crash_history.json"))
        self.assertTrue(os.path.isdir(os.path.dirname(marker)))
        self.assertFalse(os.path.exists(self.profiles))    # nothing created in Documents

    def test_old_history_is_copied_once(self):
        old = os.path.join(self.profiles, "_export_crash_history.json")
        self.write(old, '[{"sheet": "A101"}]')
        _marker, history = self.ns["crash_state_files"](self.profiles)
        with open(history) as fh:
            self.assertIn("A101", fh.read())
        self.assertTrue(os.path.isfile(old))

    def test_leftover_marker_is_moved_and_never_comes_back(self):
        """The marker is deleted after every export call; a COPY would be
        re-adopted from the old folder each time and report the same crash."""
        old = os.path.join(self.profiles, "_export_crash_marker.json")
        self.write(old, '{"sheet": "A102", "format": "PDF"}')
        marker, _history = self.ns["crash_state_files"](self.profiles)
        with open(marker) as fh:
            self.assertIn("A102", fh.read())
        self.assertFalse(os.path.exists(old))
        os.remove(marker)                               # _clear_crash_marker
        marker, _history = self.ns["crash_state_files"](self.profiles)
        self.assertFalse(os.path.exists(marker))

    def test_window_uses_the_helper_and_keeps_profiles_in_documents(self):
        src = _source(BATCHOUT)
        self.assertIn("crash_state_files(self.profiles_folder)", src)
        self.assertIn("'Documents', 'T3Lab_BatchOut_Profiles'", src)
        self.assertNotIn("os.path.join(self.profiles_folder, '_export_crash", src)
        self.assertNotIn("'Documents', 'T3Lab_Diagnostics', '_bad_geometry.json'", src)
        # writers make the breadcrumb folder, not the profiles folder
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "ExportManagerWindow")
        for name in ("_write_crash_history", "_write_crash_marker"):
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertNotIn("profiles_folder", ast.unparse(fn), name)

    def test_safe_mode_reads_the_findings_through_the_helper(self):
        tree = ast.parse(_source(BATCHOUT))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "ExportManagerWindow")
        fn = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_bad_geometry_ids")
        self.assertIn("bad_geometry_file()", ast.unparse(fn))


class DiagnosticsBothSides(_TempHome):
    def _check(self):
        stub = {"os": os, "user_data_path": user_data_path}
        return _lift(BADGEOM, BADGEOM_NAMES, dict(stub))

    def test_scan_files_live_in_appdata(self):
        ns = self._check()
        self.assertEqual(ns["DIAG_FOLDER"], self.user("diagnostics"))
        for key, name in (("MARKER_FILE", "_geometry_scan_marker.json"),
                          ("PROGRESS_FILE", "_geometry_scan_progress.json"),
                          ("FINDINGS_FILE", "_bad_geometry.json")):
            self.assertEqual(ns[key], self.user("diagnostics", name), key)

    def test_writer_and_reader_agree(self):
        check = self._check()
        batchout = _lift(BATCHOUT, BATCHOUT_NAMES, {"os": os, "user_data_path": user_data_path})
        self.assertEqual(check["FINDINGS_FILE"], batchout["bad_geometry_file"]())
        self.assertEqual(check["LEGACY_DIAG_FOLDER"], batchout["LEGACY_DIAG_FOLDER"])

    def test_old_findings_copied_transient_files_moved(self):
        legacy = os.path.join(self.home, "Documents", "T3Lab_Diagnostics")
        self.write(os.path.join(legacy, "_bad_geometry.json"), '{"doc": "M1"}')
        self.write(os.path.join(legacy, "_geometry_scan_progress.json"), '{"doc": "M1"}')
        self.write(os.path.join(legacy, "_geometry_scan_marker.json"), '{"element_id": 7}')
        ns = self._check()
        for key in ("FINDINGS_FILE", "PROGRESS_FILE", "MARKER_FILE"):
            self.assertTrue(os.path.isfile(ns[key]), key)
        self.assertTrue(os.path.isfile(os.path.join(legacy, "_bad_geometry.json")))
        self.assertFalse(os.path.exists(os.path.join(legacy, "_geometry_scan_progress.json")))
        self.assertFalse(os.path.exists(os.path.join(legacy, "_geometry_scan_marker.json")))
        # a completed scan clears marker + progress: they must stay cleared
        os.remove(ns["MARKER_FILE"])
        os.remove(ns["PROGRESS_FILE"])
        ns = self._check()
        self.assertFalse(os.path.exists(ns["MARKER_FILE"]))
        self.assertFalse(os.path.exists(ns["PROGRESS_FILE"]))

    def test_no_documents_path_left_in_the_check(self):
        src = _source(BADGEOM)
        for pattern in (r"(?m)^DIAG_FOLDER = os\.path\.join\(os\.path\.expanduser",
                        r"os\.path\.join\(DIAG_FOLDER, '_"):
            self.assertIsNone(re.search(pattern, src), pattern)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
