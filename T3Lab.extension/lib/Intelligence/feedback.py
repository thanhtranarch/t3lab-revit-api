# -*- coding: utf-8 -*-
"""
Turning 👍 / 👎 into something the assistant acts on.

The chat has had per-reply thumbs since the message-actions row was added, and
every vote went to one line in a text activity log and nowhere else. The
machinery to act on them already existed too — learn_pattern() records a
phrasing→intent mapping and find_learned_match() replays it — but nothing ever
connected the two, and there was no way at all to record that a route was
WRONG.

This module is the missing half: a small on-disk record of routes the user
rejected, plus per-skill tallies.

    negative   phrasings whose route the user marked bad. routing
               .learned_pattern_wins() consults it, so a rejected mapping can
               never win a turn again on that phrasing.
    skills     up/down tallies per skill id, for reporting.

A down-vote is deliberately narrow. It suppresses the exact (phrasing, intent)
pair the user rejected — not the intent everywhere, and not the skill
everywhere. One bad answer is evidence about one route, and a feedback loop
that over-generalizes does more damage than no loop at all.

Pure Python, guarded imports, CPython-3 testable. Same ensure_ascii + io.open
write discipline as every other JSON writer here.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Feedback"

import io
import json
import os
import threading

# Rejected routes retained. Small on purpose: this is a correction list, not a
# history, and an unbounded one would slowly poison routing with stale entries.
_MAX_NEGATIVE = 120

_lock = threading.Lock()
_cache = None


# 👍/👎 votes are the user's usage data: per user in %APPDATA%/T3LabAI/
# assistant, never in the extension folder (a shared, public git clone; this
# file used to be committed). The old in-extension file is copied over once.
_LEGACY_FEEDBACK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'config', 'assistant_feedback.json')


def _feedback_file():
    from core.paths import user_data_path
    return user_data_path('assistant', 'assistant_feedback.json',
                          legacy=[_LEGACY_FEEDBACK_FILE])


def _blank():
    return {'negative': {}, 'skills': {}}


def load(force=False):
    """Read the store (cached). Never raises; a corrupt file reads as empty."""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        data = _blank()
        try:
            path = _feedback_file()
            if os.path.exists(path):
                with io.open(path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    data['negative'] = raw.get('negative') or {}
                    data['skills'] = raw.get('skills') or {}
        except Exception:
            data = _blank()
        _cache = data
        return _cache


def save(data):
    """Persist the store. Best-effort; never raises."""
    global _cache
    try:
        payload = json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True)
        if isinstance(payload, bytes):
            payload = payload.decode('ascii')
        with io.open(_feedback_file(), 'w', encoding='utf-8') as f:
            f.write(payload)
        with _lock:
            _cache = data
        return True
    except Exception:
        return False


def _key(raw):
    """Normalize a phrasing the same way the learned-pattern store does, so a
    suppression matches exactly the entry it is meant to suppress."""
    try:
        from Intelligence.t3lab_assistant import _normalize_key
        return _normalize_key(raw or '')
    except Exception:
        return u' '.join(sorted((raw or u'').lower().split()))


# ─── votes ────────────────────────────────────────────────────────────────────

def record_vote(vote, raw, intent=None, skill=None, specialist=None):
    """Apply one 👍 / 👎.

    vote: 'up' | 'down'. Anything else is ignored.

    Up  — clears any suppression on this phrasing (the user is telling us the
          route is right after all) and tallies the skill.
    Down — suppresses this exact (phrasing, intent) pair and tallies the skill.

    Returns True when something was written.
    """
    if vote not in ('up', 'down'):
        return False
    key = _key(raw)
    if not key:
        return False

    data = load()
    negative = dict(data.get('negative') or {})
    skills = dict(data.get('skills') or {})

    if vote == 'down':
        entry = dict(negative.get(key) or {})
        intents = list(entry.get('intents') or [])
        if intent and intent not in intents:
            intents.append(intent)
        entry['intents'] = intents
        entry['count'] = int(entry.get('count', 0)) + 1
        if specialist:
            entry['specialist'] = specialist
        negative[key] = entry
        if len(negative) > _MAX_NEGATIVE:
            # Drop the least-corroborated entries first.
            for k in sorted(negative,
                            key=lambda k: negative[k].get('count', 0)
                            )[:len(negative) - _MAX_NEGATIVE]:
                del negative[k]
    else:
        negative.pop(key, None)

    if skill:
        tally = dict(skills.get(skill) or {'up': 0, 'down': 0})
        tally[vote] = int(tally.get(vote, 0)) + 1
        skills[skill] = tally

    return save({'negative': negative, 'skills': skills})


def is_suppressed(raw, intent=None):
    """True when the user rejected this route for this phrasing.

    With no `intent`, answers whether the phrasing was rejected at all.
    """
    key = _key(raw)
    if not key:
        return False
    entry = (load().get('negative') or {}).get(key)
    if not entry:
        return False
    if intent is None:
        return True
    return intent in (entry.get('intents') or [])


def skill_score(skill_id):
    """(up, down) tallies for a skill."""
    tally = (load().get('skills') or {}).get(skill_id) or {}
    return int(tally.get('up', 0)), int(tally.get('down', 0))


def reset():
    """Clear everything. Used by tests and by an explicit user reset."""
    return save(_blank())
