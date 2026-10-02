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


def main():
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
