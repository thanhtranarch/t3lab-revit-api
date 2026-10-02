# -*- coding: utf-8 -*-
"""
chat_sessions — the archived-conversation store behind the assistant's History.

Claude Projects keeps each project's conversations with the project. The
assistant used to archive every finished chat into ONE global folder,
%APPDATA%/T3LabAI/chat_history/sessions/<doc_key>_<YYYYmmdd_HHMMSS>.json, so
History mixed every project together and deleting a project left its
conversations behind.

This module is the backend the chat window calls (it does not touch WPF):

    sessions_dir(pid)            where to WRITE a new archive: the project's
                                 projects/<pid>/sessions/ when a project is
                                 active, else the existing global folder
    list_sessions(...)           newest-first metadata + full-text search
    rename_session(path, title)  stores a "title" field in the JSON
    delete_session(path)
    count_project_sessions(pid) / delete_project_sessions(pid)
                                 used by ProjectStore.delete_project

Old global files are read as they are — no migration. They belong to
"No project" unless their JSON carries a "pid" tag, in which case they are
listed (and deleted) with that project.

Session JSON (written by T3LabAssistantDialog._archive_current_session):
    {"id": "20260930_141501", "doc_key": "...", "title": "...",
     "timestamp": "2026-09-30 14:15", "message_count": 12,
     "messages": [{"role", "content", "ts"}, ...], "summary": "..."}
plus, optionally, "pid" (project tag) and "renamed" (title set by the user).

Pure Python, no Revit/WPF — testable under CPython 3.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import io
import json
import os
import re
import threading
import time

MAX_TITLE_CHARS = 120
NO_PROJECT_LABEL = u'No project'

# path -> ((mtime, size), meta, haystack). Listing reads every archive (up to
# 100 per folder) and a search needs the message text too; the History view
# re-lists on every keystroke of its search box, so parse each file once.
_CACHE = {}
_CACHE_CAP = 2000
_CACHE_LOCK = threading.Lock()

_ID_RE = re.compile(r'^(\d{8})_(\d{6})$')
_FILE_ID_RE = re.compile(r'_(\d{8}_\d{6})\.json$', re.I)


# ─── locations ────────────────────────────────────────────────────────────────

def _appdata_root():
    base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI')


def _ensure(d):
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except OSError:
            pass
    return d


def global_sessions_dir():
    """%APPDATA%/T3LabAI/chat_history/sessions — the pre-project archive."""
    return _ensure(os.path.join(_appdata_root(), 'chat_history', 'sessions'))


def _projects_root():
    return os.path.join(_appdata_root(), 'projects')


def project_sessions_dir(pid, create=True):
    """projects/<pid>/sessions (created on demand when create=True)."""
    d = os.path.join(_projects_root(), u'{}'.format(pid), 'sessions')
    return _ensure(d) if create else d


def _project_exists(pid):
    return bool(pid) and os.path.isfile(
        os.path.join(_projects_root(), u'{}'.format(pid), 'project.json'))


def sessions_dir(pid=None):
    """Folder a NEW archived session should be written to.

    The project's own sessions/ folder when `pid` names a project that
    exists, else the global archive. A stale id (project deleted while a
    chat was open) falls back to global rather than resurrecting a project
    folder that has no project.json.
    """
    if pid and _project_exists(pid):
        return project_sessions_dir(pid)
    return global_sessions_dir()


def _project_ids():
    root = _projects_root()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if os.path.isfile(
        os.path.join(root, n, 'project.json'))]


def _is_session_path(path):
    """True when `path` is a .json directly inside a sessions folder we own.

    rename/delete take a path from the UI; this keeps them from ever being
    pointed at a project.json, a chat history file or anything else.
    """
    if not path or not path.lower().endswith('.json'):
        return False
    folder = os.path.normcase(os.path.abspath(os.path.dirname(path)))
    if folder == os.path.normcase(os.path.abspath(
            os.path.join(_appdata_root(), 'chat_history', 'sessions'))):
        return True
    parent, leaf = os.path.split(folder)
    if leaf != os.path.normcase('sessions'):
        return False
    return os.path.dirname(parent) == os.path.normcase(
        os.path.abspath(_projects_root()))


# ─── reading ──────────────────────────────────────────────────────────────────

def _read_json(path):
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path, data):
    """Serialize first, then write in one go (a failed encode never truncates)."""
    try:
        from core import jsonsafe
        payload = jsonsafe.dumps(data, indent=2)
    except Exception:
        payload = json.dumps(data, ensure_ascii=True, indent=2)
    if isinstance(payload, bytes):
        payload = payload.decode('ascii')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(payload)


def _fold(text):
    """Lower-case + Vietnamese diacritics folded ('Tường' finds 'tuong')."""
    text = (text or u'').lower()
    try:
        from Intelligence.knowledge.vi_text import fold_diacritics
        return fold_diacritics(text)
    except Exception:
        return text


def _content_text(content):
    """Message content as plain text (str, or a list of content blocks)."""
    if content is None:
        return u''
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(u'{}'.format(block.get('text') or u''))
            else:
                parts.append(u'{}'.format(block))
        return u' '.join(parts)
    return u'{}'.format(content)


def _epoch(data, fname, mtime):
    """Sort key: the session id stamp (seconds), else the file name's stamp,
    else the minute-resolution timestamp, else the file mtime."""
    raw = u'{}'.format(data.get('id') or u'')
    m = _FILE_ID_RE.search(fname)
    for stamp in ((raw if _ID_RE.match(raw) else None),
                  (m.group(1) if m else None)):
        if not stamp:
            continue
        try:
            return time.mktime(time.strptime(stamp, '%Y%m%d_%H%M%S'))
        except (ValueError, OverflowError):
            continue
    ts = u'{}'.format(data.get('timestamp') or data.get('created') or u'')
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return time.mktime(time.strptime(ts, fmt))
        except (ValueError, OverflowError):
            continue
    return mtime


def _load_meta(path, pid_from_location):
    """(meta, haystack) for one archive, cached on (mtime, size)."""
    try:
        st = os.stat(path)
    except OSError:
        return None, u''
    stamp = (st.st_mtime, st.st_size)
    with _CACHE_LOCK:
        hit = _CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return dict(hit[1]), hit[2]
    data = _read_json(path)
    if data is None:
        return None, u''
    msgs = data.get('messages') or []
    if not isinstance(msgs, list):
        msgs = []
    fname = os.path.basename(path)
    tagged = data.get('pid') or None
    created = u'{}'.format(data.get('timestamp') or data.get('created') or u'')
    if not created:
        created = time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))
    title = u' '.join(u'{}'.format(data.get('title') or u'').split())
    meta = {
        'path': path,
        'id': u'{}'.format(data.get('id') or os.path.splitext(fname)[0]),
        'title': title or u'Conversation',
        'created': created,
        'doc_key': u'{}'.format(data.get('doc_key') or u''),
        'pid': pid_from_location or tagged,
        'message_count': int(data.get('message_count') or len(msgs) or 0),
        'renamed': bool(data.get('renamed')),
        '_epoch': _epoch(data, fname, st.st_mtime),
    }
    hay = [meta['title'], u'{}'.format(data.get('summary') or u'')]
    for m in msgs:
        if isinstance(m, dict):
            hay.append(_content_text(m.get('content')))
    haystack = _fold(u'\n'.join(hay))
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_CAP:
            _CACHE.clear()
        _CACHE[path] = (stamp, meta, haystack)
    return dict(meta), haystack


def _iter_folder(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.lower().endswith('.json'):
            yield os.path.join(folder, name)


def _matches_doc(meta, doc_key):
    if not doc_key:
        return True
    if meta.get('doc_key') == doc_key:
        return True
    # files written before doc_key was stored are named <doc_key>_<id>.json
    return os.path.basename(meta['path']).startswith(u'{}_'.format(doc_key))


def _matches_query(haystack, query):
    """Every whitespace-separated term must occur (title + messages)."""
    terms = _fold(query).split()
    return all(t in haystack for t in terms)


def list_sessions(pid=None, doc_key=None, query=None, all_projects=False,
                  limit=None):
    """Archived sessions, newest first.

    pid=None, all_projects=False  → "No project": the global archive minus
                                    files tagged with a project
    pid='p_x'                     → that project's sessions/ folder plus any
                                    global file tagged "pid": "p_x"
    all_projects=True             → everything (global + every project)
    doc_key                       → only sessions of that Revit document
    query                         → case/diacritic-insensitive search over the
                                    title, the summary and every message; all
                                    terms must match

    Each item: {'path', 'id', 'title', 'created', 'doc_key', 'pid',
    'message_count', 'renamed'}; pid is None for "No project".
    """
    items = []
    seen = set()

    def _take(path, loc_pid, want_pid=None, untagged_only=False):
        npath = os.path.normcase(os.path.abspath(path))
        if npath in seen:
            return
        seen.add(npath)
        meta, hay = _load_meta(path, loc_pid)
        if meta is None:
            return
        if want_pid is not None and meta.get('pid') != want_pid:
            return
        if untagged_only and meta.get('pid'):
            return
        if not _matches_doc(meta, doc_key):
            return
        if query and query.strip() and not _matches_query(hay, query):
            return
        items.append(meta)

    gdir = os.path.join(_appdata_root(), 'chat_history', 'sessions')
    if all_projects:
        for p in _iter_folder(gdir):
            _take(p, None)
        for pid_ in _project_ids():
            for p in _iter_folder(project_sessions_dir(pid_, create=False)):
                _take(p, pid_)
    elif pid:
        for p in _iter_folder(project_sessions_dir(pid, create=False)):
            _take(p, pid)
        for p in _iter_folder(gdir):
            _take(p, None, want_pid=pid)
    else:
        for p in _iter_folder(gdir):
            _take(p, None, untagged_only=True)

    items.sort(key=lambda m: (m.get('_epoch') or 0, m['path']), reverse=True)
    for m in items:
        m.pop('_epoch', None)
    if limit:
        items = items[:int(limit)]
    return items


# ─── editing ──────────────────────────────────────────────────────────────────

def clean_title(title):
    """A session title: one line, whitespace collapsed, capped."""
    text = u' '.join(u'{}'.format(title or u'').split())
    if len(text) > MAX_TITLE_CHARS:
        text = text[:MAX_TITLE_CHARS].rstrip()
    return text


def rename_session(path, title):
    """Store a user-chosen title in the session JSON. Returns bool.

    Sets "renamed": true so a later automatic re-title (if the dialog ever
    adds one) can tell the user's choice apart from the generated one.
    """
    text = clean_title(title)
    if not text or not _is_session_path(path) or not os.path.isfile(path):
        return False
    data = _read_json(path)
    if data is None:
        return False
    data['title'] = text
    data['renamed'] = True
    try:
        _write_json(path, data)
    except Exception:
        return False
    with _CACHE_LOCK:
        _CACHE.pop(path, None)
    return True


def delete_session(path):
    """Delete one archived session. Returns bool."""
    if not _is_session_path(path) or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        return False
    with _CACHE_LOCK:
        _CACHE.pop(path, None)
    return True


def tag_session(path, pid):
    """Tag an archived session with a project id (None removes the tag)."""
    if not _is_session_path(path) or not os.path.isfile(path):
        return False
    data = _read_json(path)
    if data is None:
        return False
    if pid:
        data['pid'] = u'{}'.format(pid)
    else:
        data.pop('pid', None)
    try:
        _write_json(path, data)
    except Exception:
        return False
    with _CACHE_LOCK:
        _CACHE.pop(path, None)
    return True


# ─── project lifecycle ────────────────────────────────────────────────────────

def count_project_sessions(pid):
    """Archived sessions that belong to `pid` (its folder + tagged globals)."""
    if not pid:
        return 0
    return len(list_sessions(pid=pid))


def delete_project_sessions(pid):
    """Delete every archived session of `pid`. Returns how many went.

    Covers the project's own sessions/ folder AND global archives tagged
    with the project, which ProjectStore.delete_project's rmtree of the
    project folder would otherwise leave behind.
    """
    if not pid:
        return 0
    n = 0
    for meta in list_sessions(pid=pid):
        path = meta['path']
        try:
            os.remove(path)
            n += 1
        except OSError:
            continue
        with _CACHE_LOCK:
            _CACHE.pop(path, None)
    return n
