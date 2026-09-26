# -*- coding: utf-8 -*-
"""
CPython 3 tests for the Assistant's old-context repeat guard.

Run:  python3 dev/test_assistant_repeat_guard.py
Exit 0 = all pass. Plain asserts, mirroring dev/test_assistant_history.py.

The bug this guards, observed 2026-08-10: the user typed ONE command,
"TÔ ĐỎ TƯỜNG", and the local model ran three — revit_override_color on
Walls/Red (correct), then Floors/Blue and Doors/Yellow, both lifted out of
earlier turns in the transcript — and then reported only the walls.

The turn-local guard cannot catch that: those calls carry different arguments
from anything run in the current turn. The old-context guard should have, but
it only remembered the single immediately-PREVIOUS turn, so a command from two
turns back was invisible to it. The registry is now conversation-wide (bounded).

T3LabAssistant.pushbutton/script.py cannot be imported here (clr / pyrevit /
Autodesk.Revit), so the helpers are extracted with `ast` and bound to a stand-in
object, the same approach dev/test_assistant_history.py uses.
"""
from __future__ import unicode_literals

import ast
import io
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
_dialog_path = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'T3LabAssistantDialog.py')
SCRIPT = _dialog_path if os.path.exists(_dialog_path) else os.path.join(
    TAB, 'Support.panel',
    'T3LabAssistant.pushbutton', 'script.py')

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


SRC = io.open(SCRIPT, encoding='utf-8').read()
TREE = ast.parse(SRC)


def _node(name):
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _build_guard():
    """A stand-in object carrying the two extracted guard methods."""
    ns = {'logger': type('L', (), {'debug': staticmethod(lambda *a, **k: None)})(),
          '_exc_text': lambda ex: str(ex)}
    for fn in ('_earlier_turn_calls', '_remember_turn_calls'):
        node = _node(fn)
        if node is None:
            raise AssertionError('helper not found: {}'.format(fn))
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<g>', 'exec'), ns)

    cap = None
    for n in ast.walk(TREE):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == '_MAX_REMEMBERED_CALLS':
                    cap = n.value.value
    if cap is None:
        raise AssertionError('_MAX_REMEMBERED_CALLS not found')

    Guard = type('Guard', (), {
        '_MAX_REMEMBERED_CALLS': cap,
        '_earlier_turn_calls': ns['_earlier_turn_calls'],
        '_remember_turn_calls': ns['_remember_turn_calls'],
    })
    g = Guard()
    g._prev_turn_calls = set()
    g._prev_turn_call_order = []
    return g, cap


def _key(tool, args):
    return (tool, args)


def test_remembers_across_many_turns():
    print('[repeat guard: registry spans the whole conversation]')
    g, _cap = _build_guard()

    red = _key('revit_override_color', '{"category":"Walls","color":"Red"}')
    blue = _key('revit_override_color', '{"category":"Floors","color":"Blue"}')
    yellow = _key('revit_override_color', '{"category":"Doors","color":"Yellow"}')

    g._remember_turn_calls({red})        # turn 1: "tô đỏ tường"
    g._remember_turn_calls({blue})       # turn 2: "tô xanh sàn"
    g._remember_turn_calls({yellow})     # turn 3: "tô vàng cửa"

    known = g._earlier_turn_calls()
    check('turn 1 still remembered two turns later', red in known,
          'this is the exact gap that let Floors/Blue through')
    check('turn 2 remembered', blue in known)
    check('turn 3 remembered', yellow in known)
    check('nothing else invented', len(known) == 3, known)


def test_first_call_of_a_turn_is_still_allowed():
    """The guard must not stop a user legitimately repeating a command."""
    print('[repeat guard: a repeat the user actually asked for is allowed]')
    g, _cap = _build_guard()
    red = _key('revit_override_color', '{"category":"Walls","color":"Red"}')
    g._remember_turn_calls({red})
    known = g._earlier_turn_calls()

    # The live condition is `_old_ctx = not _dup_now and bool(_turn_calls)
    # and _call_key in _prev_calls` — so a known call is only blocked once the
    # turn has already done its own, different work.
    turn_calls = set()
    blocked_first = bool(turn_calls) and red in known
    check('first call of the turn passes', not blocked_first)

    turn_calls.add(red)
    other = _key('revit_override_color', '{"category":"Floors","color":"Blue"}')
    g2, _ = _build_guard()
    g2._remember_turn_calls({other})
    blocked_later = bool(turn_calls) and other in g2._earlier_turn_calls()
    check('an earlier turn\'s different call is blocked after real work',
          blocked_later)


def test_registry_is_bounded():
    print('[repeat guard: bounded, oldest dropped first]')
    g, cap = _build_guard()
    for i in range(cap + 10):
        g._remember_turn_calls({_key('tool', '{"i":%d}' % i)})
    known = g._earlier_turn_calls()
    check('capped at _MAX_REMEMBERED_CALLS', len(known) == cap, len(known))
    check('oldest evicted', _key('tool', '{"i":0}') not in known)
    check('newest kept', _key('tool', '{"i":%d}' % (cap + 9)) in known)


def test_bookkeeping_never_raises():
    print('[repeat guard: bookkeeping is best-effort]')
    g, _cap = _build_guard()
    g._prev_turn_calls = None          # corrupted state from an older session
    g._prev_turn_call_order = None
    try:
        g._remember_turn_calls({_key('tool', '{}')})
        ok = _key('tool', '{}') in g._earlier_turn_calls()
    except Exception as ex:
        ok = False
        print('      raised: {}'.format(ex))
    check('recovers from a non-set registry', ok)

    g2, _ = _build_guard()
    try:
        g2._remember_turn_calls(set())
        empty_ok = g2._earlier_turn_calls() == set()
    except Exception:
        empty_ok = False
    check('an empty turn is a no-op', empty_ok)


def test_wired_into_the_loop():
    print('[repeat guard: the agent loop uses the helpers]')
    check('loop reads the conversation-wide set',
          '_prev_calls = self._earlier_turn_calls()' in SRC)
    check('loop writes through the helper',
          'self._remember_turn_calls(_turn_calls)' in SRC)
    check('no direct overwrite of the registry survives',
          'self._prev_turn_calls = set(_turn_calls)' not in SRC,
          'that assignment is what discarded every earlier turn')
    check('new conversation clears the order list',
          '_prev_turn_call_order = []' in SRC)


def test_server_attributes_referenced_by_the_pane_exist():
    """Every `srv.<attr>` in the pane must exist on T3LabAIServer.

    The pane passed `is_write_tool=srv.is_write_tool` to AgentLoop. That method
    has never existed, and because it is an ATTRIBUTE ACCESS it raised before
    AgentLoop was even built — so every Revit request took the exception path
    into the legacy JSON loop, where a local model's prose failed to parse and
    the user got "Could not read data from the model. Please try again."
    Nothing surfaced the AttributeError, so it read as a model problem.
    """
    print('[pane: srv.<attr> references resolve on T3LabAIServer]')
    sys.path.insert(0, os.path.join(REPO, 'T3Lab.extension', 'lib'))
    try:
        import core.server as S
    except Exception as ex:
        check('core.server importable', False, ex)
        return
    cls = S.T3LabAIServer

    # Instance attributes (self.x = ...) are not visible via hasattr on the
    # class, so collect those from the server's own source too — otherwise
    # every `srv._tools` style reference reads as missing.
    server_src = io.open(S.__file__.replace('.pyc', '.py'),
                         encoding='utf-8').read()
    instance_attrs = set(
        t.attr for n in ast.walk(ast.parse(server_src))
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
        and t.value.id == 'self')
    known = set(dir(cls)) | instance_attrs

    refs = sorted(set(
        n.attr for n in ast.walk(TREE)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        and n.value.id == 'srv'))
    check('some srv.<attr> references found', bool(refs), refs)
    missing = [a for a in refs if a not in known]
    check('all resolve on T3LabAIServer', not missing,
          'missing: {}'.format(missing))

    # And the specific wiring, so a rename cannot silently point it elsewhere.
    check('AgentLoop gets a real write-classifier',
          'is_write_tool=tool_schema.is_model_modifying' in SRC,
          'AgentLoop needs a callable name -> bool')
    check('tool_schema really exposes it',
          callable(getattr(__import__('Intelligence.tool_schema',
                                      fromlist=['x']),
                           'is_model_modifying', None)))


def main():
    test_server_attributes_referenced_by_the_pane_exist()
    test_remembers_across_many_turns()
    test_first_call_of_a_turn_is_still_allowed()
    test_registry_is_bounded()
    test_bookkeeping_never_raises()
    test_wired_into_the_loop()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All repeat-guard tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
