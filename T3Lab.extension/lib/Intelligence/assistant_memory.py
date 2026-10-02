# -*- coding: utf-8 -*-
"""
Assistant Memory

Persistent cross-session memory for the T3Lab Assistant (Claude-style).
Stores short durable facts — user preferences and project conventions —
and serves them back as a system-prompt section so every new chat starts
with what previous chats already established.

Two scopes:
    global  — user preferences that apply everywhere ("answer in mm",
              "always show element ids").
    project — conventions of one project ("sheet prefix is WH-",
              "floor-to-floor is 3.3 m"), keyed by ProjectStore project id.

Facts get in three ways:
    1. Deterministic chat triggers ("remember that ...", "nhớ là ...")
       handled in script.py without an LLM round-trip.
    2. The `remember_fact` agent tool (tool_schema.make_memory_tool) —
       the model saves facts it judges durable during a conversation.
    3. The /memory command family (view / forget N / clear).

Storage: %APPDATA%/T3LabAI/assistant/assistant_memory.json — per user. The
facts are personal (preferences, client conventions), so they never live in
the extension folder, which is a shared, public git clone; the old
lib/Intelligence/config/assistant_memory.json is copied over once. Same
ASCII-serialize-then-write pattern as learned_patterns.json (guards against
the IronPython 2.7 UnicodeEncodeError mid-write that truncates the file to
0 bytes).

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Assistant Memory"

import io
import json
import os
import threading
from datetime import datetime

GLOBAL_SCOPE  = "global"
PROJECT_SCOPE = "project"

MAX_FACTS_PER_SCOPE = 30      # oldest dropped beyond this
MAX_FACT_CHARS      = 300     # longer facts are clipped
MIN_FACT_CHARS      = 4       # reject empty / degenerate saves

_LOCK = threading.Lock()


# ─── Storage ──────────────────────────────────────────────────────────────────

# Where older builds kept the facts (inside the extension). Read once.
_LEGACY_MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'config', 'assistant_memory.json')


def _memory_file():
    from core.paths import user_data_path
    return user_data_path('assistant', 'assistant_memory.json',
                          legacy=[_LEGACY_MEMORY_FILE])


def _load():
    try:
        path = _memory_file()
        if os.path.exists(path):
            with io.open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                if not isinstance(data.get('global'), list):
                    data['global'] = []
                if not isinstance(data.get('projects'), dict):
                    data['projects'] = {}
                return data
    except Exception:
        pass
    return {'version': 1, 'global': [], 'projects': {}}


def _save(data):
    try:
        payload = json.dumps(data, ensure_ascii=True, indent=2)
        if isinstance(payload, bytes):
            payload = payload.decode('ascii')
        with io.open(_memory_file(), 'w', encoding='utf-8') as f:
            f.write(payload)
        return True
    except Exception:
        return False


def _pid_key(project_id):
    return u'{}'.format(project_id) if project_id else None


# ─── Core API ─────────────────────────────────────────────────────────────────

def add_fact(text, scope=PROJECT_SCOPE, project_id=None):
    """Store one durable fact. Returns (ok, note) — note is user-showable."""
    text = u' '.join(u'{}'.format(text or u'').split())
    if len(text) < MIN_FACT_CHARS:
        return False, u'Nothing concrete to remember in that.'
    if len(text) > MAX_FACT_CHARS:
        text = text[:MAX_FACT_CHARS].rstrip()
    if scope not in (GLOBAL_SCOPE, PROJECT_SCOPE):
        scope = PROJECT_SCOPE
    if scope == PROJECT_SCOPE and not project_id:
        scope = GLOBAL_SCOPE   # no active project — keep the fact anyway
    with _LOCK:
        data = _load()
        if scope == PROJECT_SCOPE:
            bucket = data['projects'].setdefault(_pid_key(project_id), [])
        else:
            bucket = data['global']
        low = text.lower()
        for f in bucket:
            if (f.get('text') or u'').lower() == low:
                return True, u'Already in memory.'
        bucket.append({'text': text,
                       'created': datetime.now().strftime('%Y-%m-%d')})
        while len(bucket) > MAX_FACTS_PER_SCOPE:
            bucket.pop(0)
        ok = _save(data)
    if ok:
        where = (u'this project' if scope == PROJECT_SCOPE
                 else u'all projects')
        return True, u'Remembered ({}): {}'.format(where, text)
    return False, u'Could not write the memory file.'


def list_facts(project_id=None):
    """Facts visible to `project_id`: [(scope, fact_dict)], global first.

    Order is stable, so the 1-based numbering shown by /memory maps back
    to remove_fact() as long as memory is unchanged in between.
    """
    data = _load()
    out = [(GLOBAL_SCOPE, f) for f in data['global']]
    key = _pid_key(project_id)
    if key:
        for f in data['projects'].get(key, []):
            out.append((PROJECT_SCOPE, f))
    return out


def remove_fact(number, project_id=None):
    """Remove fact #number (1-based, list_facts order). (ok, removed_text)."""
    with _LOCK:
        data = _load()
        key = _pid_key(project_id)
        combined = [(GLOBAL_SCOPE, f) for f in data['global']]
        if key:
            for f in data['projects'].get(key, []):
                combined.append((PROJECT_SCOPE, f))
        try:
            idx = int(number) - 1
        except (TypeError, ValueError):
            return False, None
        if idx < 0 or idx >= len(combined):
            return False, None
        scope, fact = combined[idx]
        if scope == GLOBAL_SCOPE:
            data['global'].remove(fact)
        else:
            data['projects'][key].remove(fact)
        ok = _save(data)
    return ok, (fact.get('text') or u'')


def _norm(text):
    """Same normalization add_fact stores under: collapsed whitespace, lower."""
    return u' '.join(u'{}'.format(text or u'').split()).lower()


def _find_fact(query, project_id=None):
    """Locate one fact by CONTENT (not by number), across the visible scopes.

    Match order, most precise first: exact normalized equality, then
    containment either direction (stored ⊇ query or query ⊇ stored) so a
    short gist like "sheet prefix" finds "our sheet prefix is WH-".

    Returns (scope, fact_dict) of the first match, or (None, None).
    """
    q = _norm(query)
    if not q:
        return None, None
    facts = list_facts(project_id)   # global first, then this project
    for scope, f in facts:           # exact wins over containment
        if _norm(f.get('text')) == q:
            return scope, f
    for scope, f in facts:
        ft = _norm(f.get('text'))
        if q in ft or ft in q:
            return scope, f
    return None, None


def forget_fact(text, project_id=None):
    """Remove one fact matched by CONTENT. Returns (ok, removed_text_or_none).

    Complements remove_fact(number): the model (and `/memory forget <text>`)
    can drop a superseded convention without knowing its position.
    """
    with _LOCK:
        data = _load()
        key = _pid_key(project_id)
        # Rebuild the same visible view _find_fact used, but keep references
        # into the real buckets so we can remove the matched dict.
        scope, fact = _find_fact(text, project_id)
        if fact is None:
            return False, None
        if scope == GLOBAL_SCOPE:
            try:
                data['global'].remove(_match_in(data['global'], fact))
            except ValueError:
                return False, None
        else:
            bucket = data['projects'].get(key, [])
            try:
                bucket.remove(_match_in(bucket, fact))
            except ValueError:
                return False, None
        ok = _save(data)
    return ok, (fact.get('text') or u'')


def _match_in(bucket, fact):
    """Return the dict in `bucket` whose normalized text equals `fact`'s.

    _find_fact reads through _load(); forget_fact/update_fact then reopen the
    file under the lock, so the dict identity differs — match on content.
    """
    target = _norm(fact.get('text'))
    for f in bucket:
        if _norm(f.get('text')) == target:
            return f
    raise ValueError('no match')


def update_fact(old, new, scope=PROJECT_SCOPE, project_id=None):
    """Replace an existing fact's text in place. Returns (ok, note).

    `old` may be a 1-based number (like remove_fact) or a content gist (like
    forget_fact). When nothing matches, this falls back to add_fact(new) so an
    "update" never silently loses the new information.
    """
    new_text = u' '.join(u'{}'.format(new or u'').split())
    if len(new_text) < MIN_FACT_CHARS:
        return False, u'Nothing concrete to remember in that.'
    if len(new_text) > MAX_FACT_CHARS:
        new_text = new_text[:MAX_FACT_CHARS].rstrip()

    # Resolve `old` to a fact. A bare integer means "fact #N" (list_facts order).
    is_number = False
    try:
        num = int(old)
        is_number = True
    except (TypeError, ValueError):
        num = None

    with _LOCK:
        data = _load()
        key = _pid_key(project_id)
        if is_number:
            combined = [(GLOBAL_SCOPE, f) for f in data['global']]
            if key:
                for f in data['projects'].get(key, []):
                    combined.append((PROJECT_SCOPE, f))
            idx = num - 1
            if idx < 0 or idx >= len(combined):
                target = None
            else:
                _sc, target = combined[idx]
        else:
            _sc, found = _find_fact(old, project_id)
            if found is None:
                target = None
            else:
                bucket = (data['global'] if _sc == GLOBAL_SCOPE
                          else data['projects'].get(key, []))
                try:
                    target = _match_in(bucket, found)
                except ValueError:
                    target = None
        if target is not None:
            target['text'] = new_text
            ok = _save(data)
            if ok:
                return True, u'Updated: {}'.format(new_text)
            return False, u'Could not write the memory file.'

    # No match — treat as a fresh save so the correction still lands.
    return add_fact(new_text, scope=scope, project_id=project_id)


def clear_facts(project_id=None, everything=False):
    """Clear this project's facts (+ global too when everything=True).

    Returns how many facts were removed.
    """
    with _LOCK:
        data = _load()
        n = 0
        key = _pid_key(project_id)
        if key and key in data['projects']:
            n += len(data['projects'][key])
            data['projects'][key] = []
        if everything:
            n += len(data['global'])
            data['global'] = []
        if n:
            _save(data)
    return n


# ─── Prompt / report rendering ────────────────────────────────────────────────

def build_memory_block(project_id=None, include_tool_hint=True):
    """System-prompt section carrying every remembered fact ('' when none).

    include_tool_hint=False for prompt paths that have no remember_fact
    tool (the legacy JSON-intent path) — mentioning an uncallable tool
    there would leak into the "message" field.
    """
    facts = list_facts(project_id)
    if not facts:
        return u''
    lines = [u'## Persistent memory '
             u'(facts saved in previous chats — apply them without re-asking)']
    for i, (scope, f) in enumerate(facts):
        tag = u'all projects' if scope == GLOBAL_SCOPE else u'this project'
        lines.append(u'{}. [{}] {}'.format(i + 1, tag, f.get('text') or u''))
    if include_tool_hint:
        lines.append(
            u'When the user states a NEW durable preference or convention '
            u'("from now on...", "remember...", "our standard is..."), call '
            u'the `remember_fact` tool ONCE with a short English sentence — '
            u'scope "global" for user preferences, "project" for facts about '
            u'this model. Never store one-off requests, secrets, or element '
            u'ids. If the user CHANGES a fact above, call `remember_fact` with '
            u'action "update" (replaces=old gist, fact=new statement) so the '
            u'old one is superseded, not duplicated. If the user CANCELS a '
            u'fact, call it with action "forget" (replaces=gist).')
    return u'\n'.join(lines)


def format_memory_report(project_id=None, viet=False):
    """Markdown chat report for the /memory command."""
    facts = list_facts(project_id)
    if not facts:
        if viet:
            return (u'Tôi chưa lưu ghi nhớ nào. Nói **"nhớ là ..."** hoặc '
                    u'**"remember that ..."** để tôi ghi nhớ sở thích của bạn '
                    u'hoặc quy ước của dự án cho các lần chat sau.')
        return (u'Nothing in memory yet. Say **"remember that ..."** and I will '
                u'keep that preference or project convention for future chats.')
    lines = []
    if viet:
        lines.append(u'**Ghi nhớ hiện có** ({} mục):'.format(len(facts)))
    else:
        lines.append(u'**Current memory** ({} facts):'.format(len(facts)))
    lines.append(u'')
    lines.append(u'| # | Scope | Fact | Saved |')
    lines.append(u'|---|-------|------|-------|')
    for i, (scope, f) in enumerate(facts):
        tag = u'All projects' if scope == GLOBAL_SCOPE else u'This project'
        txt = (f.get('text') or u'').replace(u'|', u'\\|')
        lines.append(u'| {} | {} | {} | {} |'.format(
            i + 1, tag, txt, f.get('created') or u''))
    lines.append(u'')
    if viet:
        lines.append(u'Xóa 1 mục: `/memory forget <số|nội dung>` · Xóa hết: '
                     u'`/memory clear`')
    else:
        lines.append(u'Remove one: `/memory forget <number|text>` · '
                     u'Remove all: `/memory clear`')
    return u'\n'.join(lines)
