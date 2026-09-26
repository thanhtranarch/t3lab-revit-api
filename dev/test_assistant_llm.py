# -*- coding: utf-8 -*-
"""
CPython 3 test harness for the T3Lab Assistant / LLMs Setting stack.

Run:  python3 dev/test_assistant_llm.py
Exit code 0 = all pass. No external test framework — plain asserts,
mirroring dev/test_knowledge.py and dev/test_llm_config.py conventions.

Locks down the defects fixed in the 2026-07-27 Assistant + LLMs Setting debug
pass. Every test below FAILED before that pass; most are data-loss or
"the UI confidently shows something untrue" bugs, which is why they get a
permanent regression test rather than a one-off manual check:

    settings   two Revit sessions overwriting each other's API keys
    settings   one toggle wiping every key when settings.json is truncated
    router     status cache lying about the active model after set_model
    router     a project's provider override rewriting the global default
    ollama     a custom server URL that never survived a restart
    ollama     JSON grammar forced onto prose replies (Test Connection,
               docked-pane chat, and — silently — spell-check)

WPF/Revit-bound behaviour (the chat window, the settings dialog) cannot be
exercised headlessly; those go on the in-Revit checklist instead.
"""
from __future__ import unicode_literals

import io
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

# Sandbox %APPDATA% BEFORE any config/settings import, so settings.json lands
# in a throwaway dir instead of the real one.
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='t3lab_test_')

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def _fresh_settings():
    """Return a brand-new T3LabAISettings, as a separate Revit session would."""
    import config.settings as CS
    CS.T3LabAISettings._instance = None
    return CS.T3LabAISettings()


def _reset_appdata():
    """Point APPDATA at an empty dir and drop the settings singleton."""
    os.environ['APPDATA'] = tempfile.mkdtemp(prefix='t3lab_test_')
    return _fresh_settings()


# ─── settings: concurrent sessions ────────────────────────────────────────────

def test_settings_merge_on_write():
    print('[settings: merge-on-write]')
    a = _reset_appdata()
    a.set_api_key('Claude', 'sk-ant-A')

    # Session B starts, saves its own key.
    b = _fresh_settings()
    b.set_api_key('DeepSeek', 'sk-deepseek-B')

    # Session A still holds a pre-B snapshot. Each of these setters used to
    # dump that stale dict over the whole file, destroying B's key.
    a.set_provider_model('claude', 'claude-opus-5')
    a.set_active_provider('openai')
    a.set_username('Thanh')
    a.save_window_state(10, 20, 800, 600)

    c = _fresh_settings()
    check('session B api key survives session A writes',
          c.get_api_key('DeepSeek') == 'sk-deepseek-B', c.get_api_key('DeepSeek'))
    check('session A api key survives', c.get_api_key('Claude') == 'sk-ant-A')
    check('session A model persisted',
          c.get_provider_model('claude') == 'claude-opus-5')
    check('session A provider persisted', c.get_active_provider() == 'openai')
    check('session A username persisted', c.get_username() == 'Thanh')
    check('session A window state persisted', c.get_window_state()['width'] == 800)


# ─── settings: corrupt file ───────────────────────────────────────────────────

def test_settings_corrupt_quarantine():
    print('[settings: corrupt-file quarantine]')
    s = _reset_appdata()
    s.set_api_key('Claude', 'sk-ant-REAL')
    s.set_provider_model('claude', 'claude-opus-5')
    path = s._settings_file
    d = os.path.dirname(path)

    # Truncated mid-write (Revit crash, OneDrive sync conflict).
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write('{"api_keys": {"Claude": "sk-ant-RE')

    # One toggle in LLMs Setting. This used to load defaults, patch them and
    # write them back — silently destroying every key on disk.
    s2 = _fresh_settings()
    s2.set_agent_option('quality_mode', True)

    kept = [n for n in os.listdir(d) if n.startswith('settings.corrupt-')]
    check('corrupt file quarantined, not overwritten', len(kept) == 1, kept)
    if kept:
        raw = io.open(os.path.join(d, kept[0]), encoding='utf-8').read()
        check('quarantined bytes intact (key recoverable by hand)',
              'sk-ant-RE' in raw, raw[:60])
    check('app stays writable after quarantine',
          s2.get_agent_option('quality_mode') is True)
    check('settings reported healthy after quarantine', s2.is_healthy())


def test_settings_first_run_still_writes():
    print('[settings: first run]')
    s = _reset_appdata()
    # No settings.json exists yet — absence must NOT be treated as corruption.
    check('no file on first run', not os.path.exists(s._settings_file))
    check('first write succeeds', s.set_api_key('Claude', 'sk-ant-FIRST') is True)
    check('first write round-trips',
          _fresh_settings().get_api_key('Claude') == 'sk-ant-FIRST')
    check('no spurious quarantine file',
          not [n for n in os.listdir(os.path.dirname(s._settings_file))
               if n.startswith('settings.corrupt-')])


def test_settings_unreadable_refuses_write():
    print('[settings: unreadable file]')
    s = _reset_appdata()
    s.set_api_key('Claude', 'sk-ant-KEEP')
    path = s._settings_file
    good = io.open(path, encoding='utf-8').read()

    if os.name == 'nt':
        print('  skip  running on Windows — chmod cannot make a file unreadable to owner')
        return
    if os.geteuid() == 0 if hasattr(os, 'geteuid') else False:
        print('  skip  running as root — chmod cannot make a file unreadable')
        return
    os.chmod(path, 0)
    try:
        s2 = _fresh_settings()
        check('unreadable file marks settings unhealthy', not s2.is_healthy())
        check('save refuses while unhealthy',
              s2.set_agent_option('quality_mode', True) is False)
    finally:
        os.chmod(path, 0o600)
    check('original bytes untouched',
          io.open(path, encoding='utf-8').read() == good)


# ─── router ───────────────────────────────────────────────────────────────────

def test_router_set_model_refreshes_cache():
    print('[router: set_model refreshes status cache]')
    _reset_appdata()
    import Intelligence.llm_router as LR
    LR.LLMRouter._instance = None
    r = LR.LLMRouter()
    r._status_cache = {'claude': {'available': True, 'model': 'OLD',
                                  'display_name': 'Claude', 'active': True,
                                  'supports_vision': True, 'probed': True}}
    import time
    r._status_ts = time.time()
    r.set_model('claude', 'claude-opus-5')
    check('settings has the new model',
          _fresh_settings().get_provider_model('claude') == 'claude-opus-5')
    # The composer model chip renders from this snapshot and nothing re-probes
    # on a timer, so a stale entry here is what the user actually sees.
    check('status snapshot has the new model',
          r.get_status_instant()['claude']['model'] == 'claude-opus-5',
          r.get_status_instant()['claude']['model'])


def test_router_partial_probe_does_not_arm_ttl():
    print('[router: probe_provider TTL]')
    _reset_appdata()
    import Intelligence.llm_router as LR
    LR.LLMRouter._instance = None
    r = LR.LLMRouter()
    r._status_cache = None
    r._status_ts = 0.0
    r.probe_provider('claude')
    # Arming the 30s TTL from a one-provider probe would serve a snapshot
    # missing the other four and blank their status dots.
    check('single-provider probe leaves TTL disarmed', r._status_ts == 0.0,
          r._status_ts)
    check('probed provider still merged into the cache',
          'claude' in (r._status_cache or {}))


def test_router_scoped_switch_does_not_persist():
    print('[router: scoped provider switch]')
    _reset_appdata()
    import Intelligence.llm_router as LR
    LR.LLMRouter._instance = None
    r = LR.LLMRouter()
    _fresh_settings().set_active_provider('claude')

    # A project workspace's provider override is scoped to that project.
    r.switch_provider('ollama', persist=False)
    check('scoped switch leaves the global default alone',
          _fresh_settings().get_active_provider() == 'claude',
          _fresh_settings().get_active_provider())
    check('scoped switch still swaps the live provider',
          r.get_active_name() == 'ollama')

    # An explicit user choice in LLMs Setting still persists.
    r.switch_provider('openai')
    check('explicit switch persists',
          _fresh_settings().get_active_provider() == 'openai')


def test_router_status_instant_is_offline():
    print('[router: get_status_instant does no I/O]')
    _reset_appdata()
    import Intelligence.llm_router as LR
    LR.LLMRouter._instance = None
    r = LR.LLMRouter()

    calls = []
    for name, prov in r._providers.items():
        def _boom(_n=name):
            calls.append(_n)
            raise AssertionError('get_active_model() called from the UI path')
        prov.get_active_model = _boom

    r._status_cache = None
    snap = r.get_status_instant()
    # This runs under Dispatcher.Invoke; for Ollama/LM Studio get_active_model
    # does real HTTP, which froze Revit while opening LLMs Setting.
    check('no provider network call from get_status_instant', not calls, calls)
    check('unprobed entries are flagged as guesses',
          all(v.get('probed') is False for v in snap.values()),
          {k: v.get('probed') for k, v in snap.items()})


# ─── ollama ───────────────────────────────────────────────────────────────────

def test_ollama_host_persists():
    print('[ollama: host persistence]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP
    import Intelligence.local_llm as LL

    p = OP.OllamaProvider()
    check('defaults to localhost', p._get_host() == 'http://localhost:11434',
          p._get_host())

    p.set_host('http://192.168.1.10:11434')
    check('host written to settings',
          _fresh_settings().get_api_key('Ollama_Host') == 'http://192.168.1.10:11434')
    # A brand-new provider instance is what the next Revit session builds.
    check('survives a restart',
          OP.OllamaProvider()._get_host() == 'http://192.168.1.10:11434')
    check('local_llm agrees', LL.get_host() == 'http://192.168.1.10:11434')
    check('configured host probed first',
          OP.OllamaProvider()._candidate_hosts()[0] == 'http://192.168.1.10:11434')


def test_ollama_active_model_uses_configured_host():
    print('[ollama: get_active_model honours the host]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP
    p = OP.OllamaProvider()
    p.set_host('http://192.168.1.10:11434')

    seen = {}

    def _fake_probe():
        seen['host'] = p._get_host()
        return p._get_host(), ['qwen2.5:0.5b', 'qwen3:14b']
    p._probe_tags = _fake_probe

    model = p.get_active_model()
    # This used to route through local_llm's module-level OLLAMA_HOST, so a
    # remote Ollama got a green "Ready" dot but "No model selected".
    check('ranked from the configured host', seen.get('host') ==
          'http://192.168.1.10:11434', seen)
    check('returns a real installed model', model in ('qwen2.5:0.5b', 'qwen3:14b'),
          model)


def test_ollama_auto_model_is_cached():
    print('[ollama: auto-selected model is cached, not re-probed each turn]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP
    p = OP.OllamaProvider()

    calls = {'n': 0}

    def _fake_probe():
        calls['n'] += 1
        p._active_host = 'http://localhost:11434'
        return p._active_host, ['qwen2.5:0.5b', 'qwen3:14b']
    p._probe_tags = _fake_probe

    m1 = p.get_active_model()
    m2 = p.get_active_model()
    m3 = p.get_active_model()
    check('auto-select probes /api/tags only once', calls['n'] == 1, calls)
    check('cached model is stable', m1 == m2 == m3 and bool(m1), (m1, m2, m3))

    # A pinned model never probes at all.
    calls['n'] = 0
    p.set_model('qwen3:14b')
    check('pinned model returns without probing',
          p.get_active_model() == 'qwen3:14b' and calls['n'] == 0, calls)

    # Switching host must drop the cache so the new host is re-ranked.
    p2 = OP.OllamaProvider()
    p2._probe_tags = _fake_probe
    calls['n'] = 0
    p2.get_active_model()
    p2.reload_credentials()       # e.g. host changed in Settings
    p2.get_active_model()
    check('reload_credentials invalidates the model cache', calls['n'] == 2,
          calls)


def test_lmstudio_auto_model_is_cached():
    print('[lmstudio: auto-selected model is cached, not re-probed each turn]')
    _reset_appdata()
    import Intelligence.lmstudio_provider as LP
    p = LP.LMStudioProvider()

    calls = {'n': 0}

    def _fake_get_models():
        calls['n'] += 1
        return ['some-model-7b', 'another-13b']
    p.get_models = _fake_get_models

    m1 = p.get_active_model()
    m2 = p.get_active_model()
    check('auto-select probes only once', calls['n'] == 1, calls)
    check('cached model is stable', m1 == m2 == 'some-model-7b', (m1, m2))

    calls['n'] = 0
    p.set_model('another-13b')
    check('pinned model returns without probing',
          p.get_active_model() == 'another-13b' and calls['n'] == 0, calls)

    p2 = LP.LMStudioProvider()
    p2.get_models = _fake_get_models
    calls['n'] = 0
    p2.get_active_model()
    p2.reload_credentials()
    p2.get_active_model()
    check('reload_credentials invalidates the model cache', calls['n'] == 2,
          calls)


def test_local_providers_warm_up():
    print('[ollama/lmstudio: warm_up preloads the model, guarded + backgrounded]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP
    import Intelligence.lmstudio_provider as LP

    # Ollama: one tiny (num_predict=1) call to /api/chat.
    captured = []

    def _fake_post(url, payload, timeout_ms=None, **kw):
        captured.append((url, payload))
        return json.dumps({'message': {'content': 'x'}})

    orig_op = OP.http_post
    OP.http_post = _fake_post
    try:
        p = OP.OllamaProvider()
        p.set_model('qwen3:14b')
        ok = p.warm_up()
        check('ollama warm_up posts once', len(captured) == 1, captured)
        check('ollama warm_up is 1-token',
              captured[-1][1].get('options', {}).get('num_predict') == 1,
              captured[-1][1])
        check('ollama warm_up keeps the model resident',
              captured[-1][1].get('keep_alive') == '15m', captured[-1][1])
        check('ollama warm_up returns True on success', ok is True)
    finally:
        OP.http_post = orig_op

    # No model → no call, returns False (never raises).
    captured[:] = []
    OP.http_post = _fake_post
    try:
        p2 = OP.OllamaProvider()
        p2.get_active_model = lambda: None
        check('ollama warm_up is a no-op without a model',
              p2.warm_up() is False and not captured, captured)
    finally:
        OP.http_post = orig_op

    # LM Studio: one max_tokens=1 call to the chat endpoint.
    captured[:] = []
    orig_lp = LP.http_post
    LP.http_post = _fake_post
    try:
        q = LP.LMStudioProvider()
        q.get_active_model = lambda: 'some-model-7b'
        ok = q.warm_up()
        check('lmstudio warm_up posts once', len(captured) == 1, captured)
        check('lmstudio warm_up is 1-token',
              captured[-1][1].get('max_tokens') == 1, captured[-1][1])
        check('lmstudio warm_up returns True on success', ok is True)
    finally:
        LP.http_post = orig_lp

    # A raising http_post is swallowed (warm-up must never disturb the UI).
    def _boom(*a, **kw):
        raise RuntimeError('server down')
    OP.http_post = _boom
    try:
        r = OP.OllamaProvider()
        r.set_model('qwen3:14b')
        check('warm_up swallows a failing server', r.warm_up() is False)
    finally:
        OP.http_post = orig_op


def test_local_llm_pick_best_is_pure():
    print('[local_llm: pick_best]')
    import Intelligence.local_llm as LL
    check('empty list → None', LL.pick_best([]) is None)
    check('quality mode prefers reasoning + size',
          LL.pick_best(['llama3:8b', 'qwen3:14b', 'qwen2.5:0.5b'],
                       prefer_capable=True) == 'qwen3:14b')
    check('fast mode prefers the preferred list',
          LL.pick_best(['llama3:8b', 'qwen2.5:0.5b']) == 'qwen2.5:0.5b')
    check('unknown names still return something',
          LL.pick_best(['mystery:1b']) == 'mystery:1b')


def test_ollama_chat_budgets_reasoning_models():
    """Regression: Test Connection reported "empty reply" for a healthy model.

    `chat()` passed max_tokens straight into num_predict; only the STREAMING
    path called _num_predict_for(). A thinking model spends the whole budget in
    its reasoning pass, so the dialog's 120-token probe came back with
    done_reason="length", an empty `content`, and 473 characters of `thinking`
    — measured against a real qwen3.6 on 2026-08-10.
    """
    print('[ollama: chat() widens num_predict for reasoning models]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP

    captured = []

    def _fake_post(url, payload, timeout_ms=None, **kw):
        captured.append(payload)
        return json.dumps({'message': {'role': 'assistant',
                                       'content': 'Connected OK'}})

    orig = OP.http_post
    OP.http_post = _fake_post
    try:
        p = OP.OllamaProvider()
        p.set_model('qwen3.6:latest')
        p.chat([], 'sys', 'hi', max_tokens=120)
        got = captured[-1]['options']['num_predict']
        check('reasoning model gets room to finish thinking',
              got >= OP.OllamaProvider.REASONING_MIN_PREDICT,
              'num_predict={}'.format(got))

        captured[:] = []
        p2 = OP.OllamaProvider()
        p2.set_model('llama3.1:8b')
        p2.chat([], 'sys', 'hi', max_tokens=120)
        check('plain instruct model keeps the caller budget',
              captured[-1]['options']['num_predict'] == 120,
              captured[-1]['options'])

        captured[:] = []
        p3 = OP.OllamaProvider()
        p3.set_model('qwen3.6:latest')
        p3.chat([], 'sys', 'hi', max_tokens=99999)
        check('a bigger caller budget is never shrunk',
              captured[-1]['options']['num_predict'] == 99999,
              captured[-1]['options'])
    finally:
        OP.http_post = orig


def test_ollama_reply_text_handles_separate_thinking():
    """Newer Ollama returns reasoning in message.thinking, not inline tags."""
    print('[ollama: answer text prefers content, falls back to thinking]')
    import Intelligence.ollama_provider as OP
    rt = OP.OllamaProvider._reply_text

    check('content wins when present',
          rt({'content': 'Connected OK', 'thinking': 'blah'}) == 'Connected OK')
    check('whitespace-only content is not an answer',
          rt({'content': '   ', 'thinking': 'reasoned'}) == 'reasoned')
    check('truncated mid-thought still reports a response',
          rt({'content': '', 'thinking': 'x' * 900}) != u'')
    check('thinking fallback is bounded',
          len(rt({'content': '', 'thinking': 'x' * 900})) <= 400)
    check('genuinely empty stays empty', rt({'content': '', 'thinking': ''}) == u'')
    check('missing keys never raise', rt({}) == u'')


def test_tool_calling_size_floor():
    print('[local_llm: tool-calling size floor + recommendation]')
    import Intelligence.local_llm as LL
    check('1.5B is below the floor', not LL.is_tool_capable_size('qwen2.5:1.5b'))
    check('0.5B is below the floor', not LL.is_tool_capable_size('qwen2.5:0.5b'))
    check('4B meets the floor', LL.is_tool_capable_size('qwen3:4b'))
    check('14B meets the floor', LL.is_tool_capable_size('qwen3:14b'))
    check('unknown size is not nagged', LL.is_tool_capable_size('mystery-model'))
    check('recommends an installed capable model',
          LL.recommended_tool_model(['qwen2.5:0.5b', 'qwen3:8b']) == 'qwen3:8b')
    check('falls back to the sweet-spot tag when none installed',
          LL.recommended_tool_model(['qwen2.5:0.5b']) == 'qwen3:14b')


def test_wants_json_contract():
    print('[providers: _wants_json]')
    from Intelligence.llm_provider import BaseLLMProvider as B
    check('none → False', B._wants_json(None) is False)
    check('empty dict → False', B._wants_json({}) is False)
    check('json_object dict → True', B._wants_json({'type': 'json_object'}) is True)
    check('"json" string → True', B._wants_json('json') is True)
    check('text type → False', B._wants_json({'type': 'text'}) is False)


def test_ollama_json_is_opt_in():
    print('[ollama: JSON grammar is opt-in]')
    _reset_appdata()
    import Intelligence.ollama_provider as OP

    captured = []

    def _fake_post(url, payload, timeout_ms=None, **kw):
        captured.append(payload)
        return json.dumps({'message': {'content': 'hello'}})

    orig = OP.http_post
    OP.http_post = _fake_post
    try:
        p = OP.OllamaProvider()
        p.set_model('qwen3:14b')

        p.chat([], 'sys', 'plain question', max_tokens=50)
        # Test Connection, the docked pane and spell-check all land here.
        # 'format' used to be hardcoded on, so they got JSON back.
        check('plain chat sends no format', 'format' not in captured[-1],
              captured[-1].get('format'))

        p.chat([], 'sys', 'give me json', max_tokens=50,
               response_format={'type': 'json_object'})
        check('json_object opts in', captured[-1].get('format') == 'json')

        p.chat([], 'sys', 'plain again', max_tokens=50,
               response_format={'type': 'text'})
        check('text format stays off', 'format' not in captured[-1])
    finally:
        OP.http_post = orig


def test_json_callers_opt_in():
    """The two internal callers that json.loads() the reply must ask for JSON."""
    print('[callers: explicit response_format]')
    import inspect
    from Intelligence.agents import dispatcher
    from Intelligence.comments import comment_agent

    src = inspect.getsource(dispatcher.AgentDispatcher._llm_stage)
    check('dispatcher._llm_stage requests json_object',
          'response_format' in src and 'json_object' in src)

    src2 = inspect.getsource(comment_agent)
    check('comment_agent._propose requests json_object',
          src2.count('json_object') >= 1)


# ─── project store: caching + scope plumbing ─────────────────────────────────

def test_project_meta_cache():
    """project.json used to be re-read on EVERY get_project call (18 sites,
    incl. a 30s timer and 3+ reads per chat turn)."""
    print('[project: meta cache]')
    _reset_appdata()
    import config.project_store as PS
    PS.ProjectStore._instance = None
    ps = PS.ProjectStore()
    pid = ps.create_project('Cache')['id']

    reads = {'n': 0}
    orig = PS._read_json

    def counting(path, default=None):
        if path.endswith('project.json'):
            reads['n'] += 1
        return orig(path, default)
    PS._read_json = counting
    try:
        reads['n'] = 0
        for _ in range(30):
            ps.get_project(pid)
        check('30 reads served from cache', reads['n'] <= 1, reads['n'])

        reads['n'] = 0
        for _ in range(5):
            ps.list_projects()
        check('list_projects cached too', reads['n'] == 0, reads['n'])

        # a write must invalidate
        ps.update_project(pid, {'instructions': 'WH- prefix'})
        check('update visible after write',
              ps.get_project(pid)['instructions'] == 'WH- prefix')

        # an edit by ANOTHER Revit session must still be picked up
        import time
        time.sleep(0.01)
        path = ps._project_json(pid)
        data = orig(path)
        data['name'] = 'Edited elsewhere'
        PS._write_json(path, data)
        check('external edit picked up (mtime keyed)',
              ps.get_project(pid)['name'] == 'Edited elsewhere')
    finally:
        PS._read_json = orig

    # callers mutate what they get (the tick stamps last_run) — must be a copy
    got = ps.get_project(pid)
    got['name'] = 'MUTATED'
    check('returned meta is a copy',
          ps.get_project(pid)['name'] != 'MUTATED')


def test_project_schedule_api():
    print('[project: schedule api]')
    ps = _reset_appdata() and None
    import config.project_store as PS
    PS.ProjectStore._instance = None
    ps = PS.ProjectStore()
    pid = ps.create_project('Sched')['id']

    check('pads H:MM', ps.validate_schedule_time('9:05') == '09:05')
    check('rejects 24:00', ps.validate_schedule_time('24:00') is None)
    check('rejects 09:70', ps.validate_schedule_time('09:70') is None)
    check('rejects junk', ps.validate_schedule_time('soon') is None)

    items = ps.add_schedule(pid, 'get model warnings', '9:05')
    check('added with canonical time', items[0]['time'] == '09:05')
    tid = items[0]['id']

    # due logic is pure so it can be tested without WPF
    check('not due before its time',
          ps.due_schedule(items, '08:00', '2026-07-27') is None)
    check('due after its time',
          ps.due_schedule(items, '10:00', '2026-07-27') is not None)
    check('not due twice in a day',
          ps.due_schedule([dict(items[0], last_run='2026-07-27')],
                          '10:00', '2026-07-27') is None)

    ps.set_schedule_enabled(pid, tid, False)
    items = ps.get_project(pid)['scheduled']
    check('enabled persists (field had no writer before)',
          items[0]['enabled'] is False)
    check('paused task never due',
          ps.due_schedule(items, '23:59', '2026-07-27') is None)

    check('removed', ps.remove_schedule(pid, tid) == [])


def test_project_document_counts():
    print('[project: document counts]')
    _reset_appdata()
    import io as _io
    import config.project_store as PS
    PS.ProjectStore._instance = None
    ps = PS.ProjectStore()
    pid = ps.create_project('Docs')['id']
    files_dir = os.path.join(ps.project_dir(pid), 'files')
    for name in ('bep.md', 'notes.txt', 'PROJECT_CONTEXT.md'):
        with _io.open(os.path.join(files_dir, name), 'w', encoding='utf-8') as f:
            f.write('x')

    own, dirs, docs, unscanned = ps.count_documents(pid)
    # PROJECT_CONTEXT.md is written INTO files/ by the context builder; it is
    # generated FROM the corpus, so counting it as a user document (and, worse,
    # indexing it) makes the model cite its own summary back at itself.
    check('generated summary not counted', own == 2, own)
    check('no linked dirs yet', dirs == 0 and docs == 0 and unscanned == 0)
    check('describe mentions the count',
          '2 file' in ps.describe_documents(pid), ps.describe_documents(pid))


def test_generated_docs_excluded_from_index():
    print('[knowledge: generated docs excluded]')
    from Intelligence.knowledge import knowledge_store as KS
    check('PROJECT_CONTEXT.md is in the exclusion set',
          'PROJECT_CONTEXT.md' in KS.GENERATED_DOCS)
    import inspect
    src = inspect.getsource(KS.KnowledgeStore.scan)
    check('scan() consults it', 'GENERATED_DOCS' in src)


def test_skills_respect_project_scope():
    print('[skills: project scope]')
    _reset_appdata()
    import config.project_store as PS
    PS.ProjectStore._instance = None
    ps = PS.ProjectStore()
    import Intelligence.skills_engine as SE
    SE.SkillsEngine._instance = None
    eng = SE.get_skills_engine()
    eng.scan()
    ids = [s['id'] for s in eng.all_skills()]
    if not ids:
        print('  skip  no skills found')
        return
    target = ids[0]

    pid = ps.create_project('Skills')['id']
    ps.set_active_project(pid)
    check('enabled by default',
          [s for s in eng.all_skills() if s['id'] == target][0]['enabled'])

    # skills_disabled has been written into every project.json since projects
    # existed, and had no reader anywhere until now.
    ps.update_project(pid, {'skills_disabled': [target]})
    check('project can disable a skill',
          [s for s in eng.all_skills() if s['id'] == target][0]['enabled'] is False)
    check('and it leaves the catalog',
          target not in [c['id'] for c in eng.get_catalog()])

    ps.update_project(pid, {'skills_disabled': []})
    check('re-enabled when the project list clears',
          [s for s in eng.all_skills() if s['id'] == target][0]['enabled'])

    # a project folder deleted outside the app must not keep resolving
    import shutil
    shutil.rmtree(ps.project_dir(pid), ignore_errors=True)
    ps.invalidate_meta()
    check('stale project id resolves to none', SE._active_pid() is None)
    check('and yields no project skills dir',
          SE._project_skills_dir() is None)


def test_prompt_paths_carry_project_scope():
    """Both prompt builders must inject project instructions + memory.

    The native tool-calling path did; the legacy JSON path did NOT — and the
    legacy path is what runs when the user ATTACHES a document, i.e. exactly
    when a project's conventions matter most.
    """
    print('[assistant: project scope reaches both prompt paths]')
    import io as _io
    _dlg = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'T3LabAssistantDialog.py')
    _script = _dlg if os.path.exists(_dlg) else os.path.join(
        TAB, 'Support.panel',
        'T3LabAssistant.pushbutton', 'script.py')
    src = _io.open(_script, encoding='utf-8').read()

    check('shared helper exists', 'def _project_prompt_blocks' in src)
    check('legacy path applies it',
          '_apply_project_blocks(system_prompt)' in src)
    check('native path uses the same helper',
          '_proj_instructions, _mem_block = self._project_prompt_blocks()' in src)
    check('comment agent is grounded too',
          'extra_context=_extra' in src)
    # the old inline duplicates must be gone
    check('no second addendum fetch',
          src.count('get_active_prompt_addendum()') == 1,
          src.count('get_active_prompt_addendum()'))


def test_single_edit_surface():
    """The chat panel is read-only; editing lives in LLMs Setting."""
    print('[assistant: one edit surface]')
    import io as _io
    _dlg = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'T3LabAssistantDialog.py')
    _script = _dlg if os.path.exists(_dlg) else os.path.join(
        TAB, 'Support.panel',
        'T3LabAssistant.pushbutton', 'script.py')
    src = _io.open(_script, encoding='utf-8').read()
    panel = src.split('def _build_project_panel', 1)[1]
    panel = panel.split('def _start_schedule_timer', 1)[0]

    check('panel no longer writes project.json',
          'update_project' not in panel)
    check('panel offers the settings route',
          '_edit_project_in_settings' in panel)
    check('panel shows linked folders',
          'get_knowledge_dirs' in panel)

    dlg = _io.open(os.path.join(
        REPO, 'T3Lab.extension', 'lib', 'GUI', 'LLMSettingDialog.py'),
        encoding='utf-8').read()
    check('memory management moved into the dialog',
          'def _render_project_memory' in dlg)
    check('schedule rows use the store API',
          'set_schedule_enabled' in dlg and 'remove_schedule' in dlg)


# ─── providers: streaming resilience on a mid-stream drop ─────────────────────
# Before this fix, when the SSE stream dropped after some text had already
# streamed into the live bubble, chat_stream() threw the partial away and
# re-ran the full non-streaming chat() — the user watched their answer get
# REPLACED by a different one, at double the tokens. Now the partial is kept
# and finished with ONE prefill-continuation call.

def test_claude_stream_drop_continues_partial():
    print('[claude: stream drop continues, no re-generate]')
    a = _reset_appdata()
    a.set_api_key('Claude', 'sk-ant-test')
    import Intelligence.claude_provider as CP

    orig_s, orig_p = CP.http_post_stream, CP.http_post

    def drop_after_two(url, payload, headers, on_line):
        on_line('data: {"type":"content_block_delta","delta":'
                '{"type":"text_delta","text":"Hello "}}')
        on_line('data: {"type":"content_block_delta","delta":'
                '{"type":"text_delta","text":"wor"}}')
        raise IOError('connection reset')

    calls = {'n': 0, 'last': None}

    def fake_post(url, payload, headers, timeout_ms=None, **kw):
        calls['n'] += 1
        calls['last'] = payload['messages'][-1]
        return json.dumps({'content': [{'text': 'ld, done.'}]})

    CP.http_post_stream = drop_after_two
    CP.http_post = fake_post
    try:
        p = CP.ClaudeProvider()
        p.set_model('claude-3-5-test')
        deltas = []
        res = p.chat_stream([], 'sys', 'hi',
                            on_delta=lambda d: deltas.append(d), max_tokens=80)
        check('kept the streamed partial', res == 'Hello world, done.', repr(res))
        check('no from-scratch regenerate (single blocking call)',
              calls['n'] == 1, calls['n'])
        check('the one call PREFILLED the partial (continued, not restarted)',
              calls['last'] == {'role': 'assistant', 'content': 'Hello wor'},
              calls['last'])
        check('continuation was streamed into the bubble too',
              deltas[-1] == 'ld, done.', deltas)
    finally:
        CP.http_post_stream, CP.http_post = orig_s, orig_p


def test_claude_stream_drop_with_nothing_streamed_falls_back():
    print('[claude: empty-partial drop keeps the chat() safety net]')
    a = _reset_appdata()
    a.set_api_key('Claude', 'sk-ant-test')
    import Intelligence.claude_provider as CP

    orig_s, orig_p = CP.http_post_stream, CP.http_post

    def drop_immediately(url, payload, headers, on_line):
        raise IOError('dead before first byte')

    seen = {'last_role': None}

    def fake_post(url, payload, headers, timeout_ms=None, **kw):
        seen['last_role'] = payload['messages'][-1]['role']
        return json.dumps({'content': [{'text': 'fresh full answer'}]})

    CP.http_post_stream = drop_immediately
    CP.http_post = fake_post
    try:
        p = CP.ClaudeProvider()
        p.set_model('claude-3-5-test')
        res = p.chat_stream([], 'sys', 'hi', on_delta=lambda d: None,
                            max_tokens=80)
        check('fell back to a fresh chat()', res == 'fresh full answer', repr(res))
        check('fresh call ends on the USER turn (not a prefill)',
              seen['last_role'] == 'user', seen['last_role'])
    finally:
        CP.http_post_stream, CP.http_post = orig_s, orig_p


def test_skill_ranking_uses_feedback():
    print('[skills: thumbs re-order match_scored, zero votes unchanged]')
    _reset_appdata()
    import Intelligence.skills_engine as SE
    import Intelligence.feedback as FB

    # Two skills that trigger on the SAME single word → identical trigger score,
    # so the ONLY separator is the alphabet (tie-break) or feedback. 'aaa' wins
    # the tie by id when there are no votes.
    eng = SE.SkillsEngine()
    eng._skills = {
        'aaa-skill': {'triggers': ['warning'], 'source': 'builtin',
                      'triggers_derived': False},
        'zzz-skill': {'triggers': ['warning'], 'source': 'builtin',
                      'triggers_derived': False},
    }
    eng._scanned = True
    eng._disabled = lambda: set()

    orig = FB.skill_score
    try:
        FB.skill_score = lambda sid: (0, 0)
        order = [sid for sid, _ in eng.match_scored('check the warning')]
        check('zero votes: alphabetical tie-break holds',
              order == ['aaa-skill', 'zzz-skill'], order)

        # Down-vote the alphabetical winner, up-vote the other → order flips.
        FB.skill_score = lambda sid: {'aaa-skill': (0, 4),
                                      'zzz-skill': (4, 0)}.get(sid, (0, 0))
        order2 = [sid for sid, _ in eng.match_scored('check the warning')]
        check('down-voted skill sinks below the up-voted one',
              order2 == ['zzz-skill', 'aaa-skill'], order2)
    finally:
        FB.skill_score = orig


def test_openai_stream_drop_continues_partial():
    print('[openai: stream drop continues, no re-generate]')
    a = _reset_appdata()
    a.set_api_key('OpenAI', 'sk-test')
    import Intelligence.openai_provider as OP

    orig_s, orig_p = OP.http_post_stream, OP.http_post

    def drop_after_two(url, payload, headers, on_line):
        on_line('data: {"choices":[{"delta":{"content":"Foo "}}]}')
        on_line('data: {"choices":[{"delta":{"content":"ba"}}]}')
        raise IOError('reset')

    calls = {'n': 0, 'last': None}

    def fake_post(url, payload, headers, timeout_ms=None, **kw):
        calls['n'] += 1
        calls['last'] = payload['messages'][-1]
        return json.dumps({'choices': [{'message': {'content': 'r done.'}}]})

    OP.http_post_stream = drop_after_two
    OP.http_post = fake_post
    try:
        p = OP.OpenAIProvider()
        p.set_model('gpt-4o-test')
        deltas = []
        res = p.chat_stream([], 'sys', 'hi',
                            on_delta=lambda d: deltas.append(d), max_tokens=80)
        check('kept the streamed partial', res == 'Foo bar done.', repr(res))
        check('no from-scratch regenerate (single blocking call)',
              calls['n'] == 1, calls['n'])
        check('the one call PREFILLED the partial',
              calls['last'] == {'role': 'assistant', 'content': 'Foo ba'},
              calls['last'])
    finally:
        OP.http_post_stream, OP.http_post = orig_s, orig_p


def test_deepseek_stream_drop_returns_partial():
    print('[deepseek: stream drop returns partial, no re-generate]')
    a = _reset_appdata()
    a.set_api_key('DeepSeek', 'sk-test')
    import Intelligence.deepseek_provider as DP

    orig_s, orig_p = DP.http_post_stream, DP.http_post

    def drop_after_two(url, payload, headers, on_line):
        on_line('data: {"choices":[{"delta":{"content":"Foo "}}]}')
        on_line('data: {"choices":[{"delta":{"content":"bar"}}]}')
        raise IOError('reset')

    calls = {'n': 0}

    def fake_post(url, payload, headers=None, timeout_ms=None, **kw):
        # A blocking fallback here would be the §7.2#1 bug: a second, different
        # answer generated from scratch after text already streamed.
        calls['n'] += 1
        return json.dumps({'choices': [{'message': {'content': 'DIFFERENT'}}]})

    DP.http_post_stream = drop_after_two
    DP.http_post = fake_post
    try:
        p = DP.DeepSeekProvider()
        p._resolve_model = lambda: 'deepseek-chat'
        deltas = []
        res = p.chat_stream([], 'sys', 'hi',
                            on_delta=lambda d: deltas.append(d), max_tokens=80)
        check('kept the streamed partial (not a fresh answer)',
              res == 'Foo bar', repr(res))
        check('no from-scratch blocking regenerate after partial',
              calls['n'] == 0, calls['n'])
        check('user saw the streamed deltas', deltas == ['Foo ', 'bar'], deltas)
    finally:
        DP.http_post_stream, DP.http_post = orig_s, orig_p


def main():
    test_settings_merge_on_write()
    test_settings_corrupt_quarantine()
    test_settings_first_run_still_writes()
    test_settings_unreadable_refuses_write()
    test_router_set_model_refreshes_cache()
    test_router_partial_probe_does_not_arm_ttl()
    test_router_scoped_switch_does_not_persist()
    test_router_status_instant_is_offline()
    test_ollama_host_persists()
    test_ollama_active_model_uses_configured_host()
    test_ollama_auto_model_is_cached()
    test_lmstudio_auto_model_is_cached()
    test_local_providers_warm_up()
    test_ollama_chat_budgets_reasoning_models()
    test_ollama_reply_text_handles_separate_thinking()
    test_tool_calling_size_floor()
    test_local_llm_pick_best_is_pure()
    test_wants_json_contract()
    test_ollama_json_is_opt_in()
    test_json_callers_opt_in()
    test_claude_stream_drop_continues_partial()
    test_claude_stream_drop_with_nothing_streamed_falls_back()
    test_skill_ranking_uses_feedback()
    test_openai_stream_drop_continues_partial()
    test_deepseek_stream_drop_returns_partial()
    test_project_meta_cache()
    test_project_schedule_api()
    test_project_document_counts()
    test_generated_docs_excluded_from_index()
    test_skills_respect_project_scope()
    test_prompt_paths_carry_project_scope()
    test_single_edit_surface()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant/LLM tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
