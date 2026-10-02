# -*- coding: utf-8 -*-
"""
housekeeping — keep T3Lab's per-user data on disk bounded.

T3Lab keeps logs, archives and caches under %APPDATA%\\T3LabAI (and %TEMP%).
Several of those writers only ever appended: one file per day, one archive per
conversation, one copy per attachment, forever. Each of them now prunes through
the helpers here, at most once per Revit session (run_once), so the cost is one
directory listing per session, never one per write.

Rules every caller follows:
  * only logs, archives and caches are pruned — never a setting, and never a
    file whose only copy is the user's own work unless it is an archive the
    user was told is kept for a limited time;
  * a file younger than `min_age_s` is never touched — it may belong to the
    turn in progress (a pasted image waiting to be sent);
  * nothing here raises: a failed clean-up must never cost the user the write
    that triggered it.

Pure stdlib, IronPython 2.7 and CPython 3 compatible (no f-strings).

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Housekeeping"

import io
import os
import shutil
import threading
import time

DAY_S = 86400.0

# Marker a one-time legacy import leaves in the destination folder, so a file
# the user later deletes or that pruning removed is never copied back.
LEGACY_MARKER = '.t3lab_legacy_imported'

_DONE = set()
_LOCK = threading.Lock()


# ─── once per session ─────────────────────────────────────────────────────────

def run_once(key, func, *args, **kwargs):
    """Call func(*args, **kwargs) the first time `key` is seen in this process.

    Returns func's result, or None when it already ran (or raised). sys.modules
    lives for the whole Revit session, so "once per process" is "once per
    session". Never raises.
    """
    with _LOCK:
        if key in _DONE:
            return None
        _DONE.add(key)
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def forget(key=None):
    """Let run_once(key) run again (every key when None). For tests."""
    with _LOCK:
        if key is None:
            _DONE.clear()
        else:
            _DONE.discard(key)


# ─── pruning ──────────────────────────────────────────────────────────────────

def _list_files(folder, suffixes=None, recursive=False):
    """[(mtime, size, path)] of the regular files in `folder`."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    if suffixes:
        suffixes = tuple(s.lower() for s in suffixes)
    if recursive:
        walker = os.walk(folder)
    else:
        try:
            walker = [(folder, [], os.listdir(folder))]
        except OSError:
            return out
    for root, _dirs, names in walker:
        for name in names:
            if name == LEGACY_MARKER:
                continue
            if suffixes and not name.lower().endswith(suffixes):
                continue
            full = os.path.join(root, name)
            try:
                if not os.path.isfile(full):
                    continue
                out.append((os.path.getmtime(full), os.path.getsize(full), full))
            except OSError:
                continue
    return out


def _remove_empty_dirs(folder, now, min_age_s):
    """Remove empty sub-folders of `folder` (never `folder` itself). A folder
    created less than `min_age_s` ago is kept: it may be today's attachments
    folder, made a moment before the file that goes into it."""
    removed = 0
    for root, dirs, files in os.walk(folder, topdown=False):
        if os.path.normcase(os.path.abspath(root)) == \
                os.path.normcase(os.path.abspath(folder)):
            continue
        try:
            if os.listdir(root):
                continue
            if now - os.path.getmtime(root) < min_age_s:
                continue
            os.rmdir(root)
            removed += 1
        except OSError:
            continue
    return removed


def plan_prune(entries, max_age_days=None, keep_newest=None,
               max_total_bytes=None, min_age_s=0, now=None):
    """Which of `entries` [(mtime, size, path)] to delete. Pure.

    Applied in this order, oldest files first:
      1. older than max_age_days;
      2. beyond the newest `keep_newest`;
      3. while the total of what is left exceeds max_total_bytes.
    A file younger than min_age_s survives every rule (it still counts toward
    keep_newest and the size total).
    """
    now = time.time() if now is None else now
    newest_first = sorted(entries, key=lambda e: e[0], reverse=True)

    def protected(entry):
        return now - entry[0] < min_age_s

    doomed = []
    kept = []
    for entry in newest_first:
        if (max_age_days is not None and not protected(entry)
                and now - entry[0] > max_age_days * DAY_S):
            doomed.append(entry[2])
        else:
            kept.append(entry)

    if keep_newest is not None:
        survivors = []
        for index, entry in enumerate(kept):
            if index >= keep_newest and not protected(entry):
                doomed.append(entry[2])
            else:
                survivors.append(entry)
        kept = survivors

    if max_total_bytes is not None:
        total = sum(e[1] for e in kept)
        for entry in reversed(kept):                     # oldest first
            if total <= max_total_bytes:
                break
            if protected(entry):
                continue
            doomed.append(entry[2])
            total -= entry[1]
    return doomed


def prune_files(folder, max_age_days=None, keep_newest=None,
                max_total_bytes=None, suffixes=None, recursive=False,
                min_age_s=3600, now=None):
    """Delete files in `folder` per plan_prune(). Returns how many went.

    folder     one folder, or a list of folders pruned as ONE pool (the
               newest-N and total-size caps then apply across all of them).
    suffixes   only files ending with one of these are considered (and count
               toward keep_newest / max_total_bytes); None = every file.
    recursive  walk sub-folders too, and remove the ones left empty.
    Never raises.
    """
    removed = 0
    try:
        now = time.time() if now is None else now
        folders = list(folder) if isinstance(folder, (list, tuple)) else [folder]
        entries = []
        for one in folders:
            entries.extend(_list_files(one, suffixes, recursive))
        for path in plan_prune(entries, max_age_days, keep_newest,
                               max_total_bytes, min_age_s, now):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        if recursive:
            for one in folders:
                if one and os.path.isdir(one):
                    _remove_empty_dirs(one, now, min_age_s)
    except Exception:
        pass
    return removed


# ─── one-time legacy import ───────────────────────────────────────────────────

def import_legacy_folder(src_dir, dst_dir, suffixes=None):
    """Copy the files of `src_dir` (not its sub-folders) into `dst_dir` ONCE.

    Used when a writer moves out of the extension folder: the user's existing
    files come along on first use. A marker in dst_dir records that the import
    happened, so a file the user deletes later (or that pruning removes) is
    never copied back. Files already in dst_dir are never overwritten, and
    src_dir is left untouched. Returns the number copied. Never raises.
    """
    copied = 0
    try:
        if not os.path.isdir(dst_dir):
            os.makedirs(dst_dir)
        marker = os.path.join(dst_dir, LEGACY_MARKER)
        if os.path.exists(marker):
            return 0
        if src_dir and os.path.isdir(src_dir) and \
                os.path.normcase(os.path.abspath(src_dir)) != \
                os.path.normcase(os.path.abspath(dst_dir)):
            if suffixes:
                suffixes = tuple(s.lower() for s in suffixes)
            for name in sorted(os.listdir(src_dir)):
                if suffixes and not name.lower().endswith(suffixes):
                    continue
                src = os.path.join(src_dir, name)
                dst = os.path.join(dst_dir, name)
                if not os.path.isfile(src) or os.path.exists(dst):
                    continue
                try:
                    shutil.copy2(src, dst)          # keeps mtime: age pruning
                    copied += 1
                except (IOError, OSError):
                    continue
        with io.open(marker, 'w', encoding='utf-8') as f:
            f.write(u'Imported {} file(s) from {} on {}\n'.format(
                copied, src_dir, time.strftime('%Y-%m-%d %H:%M:%S')))
    except Exception:
        pass
    return copied
