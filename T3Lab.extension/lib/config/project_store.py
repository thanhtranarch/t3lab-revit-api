# -*- coding: utf-8 -*-
"""
ProjectStore — Claude-style project workspaces for T3Lab Assistant.

A project bundles:
    - its own knowledge scope (projects/<pid>/files/ + extra dirs) with a
      project-scoped RAG index (projects/<pid>/rag_index/)
    - custom instructions injected into every specialist prompt
    - its own chat history (projects/<pid>/chats/<doc_key>.json)
    - optional default provider/model applied on activation

Layout: %APPDATA%/T3LabAI/projects/<pid>/project.json
The active project id lives in settings.json ("active_project", managed
by T3LabAISettings). No project selected = fully legacy behavior.

All JSON writes use the ensure_ascii + io.open(utf-8) pattern
(IronPython 2.7 safe with Vietnamese text/paths).

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import copy
import hashlib
import io
import json
import os
import shutil
import threading
import time


def _write_json(path, data):
    payload = json.dumps(data, ensure_ascii=True, indent=2)
    if isinstance(payload, bytes):
        payload = payload.decode('ascii')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(payload)


def _read_json(path, default=None):
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


# Files the assistant GENERATES into a project's files/ dir. files/ is itself a
# RAG source, so without this the LLM-written project summary is re-indexed as
# if it were a user document and the model ends up citing its own summary back
# to itself. context_digest already excludes its own output dir the same way.
_GENERATED_DOCS = frozenset(['PROJECT_CONTEXT.md'])

# Project description (Claude Projects' "What are you trying to achieve?"):
# a short line for the user, shown under the name. Old project.json files have
# no such key and read as ''.
MAX_DESCRIPTION_CHARS = 500

# Cap of the Settings > Projects file list (one row per file).
MAX_LISTED_FILES = 500


def _clean_description(text):
    """One-paragraph description: str, whitespace collapsed, capped."""
    if text is None:
        return u''
    if not isinstance(text, type(u'')):
        try:
            text = text.decode('utf-8')
        except Exception:
            text = u'{}'.format(text)
    text = u' '.join(text.split())
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip()
    return text


class ProjectStore(object):
    """Singleton project registry."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ProjectStore, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._store_cache = {}     # pid -> KnowledgeStore
        # pid -> (mtime, size, meta). project.json used to be re-read from disk
        # on EVERY get_project() call, and there are ~18 call sites: the 30s
        # schedule tick, at least 3 reads per chat turn, and 2N reads plus N
        # os.walk each time the project popup opens. Keyed on (mtime, size) so
        # an edit made by another Revit session is still picked up.
        self._meta_cache = {}
        self._count_cache = {}     # (pid, cap) -> (files_mtime, own_count)
        self._dir_stats_cache = {}  # linked path -> (read_at, stats)
        self._meta_lock = threading.Lock()
        self._root_ready = None    # _root() only needs to mkdir once per path
        self._initialized = True

    # ── paths ─────────────────────────────────────────────────────────────

    def _root(self):
        base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
        d = os.path.join(base, 'T3LabAI', 'projects')
        # This is called by every path helper; the isdir/makedirs pair used to
        # run on each one. Re-check when APPDATA changes (tests sandbox it).
        if self._root_ready != d:
            if not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except Exception:
                    pass
            self._root_ready = d
        return d

    def project_dir(self, pid):
        return os.path.join(self._root(), pid)

    def _project_json(self, pid):
        return os.path.join(self.project_dir(pid), 'project.json')

    # ── CRUD ──────────────────────────────────────────────────────────────

    # ── meta cache ────────────────────────────────────────────────────────

    def _read_meta_cached(self, pid):
        """project.json for `pid`, served from cache while mtime+size match."""
        path = self._project_json(pid)
        try:
            st = os.stat(path)
            stamp = (st.st_mtime, st.st_size)
        except Exception:
            self.invalidate_meta(pid)
            return None
        with self._meta_lock:
            hit = self._meta_cache.get(pid)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        meta = _read_json(path)
        if meta is None:
            return None
        with self._meta_lock:
            self._meta_cache[pid] = (stamp, meta)
        return meta

    def invalidate_meta(self, pid=None):
        """Drop cached project.json (one pid, or all when pid is None)."""
        with self._meta_lock:
            if pid is None:
                self._meta_cache.clear()
            else:
                self._meta_cache.pop(pid, None)

    def list_projects(self):
        """[{'id','name','created','description'}] sorted by name."""
        out = []
        try:
            for entry in os.listdir(self._root()):
                meta = self._read_meta_cached(entry)
                if meta and meta.get('id'):
                    out.append({'id': meta['id'],
                                'name': meta.get('name', entry),
                                'created': meta.get('created', ''),
                                'description': _clean_description(
                                    meta.get('description'))})
        except Exception:
            pass
        out.sort(key=lambda p: (p['name'] or '').lower())
        return out

    def create_project(self, name, description=u''):
        """Create a project; returns its meta dict."""
        seed = (name or 'project') + str(time.time())
        pid = 'p_' + hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]
        meta = {
            'id': pid,
            'name': name or 'Project',
            'description': _clean_description(description),
            'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'instructions': '',
            'knowledge_dirs': [],
            'provider': None,
            'model': None,
            'skills_disabled': [],
        }
        pdir = self.project_dir(pid)
        for sub in ('', 'files', 'chats', 'skills'):
            d = os.path.join(pdir, sub) if sub else pdir
            if not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except Exception:
                    pass
        _write_json(self._project_json(pid), meta)
        self.invalidate_meta(pid)
        return meta

    def get_project(self, pid):
        """project.json for `pid`, or None.

        Returns a COPY: callers routinely mutate the dict they get back (the
        schedule tick stamps last_run, the settings dialog edits lists), and a
        shared cached dict would let those edits leak into every other reader
        before they are ever written to disk.
        """
        if not pid:
            return None
        meta = self._read_meta_cached(pid)
        if meta is None:
            return None
        meta = copy.deepcopy(meta)
        # Backward compatible: projects created before descriptions existed.
        meta['description'] = _clean_description(meta.get('description'))
        return meta

    def update_project(self, pid, patch):
        """Merge `patch` into project.json. Returns the new meta or None."""
        meta = self.get_project(pid)
        if not meta:
            return None
        patch = dict(patch or {})
        if 'description' in patch:
            patch['description'] = _clean_description(patch['description'])
        meta.update(patch)
        try:
            _write_json(self._project_json(pid), meta)
        except Exception:
            return None
        self.invalidate_meta(pid)
        # knowledge_dirs may have changed, and knowledge_store_for() mutates a
        # CACHED KnowledgeStore's source_dirs in place — without this a stale
        # store keeps indexing the old set.
        self._store_cache.pop(pid, None)
        return meta

    # ── linked knowledge folders (external RAG sources) ───────────────────
    # The project always indexes its own <project>/files dir; these are EXTRA
    # folders the user links (a network share of standards, a BEP folder...).
    # knowledge_store_for() already merges them into the store's source_dirs,
    # so adding one here + a rescan is all that's needed.

    def get_knowledge_dirs(self, pid):
        """Linked external folders for a project (never the built-in files dir)."""
        meta = self.get_project(pid)
        if not meta:
            return []
        return [d for d in (meta.get('knowledge_dirs') or []) if d]

    def add_knowledge_dir(self, pid, path):
        """Link an external folder. Returns the updated list, or None."""
        path = (path or '').strip()
        if not path:
            return None
        dirs = self.get_knowledge_dirs(pid)
        # normcase compare so C:\Docs and c:\docs don't both get linked
        if not any(os.path.normcase(os.path.abspath(d)) ==
                   os.path.normcase(os.path.abspath(path)) for d in dirs):
            dirs.append(path)
        if self.update_project(pid, {'knowledge_dirs': dirs}) is None:
            return None
        self._store_cache.pop(pid, None)   # rebuild store with new source_dirs
        return dirs

    def remove_knowledge_dir(self, pid, path):
        """Unlink an external folder. Returns the updated list, or None."""
        target = os.path.normcase(os.path.abspath((path or '').strip()))
        dirs = [d for d in self.get_knowledge_dirs(pid)
                if os.path.normcase(os.path.abspath(d)) != target]
        if self.update_project(pid, {'knowledge_dirs': dirs}) is None:
            return None
        self._store_cache.pop(pid, None)
        return dirs

    def delete_summary(self, pid):
        """What delete_project(pid) would remove — counts for the confirm.

        {'name', 'files', 'chats', 'sessions', 'memory', 'linked_dirs'}.
        `linked_dirs` are only UNLINKED (the folders themselves are never
        touched). Never raises; unknown pieces count as 0.
        """
        meta = self.get_project(pid) or {}
        out = {'name': meta.get('name') or pid or u'', 'files': 0,
               'chats': 0, 'sessions': 0, 'memory': 0,
               'linked_dirs': len([d for d in (meta.get('knowledge_dirs')
                                               or []) if d])}
        if not pid:
            return out
        out['files'] = len(self.list_project_files(pid))
        try:
            out['chats'] = len([f for f in os.listdir(
                os.path.join(self.project_dir(pid), 'chats'))
                if f.lower().endswith('.json')])
        except OSError:
            pass
        try:
            from config import chat_sessions
            out['sessions'] = chat_sessions.count_project_sessions(pid)
        except Exception:
            pass
        try:
            from Intelligence import assistant_memory
            out['memory'] = len(assistant_memory.list_scope_facts(
                assistant_memory.PROJECT_SCOPE, pid))
        except Exception:
            pass
        return out

    def delete_project(self, pid):
        """Remove the project and everything that belongs only to it.

        The project folder (files, chats, skills, index, attachments, logs,
        sessions), its remembered facts in assistant_memory.json, and any
        archived session in the GLOBAL archive that is tagged with this
        project id. Clears active_project if it was this one. Linked folders
        are only unlinked — they are the user's, never deleted. Global memory
        is never touched.
        """
        if not pid:
            return False
        try:
            if self.get_active_project_id() == pid:
                self.set_active_project(None)
            # Memory + tagged sessions first: they live OUTSIDE the project
            # folder, so the rmtree below would leave them orphaned (the
            # facts kept being injected for a pid nobody can select again).
            try:
                from Intelligence import assistant_memory
                assistant_memory.clear_scope(assistant_memory.PROJECT_SCOPE,
                                             pid)
            except Exception:
                pass
            try:
                from config import chat_sessions
                chat_sessions.delete_project_sessions(pid)
            except Exception:
                pass
            self._store_cache.pop(pid, None)
            self.invalidate_meta(pid)
            self._forget_counts(pid)
            shutil.rmtree(self.project_dir(pid), ignore_errors=True)
            return True
        except Exception:
            return False

    # ── project knowledge files (projects/<pid>/files) ────────────────────

    def files_dir(self, pid):
        """projects/<pid>/files — the project's own knowledge folder."""
        return os.path.join(self.project_dir(pid), 'files')

    def list_project_files(self, pid, limit=MAX_LISTED_FILES):
        """User documents in the project's files/ folder, sorted by name.

        [{'name', 'rel', 'path', 'size', 'modified', 'indexable'}]. Walks
        sub-folders (people drop whole folders in via Explorer); skips the
        generated PROJECT_CONTEXT.md. `indexable` says whether the knowledge
        index can read the type, so the UI can flag the rest instead of
        letting them look searchable.
        """
        root = self.files_dir(pid) if pid else None
        if not root or not os.path.isdir(root):
            return []
        try:
            from Intelligence.knowledge.knowledge_store import INDEXABLE_EXTS
        except Exception:
            INDEXABLE_EXTS = ('.txt', '.md', '.pdf', '.docx', '.xlsx')
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            for fname in filenames:
                if fname in _GENERATED_DOCS or fname.startswith('~$'):
                    continue      # generated summary / Office lock file
                path = os.path.join(dirpath, fname)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                out.append({
                    'name': fname,
                    'rel': os.path.relpath(path, root),
                    'path': path,
                    'size': st.st_size,
                    'modified': time.strftime(
                        '%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
                    'indexable': (os.path.splitext(fname)[1].lower()
                                  in INDEXABLE_EXTS),
                })
        out.sort(key=lambda f: f['rel'].lower())
        return out[:limit] if limit else out

    def remove_project_file(self, pid, rel_or_path):
        """Delete one file from the project's files/ folder. Returns bool.

        Refuses anything that does not resolve INSIDE files/ (an absolute
        path elsewhere, `..` segments) — this deletes user data, so it must
        never be pointed at a linked folder or an arbitrary path.
        """
        if not pid or not rel_or_path:
            return False
        root = os.path.normcase(os.path.abspath(self.files_dir(pid)))
        path = rel_or_path
        if not os.path.isabs(path):
            path = os.path.join(self.files_dir(pid), path)
        target = os.path.normcase(os.path.abspath(path))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            return False
        try:
            os.remove(os.path.abspath(path))
        except OSError:
            return False
        self._forget_counts(pid)
        return True

    def _forget_counts(self, pid):
        """Drop the cached document counts of one project.

        count_documents() keys on files/'s own mtime, which does not change
        when a file inside a SUB-folder is removed.
        """
        with self._meta_lock:
            for key in [k for k in self._count_cache if k[0] == pid]:
                self._count_cache.pop(key, None)

    # ── document counts (one implementation, cached) ──────────────────────

    def count_documents(self, pid, cap=200):
        """(own_files, linked_dirs, linked_docs, unscanned_dirs) for `pid`.

        There used to be FOUR independent os.walk implementations of this — in
        the project overview, the popup row subtitle, the chat project panel and
        the settings dialog — with three different caps, and only the dialog
        counted linked folders at all. They all ran on the UI thread.

        Linked folders are never walked here: they can be big network shares.
        Their document counts come from each folder's own context/ digest,
        exactly as the settings dialog already did.

        Cached against the files/ dir mtime, so the popup can call it once per
        project without re-walking on every open.
        """
        files_dir = os.path.join(self.project_dir(pid), 'files')
        try:
            stamp = os.stat(files_dir).st_mtime
        except Exception:
            stamp = None
        key = (pid, cap)
        with self._meta_lock:
            hit = self._count_cache.get(key)
            if hit is not None and hit[0] == stamp:
                own = hit[1]
            else:
                own = None
        if own is None:
            own = 0
            for _r, _s, fs in os.walk(files_dir):
                for f in fs:
                    # PROJECT_CONTEXT.md is generated INTO files/ by the
                    # context builder; it is not a user document.
                    if f in _GENERATED_DOCS:
                        continue
                    own += 1
                    if own > cap:
                        break
                if own > cap:
                    break
            with self._meta_lock:
                self._count_cache[key] = (stamp, own)

        dirs = self.get_knowledge_dirs(pid)
        linked_docs = 0
        unscanned = 0
        for d in dirs:
            st = self.linked_dir_stats(d)
            if st.get('exists'):
                linked_docs += st.get('files') or 0
            else:
                unscanned += 1
        return own, len(dirs), linked_docs, unscanned

    def linked_dir_stats(self, path, max_age=10.0):
        """context/ digest stats for one linked folder, cached for max_age s.

        Linked folders are typically network shares, and every surface that
        shows a project (this counter, the settings dialog's folder rows, the
        chat project popup) read the same sidecar again — several SMB round
        trips per repaint. The TTL is deliberately short so a rescan finished
        elsewhere still surfaces on its own; call invalidate_dir_stats() to
        drop it immediately.
        """
        now = time.time()
        with self._meta_lock:
            hit = self._dir_stats_cache.get(path)
            if hit is not None and (now - hit[0]) <= max_age:
                return hit[1]
        try:
            from Intelligence.knowledge import context_digest
            st = context_digest.read_context_stats(path)
        except Exception:
            st = {'files': 0, 'skipped': 0, 'llm': 0, 'pages': 0,
                  'updated': '', 'path': '', 'exists': False}
        with self._meta_lock:
            self._dir_stats_cache[path] = (now, st)
        return st

    def invalidate_dir_stats(self, path=None):
        """Drop cached digest stats (one folder, or all when path is None)."""
        with self._meta_lock:
            if path is None:
                self._dir_stats_cache.clear()
            else:
                self._dir_stats_cache.pop(path, None)

    def describe_documents(self, pid, cap=200):
        """Human-readable one-liner for the counts above (shared wording)."""
        own, n_dirs, docs, unscanned = self.count_documents(pid, cap=cap)
        shown = u"{}+".format(cap) if own > cap else u"{}".format(own)
        txt = u"{} file{} in the knowledge folder".format(
            shown, u"" if own == 1 else u"s")
        if n_dirs:
            txt += u" + {} linked folder{}".format(
                n_dirs, u"" if n_dirs == 1 else u"s")
            if docs:
                txt += u" ({} doc{} indexed)".format(
                    docs, u"" if docs == 1 else u"s")
            if unscanned:
                txt += u" · {} not scanned yet".format(unscanned)
        return txt

    # ── scheduled prompts ─────────────────────────────────────────────────

    def validate_schedule_time(self, text):
        """'H:MM'/'HH:MM' -> canonical 'HH:MM', or None when invalid.

        Zero-padding matters: _schedule_tick compares times as STRINGS, so a
        hand-written '9:00' would sort after '10:00'.
        """
        import re as _re
        m = _re.match(r'^(\d{1,2}):(\d{2})$', (text or u'').strip())
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            return None
        return u"{:02d}:{:02d}".format(h, mi)

    def add_schedule(self, pid, prompt, time_text):
        """Append one scheduled prompt. Returns the new list, or None."""
        prompt = (prompt or u'').strip()
        hhmm = self.validate_schedule_time(time_text)
        if not prompt or not hhmm:
            return None
        items = list((self.get_project(pid) or {}).get('scheduled') or [])
        items.append({
            'id': u's_{}'.format(int(time.time() * 1000)),
            'prompt': prompt,
            'time': hhmm,
            'enabled': True,
            'last_run': u'',
        })
        return items if self.update_project(
            pid, {'scheduled': items}) is not None else None

    def remove_schedule(self, pid, task_id):
        """Drop one scheduled prompt by id. Returns the new list, or None."""
        items = [t for t in ((self.get_project(pid) or {}).get('scheduled') or [])
                 if t.get('id') != task_id]
        return items if self.update_project(
            pid, {'scheduled': items}) is not None else None

    def set_schedule_enabled(self, pid, task_id, enabled):
        """Toggle one scheduled prompt. `enabled` was read by the tick but no
        UI ever wrote it — this is what makes the field real."""
        items = list((self.get_project(pid) or {}).get('scheduled') or [])
        for t in items:
            if t.get('id') == task_id:
                t['enabled'] = bool(enabled)
                break
        else:
            return None
        return items if self.update_project(
            pid, {'scheduled': items}) is not None else None

    @staticmethod
    def due_schedule(items, now_hm, today):
        """First task due at `now_hm` on `today`, or None. Pure — no I/O, no
        WPF — so the schedule rule is testable headlessly."""
        for it in (items or []):
            if not it.get('enabled', True):
                continue
            if it.get('last_run') == today:
                continue
            if (it.get('time') or u'99:99') <= now_hm:
                return it
        return None

    # ── active project ────────────────────────────────────────────────────

    def get_active_project_id(self):
        try:
            from config.settings import get_settings
            pid = get_settings().get_active_project()
            # stale id (folder deleted outside the app) counts as none
            if pid and not os.path.isfile(self._project_json(pid)):
                return None
            return pid
        except Exception:
            return None

    def set_active_project(self, pid):
        try:
            from config.settings import get_settings
            return get_settings().set_active_project(pid)
        except Exception:
            return False

    # ── attachments archive + daily activity log ──────────────────────────
    #
    # Retention. Attachments are COPIES (the original stays where the user
    # had it) plus pasted screenshots and downloaded links of up to 20 MB
    # each; the activity log is a journal, one file per day. Both only ever
    # grew. prune_storage() trims every scope (the T3LabAI root and each
    # project), at most once per Revit session, from the worker threads that
    # archive and journal — never on the UI thread.
    ATTACHMENTS_MAX_AGE_DAYS = 30
    ATTACHMENTS_MAX_TOTAL_BYTES = 500 * 1024 * 1024    # across every scope
    ACTIVITY_LOG_MAX_AGE_DAYS = 90

    def _scope_dirs(self):
        """The T3LabAI root scope plus every project folder."""
        scopes = [self._scope_dir(None)]
        root = self._root()
        try:
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if os.path.isdir(d):
                    scopes.append(d)
        except OSError:
            pass
        return scopes

    def prune_storage(self, now=None):
        """Apply the retention rules. Returns {'attachments': n, 'logs': n}.

        Attachments: older than ATTACHMENTS_MAX_AGE_DAYS go, then the oldest
        until all scopes together fit ATTACHMENTS_MAX_TOTAL_BYTES. Activity
        logs: older than ACTIVITY_LOG_MAX_AGE_DAYS. Files from the last hour
        are never touched (a pasted image may still be waiting to be sent).
        Never raises.
        """
        out = {'attachments': 0, 'logs': 0}
        try:
            from core import housekeeping
            scopes = self._scope_dirs()
            out['attachments'] = housekeeping.prune_files(
                [os.path.join(sc, 'attachments') for sc in scopes],
                max_age_days=self.ATTACHMENTS_MAX_AGE_DAYS,
                max_total_bytes=self.ATTACHMENTS_MAX_TOTAL_BYTES,
                recursive=True, now=now)
            out['logs'] = housekeeping.prune_files(
                [os.path.join(sc, 'logs') for sc in scopes],
                max_age_days=self.ACTIVITY_LOG_MAX_AGE_DAYS,
                suffixes=('.md',), now=now)
        except Exception:
            pass
        return out

    def prune_storage_once(self):
        """prune_storage() the first time this is called in the session."""
        try:
            from core import housekeeping
            housekeeping.run_once('project_store.prune_storage',
                                  self.prune_storage)
        except Exception:
            pass

    def _scope_dir(self, pid):
        """projects/<pid> when a project is given, else the T3LabAI root
        (attachments/logs still work with no project selected)."""
        if pid:
            return self.project_dir(pid)
        base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
        return os.path.join(base, 'T3LabAI')

    def attachments_dir(self, pid=None, day=None):
        """<scope>/attachments/<YYYY-MM-DD>/ — created on demand."""
        day = day or time.strftime('%Y-%m-%d')
        d = os.path.join(self._scope_dir(pid), 'attachments', day)
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                pass
        return d

    def archive_attachments(self, paths, pid=None):
        """Copy attached files into today's dated folder of the scope.

        Returns the archived paths (a file that fails to copy keeps its
        original path). Existing names get a _N suffix, never overwritten.
        """
        out = []
        self.prune_storage_once()          # before today's folder is made
        dest_dir = self.attachments_dir(pid)
        for src in (paths or []):
            try:
                name = os.path.basename(src)
                dst = os.path.join(dest_dir, name)
                if os.path.abspath(src) == os.path.abspath(dst):
                    out.append(src)
                    continue
                stem, ext = os.path.splitext(name)
                n = 1
                while os.path.exists(dst):
                    dst = os.path.join(dest_dir,
                                       u'{}_{}{}'.format(stem, n, ext))
                    n += 1
                shutil.copy2(src, dst)
                out.append(dst)
            except Exception:
                out.append(src)
        return out

    def activity_log_path(self, pid=None, day=None):
        """<scope>/logs/<YYYY-MM-DD>.md — folder created on demand."""
        day = day or time.strftime('%Y-%m-%d')
        d = os.path.join(self._scope_dir(pid), 'logs')
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                pass
        return os.path.join(d, day + '.md')

    def append_activity(self, text, pid=None):
        """Append one timestamped markdown bullet to today's log. Never raises."""
        self.prune_storage_once()
        try:
            path = self.activity_log_path(pid)
            is_new = not os.path.exists(path)
            with io.open(path, 'a', encoding='utf-8') as f:
                if is_new:
                    f.write(u'# T3Lab Assistant Activity Log — {}\n\n'.format(
                        time.strftime('%Y-%m-%d')))
                f.write(u'- **{}** {}\n'.format(
                    time.strftime('%H:%M'), text or u''))
            return True
        except Exception:
            return False

    # ── scoped resources ──────────────────────────────────────────────────

    def history_path(self, pid, doc_key):
        """Project-scoped chat history file for a Revit document."""
        d = os.path.join(self.project_dir(pid), 'chats')
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                pass
        return os.path.join(d, '{}.json'.format(doc_key))

    def knowledge_store_for(self, pid):
        """Project-scoped KnowledgeStore (cached). None on failure."""
        meta = self.get_project(pid)
        if not meta:
            return None
        store = self._store_cache.get(pid)
        # lazy import — knowledge_store imports this module for
        # get_active_store(), keep the cycle import-time safe
        from Intelligence.knowledge.knowledge_store import KnowledgeStore
        dirs = [os.path.join(self.project_dir(pid), 'files')]
        for d in meta.get('knowledge_dirs', []):
            if d and d not in dirs:
                dirs.append(d)
        if store is None:
            store = KnowledgeStore(
                os.path.join(self.project_dir(pid), 'rag_index'),
                dirs, scope_label=pid)
            self._store_cache[pid] = store
        else:
            store.source_dirs = dirs
        return store

    def get_prompt_addendum(self, pid):
        meta = self.get_project(pid)
        return (meta or {}).get('instructions', '') or ''

    def get_active_prompt_addendum(self):
        """Instructions of the active project, or ''. Never raises."""
        try:
            pid = self.get_active_project_id()
            if not pid:
                return ''
            return self.get_prompt_addendum(pid)
        except Exception:
            return ''
