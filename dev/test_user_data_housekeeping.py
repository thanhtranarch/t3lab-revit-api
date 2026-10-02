# -*- coding: utf-8 -*-
"""
Per-user data leaves the extension folder, and every append-only writer is
bounded.

Run:  python3 dev/test_user_data_housekeeping.py
Exit 0 = all pass. unittest, pure CPython 3: no Revit, no WPF.

The extension is a shared, PUBLIC git clone. Tools used to write their state
next to the scripts (AutoJoin rules, IFC-SG configs and reports, chat
transcripts, assistant memory, learned patterns, 👍/👎 votes, the tool
registry) and several logs/archives/caches only ever grew. These tests pin:

  * every moved writer resolves under %APPDATA%\\T3LabAI (APPDATA is pointed
    at a temp dir here), never under T3Lab.extension;
  * the old in-extension file is carried over once, never overwritten, never
    copied back after the user deleted it;
  * each pruning rule deletes only what it should (age, newest-N, total size,
    files from the last hour protected, unprocessed teaching sessions kept);
  * the write-only logs are gone, gated, capped or de-duplicated.

Modules that need Revit/WPF to import are tested the way the other suites do
it: the helpers are pulled out of the source with `ast` and run in a namespace
with the few globals they use.
"""
from __future__ import unicode_literals

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')
LIB = os.path.join(EXT, 'lib')
sys.path.insert(0, LIB)

# Sandbox %APPDATA% BEFORE anything resolves a user path.
_APPDATA_ROOT = tempfile.mkdtemp(prefix='t3lab_userdata_')
os.environ['APPDATA'] = _APPDATA_ROOT

from core import housekeeping as hk          # noqa: E402

DAY = 86400.0


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _write(path, text=u'x', age_days=None, size=None):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(text if size is None else u'x' * size)
    if age_days is not None:
        stamp = time.time() - age_days * DAY
        os.utime(path, (stamp, stamp))
    return path


def _functions(src_path, names, extra=None):
    """exec the named top-level functions of a source file in a namespace."""
    src = _read(src_path)
    tree = ast.parse(src)
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    found = set(n.name for n in nodes)
    missing = set(names) - found
    if missing:
        raise AssertionError('not found in {}: {}'.format(src_path, missing))
    ns = {'os': os, 'io': io, 'json': json, 'sys': sys}
    ns.update(extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), src_path, 'exec'), ns)
    return ns


class _Sandbox(unittest.TestCase):
    """Fresh APPDATA + a fake 'extension' folder per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='t3lab_hk_')
        self.appdata = os.path.join(self.tmp, 'appdata')
        self.ext = os.path.join(self.tmp, 'extension')
        os.makedirs(self.appdata)
        os.makedirs(self.ext)
        os.environ['APPDATA'] = self.appdata
        self.t3 = os.path.join(self.appdata, 'T3LabAI')
        hk.forget()

    def tearDown(self):
        os.environ['APPDATA'] = _APPDATA_ROOT
        hk.forget()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertUnderAppdata(self, path):
        self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(self.t3)),
                        path)
        self.assertNotIn('T3Lab.extension', path)


# ─── core.housekeeping ────────────────────────────────────────────────────────

class PlanPruneTests(unittest.TestCase):
    NOW = 1000000000.0

    def e(self, age_days, size=1, name=None):
        return (self.NOW - age_days * DAY, size, name or 'f{}'.format(age_days))

    def test_age_rule(self):
        doomed = hk.plan_prune([self.e(1), self.e(31), self.e(40)],
                               max_age_days=30, now=self.NOW)
        self.assertEqual(sorted(doomed), ['f31', 'f40'])

    def test_keep_newest(self):
        entries = [self.e(d) for d in range(1, 8)]
        doomed = hk.plan_prune(entries, keep_newest=3, now=self.NOW)
        self.assertEqual(sorted(doomed), ['f4', 'f5', 'f6', 'f7'])

    def test_total_size_drops_oldest_first(self):
        entries = [self.e(1, 100), self.e(2, 100), self.e(3, 100)]
        doomed = hk.plan_prune(entries, max_total_bytes=150, now=self.NOW)
        self.assertEqual(sorted(doomed), ['f2', 'f3'])

    def test_recent_files_are_protected_from_every_rule(self):
        fresh = (self.NOW - 60, 10 ** 9, 'fresh')       # one minute old, 1 GB
        doomed = hk.plan_prune([fresh, self.e(2, 1)], max_age_days=0,
                               keep_newest=0, max_total_bytes=1,
                               min_age_s=3600, now=self.NOW)
        self.assertEqual(doomed, ['f2'])


class PruneFilesTests(_Sandbox):

    def test_pool_of_folders_and_empty_dirs(self):
        a = os.path.join(self.tmp, 'a')
        b = os.path.join(self.tmp, 'b')
        old = _write(os.path.join(a, '2026-01-01', 'old.png'), age_days=60)
        keep = _write(os.path.join(b, '2026-09-30', 'new.png'), age_days=2)
        removed = hk.prune_files([a, b], max_age_days=30, recursive=True)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(keep))
        # The emptied day folder goes once it is old enough; the root stays.
        os.utime(os.path.join(a, '2026-01-01'), (time.time() - 2 * DAY,) * 2)
        hk.prune_files([a, b], max_age_days=30, recursive=True)
        self.assertFalse(os.path.isdir(os.path.join(a, '2026-01-01')))
        self.assertTrue(os.path.isdir(a))

    def test_fresh_empty_folder_survives(self):
        a = os.path.join(self.tmp, 'a')
        os.makedirs(os.path.join(a, 'today'))
        hk.prune_files(a, max_age_days=1, recursive=True)
        self.assertTrue(os.path.isdir(os.path.join(a, 'today')),
                        'today\'s attachments folder is made before its file')

    def test_suffix_filter(self):
        d = os.path.join(self.tmp, 'd')
        log = _write(os.path.join(d, 'a.md'), age_days=200)
        other = _write(os.path.join(d, 'a.txt'), age_days=200)
        hk.prune_files(d, max_age_days=90, suffixes=('.md',))
        self.assertFalse(os.path.exists(log))
        self.assertTrue(os.path.exists(other))

    def test_missing_folder_is_not_an_error(self):
        self.assertEqual(hk.prune_files(os.path.join(self.tmp, 'nope'),
                                        max_age_days=1), 0)

    def test_run_once(self):
        calls = []
        self.assertEqual(hk.run_once('k', lambda: calls.append(1) or 'r'), 'r')
        self.assertIsNone(hk.run_once('k', lambda: calls.append(2)))
        self.assertEqual(calls, [1])
        self.assertIsNone(hk.run_once('boom', lambda: 1 / 0), 'never raises')


class ImportLegacyFolderTests(_Sandbox):

    def test_copies_once_never_overwrites_never_resurrects(self):
        src = os.path.join(self.ext, 'chat_history')
        dst = os.path.join(self.t3, 'chat_history')
        _write(os.path.join(src, 'a.json'), u'old-a')
        _write(os.path.join(src, 'b.json'), u'old-b')
        _write(os.path.join(dst, 'b.json'), u'new-b')
        self.assertEqual(hk.import_legacy_folder(src, dst, ('.json',)), 1)
        self.assertEqual(_read(os.path.join(dst, 'a.json')), u'old-a')
        self.assertEqual(_read(os.path.join(dst, 'b.json')), u'new-b')
        os.remove(os.path.join(dst, 'a.json'))            # the user deletes it
        self.assertEqual(hk.import_legacy_folder(src, dst, ('.json',)), 0)
        self.assertFalse(os.path.exists(os.path.join(dst, 'a.json')))
        self.assertTrue(os.path.exists(os.path.join(src, 'a.json')),
                        'the legacy folder is left untouched')


# ─── B1: writers that wrote INTO the extension ────────────────────────────────

class AutoJoinRulesTests(_Sandbox):

    def _ns(self):
        legacy = os.path.join(self.ext, 'join_rules.json')

        class _Log(object):
            def warning(self, *_a):
                pass
        return legacy, _functions(
            os.path.join(LIB, 'GUI', 'AutoJoinDialog.py'),
            ['_rules_file', 'save_rules_to_file', 'load_rules_from_file'],
            {'_LEGACY_RULES_FILE': legacy, 'logger': _Log(),
             'DEFAULT_RULES': [{'priority': 'Walls', 'join_with': 'Floors'}]})

    def test_rules_live_in_appdata_and_legacy_is_adopted(self):
        legacy, ns = self._ns()
        _write(legacy, json.dumps([{'priority': 'A', 'join_with': 'B'}]))
        path = ns['_rules_file']()
        self.assertUnderAppdata(path)
        self.assertEqual(ns['load_rules_from_file'](),
                         [{'priority': 'A', 'join_with': 'B'}])
        ns['save_rules_to_file']([{'priority': 'C', 'join_with': 'D'}])
        self.assertEqual(json.loads(_read(path))[0]['priority'], 'C')
        self.assertEqual(json.loads(_read(legacy))[0]['priority'], 'A',
                         'the extension copy is never written again')

    def test_no_rules_file_in_the_extension(self):
        src = _read(os.path.join(LIB, 'GUI', 'AutoJoinDialog.py'))
        self.assertNotIn('\nRULES_FILE =', src)


class IfcsgConfigTests(_Sandbox):
    SRC = os.path.join(LIB, 'GUI', 'IFCSGDialog.py')
    NAMES = ['_hidden_configs_file', '_user_configs_dir', '_config_names',
             '_hidden_configs', '_save_hidden_configs', 'list_config_names',
             'config_path', 'unhide_config', 'delete_config',
             '_default_report_dir']

    def setUp(self):
        _Sandbox.setUp(self)
        self.ns = _functions(self.SRC, self.NAMES,
                             {'_HIDDEN_CONFIGS_FILE': 'hidden_configs.json'})
        self.builtin = os.path.join(self.ext, 'configs')
        _write(os.path.join(self.builtin, 'Shipped.json'), u'{}')
        self.user = self.ns['_user_configs_dir']()

    def test_user_configs_live_in_appdata(self):
        self.assertUnderAppdata(self.user)
        self.assertTrue(os.path.isdir(self.user))

    def test_list_load_delete_hide_unhide(self):
        ns = self.ns
        _write(os.path.join(self.user, 'Mine.json'), u'{}')
        self.assertEqual(ns['list_config_names'](self.builtin, self.user),
                         ['Mine', 'Shipped'])
        self.assertEqual(ns['config_path'](self.builtin, self.user, 'Shipped'),
                         os.path.join(self.builtin, 'Shipped.json'))
        ns['delete_config'](self.builtin, self.user, 'Shipped')
        self.assertTrue(os.path.isfile(os.path.join(self.builtin, 'Shipped.json')),
                        'a shipped config is never deleted from the extension')
        self.assertEqual(ns['list_config_names'](self.builtin, self.user), ['Mine'])
        ns['delete_config'](self.builtin, self.user, 'Mine')
        self.assertEqual(ns['list_config_names'](self.builtin, self.user), [])
        # Importing a config under the hidden shipped name shows it again,
        # and the user's copy wins.
        _write(os.path.join(self.user, 'Shipped.json'), u'{"mine": 1}')
        ns['unhide_config'](self.user, 'Shipped')
        self.assertEqual(ns['list_config_names'](self.builtin, self.user), ['Shipped'])
        self.assertEqual(ns['config_path'](self.builtin, self.user, 'Shipped'),
                         os.path.join(self.user, 'Shipped.json'))

    def test_window_never_creates_folders_in_the_extension(self):
        src = _read(self.SRC)
        self.assertNotIn('"reports")', src)
        self.assertNotIn('self.reports_dir', src)
        self.assertIn('self.configs_dir = _user_configs_dir()', src)

    def test_report_dir_is_never_the_extension(self):
        class _Doc(object):
            PathName = os.path.join(self.tmp, 'job', 'Tower.rvt')
        os.makedirs(os.path.join(self.tmp, 'job'))
        got = self.ns['_default_report_dir'](_Doc())
        self.assertEqual(got, os.path.join(self.tmp, 'job'),
                         'no System here: falls back to the model folder')


class ChatHistoryTests(_Sandbox):
    SRC = os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py')

    def _ns(self, legacy):
        return _functions(self.SRC, ['_chat_history_dir', '_prune_archived_sessions',
                                     '_history_file'],
                          {'_LEGACY_CHAT_HISTORY_DIR': legacy,
                           'MAX_ARCHIVED_SESSIONS': 100})

    def test_history_and_sessions_move_to_appdata_with_one_time_import(self):
        legacy = os.path.join(self.ext, 'chat_history')
        _write(os.path.join(legacy, 'Tower.json'), u'{"messages": []}')
        _write(os.path.join(legacy, 'sessions', 'Tower_20260101_000000.json'), u'{}')
        ns = self._ns(legacy)
        path = ns['_history_file']('Tower')
        self.assertUnderAppdata(path)
        self.assertTrue(os.path.isfile(path), 'legacy transcript carried over')
        sessions = ns['_chat_history_dir']('sessions')
        self.assertUnderAppdata(sessions)
        self.assertTrue(os.path.isfile(
            os.path.join(sessions, 'Tower_20260101_000000.json')))
        # A cleared history is not resurrected from the legacy folder.
        os.remove(path)
        hk.forget()
        ns['_history_file']('Tower')
        self.assertFalse(os.path.isfile(path))

    def test_archived_sessions_are_capped(self):
        ns = self._ns(os.path.join(self.ext, 'none'))
        sessions = ns['_chat_history_dir']('sessions')
        for i in range(105):
            _write(os.path.join(sessions, 'Doc_{:03d}.json'.format(i)),
                   age_days=200 - i)
        ns['_prune_archived_sessions'](sessions)
        left = sorted(f for f in os.listdir(sessions) if f.endswith('.json'))
        self.assertEqual(len(left), 100)
        self.assertEqual(left[0], 'Doc_005.json', 'the oldest five went')

    def test_no_writer_left_in_the_extension(self):
        src = _read(self.SRC)
        self.assertEqual(src.count("os.path.join(lib_dir, 'config', 'chat_history'"), 1,
                         'only the legacy constant may name the old folder')


class AssistantStoresTests(_Sandbox):

    def _check(self, module, attr, fn_name, fname):
        legacy = os.path.join(self.ext, fname)
        _write(legacy, u'{"from": "legacy"}')
        old = getattr(module, attr)
        setattr(module, attr, legacy)
        try:
            path = getattr(module, fn_name)()
        finally:
            setattr(module, attr, old)
        self.assertUnderAppdata(path)
        self.assertEqual(os.path.basename(os.path.dirname(path)), 'assistant')
        self.assertEqual(json.loads(_read(path)), {'from': 'legacy'})

    def test_memory(self):
        from Intelligence import assistant_memory as m
        self._check(m, '_LEGACY_MEMORY_FILE', '_memory_file', 'assistant_memory.json')

    def test_learned_patterns(self):
        from Intelligence import t3lab_assistant as t
        self._check(t, '_LEGACY_PATTERNS_FILE', '_patterns_file', 'learned_patterns.json')

    def test_feedback(self):
        from Intelligence import feedback as f
        self._check(f, '_LEGACY_FEEDBACK_FILE', '_feedback_file', 'assistant_feedback.json')

    def test_tool_registry(self):
        from Services import tool_discovery as td
        old = td.REGISTRY_FILE
        td.REGISTRY_FILE = None
        try:
            self.assertUnderAppdata(td._registry_file())
            td.REGISTRY_FILE = os.path.join(self.tmp, 'reg.json')
            self.assertEqual(td._registry_file(), td.REGISTRY_FILE)
        finally:
            td.REGISTRY_FILE = old


# ─── B2: unbounded / write-only logs ──────────────────────────────────────────

class TelemetryRetentionTests(_Sandbox):

    def test_prune_covers_graph_traces_and_runs_once(self):
        from Intelligence import telemetry as tm
        d = tm.telemetry_dir()
        self.assertUnderAppdata(d)
        old_turn = _write(os.path.join(d, '2026-01-01.jsonl'), age_days=60)
        old_graph = _write(os.path.join(d, 'graph', '2026-01-01.jsonl'), age_days=60)
        new_graph = _write(os.path.join(d, 'graph', '2026-09-30.jsonl'), age_days=2)
        del tm._PRUNED[:]
        self.assertTrue(tm.record({'k': 1}))
        self.assertFalse(os.path.exists(old_turn))
        self.assertFalse(os.path.exists(old_graph))
        self.assertTrue(os.path.exists(new_graph))
        again = _write(os.path.join(d, '2026-01-02.jsonl'), age_days=60)
        tm.record({'k': 2})
        self.assertTrue(os.path.exists(again), 'pruned once per session only')

    def test_explicit_path_never_prunes(self):
        from Intelligence import telemetry as tm
        old = _write(os.path.join(tm.telemetry_dir(), '2026-01-01.jsonl'), age_days=60)
        del tm._PRUNED[:]
        tm.record({'k': 1}, path=os.path.join(self.tmp, 'x.jsonl'))
        self.assertTrue(os.path.exists(old))

    def test_graph_record_triggers_the_same_prune(self):
        from Intelligence import telemetry as tm
        from Intelligence.graph import observability as ob
        old = _write(os.path.join(tm.telemetry_dir(), 'graph', '2026-01-01.jsonl'),
                     age_days=60)
        del tm._PRUNED[:]
        self.assertTrue(ob.record({'goal': 'x'}))
        self.assertFalse(os.path.exists(old))


class ProjectStoreRetentionTests(_Sandbox):

    def test_attachments_and_activity_logs(self):
        from config.project_store import ProjectStore
        ps = ProjectStore()
        root_att = os.path.join(self.t3, 'attachments')
        proj_att = os.path.join(self.t3, 'projects', 'p1', 'attachments')
        old = _write(os.path.join(root_att, '2026-01-01', 'old.pdf'), age_days=40)
        big1 = _write(os.path.join(proj_att, '2026-09-20', 'a.bin'), age_days=12, size=600)
        big2 = _write(os.path.join(root_att, '2026-09-25', 'b.bin'), age_days=7, size=600)
        fresh = _write(os.path.join(proj_att, 'today', 'pasted.png'), size=600)
        old_log = _write(os.path.join(self.t3, 'logs', '2026-01-01.md'), age_days=100)
        new_log = _write(os.path.join(self.t3, 'projects', 'p1', 'logs',
                                      '2026-09-01.md'), age_days=30)
        cap = ProjectStore.ATTACHMENTS_MAX_TOTAL_BYTES
        ProjectStore.ATTACHMENTS_MAX_TOTAL_BYTES = 1300
        try:
            out = ps.prune_storage()
        finally:
            ProjectStore.ATTACHMENTS_MAX_TOTAL_BYTES = cap
        self.assertFalse(os.path.exists(old), 'older than 30 days')
        self.assertFalse(os.path.exists(big1), 'oldest first over the size cap')
        self.assertTrue(os.path.exists(big2))
        self.assertTrue(os.path.exists(fresh), 'last hour is protected')
        self.assertFalse(os.path.exists(old_log))
        self.assertTrue(os.path.exists(new_log))
        self.assertEqual(out, {'attachments': 2, 'logs': 1})

    def test_hooked_once_into_the_worker_writers(self):
        from config.project_store import ProjectStore
        ps = ProjectStore()
        old = _write(os.path.join(self.t3, 'logs', '2026-01-01.md'), age_days=100)
        self.assertTrue(ps.append_activity(u'hello'))
        self.assertFalse(os.path.exists(old))
        again = _write(os.path.join(self.t3, 'logs', '2026-01-02.md'), age_days=100)
        ps.append_activity(u'again')
        self.assertTrue(os.path.exists(again), 'once per session')


class TeachingSessionCapTests(_Sandbox):

    def test_processed_capped_unprocessed_kept(self):
        from core import teaching
        d = teaching.session_dir()
        self.assertUnderAppdata(d)
        for i in range(8):
            _write(os.path.join(d, 's{}.jsonl.done'.format(i)), age_days=10 - i)
        nolabel = _write(os.path.join(d, 'n.jsonl.nolabel'), age_days=20)
        pending = _write(os.path.join(d, 'pending.jsonl'), age_days=400)
        self.assertEqual(teaching.prune_sessions(d, keep=3), 5)
        left = sorted(os.listdir(d))
        self.assertIn('pending.jsonl', left, 'the miner still needs it')
        self.assertTrue(os.path.exists(nolabel), 'its only copy: never pruned')
        self.assertEqual(sorted(f for f in left if f.endswith('.done')),
                         ['s5.jsonl.done', 's6.jsonl.done', 's7.jsonl.done'])
        self.assertTrue(os.path.exists(pending))

    def test_append_prunes_its_folder_once(self):
        from core import teaching
        d = os.path.join(self.tmp, 'sessions')
        for i in range(3):
            _write(os.path.join(d, 's{}.jsonl.done'.format(i)), age_days=10 + i)
        old_cap = teaching.MAX_PROCESSED_SESSIONS
        teaching._PRUNED_DIRS.discard(d)
        teaching.MAX_PROCESSED_SESSIONS = 1
        try:
            self.assertTrue(teaching.append_step_line(
                os.path.join(d, 'new.jsonl'), u'goal', {'tool': 'x'}))
            _write(os.path.join(d, 'late.jsonl.done'), age_days=1)
            teaching.append_step_line(os.path.join(d, 'new.jsonl'), u'goal',
                                      {'tool': 'y'})
        finally:
            teaching.MAX_PROCESSED_SESSIONS = old_cap
        self.assertEqual(sorted(f for f in os.listdir(d) if f.endswith('.done')),
                         ['late.jsonl.done', 's0.jsonl.done'],
                         'pruned on the first write only')
        self.assertTrue(os.path.exists(os.path.join(d, 'new.jsonl')))


class WriteOnlyLogTests(_Sandbox):

    def test_model_setup_log_writer_is_gone(self):
        settings = _read(os.path.join(LIB, 'config', 'settings.py'))
        router = _read(os.path.join(LIB, 'Intelligence', 'llm_router.py'))
        self.assertNotIn('model_setup.log', settings)
        self.assertNotIn('log_model_usage', settings + router)

    def test_pane_log_is_off_by_default(self):
        log = os.path.join(self.tmp, 'pane.log')
        src = os.path.join(LIB, 'GUI', 'AssistantPaneControl.py')
        off = _functions(src, ['_log_pane'], {'_LOG_ENABLED': False,
                                              '_LOG_PATH': log,
                                              '_LOG_MAX_BYTES': 64})
        off['_log_pane'](u'hello')
        self.assertFalse(os.path.exists(log))
        on = _functions(src, ['_log_pane'], {'_LOG_ENABLED': True,
                                             '_LOG_PATH': log,
                                             '_LOG_MAX_BYTES': 64})
        for _ in range(10):
            on['_log_pane'](u'a line long enough to pass the cap')
        self.assertTrue(os.path.getsize(log) < 64 + 100, 'capped')


class StartupLogTests(_Sandbox):

    def _ns(self):
        path = os.path.join(EXT, 'startup.py')
        body = _read(path).rsplit('main()', 1)[0]
        ns = {'__name__': 'startup_probe', '__file__': path}
        exec(compile(body, path, 'exec'), ns)
        return ns

    def test_identical_entries_are_written_once(self):
        ns = self._ns()
        log = os.path.join(self.t3, 'engine_check.log')
        ns['_log'](u'pyRevit reload patch not applied (unsupported): x')
        ns['_log'](u'pyRevit reload patch not applied (unsupported): x')
        self.assertEqual(_read(log).count(u'not applied'), 1)
        ns['_log'](u'Revit 2030 is outside the supported range.\n\nSecond paragraph.')
        ns['_log'](u'Revit 2030 is outside the supported range.\n\nSecond paragraph.')
        ns['_log'](u'pyRevit reload patch not applied (unsupported): x')
        text = _read(log)
        self.assertEqual(text.count(u'outside the supported range'), 1)
        self.assertEqual(text.count(u'not applied'), 2, 'a state change logs again')

    def test_rotation(self):
        ns = self._ns()
        ns['LOG_MAX_BYTES'] = 200
        log = os.path.join(self.t3, 'engine_check.log')
        for i in range(20):
            ns['_log'](u'entry number {} with some padding text'.format(i))
        self.assertTrue(os.path.getsize(log) < 400)
        self.assertTrue(os.path.exists(log + '.1'))


class BootstrapLogTests(_Sandbox):

    def test_report_written_once_per_state(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            '_t3_bootstrap_hk_test', os.path.join(LIB, '_cpython_bootstrap.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.ENGINE_DIAGNOSIS.update({'stdlib_missing': ['json'], 'status': 'x'})
        log = os.path.join(self.t3, 'bootstrap.log')
        mod._log_diagnosis()
        mod._log_diagnosis()
        self.assertEqual(_read(log).count('MISSING stdlib'), 1)
        mod._LOGGED_REPORTS.clear()          # a new session, same broken state
        mod._log_diagnosis()
        self.assertEqual(_read(log).count('MISSING stdlib'), 1,
                         'identical to the last entry on disk')
        mod.ENGINE_DIAGNOSIS['status'] = 'changed'
        mod._log_diagnosis()
        self.assertEqual(_read(log).count('MISSING stdlib'), 2)


class TempCacheTests(_Sandbox):

    def test_osm_tile_cache_is_pruned(self):
        from Snippets import _parcel_map as pm
        old_dir, old_cap = pm.DISK_CACHE_DIR, pm.DISK_CACHE_MAX_TILES
        pm.DISK_CACHE_DIR = os.path.join(self.tmp, 'tiles')
        pm.DISK_CACHE_MAX_TILES = 2
        try:
            expired = _write(os.path.join(pm.DISK_CACHE_DIR, '18', '1', '1.png'),
                             age_days=pm.DISK_CACHE_DAYS + 1)
            part = _write(os.path.join(pm.DISK_CACHE_DIR, '18', '1', '2.png.9.part'),
                          age_days=2)
            keep = [_write(os.path.join(pm.DISK_CACHE_DIR, '18', '2', '{}.png'.format(i)),
                           age_days=1 + i) for i in range(3)]
            self.assertEqual(pm.prune_disk_cache(), 3)
            self.assertFalse(os.path.exists(expired))
            self.assertFalse(os.path.exists(part))
            self.assertEqual([os.path.exists(p) for p in keep], [True, True, False])
        finally:
            pm.DISK_CACHE_DIR, pm.DISK_CACHE_MAX_TILES = old_dir, old_cap

    def test_view_shots_are_deleted_after_use(self):
        src = _read(os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py'))
        i_build = src.find('user_content, _vision_files)')
        i_remove = src.find('os.remove(shot)')
        self.assertTrue(0 < i_build < i_remove, 'deleted once it is base64')
        self.assertIn("'assistant.viewshots'", src)


# ─── B3: side effects next to the model / in Revit's settings ─────────────────

class StoredDataTests(_Sandbox):

    def test_stored_data_moves_to_appdata_with_legacy_read(self):
        import core.server as S
        model_dir = os.path.join(self.tmp, 'Jobs', 'A')
        doc_path = os.path.join(model_dir, 'Tower.rvt')
        legacy = _write(os.path.join(model_dir, 'T3Lab_AI_Data', 'room_data.json'),
                        u'{"rooms": [1]}')
        path = S._stored_data_file(doc_path, 'Tower', 'room_data.json')
        self.assertUnderAppdata(path)
        self.assertEqual(json.loads(_read(path)), {'rooms': [1]})
        self.assertTrue(os.path.exists(legacy), 'the old file is left alone')

    def test_same_name_in_two_folders_never_collides(self):
        import core.server as S
        a = S._stored_data_key(os.path.join('C:', 'A', 'Tower.rvt'), 'Tower')
        b = S._stored_data_key(os.path.join('C:', 'B', 'Tower.rvt'), 'Tower')
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('Tower_') and b.startswith('Tower_'))
        self.assertTrue(S._stored_data_key('', 'Untitled 1').startswith('Untitled_1_'))

    def test_store_tools_no_longer_write_next_to_the_model(self):
        src = _read(os.path.join(LIB, 'core', 'server.py'))
        for tool in ('store_project_data', 'store_room_data', 'query_stored_data'):
            start = src.index("elif tool_name == '{}':".format(tool))
            branch = src[start:src.index('elif tool_name ==', start + 10)]
            self.assertIn('_stored_data_file(', branch, tool)
            self.assertNotIn("'T3Lab_AI_Data'", branch, tool)

    def test_shared_parameter_setting_is_restored(self):
        src = _read(os.path.join(LIB, 'core', 'server.py'))
        start = src.index("elif tool_name == 'create_project_parameter':")
        branch = src[start:src.index('elif tool_name ==', start + 10)]
        self.assertIn('original_sp = sp_path or', branch)
        i_finally = branch.index('finally:')
        self.assertIn('app.SharedParametersFilename = original_sp',
                      branch[i_finally:])


if __name__ == '__main__':
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(_APPDATA_ROOT, ignore_errors=True)
