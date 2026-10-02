# -*- coding: utf-8 -*-
"""
CPython 3 tests for the Assistant's Cowork-style agent behaviour.

Run:  python3 dev/test_assistant_cowork.py
Exit code 0 = all pass. Plain asserts, mirroring dev/test_assistant_history.py.

What is locked down here:

  * Plan → approve → run: a multi-step plan with a step that changes the model
    waits on its plan card and runs NOTHING until Run; a read-only plan runs
    immediately; Cancel / Stop / a new message never run it.
  * "Ask before edits" is enforced in code (ToolGate), on the native AgentLoop
    path AND the legacy JSON-intent path, which share one execution helper;
    the legacy path also gets the destructive confirm and the first-purge dry
    run it used to skip.
  * The memory block reaches the legacy prompt exactly once and never tells
    that path to call `remember_fact`; personal instructions come before
    project instructions.
  * Levels and grids reach the live context from a cached per-document digest.
  * Workspace pieces: /memory clear keeps global memory, archives go to the
    project's own folder, History search / scope / rename are wired.

T3LabAssistantDialog.py cannot be imported here (clr / pyrevit / WPF), so its
methods are extracted with `ast` and bound to stand-in objects — the same
approach dev/test_assistant_history.py and test_assistant_repeat_guard.py use.
"""
from __future__ import unicode_literals

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB not in sys.path:
    sys.path.insert(0, LIB)

DLG = os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py')
XAML = os.path.join(LIB, 'GUI', 'Tools', 'T3LabAssistant.xaml')
SRC = io.open(DLG, encoding='utf-8').read()
TREE = ast.parse(SRC)
XAML_SRC = io.open(XAML, encoding='utf-8').read()

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


# ─── ast extraction ───────────────────────────────────────────────────────────

def _window_class():
    for n in TREE.body:
        if isinstance(n, ast.ClassDef) and n.name == 'T3LabAssistantWindow':
            return n
    raise AssertionError('T3LabAssistantWindow not found')


_CLS = _window_class()


def _method_node(name):
    for n in _CLS.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError('method not found: {}'.format(name))


def _class_attr_nodes(names):
    out = []
    for n in _CLS.body:
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in n.targets):
            out.append(n)
    return out


def _module_assign_nodes(names):
    out = []
    for n in TREE.body:
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in n.targets):
            out.append(n)
    return out


class _Log(object):
    def debug(self, *a, **k):
        pass
    error = warning = info = debug


def _base_ns(extra=None):
    ns = {'logger': _Log(), '_exc_text': lambda ex: u'{}'.format(ex),
          'os': os, 'io': io, 'json': json, 'sys': sys, 're': __import__('re'),
          'datetime': __import__('datetime'),
          '_is_viet_text': lambda t: False}
    consts = ['_PLAN_RUN', '_PLAN_EDIT', '_PLAN_CANCEL', '_PLAN_SUPERSEDED',
              '_PLAN_APPROVAL_TIMEOUT', '_CONFIRM_TIMEOUT']
    for node in _module_assign_nodes(consts):
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<c>', 'exec'),
             ns)
    for icon in ('_ICON_SYNC', '_ICON_SLATE', '_ICON_WARNING', '_ICON_AMBER',
                 '_ICON_INFO', '_ICON_REFRESH', '_ICON_LIST', '_ICON_GREEN',
                 '_ICON_SUCCESS', '_ICON_STOP', '_ICON_EDIT'):
        ns.setdefault(icon, icon)
    ns.update(extra or {})
    return ns


def _standin(methods, ns=None, class_attrs=()):
    """A class carrying the named dialog methods, exec'd in `ns`."""
    ns = _base_ns(ns)
    body = {}
    for name in methods:
        node = _method_node(name)
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     '<{}>'.format(name), 'exec'), ns)
        body[name] = ns[name]
    for node in _class_attr_nodes(set(class_attrs)):
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<a>', 'exec'),
             ns)
        for t in node.targets:
            body[t.id] = ns[t.id]
    return type('Stand', (object,), body)


def _nested(func_name, inner_name):
    """The nested def `inner_name` inside method `func_name`."""
    for n in ast.walk(_method_node(func_name)):
        if isinstance(n, ast.FunctionDef) and n.name == inner_name:
            return n
    raise AssertionError('{} not found in {}'.format(inner_name, func_name))


def _calls(node, attr, owner=None):
    """Call nodes `<owner>.<attr>(...)` (any owner when owner is None)."""
    out = []
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == attr):
            if owner is None or (isinstance(n.func.value, ast.Name)
                                 and n.func.value.id == owner):
                out.append(n)
    return out


class _Patch(object):
    """Set attributes / sys.modules entries for one test, restore after."""

    def __init__(self):
        self._undo = []

    def attr(self, obj, name, value):
        missing = object()
        old = getattr(obj, name, missing)
        self._undo.append((obj, name, old, missing))
        setattr(obj, name, value)

    def module(self, name, mod):
        self._undo.append((sys.modules, name, sys.modules.get(name), None))
        sys.modules[name] = mod

    def env(self, key, value):
        self._undo.append((os.environ, key, os.environ.get(key), None))
        os.environ[key] = value

    def undo(self):
        for obj, name, old, missing in reversed(self._undo):
            if obj is sys.modules or obj is os.environ:
                if old is None:
                    obj.pop(name, None)
                else:
                    obj[name] = old
            elif old is missing:
                delattr(obj, name)
            else:
                setattr(obj, name, old)
        self._undo = []


# ─────────────────────────────────────────────────────────────────────────────
# Tool gate (pure)
# ─────────────────────────────────────────────────────────────────────────────

def _destructive(name, args):
    if name == 'delete_element':
        return True
    if name == 'purge_unused' and not bool(args.get('dry_run', True)):
        return True
    if name == 'check_bad_geometry' and bool(args.get('deep_probe')):
        return True
    return False


def test_tool_gate_policy():
    print('[gate: what needs approval]')
    from Intelligence.agent_loop import (ToolGate, GATE_RUN, GATE_EDIT,
                                         GATE_DESTRUCTIVE)
    from Intelligence.tool_schema import LAUNCHER_TOOL_NAME, MEMORY_TOOL_NAME

    auto = ToolGate('auto', is_destructive=_destructive)
    check('auto: an ordinary edit runs',
          auto.verdict('revit_override_color', {}) == GATE_RUN)
    check('auto: a read runs', auto.verdict('list_levels', {}) == GATE_RUN)
    check('auto: destructive still confirms',
          auto.verdict('delete_element', {}) == GATE_DESTRUCTIVE)

    conf = ToolGate('confirm', is_destructive=_destructive)
    check('confirm: a NON-destructive edit is held',
          conf.verdict('revit_override_color', {}) == GATE_EDIT)
    check('confirm: an unknown tool is held (fail closed)',
          conf.verdict('some_new_tool', {}) == GATE_EDIT)
    check('confirm: reads never ask',
          conf.verdict('list_levels', {}) == GATE_RUN
          and conf.verdict('ai_element_filter', {}) == GATE_RUN)
    check('confirm: a purge DRY RUN only reports',
          conf.verdict('purge_unused', {'dry_run': True}) == GATE_RUN)
    check('confirm: destructive stays destructive',
          conf.verdict('delete_element', {}) == GATE_DESTRUCTIVE)
    check('confirm: exports only write a file, so they never ask',
          all(conf.verdict(n, {}) == GATE_RUN for n in
              ('export_sheets_pdf', 'export_dwg', 'export_image',
               'export_model', 'export_room_data')))
    check('confirm: exports still count as modifying elsewhere (skills)',
          conf.modifies('export_sheets_pdf', {}))
    check('confirm: launcher / memory pseudo-tools never ask',
          conf.verdict(LAUNCHER_TOOL_NAME, {}) == GATE_RUN
          and conf.verdict(MEMORY_TOOL_NAME, {}) == GATE_RUN)
    check('destructive read (deep probe) confirms in every mode',
          auto.verdict('check_bad_geometry', {'deep_probe': True})
          == GATE_DESTRUCTIVE)

    pre = ToolGate('confirm', is_destructive=_destructive,
                   preapproved_edits=True)
    check('approved plan: edits run without a second ask',
          pre.verdict('revit_override_color', {}) == GATE_RUN)
    check('approved plan: destructive still asks',
          pre.verdict('delete_element', {}) == GATE_DESTRUCTIVE)

    def _boom(name, args):
        raise RuntimeError('classifier broke')
    check('a classifier that raises asks instead of running',
          ToolGate('auto', is_destructive=_boom).verdict('x', {})
          == GATE_DESTRUCTIVE)
    check('a bogus mode reads as auto', ToolGate('nonsense').action_mode
          == 'auto')

    g = ToolGate('auto', is_destructive=_destructive)
    args, v = g.prepare('purge_unused', {'dry_run': False})
    check('first purge is forced to a dry run', args.get('dry_run') is True
          and v == GATE_RUN, (args, v))
    args2, v2 = g.prepare('purge_unused', {'dry_run': False})
    check('second purge is the real (destructive) one',
          args2.get('dry_run') is False and v2 == GATE_DESTRUCTIVE, (args2, v2))
    check('modifies(): dry-run purge does not modify',
          not g.modifies('purge_unused', {'dry_run': True})
          and g.modifies('revit_override_color', {}))


def test_read_only_tool_filter():
    print('[gate: background tasks only see read tools]')
    from Intelligence.agent_loop import read_only_tools
    tools = [{'name': 'list_levels'}, {'name': 'revit_override_color'},
             {'function': {'name': 'ai_element_filter'}},
             {'function': {'name': 'delete_element'}}]
    names = []
    for t in read_only_tools(tools):
        names.append(t.get('name') or t['function']['name'])
    check('write tools removed, both schema shapes',
          names == ['list_levels', 'ai_element_filter'], names)
    run = _method_node('_run_agent_text')
    check('parallel runner filters its catalog',
          bool(_calls(run, 'read_only_tools') or
               [n for n in ast.walk(run) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == 'read_only_tools']))
    ex = _nested('_run_agent_text', '_exec')
    check('parallel _exec refuses a modifying call',
          bool(_calls(ex, 'is_model_modifying')))


def test_pending_decision():
    print('[gate: one answer from the UI thread]')
    from Intelligence.agent_loop import PendingDecision
    p = PendingDecision()
    check('first decision wins', p.decide('run') and not p.decide('cancel')
          and p.value == 'run')
    p2 = PendingDecision()
    check('Stop releases a waiting card',
          p2.wait(5, should_abort=lambda: True, poll=0.01)
          == PendingDecision.STOPPED)
    check('a click after Stop changes nothing',
          not p2.decide('run') and p2.value == PendingDecision.STOPPED)
    p3 = PendingDecision()
    check('no answer expires', p3.wait(0.05, poll=0.01)
          == PendingDecision.EXPIRED)
    p4 = PendingDecision()
    threading.Timer(0.05, lambda: p4.decide(True)).start()
    t0 = time.time()
    got = p4.wait(5, poll=0.01)
    check('a click from another thread unblocks the worker',
          got is True and time.time() - t0 < 2, (got, time.time() - t0))


# ─────────────────────────────────────────────────────────────────────────────
# Plan → approve → run
# ─────────────────────────────────────────────────────────────────────────────

def _planner():
    from Intelligence.graph.planner import Planner
    from Intelligence.agents.dispatcher import AgentDispatcher
    return Planner(dispatcher=AgentDispatcher())


WRITE_PLAN = u'count the walls then color all walls red'
READ_PLAN = u'count the walls then list the levels'


def test_plan_review_helpers():
    print('[plan: review helpers]')
    wp = _planner().plan(WRITE_PLAN)
    rp = _planner().plan(READ_PLAN)
    check('a plan with an edit needs approval', wp.needs_approval())
    check('a read-only plan does not', rp.is_multi and not rp.needs_approval())
    single = _planner().plan(u'color all walls red')
    check('a single goal never needs a plan card',
          not single.needs_approval())
    steps = wp.review_steps()
    check('steps are numbered in order',
          [s['index'] for s in steps] == [1, 2], steps)
    check('the edit step is flagged, the read is not',
          [s['modifies'] for s in steps] == [False, True], steps)
    check('steps carry a plain-English role',
          steps[0]['role'] == 'Reads model data'
          and steps[1]['role'] == 'Edits the model', steps)
    text = wp.review_text()
    check('Edit text is a numbered list',
          text == u'1. count the walls\n2. color all walls red', text)
    again = _planner().plan(text)
    check('resending the edited list re-plans the same steps',
          [n.goal for n in again.agent_nodes()]
          == [n.goal for n in wp.agent_nodes()],
          [n.goal for n in again.agent_nodes()])


class _FakeOrchestrator(object):
    def __init__(self, plan, owner):
        self.plan = plan
        self.owner = owner
        self.handled = []

    def plan_for(self, raw, utterance=None):
        return self.plan

    def handle(self, raw, utterance=None, plan=None, lang=None):
        self.handled.append({'t': time.time(),
                             'preapproved': self.owner._plan_preapproved})
        from Intelligence.graph.primitives import GraphState
        return types.SimpleNamespace(plan=plan, state=GraphState(goal=raw))


def _graph_standin(plan, cancel=False):
    S = _standin(['_run_graph_plan', '_plan_needs_approval',
                  '_await_plan_approval', '_end_unapproved_plan'])
    s = S()
    s._request_id = 1
    s._plan_preapproved = False
    s._pending_plan = None
    s.cancel_flag = cancel
    s.cards, s.seals, s.events, s.history = [], [], [], []
    s.orch = _FakeOrchestrator(plan, s)
    s._graph_enabled = lambda: True
    s._parallel_tasks_enabled = lambda: False
    s._cancelled = lambda: s.cancel_flag
    s._build_orchestrator = (lambda provider, history, viet, progress=None:
                             s.orch)
    s._ui_invoke = lambda fn: fn()
    s._hide_typing_indicator = lambda: None
    s._show_loading_bar = lambda visible: s.events.append(('bar', visible))

    def _card(plan_, pending, viet):
        s.cards.append(pending)
        return lambda msg: s.seals.append(msg)
    s._append_plan_card = _card
    s._append_plan_progress_card = (
        lambda plan_, viet: s.events.append('progress') or {'card': True})
    s._finalize_plan_progress = (
        lambda progress, state, cancelled=False: s.events.append('finalize'))
    s._report_error = lambda *a, **k: s.events.append('error')
    s._finish_cancelled = lambda rid=None, note=None: s.events.append(
        ('stopped', note))
    s._claim_turn = lambda rid=None: True
    s._append_bot_message = lambda *a, **k: s.events.append('bubble')
    s._add_to_history = lambda role, text: s.history.append((role, text))
    s._set_busy = lambda b: s.events.append(('busy', b))
    s._graph_summary = lambda result, viet: u'summary'
    s._action_mode = lambda: 'auto'
    return s


def _with_fake_router(fn):
    patch = _Patch()
    mod = types.ModuleType('Intelligence.llm_router')

    class LLMRouter(object):
        def get_active_provider(self):
            return types.SimpleNamespace(SUPPORTS_NATIVE_TOOLS=True,
                                         NAME='claude')
    mod.LLMRouter = LLMRouter
    patch.module('Intelligence.llm_router', mod)
    try:
        return fn()
    finally:
        patch.undo()


def test_plan_with_an_edit_waits_for_run():
    print('[plan: an edit plan waits for Run]')
    plan = _planner().plan(WRITE_PLAN)
    s = _graph_standin(plan)
    out = {}

    def _go():
        out['ret'] = s._run_graph_plan(WRITE_PLAN, [])
    t = threading.Thread(target=lambda: _with_fake_router(_go))
    t.daemon = True
    t.start()
    deadline = time.time() + 5
    while not s.cards and time.time() < deadline:
        time.sleep(0.01)
    check('the plan card was shown', len(s.cards) == 1)
    time.sleep(0.4)
    check('NOTHING ran while the card waits', s.orch.handled == [],
          s.orch.handled)
    check('the worker is still blocked on the card', t.is_alive())
    check('a new message would find the pending plan',
          s._pending_plan is not None)
    t_click = time.time()
    if s.cards:
        s.cards[0].decide('run')
    t.join(5)
    check('Run released it and the plan ran',
          len(s.orch.handled) == 1 and s.orch.handled[0]['t'] >= t_click,
          s.orch.handled)
    check('its edits were pre-approved while it ran',
          bool(s.orch.handled) and s.orch.handled[0]['preapproved'])
    check('pre-approval ends with the plan', s._plan_preapproved is False)
    check('a progress card was shown, then settled',
          'progress' in s.events and 'finalize' in s.events, s.events)
    check('no busy bar while waiting on the user, back on Run',
          ('bar', False) in s.events and ('bar', True) in s.events
          and s.events.index(('bar', False)) < s.events.index(('bar', True)),
          s.events)
    check('turn handled', out.get('ret') is True)


def test_read_only_plan_runs_immediately():
    print('[plan: a read-only plan runs at once]')
    plan = _planner().plan(READ_PLAN)
    s = _graph_standin(plan)
    ret = _with_fake_router(lambda: s._run_graph_plan(READ_PLAN, []))
    check('no plan card for a read-only plan', s.cards == [])
    check('it ran straight away', len(s.orch.handled) == 1)
    check('reads are not "pre-approved" edits',
          s.orch.handled and not s.orch.handled[0]['preapproved'])
    check('no progress card (only approved plans get one)',
          'progress' not in s.events, s.events)
    check('turn handled', ret is True)


def _decided_run(decision):
    plan = _planner().plan(WRITE_PLAN)
    s = _graph_standin(plan)

    def _go():
        return s._run_graph_plan(WRITE_PLAN, [])
    t = threading.Thread(target=lambda: _with_fake_router(_go))
    t.daemon = True
    t.start()
    deadline = time.time() + 5
    while not s.cards and time.time() < deadline:
        time.sleep(0.01)
    if s.cards:
        s.cards[0].decide(decision)
    t.join(5)
    return s


def test_unapproved_plans_never_run():
    print('[plan: Cancel / Edit / new message / Stop run nothing]')
    for decision in ('cancel', 'edit', 'superseded'):
        s = _decided_run(decision)
        check('{}: nothing ran'.format(decision), s.orch.handled == [])
        check('{}: turn released'.format(decision),
              ('busy', False) in s.events, s.events)
        rec = [h for r, h in s.history if r == 'assistant']
        check('{}: the plan is in the transcript for follow-ups'.format(
            decision), rec and u'2. color all walls red' in rec[-1], rec)
    s = _decided_run('cancel')
    check('cancel: no extra bubble (the card says it)',
          'bubble' not in s.events, s.events)

    plan = _planner().plan(WRITE_PLAN)
    s = _graph_standin(plan, cancel=True)
    _with_fake_router(lambda: s._run_graph_plan(WRITE_PLAN, []))
    check('Stop: nothing ran', s.orch.handled == [])
    check('Stop: ends as a stopped turn',
          any(isinstance(e, tuple) and e[0] == 'stopped' for e in s.events),
          s.events)
    check('Stop: the card is sealed', s.seals and 'Stopped' in s.seals[-1],
          s.seals)

    check('a message sent while the card waits supersedes it',
          '_plan_wait.decide(_PLAN_SUPERSEDED)' in SRC)


def test_progress_card_final_states():
    print('[plan: progress card settles from the graph state]')
    S = _standin(['_final_step_state'])
    f = S._final_step_state
    check('graph says done', f('done', 'running', False) == 'done')
    check('verifier failed a step that returned text',
          f('failed', 'done', False) == 'failed')
    check('a fallback route rescued a failed step',
          f('done', 'failed', False) == 'done')
    check('never started + Stop = stopped',
          f('pending', 'pending', True) == 'cancelled')
    check('never started, no Stop = skipped',
          f('pending', 'pending', False) == 'skipped')
    check('crashed run keeps what the card saw',
          f(None, 'done', False) == 'done')
    check('crashed mid-step = failed', f(None, 'running', False) == 'failed')

    # every state has a word as well as a colour
    look = None
    for node in _module_assign_nodes({'_STEP_LOOK'}):
        ns = _base_ns()
        for icon in ('_ICON_PENDING', '_ICON_FAILED'):
            ns[icon] = icon
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<l>', 'exec'),
             ns)
        look = ns['_STEP_LOOK']
    check('step states: pending/running/done/failed all present',
          look and all(k in look for k in ('pending', 'running', 'done',
                                           'failed')), look)
    check('every state carries a word, not just a colour',
          look and all(v[1] for v in look.values()), look)


# ─────────────────────────────────────────────────────────────────────────────
# "Ask before edits" on both paths
# ─────────────────────────────────────────────────────────────────────────────

class _FakeSrv(object):
    def __init__(self):
        self.ran = []

    def is_destructive(self, name, args=None):
        return _destructive(name, args or {})

    def _execute_tool(self, name, args):
        self.ran.append((name, dict(args)))
        return {'success': True, 'name': name}


def _gate_standin(mode, confirm_answer):
    S = _standin(['_new_tool_gate', '_run_gated_tool', '_note_model_changed'])
    s = S()
    s.asked = []
    s._plan_preapproved = False
    s._action_mode = lambda: mode

    def _confirm(name, args, viet, timeout_sec=None, kind='destructive'):
        s.asked.append((name, kind))
        return confirm_answer
    s._confirm_tool_blocking = _confirm
    return s


def test_confirm_mode_gates_a_write_on_both_paths():
    print('[ask before edits: enforced in code]')
    s = _gate_standin('confirm', False)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    res = s._run_gated_tool(srv, gate, 'revit_override_color',
                            {'category': 'Walls', 'color': 'red'}, False)
    check('a non-destructive edit asked first', s.asked ==
          [('revit_override_color', 'edit')], s.asked)
    check('declined = never executed', srv.ran == [], srv.ran)
    check('the model is told it was declined',
          isinstance(res, dict) and res.get('cancelled'), res)

    s = _gate_standin('confirm', True)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    s._run_gated_tool(srv, gate, 'revit_override_color', {}, False)
    check('confirmed = executed once', srv.ran ==
          [('revit_override_color', {})], srv.ran)
    s._run_gated_tool(srv, gate, 'list_levels', {}, False)
    check('reads never asked', s.asked == [('revit_override_color', 'edit')],
          s.asked)

    s = _gate_standin('auto', True)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    s._run_gated_tool(srv, gate, 'revit_override_color', {}, False)
    check('auto mode: an ordinary edit runs without a card',
          s.asked == [] and len(srv.ran) == 1, (s.asked, srv.ran))

    # Native path end to end: a real AgentLoop driving the shared helper.
    from Intelligence.agent_loop import AgentLoop
    from Intelligence.tool_schema import is_model_modifying

    class _Prov(object):
        NAME = 'claude'

        def __init__(self):
            self.turn = 0

        def chat_agent(self, system_prompt, messages, tools, on_delta=None,
                       max_tokens=None):
            self.turn += 1
            if self.turn == 1:
                return {'text': u'', 'tool_calls': [
                    {'id': 't1', 'name': 'revit_override_color',
                     'args': {'category': 'Walls', 'color': 'red'}}],
                    'assistant_msg': {'role': 'assistant', 'content': u''}}
            return {'text': u'Skipped — you declined.', 'tool_calls': [],
                    'assistant_msg': {'role': 'assistant',
                                      'content': u'Skipped'}}

        def agent_tool_results(self, calls, results):
            self.results = results
            return [{'role': 'user', 'content': r} for r in results]

    s = _gate_standin('confirm', False)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    prov = _Prov()
    loop = AgentLoop(prov, lambda n, a: s._run_gated_tool(srv, gate, n, a,
                                                          False),
                     [{'name': 'revit_override_color'}],
                     is_write_tool=is_model_modifying)
    out = loop.run([], u'sys', u'color the walls red')
    check('native loop: the write was held and declined',
          srv.ran == [] and s.asked == [('revit_override_color', 'edit')],
          (srv.ran, s.asked))
    check('native loop: the model saw the decline and finished',
          out.get('status') == 'done'
          and 'cancelled' in (getattr(prov, 'results', [u''])[0] or u''),
          out)

    # Wiring: both paths execute through the ONE shared helper.
    ex = _nested('_run_native_agent', '_exec_tool')
    check('native _exec_tool goes through _run_gated_tool',
          bool(_calls(ex, '_run_gated_tool', 'self')))
    check('native _exec_tool never calls srv._execute_tool directly',
          not _calls(ex, '_execute_tool', 'srv'))
    nlp = _nested('_route_input', 'do_nlp')
    check('legacy loop goes through _run_gated_tool',
          bool(_calls(nlp, '_run_gated_tool', 'self')))
    check('legacy loop never calls srv._execute_tool directly',
          not _calls(nlp, '_execute_tool', 'srv'))
    batch = _nested('_run_native_agent', '_exec_reads_batch')
    check('read batch defers to the gate for a held read',
          bool(_calls(batch, 'verdict', 'gate')))
    check('the confirm-mode prompt no longer asks for a typed yes',
          'REPLY first with a' not in SRC
          and 'ACTION MODE: ASK BEFORE EDITS' in SRC)


def test_legacy_path_gates_destructive_tools():
    print('[legacy path: destructive confirm + purge dry run]')
    s = _gate_standin('auto', False)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    res = s._run_gated_tool(srv, gate, 'delete_element',
                            {'element_ids': [1]}, False)
    check('delete asks with the destructive card',
          s.asked == [('delete_element', 'destructive')], s.asked)
    check('declined delete never ran', srv.ran == [] and res.get('cancelled'))

    s = _gate_standin('auto', True)
    s._note_model_changed = lambda: None
    srv = _FakeSrv()
    gate = s._new_tool_gate(srv)
    s._run_gated_tool(srv, gate, 'purge_unused', {'dry_run': False}, False)
    check('the first purge ran as a dry run, no card',
          srv.ran == [('purge_unused', {'dry_run': True})]
          and s.asked == [], (srv.ran, s.asked))
    s._run_gated_tool(srv, gate, 'purge_unused', {'dry_run': False}, False)
    check('the second, real purge asked first',
          s.asked == [('purge_unused', 'destructive')], s.asked)

    nlp = _nested('_route_input', 'do_nlp')
    src = ast.get_source_segment(SRC, nlp) or u''
    check('one gate per legacy request', '_legacy_gate = {"g": None}' in src
          and 'self._new_tool_gate(srv)' in src)


def test_confirm_card_stops_on_either_path():
    print('[confirm card: Stop releases it on both paths]')
    S = _standin(['_confirm_tool_blocking'], ns={'Action': lambda f: f})
    s = S()
    s._agent_loop = None              # legacy path: no AgentLoop
    s._cancelled = lambda: True       # Stop pressed
    s.Dispatcher = types.SimpleNamespace(
        Invoke=lambda a: a(), BeginInvoke=lambda a: a())
    s._hide_typing_indicator = lambda: None
    sealed = []
    s._append_confirm_card = (lambda name, args, pending, viet, kind=None:
                              (lambda msg: sealed.append(msg)))
    t0 = time.time()
    ok = s._confirm_tool_blocking('delete_element', {}, False, timeout_sec=5)
    check('Stop on the legacy path declines at once',
          ok is False and time.time() - t0 < 2, (ok, time.time() - t0))
    check('and seals the card', sealed and 'Stopped' in sealed[-1], sealed)


def test_action_mode_chip_says_what_it_does():
    print('[ask before edits: the chip explains itself]')
    S = _standin([], class_attrs=('_MODE_TIP_AUTO', '_MODE_TIP_CONFIRM'))
    auto, conf = S._MODE_TIP_AUTO, S._MODE_TIP_CONFIRM
    check('Auto tooltip: edits apply right away, destructive still asks',
          'right away' in auto and 'Destructive' in auto)
    check('Ask tooltip: every model change waits for Confirm; reads and '
          'exports never ask',
          'Confirm' in conf and 'never ask' in conf and 'exports' in conf)
    chip = _method_node('_update_action_mode_chip')
    seg = ast.get_source_segment(SRC, chip)
    check('the chip uses both tooltips',
          '_MODE_TIP_CONFIRM' in seg and '_MODE_TIP_AUTO' in seg)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt grounding: memory once, personal before project
# ─────────────────────────────────────────────────────────────────────────────

def _grounding_patch(personal=u'Always answer in metres.',
                     project=u'Sheet prefix is WH-.'):
    from Intelligence import assistant_memory as M
    import config.project_store as PS
    import config.settings as ST
    patch = _Patch()
    path = os.path.join(tempfile.mkdtemp(prefix='t3lab_cowork_mem_'),
                        'assistant_memory.json')
    patch.attr(M, '_memory_file', lambda: path)
    M.add_fact('levels are named L01, L02', scope='project',
               project_id='PID-COWORK')
    M.add_fact('reply briefly', scope='global')

    class _PS(object):
        def get_active_prompt_addendum(self):
            return project

        def get_active_project_id(self):
            return 'PID-COWORK'
    patch.attr(PS, 'ProjectStore', _PS)

    class _Settings(object):
        def build_user_instructions_block(self):
            return (u'## Personal instructions\n' + personal) if personal else u''
    patch.attr(ST, 'get_settings', lambda: _Settings())
    return patch


def test_memory_block_once_on_the_legacy_prompt():
    print('[grounding: legacy prompt carries memory ONCE, no tool hint]')
    patch = _grounding_patch()
    try:
        from Intelligence.t3lab_assistant import _build_system_prompt
        S = _standin(['_project_prompt_blocks', '_apply_project_blocks'],
                     class_attrs=('_MEMORY_BLOCK_HEAD',))
        s = S()
        base = _build_system_prompt(revit_context=u'')
        prompt = s._apply_project_blocks(base)
        check('memory block appears exactly once',
              prompt.count('## Persistent memory') == 1,
              prompt.count('## Persistent memory'))
        check('both scopes of facts are in it',
              'L01' in prompt and 'reply briefly' in prompt)
        check('legacy prompt never advertises remember_fact',
              'remember_fact' not in prompt)
        check('project instructions present once',
              prompt.count('## Project instructions') == 1)
        # Degraded base prompt (no memory of its own) still gets it, once.
        bare = s._apply_project_blocks(u'BASE')
        check('a base without memory gets it added once',
              bare.count('## Persistent memory') == 1
              and 'remember_fact' not in bare)
        instr, mem = s._project_prompt_blocks()
        check('native path (has the tool) keeps the hint',
              'remember_fact' in mem)
        _i2, mem2 = s._project_prompt_blocks(memory_tool=False)
        check('tool-less paths drop the hint', 'remember_fact' not in mem2
              and 'L01' in mem2)
    finally:
        patch.undo()

    check('pinned: addendum fetched once in the dialog',
          SRC.count('get_active_prompt_addendum()') == 1)
    check('pinned: legacy path still applies the helper',
          '_apply_project_blocks(system_prompt)' in SRC)
    check('pinned: native path unpack line kept',
          '_proj_instructions, _mem_block = self._project_prompt_blocks()'
          in SRC)
    for meth in ('_run_knowledge_agent', '_run_comment_agent',
                 '_run_agent_text'):
        seg = ast.get_source_segment(SRC, _method_node(meth))
        check('{} asks for the hint-free memory block'.format(meth),
              'memory_tool=False' in seg)


def test_personal_instructions_come_first():
    print('[grounding: personal instructions before project instructions]')
    patch = _grounding_patch()
    try:
        S = _standin(['_project_prompt_blocks', '_apply_project_blocks'],
                     class_attrs=('_MEMORY_BLOCK_HEAD',))
        s = S()
        instr, _mem = s._project_prompt_blocks()
        check('both sections present', '## Personal instructions' in instr
              and '## Project instructions' in instr, instr)
        check('personal comes before project',
              instr.index('## Personal instructions')
              < instr.index('## Project instructions'), instr)
        legacy = s._apply_project_blocks(u'BASE')
        check('legacy prompt: personal before project',
              legacy.index('## Personal instructions')
              < legacy.index('## Project instructions'))
        check('no doubled project header',
              legacy.count('## Project instructions') == 1, legacy)
    finally:
        patch.undo()
    patch = _grounding_patch(personal=u'')
    try:
        S = _standin(['_project_prompt_blocks'])
        instr, _m = S()._project_prompt_blocks()
        check('no personal text = no personal header',
              instr == u'## Project instructions\nSheet prefix is WH-.', instr)
    finally:
        patch.undo()

    # The specialist builder adds its own "## Project instructions" header in
    # front of whatever it is given — the pane must not hand it the personal
    # block, and must append grounding before the skill block itself.
    for meth in ('_run_native_agent', '_run_agent_text'):
        node = _method_node(meth)
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == 'build_specialist_prompt']
        kw = [k for c in calls for k in c.keywords
              if k.arg == 'project_instructions']
        check('{}: specialist builder gets no instructions of its own'.format(
            meth), kw and all(isinstance(k.value, ast.Constant)
                              and k.value.value == u'' for k in kw))
    seg = ast.get_source_segment(SRC, _method_node('_run_native_agent'))
    check('native: grounding appended before the skill block',
          seg.index('system_prompt += u"\\n\\n" + _proj_instructions')
          < seg.index('system_prompt += u"\\n\\n" + _skills_block'))
    from Intelligence.agents.specialists import build_specialist_prompt
    a = build_specialist_prompt(None, project_instructions=u'P',
                                skills_block=u'SK')
    b = (build_specialist_prompt(None, project_instructions=u'',
                                 skills_block=u'')
         + u'\n\n## Project instructions\nP' + u'\n\nSK')
    check('without personal text the specialist prompt is byte-identical',
          a == b)


# ─────────────────────────────────────────────────────────────────────────────
# Live context: levels + grids
# ─────────────────────────────────────────────────────────────────────────────

class _Elem(object):
    def __init__(self, name, elevation=None):
        self.Name = name
        if elevation is not None:
            self.Elevation = elevation


def _fake_pyrevit(doc_ref, counter):
    pyrevit = types.ModuleType('pyrevit')

    class Level(object):
        pass

    class Grid(object):
        pass

    class FilteredElementCollector(object):
        def __init__(self, doc):
            self.doc = doc

        def OfClass(self, cls):
            counter[cls.__name__] = counter.get(cls.__name__, 0) + 1
            self.items = list(self.doc.elements.get(cls.__name__, []))
            return self

        def __iter__(self):
            return iter(self.items)

    DB = types.SimpleNamespace(Level=Level, Grid=Grid,
                               FilteredElementCollector=FilteredElementCollector)

    class _Revit(object):
        @property
        def doc(self):
            return doc_ref['doc']

        @property
        def uidoc(self):
            return None
    pyrevit.revit = _Revit()
    pyrevit.DB = DB
    return pyrevit


class _FakeDoc(object):
    def __init__(self, title, levels, grids):
        self.Title = title
        self.PathName = u'C:\\models\\{}.rvt'.format(title)
        self.elements = {'Level': levels, 'Grid': grids}
        info = types.SimpleNamespace(Name=title, Number=u'001', Address=u'')
        self.ProjectInformation = info
        self.ActiveView = types.SimpleNamespace(
            Name=u'Level 1', ViewType=u'FloorPlan', Id=types.SimpleNamespace(
                Value=7), Scale=100, Discipline=u'Architectural')
        self.Application = types.SimpleNamespace(VersionNumber=u'2026',
                                                 Language=u'English_USA')


def test_levels_and_grids_in_the_context_summary():
    print('[context: levels + grids from a fake document]')
    import importlib
    counter = {}
    doc_ref = {}
    patch = _Patch()
    patch.module('pyrevit', _fake_pyrevit(doc_ref, counter))
    sys.modules.pop('Selection.scout', None)
    try:
        scout = importlib.import_module('Selection.scout')
        ft = 1 / 0.3048
        levels = [_Elem(u'Level 2', 3.5 * ft), _Elem(u'Basement', -3.0 * ft),
                  _Elem(u'Level 1', 0.0)]
        grids = [_Elem(u'{}'.format(i)) for i in range(1, 31)] + \
            [_Elem(c) for c in u'BCADE']
        doc_ref['doc'] = _FakeDoc(u'Tower', levels, grids)
        summary = scout.ContextScout.get_context_summary_for_ai()
        check('levels listed by elevation, in metres',
              u'- Levels (3, by elevation): Basement (-3 m), Level 1 (0 m), '
              u'Level 2 (3.5 m)' in summary, summary)
        check('grid count is exact', u'- Grids (35):' in summary, summary)
        check('grid names natural-sorted and capped at 30',
              u': 1, 2, 3,' in summary and u'30, A' not in summary
              and u'+5 more' in summary, summary)
        check('the active view is still there',
              u'Active View: Level 1' in summary)
        n_first = dict(counter)
        scout.ContextScout.get_context_summary_for_ai()
        check('second turn costs nothing (cached per document)',
              counter == n_first, (counter, n_first))
        scout.ContextScout.invalidate_datums()
        scout.ContextScout.get_context_summary_for_ai()
        check('an assistant edit forces a re-read',
              counter.get('Level') == n_first.get('Level', 0) + 1, counter)
        doc_ref['doc'] = _FakeDoc(u'Annex', [_Elem(u'GF', 0.0)], [])
        s2 = scout.ContextScout.get_context_summary_for_ai()
        check('switching documents re-reads for that document',
              u'- Levels (1, by elevation): GF (0 m)' in s2
              and u'Grids' not in s2, s2)
        lv, gr = scout.get_levels_and_grids(doc_ref['doc'],
                                            now=time.time()
                                            + scout.DATUM_TTL_SEC + 1)
        check('a stale digest is re-read after the TTL', lv == [(u'GF', 0.0)])
        check('formatter: nothing to say = empty',
              scout.format_levels_and_grids([], []) == u'')
    finally:
        sys.modules.pop('Selection.scout', None)
        patch.undo()

    native = ast.get_source_segment(SRC, _method_node('_run_native_agent'))
    check('live context still rides the user turn, not the system prompt',
          'build_context_block(revit_context=ctx' in native)
    check('the context tick keeps the digest warm on Revit\'s thread',
          '_warm_live_context()' in ast.get_source_segment(
              SRC, _method_node('_on_context_tick')))
    check('a model change invalidates the digest',
          bool(_calls(_method_node('_run_gated_tool'),
                      '_note_model_changed', 'self')))


# ─────────────────────────────────────────────────────────────────────────────
# Workspace: /memory clear, project archives, History
# ─────────────────────────────────────────────────────────────────────────────

def test_memory_clear_keeps_global():
    print('[workspace: /memory clear clears one scope]')
    from Intelligence import assistant_memory as M
    patch = _Patch()
    path = os.path.join(tempfile.mkdtemp(prefix='t3lab_cowork_clr_'),
                        'assistant_memory.json')
    patch.attr(M, '_memory_file', lambda: path)
    try:
        M.add_fact('prefer metric units', scope='global')
        M.add_fact('sheet prefix is WH-', scope='project', project_id='P1')
        M.add_fact('levels are L01..L09', scope='project', project_id='P1')
        S = _standin(['_memory_clear_reply'])
        msg, _i, _c = S._memory_clear_reply(u'', 'P1', False)
        check('project facts cleared', M.count_scope(M.PROJECT_SCOPE, 'P1')
              == 0)
        check('global fact KEPT', M.count_scope(M.GLOBAL_SCOPE) == 1)
        check('reply names the scope and the count',
              u"Cleared 2 facts from this project's memory" in msg
              and u'1 global fact' in msg, msg)

        msg2, _i, _c = S._memory_clear_reply(u'', None, False)
        check('no project: nothing cleared',
              M.count_scope(M.GLOBAL_SCOPE) == 1)
        check('no project: says how to clear global',
              u'/memory clear global' in msg2 and u'No project' in msg2, msg2)

        M.add_fact('sheet prefix is WH-', scope='project', project_id='P1')
        msg3, _i, _c = S._memory_clear_reply(u'global', 'P1', False)
        check('global clear empties global only',
              M.count_scope(M.GLOBAL_SCOPE) == 0
              and M.count_scope(M.PROJECT_SCOPE, 'P1') == 1)
        check('global reply says what was kept',
              u'Cleared 1 global fact' in msg3 and u'kept' in msg3, msg3)
    finally:
        patch.undo()
    seg = ast.get_source_segment(SRC, _method_node('_try_memory_command'))
    check('/memory clear no longer wipes everything',
          'everything=True' not in seg and '_memory_clear_reply' in seg)


def _appdata_sandbox():
    root = tempfile.mkdtemp(prefix='t3lab_cowork_app_')
    patch = _Patch()
    patch.env('APPDATA', root)
    return root, patch


def _make_project(root, pid):
    d = os.path.join(root, 'T3LabAI', 'projects', pid)
    os.makedirs(d)
    with io.open(os.path.join(d, 'project.json'), 'w', encoding='utf-8') as f:
        f.write(u'{"id": "%s", "name": "Tower"}' % pid)


def test_archive_goes_to_the_project_folder():
    print('[workspace: archives live with their project]')
    root, patch = _appdata_sandbox()
    try:
        from config import chat_sessions
        _make_project(root, 'p_tower')
        gdir = os.path.join(root, 'T3LabAI', 'chat_history', 'sessions')
        os.makedirs(gdir)
        S = _standin(['_archive_current_session'],
                     ns={'_chat_history_dir': lambda sub=None: gdir,
                         '_prune_archived_sessions': lambda d: 0})
        s = S()
        s._doc_key = u'doc_abc'
        s._history_summary = u''
        s._persisted_msgs = [{'role': 'user', 'content': u'count the walls'},
                             {'role': 'assistant', 'content': u'84 walls'}]
        s._active_pid = lambda: 'p_tower'
        path = s._archive_current_session()
        check('written into projects/<pid>/sessions',
              path and os.path.dirname(path)
              == chat_sessions.project_sessions_dir('p_tower', create=False),
              path)
        data = json.load(io.open(path, encoding='utf-8')) if path else {}
        check('tagged with the project id', data.get('pid') == 'p_tower', data)
        listed = chat_sessions.list_sessions(pid='p_tower', doc_key=u'doc_abc')
        check('History lists it under the project',
              [m['path'] for m in listed] == [path], listed)

        time.sleep(1.1)      # session ids have one-second resolution
        s._active_pid = lambda: None
        gpath = s._archive_current_session()
        check('no project: the global archive, untagged',
              gpath and os.path.dirname(gpath) == gdir
              and 'pid' not in json.load(io.open(gpath, encoding='utf-8')),
              gpath)
    finally:
        patch.undo()
        shutil.rmtree(root, ignore_errors=True)


def test_history_search_scope_and_rename():
    print('[workspace: History search / scope / rename]')
    root, patch = _appdata_sandbox()
    try:
        from config import chat_sessions
        _make_project(root, 'p_tower')
        pdir = chat_sessions.project_sessions_dir('p_tower')
        gdir = chat_sessions.global_sessions_dir()

        def _write(folder, sid, title, text, doc=u'doc_abc'):
            p = os.path.join(folder, u'{}_{}.json'.format(doc, sid))
            with io.open(p, 'w', encoding='utf-8') as f:
                json.dump({'id': sid, 'doc_key': doc, 'title': title,
                           'timestamp': u'2026-10-01 10:00',
                           'messages': [{'role': 'user', 'content': text}]},
                          f)
            return p
        a = _write(pdir, '20261001_100000', u'Walls', u'tô đỏ tường')
        b = _write(pdir, '20261001_110000', u'Levels', u'list the levels')
        c = _write(gdir, '20261001_120000', u'Other', u'tường khác', u'doc_x')

        S = _standin(['_get_history_sessions', '_history_query',
                      '_history_scope_all'],
                     ns={'_chat_history_dir': lambda sub=None: gdir})
        s = S()
        s._doc_key = u'doc_abc'
        s._active_pid = lambda: 'p_tower'
        s.history_search_box = types.SimpleNamespace(Text=u'')
        s.history_scope_all = types.SimpleNamespace(IsChecked=False)
        got = [m['path'] for m in s._get_history_sessions()]
        check('This project: the project\'s sessions, newest first',
              got == [b, a], got)
        s.history_search_box.Text = u'tuong'
        got = [m['path'] for m in s._get_history_sessions()]
        check('search folds diacritics and filters', got == [a], got)
        s.history_scope_all.IsChecked = True
        got = [m['path'] for m in s._get_history_sessions()]
        check('All projects: every project and document', set(got) == {a, c},
              got)
        check('rename stores the title',
              chat_sessions.rename_session(a, u'Red walls') and
              chat_sessions.list_sessions(pid='p_tower')[-1]['title']
              == u'Red walls')
    finally:
        patch.undo()
        shutil.rmtree(root, ignore_errors=True)

    # Wiring: the XAML names and handlers the pane relies on.
    for name in ('history_search_box', 'history_scope_project',
                 'history_scope_all', 'history_subtitle',
                 'history_empty_title', 'history_empty_hint'):
        check('XAML declares {}'.format(name),
              'x:Name="{}"'.format(name) in XAML_SRC)
    check('search box wired', 'TextChanged="history_search_changed"'
          in XAML_SRC and 'def history_search_changed' in SRC)
    check('scope chips wired', XAML_SRC.count(
        'Checked="history_scope_changed"') == 2
        and 'def history_scope_changed' in SRC)
    check('search is debounced',
          'DispatcherTimer' in ast.get_source_segment(
              SRC, _method_node('history_search_changed')))
    render = ast.get_source_segment(SRC, _method_node('_render_history_sessions'))
    check('cards offer Rename', '_begin_history_rename' in render)
    check('delete goes through chat_sessions', 'delete_session(' in render
          and 'os.remove(' not in render)
    check('rename goes through chat_sessions', 'rename_session(' in
          ast.get_source_segment(SRC, _method_node('_begin_history_rename')))
    check('cards read the new item keys',
          "sess.get('path')" in render and "sess.get('created')" in render
          and "'fpath'" not in render)
    clear = ast.get_source_segment(SRC, _method_node('clear_all_history_clicked'))
    check('Clear all asks before deleting', 'MessageBox.Show' in clear
          and 'MessageBoxResult.Yes' in clear)


def test_project_panel_shows_the_description():
    print('[workspace: project panel shows the description]')
    panel = SRC.split('def _build_project_panel', 1)[1]
    panel = panel.split('def _start_schedule_timer', 1)[0]
    check('panel reads the description', "meta.get('description')" in panel)
    check('description sits above the instructions',
          panel.index("meta.get('description')")
          < panel.index("meta.get('instructions')"))
    check('panel stays read-only', 'update_project' not in panel)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    test_tool_gate_policy()
    test_read_only_tool_filter()
    test_pending_decision()
    test_plan_review_helpers()
    test_plan_with_an_edit_waits_for_run()
    test_read_only_plan_runs_immediately()
    test_unapproved_plans_never_run()
    test_progress_card_final_states()
    test_confirm_mode_gates_a_write_on_both_paths()
    test_legacy_path_gates_destructive_tools()
    test_confirm_card_stops_on_either_path()
    test_action_mode_chip_says_what_it_does()
    test_memory_block_once_on_the_legacy_prompt()
    test_personal_instructions_come_first()
    test_levels_and_grids_in_the_context_summary()
    test_memory_clear_keeps_global()
    test_archive_goes_to_the_project_folder()
    test_history_search_scope_and_rename()
    test_project_panel_shows_the_description()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All cowork tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
