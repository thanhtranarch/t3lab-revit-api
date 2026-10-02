# -*- coding: utf-8 -*-
"""
pdf_cache — one extraction per (file, mtime, size), shared by every consumer.

Measured on the FJX `00_Appendix (IEP)` share (32 documents, 46 MB): reading
the files over SMB costs ~164 s and parsing them ~21 s. A single Rescan paid
that TWICE, because `context_digest` walks the folder to build CONTEXT.md and
then `KnowledgeStore.scan()` re-reads and re-parses the very same files to
build chunks. Neither knew about the other.

This module is the seam they now share: extraction results are memoised in
memory for the current session and on disk under
`%APPDATA%/T3LabAI/pdfcache/`, keyed by the file's (mtime, size). An unchanged
document is never read from the share, never inflated and never tokenised
again — the second consumer, and every later Rescan, get it for free.

Cache entries are plain JSON page lists, so they are also the cheapest way to
answer "what text does this document actually contain" when debugging.

Pure Python + guarded imports — importable under CPython 3 for tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import hashlib
import io
import json
import os
import threading
import time

TEXT_EXTS = ('.txt', '.md')
PDF_EXT = '.pdf'
# .docx / .xlsx — read with the stdlib in office_text (zip + XML).
from Intelligence.knowledge.office_text import OFFICE_EXTS

CACHE_VERSION = 1           # bump when the extractors change their output
_MAX_CACHE_BYTES = 80 * 1024 * 1024
_MAX_MEM_DOCS = 24          # per-session in-memory entries

_mem = {}                   # key -> (fingerprint, pages, reason)
_mem_order = []
_lock = threading.Lock()


def _appdata_dir():
    base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI')


def cache_dir():
    d = os.path.join(_appdata_dir(), 'pdfcache')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def _key(path):
    norm = os.path.normcase(os.path.abspath(path))
    return hashlib.md5(norm.encode('utf-8')).hexdigest()[:16]


def fingerprint(path):
    """(mtime, size) — cheap identity of a file's content. None if missing."""
    try:
        st = os.stat(path)
        return [st.st_mtime, st.st_size]
    except Exception:
        return None


def _same(a, b):
    if not a or not b or len(a) != 2 or len(b) != 2:
        return False
    # mtime over SMB can wobble in the sub-millisecond digits between calls
    return abs(float(a[0]) - float(b[0])) < 0.001 and int(a[1]) == int(b[1])


def _mem_get(key, fp):
    with _lock:
        hit = _mem.get(key)
    if hit and _same(hit[0], fp):
        return hit[1], hit[2]
    return None


def _mem_put(key, fp, pages, reason):
    with _lock:
        _mem[key] = (fp, pages, reason)
        if key in _mem_order:
            _mem_order.remove(key)
        _mem_order.append(key)
        while len(_mem_order) > _MAX_MEM_DOCS:
            _mem.pop(_mem_order.pop(0), None)


def _disk_path(key):
    return os.path.join(cache_dir(), key + '.json')


def _disk_get(key, fp):
    p = _disk_path(key)
    if not os.path.isfile(p):
        return None
    try:
        with io.open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get('v') != CACHE_VERSION or not _same(data.get('fp'), fp):
        return None
    pages = [(int(n), t) for n, t in (data.get('pages') or [])]
    return pages, data.get('reason') or ''


def _disk_put(key, fp, pages, reason):
    payload = {
        'v': CACHE_VERSION,
        'fp': fp,
        'reason': reason,
        'pages': [[n, t] for n, t in pages],
        'saved': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        text = json.dumps(payload, ensure_ascii=True)
        if isinstance(text, bytes):
            text = text.decode('ascii')
        with io.open(_disk_path(key), 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass


def _extract(path):
    """The real work: ([(page_no, text)], reason). No caching here."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
            if (text or '').strip():
                return [(0, text)], ''
            return [], 'empty file (no text)'
        if ext == PDF_EXT:
            from Intelligence import rag_processor
            pages, reason = rag_processor.extract_pdf_pages_ex(
                path, max_pages=1500)
            pages = [(p, t) for p, t in (pages or []) if (t or '').strip()]
            return pages, ('' if pages else (reason or 'no extractable text'))
        if ext in OFFICE_EXTS:
            from Intelligence.knowledge import office_text
            return office_text.extract_pages(path)
    except Exception as ex:
        return [], 'reader error ({0})'.format(ex)
    return [], 'unsupported file type'


def get_pages(path, use_cache=True):
    """([(page_no, text)], reason) for a document, cached by (mtime, size)."""
    fp = fingerprint(path)
    if fp is None:
        return [], 'cannot be opened'
    key = _key(path)
    if use_cache:
        hit = _mem_get(key, fp)
        if hit is not None:
            return hit[0], hit[1]
        hit = _disk_get(key, fp)
        if hit is not None:
            _mem_put(key, fp, hit[0], hit[1])
            return hit[0], hit[1]

    pages, reason = _extract(path)
    if use_cache:
        _mem_put(key, fp, pages, reason)
        _disk_put(key, fp, pages, reason)
    return pages, reason


def prune(max_bytes=_MAX_CACHE_BYTES):
    """Drop the oldest entries when the cache outgrows its budget."""
    d = cache_dir()
    try:
        entries = []
        total = 0
        for name in os.listdir(d):
            if not name.endswith('.json'):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except Exception:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= max_bytes:
            return 0
        entries.sort()
        dropped = 0
        for _mt, size, p in entries:
            if total <= max_bytes:
                break
            try:
                os.remove(p)
                total -= size
                dropped += 1
            except Exception:
                pass
        return dropped
    except Exception:
        return 0


def clear():
    """Forget everything (used by tests and by a forced full re-read)."""
    with _lock:
        _mem.clear()
        del _mem_order[:]
    try:
        d = cache_dir()
        for name in os.listdir(d):
            if name.endswith('.json'):
                try:
                    os.remove(os.path.join(d, name))
                except Exception:
                    pass
    except Exception:
        pass
