# -*- coding: utf-8 -*-
"""
CPython 3 test harness for Intelligence/assistant_memory.py.

Run:  python3 dev/test_assistant_memory.py
Exit code 0 = all pass. Plain asserts, mirroring dev/test_assistant_llm.py.

Locks down the memory-editing feature (update_fact / forget_fact) added so a
revised project convention supersedes the stale one instead of both being
injected into the system prompt every turn.

NOTE: assistant memory persists per user to
%APPDATA%/T3LabAI/assistant/assistant_memory.json, so every test repoints
M._memory_file at a throwaway temp file first — the real file is never touched.
"""
from __future__ import unicode_literals

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

from Intelligence import assistant_memory as M

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def _sandbox():
    """Point M at a fresh, empty memory file and return the project id used."""
    path = os.path.join(tempfile.mkdtemp(prefix='t3lab_mem_'),
                        'assistant_memory.json')
    M._memory_file = lambda: path
    return 'PID-TEST'


def _texts(pid):
    return [f.get('text') for _scope, f in M.list_facts(pid)]


# ─── update_fact ──────────────────────────────────────────────────────────────

def test_update_supersedes_in_place():
    print('[memory: update supersedes, no duplicate]')
    pid = _sandbox()
    M.add_fact('sheet prefix is WH-', scope='project', project_id=pid)
    M.add_fact('floor height 3.3m', scope='project', project_id=pid)

    ok, note = M.update_fact('sheet prefix', 'sheet prefix is EA-',
                             project_id=pid)
    check('update reports ok', ok, note)
    texts = _texts(pid)
    check('new text present', 'sheet prefix is EA-' in texts, texts)
    check('old text gone', 'sheet prefix is WH-' not in texts, texts)
    check('count unchanged (superseded, not appended)', len(texts) == 2, texts)
    check('the OTHER fact is untouched', 'floor height 3.3m' in texts, texts)


def test_update_by_number():
    print('[memory: update by 1-based number]')
    pid = _sandbox()
    M.add_fact('alpha', scope='project', project_id=pid)
    M.add_fact('beta', scope='project', project_id=pid)
    ok, _ = M.update_fact(1, 'alpha renamed', project_id=pid)
    check('numeric update ok', ok)
    check('fact #1 rewritten', _texts(pid) == ['alpha renamed', 'beta'],
          _texts(pid))


def test_update_missing_falls_back_to_add():
    print('[memory: update of an unknown fact still lands as a save]')
    pid = _sandbox()
    M.add_fact('existing', scope='project', project_id=pid)
    ok, _ = M.update_fact('nothing like this', 'brand new fact',
                          scope='project', project_id=pid)
    check('fell back to add (no info lost)', ok)
    check('new fact stored', 'brand new fact' in _texts(pid), _texts(pid))
    check('nothing overwritten', 'existing' in _texts(pid), _texts(pid))


def test_update_rejects_degenerate_new_text():
    print('[memory: update rejects empty new text]')
    pid = _sandbox()
    M.add_fact('keep me', scope='project', project_id=pid)
    ok, _ = M.update_fact('keep me', '   ', project_id=pid)
    check('empty new text refused', not ok)
    check('original left intact', _texts(pid) == ['keep me'], _texts(pid))


# ─── forget_fact ──────────────────────────────────────────────────────────────

def test_forget_by_exact_and_containment():
    print('[memory: forget by content match]')
    pid = _sandbox()
    M.add_fact('our sheet prefix is WH-', scope='project', project_id=pid)
    M.add_fact('always answer in millimetres', scope='global', project_id=pid)

    ok, removed = M.forget_fact('answer in millimetres', project_id=pid)
    check('containment match forgets', ok, removed)
    check('returns the stored text', removed == 'always answer in millimetres',
          removed)
    check('it left memory', 'always answer in millimetres' not in _texts(pid),
          _texts(pid))

    ok2, removed2 = M.forget_fact('our sheet prefix is WH-', project_id=pid)
    check('exact match forgets', ok2 and removed2 == 'our sheet prefix is WH-')
    check('memory now empty', _texts(pid) == [], _texts(pid))


def test_forget_missing_is_a_clean_miss():
    print('[memory: forget of an unknown fact reports no match]')
    pid = _sandbox()
    M.add_fact('the only fact', scope='project', project_id=pid)
    ok, removed = M.forget_fact('completely unrelated', project_id=pid)
    check('missing forget returns (False, None)',
          ok is False and removed is None, (ok, removed))
    check('nothing removed', _texts(pid) == ['the only fact'], _texts(pid))


# ─── regression: add_fact behaviour unchanged ─────────────────────────────────

def test_add_exact_dedup_still_holds():
    print('[memory: add still dedups on exact repeat]')
    pid = _sandbox()
    M.add_fact('one fact', scope='project', project_id=pid)
    ok, note = M.add_fact('one fact', scope='project', project_id=pid)
    check('duplicate save is a no-op', ok and 'Already' in note, note)
    check('still a single fact', len(_texts(pid)) == 1, _texts(pid))


# ─── scope API (the Settings memory manager) ──────────────────────────────────

def _scope_texts(scope, pid=None):
    return [f['text'] for f in M.list_scope_facts(scope, pid)]


def test_scope_listing_is_per_bucket():
    print('[memory scope: list one scope, 0-based index]')
    pid = _sandbox()
    M.add_fact('global one', scope='global')
    M.add_fact('global two', scope='global')
    M.add_fact('project one', scope='project', project_id=pid)
    M.add_fact('other project fact', scope='project', project_id='PID-OTHER')

    g = M.list_scope_facts(M.GLOBAL_SCOPE)
    p = M.list_scope_facts(M.PROJECT_SCOPE, pid)
    check('global list holds only global facts',
          [f['text'] for f in g] == ['global one', 'global two'], g)
    check('project list holds only this project',
          [f['text'] for f in p] == ['project one'], p)
    check('index is 0-based within the scope',
          [f['index'] for f in g] == [0, 1] and p[0]['index'] == 0)
    check('created date carried', bool(g[0]['created']), g[0])
    check('project scope without a project is empty',
          M.list_scope_facts(M.PROJECT_SCOPE, None) == [])
    check('count_scope', M.count_scope(M.GLOBAL_SCOPE) == 2
          and M.count_scope(M.PROJECT_SCOPE, pid) == 1)


def test_add_scope_fact_targets_exact_scope():
    print('[memory scope: add lands in the chosen list]')
    pid = _sandbox()
    ok, note = M.add_scope_fact('use metric units', M.PROJECT_SCOPE, None)
    check('project add without a project is refused (not silently global)',
          not ok and 'project' in note.lower(), note)
    check('nothing was stored', M.count_scope(M.GLOBAL_SCOPE) == 0)
    ok, _ = M.add_scope_fact('use metric units', M.GLOBAL_SCOPE)
    check('global add ok', ok and _scope_texts(M.GLOBAL_SCOPE) == ['use metric units'])
    ok, _ = M.add_scope_fact('prefix WH-', M.PROJECT_SCOPE, pid)
    check('project add ok', ok and _scope_texts(M.PROJECT_SCOPE, pid) == ['prefix WH-'])
    ok, note = M.add_scope_fact('x y z w', 'bogus', pid)
    check('unknown scope refused', not ok, note)


def test_edit_fact_at():
    print('[memory scope: edit by index]')
    pid = _sandbox()
    M.add_fact('alpha fact', scope='global')
    M.add_fact('beta fact', scope='project', project_id=pid)
    M.add_fact('gamma fact', scope='project', project_id=pid)

    ok, note = M.edit_fact_at(M.PROJECT_SCOPE, 1, '  gamma   revised ', pid)
    check('edit ok', ok, note)
    check('text normalised and replaced in place',
          _scope_texts(M.PROJECT_SCOPE, pid) == ['beta fact', 'gamma revised'],
          _scope_texts(M.PROJECT_SCOPE, pid))
    check('updated stamp written',
          bool(M.list_scope_facts(M.PROJECT_SCOPE, pid)[1]['updated']))
    check('global untouched by a project edit',
          _scope_texts(M.GLOBAL_SCOPE) == ['alpha fact'])

    ok, note = M.edit_fact_at(M.PROJECT_SCOPE, 1, 'BETA FACT', pid)
    check('duplicate of another fact in the scope refused', not ok, note)
    ok, note = M.edit_fact_at(M.PROJECT_SCOPE, 0, '  ', pid)
    check('degenerate text refused', not ok, note)
    ok, note = M.edit_fact_at(M.PROJECT_SCOPE, 7, 'out of range', pid)
    check('out-of-range index refused', not ok, note)
    ok, note = M.edit_fact_at(M.GLOBAL_SCOPE, 0, 'alpha fact', pid)
    check('same text is a no-op success', ok, note)
    long_text = 'x' * (M.MAX_FACT_CHARS + 50)
    M.edit_fact_at(M.GLOBAL_SCOPE, 0, long_text)
    check('edit is clipped like add',
          len(_scope_texts(M.GLOBAL_SCOPE)[0]) == M.MAX_FACT_CHARS)


def test_delete_fact_at():
    print('[memory scope: delete by index]')
    pid = _sandbox()
    M.add_fact('g keep', scope='global')
    M.add_fact('p first', scope='project', project_id=pid)
    M.add_fact('p second', scope='project', project_id=pid)

    ok, removed = M.delete_fact_at(M.PROJECT_SCOPE, 0, pid)
    check('delete ok + returns text', ok and removed == 'p first', removed)
    check('only that fact left the project',
          _scope_texts(M.PROJECT_SCOPE, pid) == ['p second'])
    check('global untouched', _scope_texts(M.GLOBAL_SCOPE) == ['g keep'])
    ok, removed = M.delete_fact_at(M.PROJECT_SCOPE, 5, pid)
    check('bad index is a clean miss', ok is False and removed is None)
    ok, removed = M.delete_fact_at(M.GLOBAL_SCOPE, 0)
    check('global delete', ok and removed == 'g keep'
          and M.count_scope(M.GLOBAL_SCOPE) == 0)


def test_clear_scope_never_crosses_scopes():
    print('[memory scope: clear one scope only]')
    pid = _sandbox()
    M.add_fact('g one', scope='global')
    M.add_fact('g two', scope='global')
    M.add_fact('p one', scope='project', project_id=pid)
    M.add_fact('p two', scope='project', project_id=pid)
    M.add_fact('other', scope='project', project_id='PID-OTHER')

    n = M.clear_scope(M.PROJECT_SCOPE, pid)
    check('project clear returns its count', n == 2, n)
    check('global memory survives a project clear',
          _scope_texts(M.GLOBAL_SCOPE) == ['g one', 'g two'])
    check('other projects survive', _scope_texts(M.PROJECT_SCOPE, 'PID-OTHER') == ['other'])
    check('cleared project key is dropped from the file',
          pid not in M._load()['projects'])
    check('clearing an unknown project is 0', M.clear_scope(M.PROJECT_SCOPE, 'nope') == 0)
    check('project clear without pid is 0 (never global)',
          M.clear_scope(M.PROJECT_SCOPE, None) == 0
          and M.count_scope(M.GLOBAL_SCOPE) == 2)

    n = M.clear_scope(M.GLOBAL_SCOPE)
    check('global clear returns its count', n == 2, n)
    check('projects survive a global clear',
          _scope_texts(M.PROJECT_SCOPE, 'PID-OTHER') == ['other'])
    check('prompt block now only carries the other project',
          'other' in M.build_memory_block('PID-OTHER')
          and M.build_memory_block(pid) == '')


def main():
    test_scope_listing_is_per_bucket()
    test_add_scope_fact_targets_exact_scope()
    test_edit_fact_at()
    test_delete_fact_at()
    test_clear_scope_never_crosses_scopes()
    test_update_supersedes_in_place()
    test_update_by_number()
    test_update_missing_falls_back_to_add()
    test_update_rejects_degenerate_new_text()
    test_forget_by_exact_and_containment()
    test_forget_missing_is_a_clean_miss()
    test_add_exact_dedup_still_holds()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant memory tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
