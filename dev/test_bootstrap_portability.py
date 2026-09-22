# -*- coding: utf-8 -*-
"""
Tests that the extension installs and runs on a machine other than the
developer's.

Guards the regression that produced "Command Failure for External Command" for
every colleague: the pyRevit CPython engine was located by hardcoding the clone
name (pyRevit-Master / pyRevit) and the engine folder (CPY3123), copy-pasted
into 47 files. Any other layout got no Python 3 stdlib on sys.path and every
tool died on its first import.

Run: python dev/test_bootstrap_portability.py
"""
import io
import os
import sys
import unittest
import tempfile
import shutil
import importlib.util

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(REPO, 'T3Lab.extension')
BOOTSTRAP = os.path.join(EXT_DIR, 'lib', '_cpython_bootstrap.py')


def _load_bootstrap():
    """Load the shipped module under a private name, without importing lib/."""
    spec = importlib.util.spec_from_file_location('_t3_bootstrap_under_test',
                                                  BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EngineVersionParsing(unittest.TestCase):
    """CPY folder names and stdlib zips must map to the right Python version."""

    def setUp(self):
        self.mod = _load_bootstrap()

    def test_folder_names(self):
        cases = {
            'CPY3123': (3, 12),   # pyRevit 5.x — Python 3.12.3
            'CPY3132': (3, 13),
            'CPY387': (3, 8),     # pyRevit 4.8.x — Python 3.8.7
            'CPY385': (3, 8),
            'CPY3100': (3, 10),
        }
        for name, expected in cases.items():
            self.assertEqual(self.mod._engine_version('(missing)', name), expected,
                             'folder %s' % name)

    def test_stdlib_zip_wins_over_folder_name(self):
        tmp = tempfile.mkdtemp()
        try:
            # A deliberately misleading folder name; the zip is authoritative.
            io.open(os.path.join(tmp, 'python38.zip'), 'w').close()
            self.assertEqual(self.mod._engine_version(tmp, 'CPY3123'), (3, 8))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unparseable_name(self):
        self.assertIsNone(self.mod._engine_version('(missing)', 'CPY'))
        self.assertIsNone(self.mod._engine_version('(missing)', ''))


class CloneRootDerivation(unittest.TestCase):
    """A clone must be recognised by shape, never by name."""

    def setUp(self):
        self.mod = _load_bootstrap()

    def test_engine_path(self):
        self.assertEqual(
            self.mod._clone_root_from_path(r'D:\anything\my-clone\bin\cengines\CPY3123\Lib'),
            r'D:\anything\my-clone')

    def test_pyrevitlib_path(self):
        self.assertEqual(
            self.mod._clone_root_from_path(r'E:\pyrevit_5_custom\pyrevitlib'),
            r'E:\pyrevit_5_custom')

    def test_forward_slashes(self):
        self.assertEqual(
            self.mod._clone_root_from_path('D:/clone/bin/cengines/CPY387'),
            r'D:\clone')

    def test_unrelated_path(self):
        self.assertEqual(self.mod._clone_root_from_path(r'C:\Windows\System32'), '')
        self.assertEqual(self.mod._clone_root_from_path(''), '')


class DiscoveryFromRunningInterpreter(unittest.TestCase):
    """The clone is found through sys.path whatever it is called."""

    def setUp(self):
        self.mod = _load_bootstrap()
        self.tmp = tempfile.mkdtemp()
        # A clone with a name no hardcoded list would ever contain.
        self.engine = os.path.join(self.tmp, 'totally-custom-name',
                                   'bin', 'cengines', 'CPY3123')
        os.makedirs(os.path.join(self.engine, 'Lib'))
        io.open(os.path.join(self.engine, 'python312.zip'), 'w').close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_engine_found_via_syspath(self):
        saved = list(sys.path)
        sys.path.insert(0, os.path.join(self.engine, 'Lib'))
        try:
            found = self.mod.find_cpython_engines(refresh=True)
        finally:
            sys.path[:] = saved
        paths = [p for p, _ in found]
        self.assertIn(self.engine, paths,
                      'a clone named "totally-custom-name" must still be found')
        version = dict(found)[self.engine]
        self.assertEqual(version, (3, 12))

    def test_version_gate_blocks_mismatched_stdlib(self):
        """A 3.12 zip must never be injected into a non-3.12 interpreter.

        Mixing minor versions raises "bad magic number in 'json'", which is
        worse than having no stdlib injection at all.
        """
        saved_path = list(sys.path)
        sys.path.insert(0, os.path.join(self.engine, 'Lib'))
        try:
            self.mod.find_cpython_engines(refresh=True)
            self.mod._candidate_clone_roots(refresh=True)
            self.mod.init_cpython_paths()
            used = self.mod.ENGINE_DIAGNOSIS['engines_used']
            if sys.version_info[:2] == (3, 12):
                self.assertIn(self.engine, used)
            else:
                self.assertNotIn(self.engine, used)
                self.assertIn('no engine matches',
                              self.mod.ENGINE_DIAGNOSIS['status'])
        finally:
            sys.path[:] = saved_path

    def test_diagnosis_is_ascii(self):
        """The report is appended to a log and may reach a WPF dialog."""
        self.mod.init_cpython_paths()
        text = self.mod.describe_environment()
        self.assertEqual(text, text.encode('ascii', 'replace').decode('ascii'),
                         'describe_environment() must stay ASCII-safe')


class NoHardcodedEngineLayout(unittest.TestCase):
    """No shipped file may pin the developer's clone or engine folder."""

    # The bootstrap explains the history in its docstring and derives versions
    # from the folder name, so the strings legitimately appear there.
    EXEMPT = {
        os.path.join('lib', '_cpython_bootstrap.py'),
    }
    NEEDLES = ('pyRevit-Master', 'CPY3123', 'python312.zip')

    def test_extension_has_no_hardcoded_paths(self):
        offenders = []
        for root, dirs, files in os.walk(EXT_DIR):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '_archive')]
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, EXT_DIR)
                if rel in self.EXEMPT:
                    continue
                with io.open(full, encoding='utf-8') as f:
                    src = f.read()
                for lineno, line in enumerate(src.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue          # comments may explain the history
                    for needle in self.NEEDLES:
                        if needle in line:
                            offenders.append('%s:%d  %s' % (rel, lineno, stripped))
        self.assertEqual(
            offenders, [],
            'Engine discovery belongs in lib/_cpython_bootstrap.py only:\n  '
            + '\n  '.join(offenders))


if __name__ == '__main__':
    unittest.main(verbosity=2)
