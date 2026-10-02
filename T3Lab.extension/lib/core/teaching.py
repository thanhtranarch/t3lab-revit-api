# -*- coding: utf-8 -*-
"""
teaching — pure helpers for the "Opus teaches via MCP" capture pipeline.

Deliberately Revit-free and CPython-3 importable so the sandbox-guard decision,
the document-identity match, and the raw-session <-> trajectory conversion can
be unit-tested without Revit/WPF. core/server.py (in-Revit) and the session
miner (Intelligence/learning/enrichers/mcp_session_miner.py) both delegate here
so there is ONE source of truth for the teaching data shapes.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Teaching Helpers"

import io
import json
import os

from core import jsonsafe

# Document-name substrings that mark a scratch/sandbox model when the user has
# not explicitly marked one. Kept generous but distinctive.
SANDBOX_NAME_HINTS = ('sandbox', 'nhap', u'nháp', 'teach', 'scratch', 'test')

# Metadata source/quality tags for trajectories that came from the MCP teacher.
SOURCE  = 'mcp_teacher'
QUALITY = 'teacher'


def is_sandbox(title, path, sandbox_doc):
    """True when a document (by title/path) is the teaching sandbox.

    `sandbox_doc` is the explicitly-marked doc ({'title','path'}) or None. With
    an explicit mark, match by path (preferred) or title. With no mark, fall
    back to the name convention (SANDBOX_NAME_HINTS). Pure.
    """
    title = u'{}'.format(title or u'')
    path  = u'{}'.format(path or u'')
    if isinstance(sandbox_doc, dict):
        sp = u'{}'.format(sandbox_doc.get('path') or u'')
        st = u'{}'.format(sandbox_doc.get('title') or u'')
        if sp and path and sp == path:
            return True
        if st and title and st == title:
            return True
        return False
    low = (title + u' ' + path).lower()
    return any(h in low for h in SANDBOX_NAME_HINTS)


def should_block_write(teaching_enabled, modifies_model, sandbox_ok):
    """The sandbox guard decision: block a model-modifying tool during teaching
    mode unless it targets the sandbox. Pure."""
    return bool(teaching_enabled) and bool(modifies_model) and not bool(sandbox_ok)


def is_error_result(result):
    """True when a tool-result dict signals failure (error key or isError)."""
    return isinstance(result, dict) and ('error' in result or result.get('isError'))


def should_launch_training(count, force, floor):
    """Whether t3lab_train_model should actually launch the fine-tune. Pure.

    Below the floor a run wastes time (and the trainer would abort anyway),
    unless the caller explicitly forces it. At/above the floor it always runs.
    """
    try:
        if force:
            return True
        return int(count) >= int(floor)
    except Exception:
        return False


# ─── Raw session files (%APPDATA%/T3LabAI/training/mcp_sessions/*.jsonl) ──────────

def session_dir(appdata=None):
    base = appdata or os.environ.get('APPDATA', '') or os.path.expanduser('~')
    d = os.path.join(base, 'T3LabAI', 'training', 'mcp_sessions')
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
    except Exception:
        pass
    return d


# Session files the miner has ingested (renamed .done once their content is in
# the dataset) kept on disk, newest first. Nothing reads them again, so they
# are capped. Never deleted: an unprocessed *.jsonl (the miner still needs it)
# and a .nolabel (set aside without a goal — its only copy, not in the dataset).
MAX_PROCESSED_SESSIONS = 500
_PROCESSED_SUFFIXES = ('.jsonl.done',)
_PRUNED_DIRS = set()


def prune_sessions(directory=None, keep=None):
    """Keep the newest `keep` (default MAX_PROCESSED_SESSIONS) processed
    session files. Returns how many went. Never raises."""
    try:
        from core import housekeeping
        if keep is None:
            keep = MAX_PROCESSED_SESSIONS
        return housekeeping.prune_files(directory or session_dir(),
                                        keep_newest=keep,
                                        suffixes=_PROCESSED_SUFFIXES)
    except Exception:
        return 0


def append_step_line(path, goal, step):
    """Stream-append one {goal, step} record to a session file. ASCII-serialize-
    then-write via jsonsafe (IronPython 2.7 safe); never raises.

    The first write into a folder in this session also caps the processed
    session files there (prune_sessions)."""
    try:
        folder = os.path.dirname(path)
        if folder not in _PRUNED_DIRS:
            _PRUNED_DIRS.add(folder)
            prune_sessions(folder)
    except Exception:
        pass
    try:
        payload = jsonsafe.dumps({'goal': goal or u'', 'step': step})
        with io.open(path, 'a', encoding='utf-8') as f:
            f.write(payload + u'\n')
        return True
    except Exception:
        return False


def read_session(path):
    """Parse a raw session .jsonl into (goal, steps). goal is the last non-empty
    goal seen; steps is the ordered list of step dicts. Malformed lines skipped.
    Never raises."""
    goal = u''
    steps = []
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                g = u'{}'.format(obj.get('goal') or u'').strip()
                if g:
                    goal = g
                step = obj.get('step')
                if isinstance(step, dict):
                    steps.append(step)
    except Exception:
        pass
    return goal, steps
