# -*- coding: utf-8 -*-
"""
Tests for lib/pyrevit_patches.py — the opt-in fix for pyRevit reloads leaving
every '#! python3' tool failing with "This property must be set before runtime
is initialized" on Revit 2025+.

Every file operation runs on a temporary copy; the installed pyRevit is only
ever READ (to prove the patch anchor still matches it).

Run: python dev/test_pyrevit_patches.py
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import pyrevit_patches as pp       # noqa: E402

HEADER = u"# -*- coding: utf-8 -*-\n\"\"\"stand-in sessionmgr\"\"\"\nimport sys\n\n\n"
FOOTER = u"\n\ndef load_session():\n    return _clear_running_engines()\n"


def _installed():
    paths = pp.sessionmgr_paths()
    return paths[0] if paths else None


class _Tmp(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sessionmgr.py")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text, newline=u"\n"):
        with io.open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace(u"\n", newline))

    def raw(self, path=None):
        with io.open(path or self.path, "rb") as fh:
            return fh.read()


class AnchorTests(unittest.TestCase):

    def test_anchor_matches_the_installed_pyrevit(self):
        path = _installed()
        if path is None:
            self.skipTest("no pyRevit clone on this machine")
        self.assertIn(pp.status(path), (pp.MISSING, pp.ALREADY),
                      "pyRevit's _clear_running_engines changed; update ORIGINAL/PATCHED")

    def test_patched_block_compiles(self):
        compile(pp.PATCHED, "<patched>", "exec")


class ApplyRestoreTests(_Tmp):

    def test_apply_is_idempotent_and_keeps_crlf(self):
        self.write(HEADER + pp.ORIGINAL + FOOTER, newline=u"\r\n")
        before = self.raw()
        self.assertEqual(pp.ensure(self.path), (pp.APPLIED, self.path))
        after = self.raw()
        self.assertIn(pp.MARKER.encode("utf-8"), after)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))     # no bare LF
        self.assertEqual(self.raw(self.path + pp.BACKUP_SUFFIX), before)
        self.assertEqual(pp.ensure(self.path)[0], pp.ALREADY)
        self.assertEqual(self.raw(), after)
        compile(after.decode("utf-8"), self.path, "exec")

    def test_restore_gives_back_the_original_bytes(self):
        self.write(HEADER + pp.ORIGINAL + FOOTER, newline=u"\r\n")
        before = self.raw()
        pp.ensure(self.path)
        self.assertEqual(pp.restore(self.path)[0], pp.APPLIED)
        self.assertEqual(self.raw(), before)
        self.assertEqual(pp.restore(self.path)[0], pp.ALREADY)

    def test_real_pyrevit_file_round_trips(self):
        path = _installed()
        if path is None:
            self.skipTest("no pyRevit clone on this machine")
        shutil.copyfile(path, self.path)
        if pp.status(self.path) == pp.ALREADY:
            self.skipTest("installed pyRevit is already patched")
        before = self.raw()
        self.assertEqual(pp.ensure(self.path)[0], pp.APPLIED)
        compile(self.raw().decode("utf-8"), self.path, "exec")
        pp.restore(self.path)
        self.assertEqual(self.raw(), before)

    def test_unknown_pyrevit_version_is_left_untouched(self):
        changed = pp.ORIGINAL.replace("close_others(all_open_outputs=True)",
                                      "close_others()")
        self.write(HEADER + changed + FOOTER)
        before = self.raw()
        self.assertEqual(pp.ensure(self.path)[0], pp.UNSUPPORTED)
        self.assertEqual(self.raw(), before)
        self.assertFalse(os.path.exists(self.path + pp.BACKUP_SUFFIX))

    def test_missing_file_reports_error(self):
        self.assertEqual(pp.ensure(os.path.join(self.dir, "nope.py"))[0], pp.ERROR)


# ── the patched function, run against stand-ins for pyRevit's runtime ─────────

class _Type(object):
    def __init__(self, name):
        self.Name = name


class _Engine(object):
    def __init__(self, type_name):
        self._type = _Type(type_name)
        self.shut_down = False

    def GetType(self):
        return self._type


class _EngineDict(dict):
    """Dictionary<string, object> as IronPython shows it."""

    @property
    def Keys(self):
        return list(self.keys())

    def Remove(self, key):
        del self[key]


class _Manager(object):
    def __init__(self, engines):
        self.EngineDict = _EngineDict(engines)
        self.excluded = None

    def ClearEngines(self, excludeEngine=None):
        # pyRevit 6.5.5: Shutdown every engine but the excluded one, then a new dict.
        self.excluded = excludeEngine
        for key, engine in self.EngineDict.items():
            if key != excludeEngine:
                engine.shut_down = True
        self.EngineDict = _EngineDict()


class PatchedBehaviourTests(unittest.TestCase):

    def _run(self, source, manager):
        ns = {
            "output": type("O", (), {"get_output": staticmethod(lambda: None)}),
            "runtime_types": type("RT", (), {"ScriptEngineManager": manager}),
            "EXEC_PARAMS": type("EP", (), {"engine_id": "IronPython-reload"}),
        }
        exec(compile(source, "<sessionmgr>", "exec"), ns)
        return ns["_clear_running_engines"]()

    def _engines(self):
        return {"CPython3123": _Engine("CPythonEngine"),
                "IronPython-2712": _Engine("IronPythonEngine"),
                "IronPython-reload": _Engine("IronPythonEngine")}

    def test_original_shuts_cpython_down(self):
        manager = _Manager(self._engines())
        cpython = manager.EngineDict["CPython3123"]
        self.assertTrue(self._run(pp.ORIGINAL, manager))
        self.assertTrue(cpython.shut_down)          # the bug
        self.assertNotIn("CPython3123", manager.EngineDict)

    def test_patched_keeps_cpython_cached_and_alive(self):
        manager = _Manager(self._engines())
        cpython = manager.EngineDict["CPython3123"]
        other = manager.EngineDict["IronPython-2712"]
        self.assertTrue(self._run(pp.PATCHED, manager))
        self.assertFalse(cpython.shut_down)
        self.assertIs(manager.EngineDict["CPython3123"], cpython)
        self.assertTrue(other.shut_down)            # everything else as before
        self.assertEqual(manager.excluded, "IronPython-reload")
        self.assertEqual(list(manager.EngineDict), ["CPython3123"])

    def test_patched_without_cpython_behaves_like_the_original(self):
        manager = _Manager({"IronPython-2712": _Engine("IronPythonEngine")})
        self.assertTrue(self._run(pp.PATCHED, manager))
        self.assertEqual(dict(manager.EngineDict), {})


def _startup_namespace():
    """startup.py's functions, without running its main()."""
    src_path = os.path.join(REPO, "T3Lab.extension", "startup.py")
    with io.open(src_path, encoding="utf-8") as fh:
        body = fh.read().rsplit("main()", 1)[0]
    ns = {"__name__": "startup_probe", "__file__": src_path}
    exec(compile(body, src_path, "exec"), ns)
    return ns


class StartupDetectorTests(unittest.TestCase):

    def test_detector_is_silent_outside_revit(self):
        # No .NET here: the detector must say False, never raise.
        self.assertFalse(_startup_namespace()["pythonnet_half_started"]())


class _PatchesAt(object):
    """pyrevit_patches, pointed at a temporary sessionmgr.py."""

    def __init__(self, path):
        self._path = path
        self.ensure = pp.ensure

    def sessionmgr_path(self):
        return self._path


class StartupAutoPatchTests(_Tmp):

    def setUp(self):
        _Tmp.setUp(self)
        self.ns = _startup_namespace()
        self._env = {k: os.environ.get(k) for k in ("APPDATA", "T3LAB_NO_PYREVIT_PATCH")}
        os.environ["APPDATA"] = self.dir                 # opt-out file lives here
        os.environ.pop("T3LAB_NO_PYREVIT_PATCH", None)
        self.write(HEADER + pp.ORIGINAL + FOOTER, newline=u"\r\n")

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _Tmp.tearDown(self)

    def test_missing_patch_is_applied_then_left_alone(self):
        patches = _PatchesAt(self.path)
        self.assertEqual(self.ns["auto_patch_pyrevit"](patches), (pp.APPLIED, self.path))
        self.assertEqual(self.ns["auto_patch_pyrevit"](patches)[0], pp.ALREADY)
        self.assertTrue(os.path.exists(self.path + pp.BACKUP_SUFFIX))

    def test_env_var_opts_out(self):
        os.environ["T3LAB_NO_PYREVIT_PATCH"] = "1"
        before = self.raw()
        self.assertEqual(self.ns["auto_patch_pyrevit"](_PatchesAt(self.path)), (None, "opted out"))
        self.assertEqual(self.raw(), before)

    def test_opt_out_file_opts_out(self):
        os.makedirs(os.path.join(self.dir, "T3LabAI"))
        io.open(os.path.join(self.dir, "T3LabAI", "pyrevit_patch.disabled"), "w").close()
        before = self.raw()
        self.assertEqual(self.ns["auto_patch_pyrevit"](_PatchesAt(self.path))[0], None)
        self.assertEqual(self.raw(), before)

    def test_unknown_pyrevit_is_never_touched(self):
        self.write(HEADER + pp.ORIGINAL.replace("return True", "return 1") + FOOTER)
        before = self.raw()
        self.assertEqual(self.ns["auto_patch_pyrevit"](_PatchesAt(self.path))[0], pp.UNSUPPORTED)
        self.assertEqual(self.raw(), before)

    def test_finds_the_patch_module_next_to_startup(self):
        self.assertIsNotNone(self.ns["_load_patches"]())


if __name__ == '__main__':
    unittest.main()
