# -*- coding: utf-8 -*-
"""
CPython 3 test harness for the T3Lab Assistant's tool catalog and routing.

Run:  python3 dev/test_assistant_routing.py
Exit code 0 = all pass. No external test framework — plain asserts, mirroring
dev/test_assistant_llm.py / dev/test_knowledge.py conventions.

Locks down the defects fixed in the 2026-07-28 Assistant logic pass. Every
test below FAILED before that pass. They fall into two families, both of
which are "the assistant confidently states something untrue":

    launcher   a pushbutton whose entry point sits in `if __name__ ==
               '__main__':` was imported, never run, and reported as opened
    catalog    intents advertised to the LLM pointed at pushbuttons that had
               been deleted or renamed, while a tool that DOES exist
               (PropertyLine) was unreachable

The catalog tests are deliberately written against the real extension tree
rather than a fixture: their whole job is to fail the moment the advertised
catalog drifts from what is actually on disk again.
"""
from __future__ import unicode_literals

import io
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')
LIB = os.path.join(EXT, 'lib')
from tabdir import TAB  # noqa: E402
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _write_button(root, name, body):
    """Create <root>/<name>.pushbutton/script.py containing `body`."""
    btn = os.path.join(root, name + '.pushbutton')
    os.makedirs(btn)
    path = os.path.join(btn, 'script.py')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 1. Launcher actually runs the script
# ─────────────────────────────────────────────────────────────────────────────

def test_launcher_runs_main_guard():
    """The bug: 36 of 42 pushbuttons guard their entry point with
    `if __name__ == '__main__':`. The old launcher used imp.load_source with
    module name '_auto_<title>', so the guard never fired — yet it returned
    True. The assistant said "Opening ..." and nothing happened."""
    from Services.tool_discovery import make_generic_launcher, run_tool_script

    tmp = tempfile.mkdtemp(prefix='t3lab_launch_')
    try:
        sentinel = os.path.join(tmp, 'ran.txt')
        # The coding declaration matters: source must be compiled as bytes or
        # Python 2 raises "encoding declaration in Unicode string".
        body = (
            '# -*- coding: utf-8 -*-\n'
            'def main():\n'
            '    with open({!r}, "w") as f:\n'
            '        f.write("ran")\n'
            '\n'
            'if __name__ == "__main__":\n'
            '    main()\n'
        ).format(sentinel)
        path = _write_button(tmp, 'GuardedTool', body)

        ok, err = make_generic_launcher(path, 'Guarded Tool')()
        check('launcher reports success', ok is True, err)
        check('launcher actually ran the __main__ block',
              os.path.exists(sentinel),
              'sentinel not written — script was imported, not run')

        # Direct call surface used by the registry-driven catalog
        os.remove(sentinel)
        ok, err = run_tool_script(path, 'Guarded Tool')
        check('run_tool_script runs __main__', ok and os.path.exists(sentinel), err)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_launcher_reports_failure_honestly():
    """A missing path and a broken script must both report failure WITH a
    reason — the old code returned a bare False and the UI said
    "Could not open the tool. Check the console."."""
    from Services.tool_discovery import run_tool_script

    ok, err = run_tool_script(os.path.join(REPO, 'no', 'such', 'script.py'), 'Ghost')
    check('missing script fails', ok is False)
    check('missing script explains why', 'not found' in err.lower(), err)

    tmp = tempfile.mkdtemp(prefix='t3lab_launch_')
    try:
        path = _write_button(tmp, 'BrokenTool',
                             '# -*- coding: utf-8 -*-\nraise ValueError("boom")\n')
        ok, err = run_tool_script(path, 'Broken Tool')
        check('broken script fails', ok is False)
        check('broken script surfaces the error', 'boom' in err, err)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_launcher_treats_exitscript_as_success():
    """pyRevit's forms.alert(exitscript=True) raises SystemExit — a normal
    exit path, not a failure."""
    from Services.tool_discovery import run_tool_script

    tmp = tempfile.mkdtemp(prefix='t3lab_launch_')
    try:
        path = _write_button(tmp, 'ExitingTool',
                             '# -*- coding: utf-8 -*-\nimport sys\nsys.exit(0)\n')
        ok, err = run_tool_script(path, 'Exiting Tool')
        check('SystemExit counts as success', ok is True, err)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_api_context_runner_normalises_every_launcher_shape():
    """Launchers return (ok, err), a bare bool, or None. run_in_api_context is
    the single place that folds all three into (ok, error_text) — and it must
    never raise, because the caller is a WPF click handler with no handler
    above it."""
    from Services.revit_context import run_in_api_context, ensure_api_context

    seen = []
    def _done(ok, err):
        seen.append((ok, err))

    run_in_api_context(lambda: (True, u''), _done)
    run_in_api_context(lambda: (False, u'no window'), _done)
    run_in_api_context(lambda: True, _done)
    run_in_api_context(lambda: None, _done)

    def _boom():
        raise ValueError('boom')
    run_in_api_context(_boom, _done)

    check('tuple success', seen[0] == (True, u''), seen[0])
    check('tuple failure keeps the reason', seen[1] == (False, u'no window'), seen[1])
    check('bare True is success', seen[2][0] is True, seen[2])
    check('None is success', seen[3][0] is True, seen[3])
    check('an exception is a reported failure, not a crash',
          seen[4][0] is False and 'boom' in seen[4][1], seen[4])

    ok, err = ensure_api_context()
    check('ensure_api_context degrades quietly outside Revit',
          ok is False and bool(err), err)


def test_raise_failure_only_discards_its_own_task():
    """With more than one assistant window, the task queue can hold another
    caller's still-good task when Raise() fails for THIS call. The recovery
    path must remove only the entry it just queued, not whatever is at the
    front of the queue — otherwise it silently drops someone else's task and
    its on_done callback never fires."""
    from Services import revit_context as RC

    seen = []
    RC._TASKS.put(('other-token', lambda: (True, u'other'),
                   lambda ok, err: seen.append(('other', ok, err))))

    real_event, RC._EVENT = RC._EVENT, _FakeRaisingEvent()
    try:
        def _done(ok, err):
            seen.append(('mine', ok, err))
        RC.run_in_api_context(lambda: (True, u'mine'), _done)
    finally:
        RC._EVENT = real_event

    check('the other caller\'s task is still queued, untouched',
          RC._TASKS.qsize() == 1, RC._TASKS.qsize())
    token, func, on_done = RC._TASKS.get_nowait()
    check('it is still the other task, not a stray copy',
          token == 'other-token', token)
    check('only the failing call\'s own outcome was reported',
          seen == [('mine', True, u'mine')], seen)


class _FakeRaisingEvent(object):
    def Raise(self):
        raise RuntimeError('event disposed')


def test_tools_are_launched_through_the_api_context():
    """Drift lock for the crash that made every tool unopenable from chat.

    A pushbutton script is a Revit command: it may call ExternalEvent.Create
    or open a Transaction. The assistant's callbacks run while Revit is IDLE,
    which is not a "standard API execution", so calling a launcher directly
    threw

        Attempting to create an ExternalEvent outside of a standard API execution

    right after the assistant had announced it was opening the tool (BCF
    Reader, ManaLoca, BatchOut). Every launcher call must go through
    _launch_tool → run_in_api_context."""
    import re as _re
    path = os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py')
    if not os.path.exists(path):
        path = os.path.join(TAB, 'Support.panel', 'T3LabAssistant.pushbutton',
                            'script.py')
    with io.open(path, encoding='utf-8') as f:
        src = f.read()

    direct = []
    for lineno, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith('#'):
            continue
        if _re.search(r'TOOL_LAUNCHERS\s*(\[[^\]]+\]|\.get\([^)]*\))\s*\(\s*\)', line):
            direct.append(lineno)
    check('no launcher is invoked outside the API context hop', not direct, direct)
    # Scoped to the method BODY (up to the next `def` at method indent) rather
    # than a character budget: the old {0,1400} window failed the moment the
    # success-reporting branch was added, which says nothing about the hop.
    body = _re.search(r'\n    def _launch_tool\b[\s\S]*?(?=\n    def )', src)
    check('_launch_tool marshals through run_in_api_context',
          body is not None and 'run_in_api_context' in body.group(0))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tool catalog matches what is actually installed
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_registry():
    """Populate the tool registry in a temp file and return (module, tools)."""
    from Services import tool_discovery as td
    tmp = tempfile.mkdtemp(prefix='t3lab_reg_')
    td.REGISTRY_FILE = os.path.join(tmp, 'tool_registry.json')
    td.discover_new_tools()
    return td, td.get_registered_tools()


def test_every_registered_tool_exists_on_disk():
    """The catalog must never advertise an intent the assistant cannot honour.
    Eight intents (ParaSync, ProjectName, Workset, DimText, UpperAll, Reset
    Overrides, Grids, LoadFamilyCloud) pointed at deleted pushbuttons.

    A urlbutton has no script.py at all — its hyperlink is the target."""
    _td, tools = _fresh_registry()
    check('registry is not empty', len(tools) > 10, '{} tools'.format(len(tools)))
    missing = [t.get('intent') for t in tools
               if not (t.get('url')
                       or (t.get('script_path') and os.path.exists(t['script_path'])))]
    check('every registered intent has a launchable target', not missing, missing)


def test_propertyline_is_reachable():
    """PropertyLine.pushbutton exists but was listed in _SKIP_BUTTONS as
    "already hard-coded in TOOL_LAUNCHERS" — where it never appeared. The
    assistant could not open it at all."""
    _td, tools = _fresh_registry()
    intents = set(t.get('intent') for t in tools)
    check('PropertyLine is discoverable',
          any('propertyline' in (i or '') for i in intents),
          sorted(i for i in intents if 'prop' in (i or '')))


def test_registry_prunes_vanished_buttons():
    """Registration was append-only: a renamed or deleted pushbutton stayed in
    the registry forever and kept being offered to the model."""
    td, _tools = _fresh_registry()
    reg = td.load_registry()
    reg['tools']['Ghost.pushbutton'] = {
        'button': 'Ghost.pushbutton', 'intent': 'open_ghost',
        'script_path': os.path.join(REPO, 'no', 'such', 'script.py'),
        'title': 'Ghost',
    }
    td.save_registry(reg)
    td.discover_new_tools()
    check('vanished button is pruned from the registry',
          'Ghost.pushbutton' not in td.load_registry().get('tools', {}))


def test_skip_list_only_hides_real_buttons():
    """_SKIP_BUTTONS must not name buttons that do not exist — a stale entry
    there is how PropertyLine stayed invisible."""
    from Services.tool_discovery import _SKIP_BUTTONS, scan_all_pushbuttons
    on_disk = set(t['button'] for t in scan_all_pushbuttons())
    stale = sorted(b for b in _SKIP_BUTTONS if b not in on_disk)
    check('no stale entries in _SKIP_BUTTONS', not stale, stale)


def test_builtin_tools_are_installed():
    """nlu_engine._BUILTIN_TOOLS covers only tools with a dedicated launcher;
    each must correspond to something really shipped."""
    from Intelligence.nlu_engine import _BUILTIN_TOOLS
    intents = set(t[0] for t in _BUILTIN_TOOLS)
    check('_BUILTIN_TOOLS is only the special launchers',
          intents == set(['open_batchout', 'open_loadfamily']), sorted(intents))
    check('BatchOut ships',
          os.path.exists(os.path.join(TAB, 'Views & Sheets.panel',
                                      'BatchOut.pushbutton', 'script.py')))
    check('Family Manager ships',
          os.path.exists(os.path.join(TAB, 'Modeling & Datum.panel',
                                      'ManaFami.pushbutton', 'script.py')))


def test_renamed_tools_resolve_to_real_tools():
    """"workset" used to hit the hardcoded open_workset trigger (score 35) and
    never reached ManaWorkset, which is the tool that replaced it."""
    _fresh_registry()
    from Intelligence.nlu_engine import resolve_tool

    match, cands = resolve_tool('manaworkset')
    check('manaworkset resolves',
          bool(match) and 'workset' in (match.get('intent') or ''),
          '{} / {}'.format(match, cands))
    check('resolved workset tool is not the deleted intent',
          not match or match.get('intent') != 'open_workset',
          match)

    match, _c = resolve_tool('manaviews')
    check('manaviews resolves', bool(match) and 'view' in (match.get('intent') or ''),
          match)


def test_every_ribbon_button_is_openable():
    """Full coverage lock: every launchable button on the ribbon must be in the
    registry, except the ones with a dedicated launcher (BatchOut, ManaFami)
    and the assistant itself.

    Before this, `.urlbutton` folders were not scanned at all — "open Autodesk
    Forma" answered that no such tool existed while the button sat on the
    ribbon two panels away."""
    td, tools = _fresh_registry()
    on_disk = set(t['button'] for t in td.scan_all_buttons())
    registered = set(t['button'] for t in tools)
    gap = sorted(on_disk - registered - set(td._SKIP_BUTTONS))
    check('every ribbon button is registered', not gap, gap)
    check('url buttons are discoverable',
          any(t.get('kind') == 'url' for t in tools),
          sorted(t['button'] for t in tools if t.get('kind') == 'url'))


def test_skipped_buttons_are_pruned_from_an_old_registry():
    """_SKIP_BUTTONS was applied only when REGISTERING. ManaFami was registered
    before it was added to the list, so the stale entry survived every rescan
    and collided with the open_loadfamily special launcher — "Family Manager",
    "Mana Fami" and "ManaFami" all resolved to nothing (two exact matches =
    ambiguous)."""
    td, _tools = _fresh_registry()
    reg = td.load_registry()
    skipped = sorted(td._SKIP_BUTTONS)[0]
    reg['tools'][skipped] = {
        'button': skipped, 'intent': 'open_stale', 'title': 'Stale',
        'script_path': os.path.join(TAB, 'Views & Sheets.panel',
                                    'BatchOut.pushbutton', 'script.py'),
    }
    td.save_registry(reg)
    td.discover_new_tools()
    check('a skipped button is pruned even when already registered',
          skipped not in td.load_registry().get('tools', {}))


def _bundle_title(button_dir):
    """The ribbon label from bundle.yaml, or None."""
    import re as _re
    path = os.path.join(button_dir, 'bundle.yaml')
    if not os.path.exists(path):
        return None
    with io.open(path, encoding='utf-8') as f:
        for line in f:
            m = _re.match(r'\s*title\s*:\s*(.+)', line)
            if m:
                return (m.group(1).strip().strip('"\'')
                        .replace('\\n', ' ').strip())
    return None


def test_every_tool_resolves_by_every_name_it_shows():
    """A tool must be openable by each name the user can see: its script
    __title__, its folder name, and the RIBBON LABEL.

    The ribbon label is not always the script title — the Wall_Adjust Base
    button reads "Auto Adj Base Offset" on the ribbon and "Auto Adjust Base
    Offset" in its script — and typing what the ribbon showed resolved to
    nothing at all."""
    td, tools = _fresh_registry()
    from Intelligence.nlu_engine import resolve_tool

    by_button = dict((t['button'], t) for t in tools)
    misses = []
    for scanned in td.scan_all_buttons():
        entry = by_button.get(scanned['button'])
        if not entry:
            continue                      # skip-listed: covered by its own test
        names = [entry['title'], td._strip_suffix(scanned['button'])]
        label = _bundle_title(scanned['path'])
        if label:
            names.append(label)
        for name in names:
            for phrase in (name, u'open ' + name, u'mở ' + name):
                match, cands = resolve_tool(phrase)
                if not match or match.get('intent') != entry['intent']:
                    misses.append(u'{!r} → {} (want {})'.format(
                        phrase, match and match.get('intent'), entry['intent']))
    check('every tool resolves by every name it shows', not misses, misses[:6])


def _server_tool_names():
    """Tool names registered in core/server.py, read from source.

    core.server cannot be imported headlessly (it pulls in the Revit API), so
    the names are lifted from the schema literals.
    """
    import re as _re
    path = os.path.join(LIB, 'core', 'server.py')
    with io.open(path, encoding='utf-8') as f:
        return set(_re.findall(r"['\"]name['\"]:\s*['\"]([a-z0-9_]+)['\"]", f.read()))


def test_no_dead_intent_advertised_anywhere():
    """Drift lock. Every open_* intent named in a static prompt or table must
    be either a special launcher or a live registry intent."""
    _td, tools = _fresh_registry()
    live = set(t.get('intent') for t in tools)
    live.update(['open_batchout', 'open_batchout_configured', 'open_loadfamily'])
    # The MCP Revit tools registered in core/server.py share the open_* shape
    # (open_document). They are a separate catalog, validated at runtime by
    # t3lab_agent.is_mcp_intent() against the live server registry.
    live.update(_server_tool_names())

    import re as _re
    sources = [
        os.path.join(LIB, 'Intelligence', 'nlu_engine.py'),
        os.path.join(LIB, 'Intelligence', 'local_llm.py'),
        os.path.join(LIB, 'Intelligence', 't3lab_agent.py'),
        os.path.join(LIB, 'Intelligence', 't3lab_assistant.py'),
    ]
    dead = {}
    for path in sources:
        with io.open(path, encoding='utf-8') as f:
            for lineno, line in enumerate(f, 1):
                # Skip the comments that deliberately name the removed intents.
                if line.lstrip().startswith('#'):
                    continue
                # Quoted only: bare open_verb / open_documents are local
                # identifiers, not intents.
                for intent in _re.findall(r'["\']([a-z]*open_[a-z0-9_]+)["\']', line):
                    if intent.startswith('open_') and intent not in live:
                        dead.setdefault(os.path.basename(path), []).append(
                            '{}:{}'.format(intent, lineno))
    check('no dead open_* intent is advertised', not dead, dead)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Language is consistent
# ─────────────────────────────────────────────────────────────────────────────

def test_agent_prompt_language_follows_setting():
    """The agent prompt used to hardcode "Always reply in English", which
    contradicted the UI once the assistant's own strings followed the user."""
    from Intelligence.agent_loop import build_agent_system_prompt

    en = build_agent_system_prompt(lang='en')
    vi = build_agent_system_prompt(lang='vi')
    auto = build_agent_system_prompt(lang='auto')

    check('en pins English', 'reply in English' in en)
    check('vi pins Vietnamese', 'reply in Vietnamese' in vi)
    check('vi does not also demand English', 'reply in English' not in vi)
    check('auto mirrors the user', 'SAME language' in auto)
    check('auto pins neither language',
          'Always reply in English' not in auto
          and 'Always reply in Vietnamese' not in auto)


def test_specialist_prompt_forwards_language():
    from Intelligence.agents.specialists import build_specialist_prompt
    vi = build_specialist_prompt(None, lang='vi')
    check('specialist prompt honours lang', 'reply in Vietnamese' in vi)


def test_system_prompt_is_static_for_prompt_cache():
    """The whole point of P1: the system block must be byte-identical across
    turns so Anthropic's cache breakpoint on it actually hits.

    Live Revit state (active view, selection — re-read by a 2s timer) used to
    be interpolated into the prompt, so the system block never repeated and
    the entire prefix was re-processed every single turn.
    """
    from Intelligence.agent_loop import (build_agent_system_prompt,
                                         build_context_block,
                                         apply_context_block,
                                         strip_context_block)

    a = build_agent_system_prompt(lang='en')
    b = build_agent_system_prompt(u"View: Level 1 | Selection: 3", lang='en')
    check('system prompt ignores live context', a == b)
    check('no stale context placeholder left behind', '{context}' not in a)

    empty = build_context_block()
    check('no live state = no extra tokens', empty == u"")

    blk = build_context_block(revit_context=u"View: Level 1",
                              knowledge_ref=u"## Reference\n[1] spec")
    check('context block carries the view', 'View: Level 1' in blk)
    check('context block carries the knowledge ref', '[1] spec' in blk)

    merged = apply_context_block(u"tô đỏ tường", blk)
    check('user text survives', merged.endswith(u"tô đỏ tường"))
    check('context comes first', merged.startswith('<<<T3LAB_LIVE_CONTEXT'))
    check('round-trips back to the raw turn',
          strip_context_block(merged) == u"tô đỏ tường")

    # Vision turns send a block list, not a string — the image blocks must
    # keep their own positions with the context prepended as text.
    blocks = apply_context_block(
        [{"type": "text", "text": "look"}, {"type": "image", "source": {}}], blk)
    check('block list stays a list', isinstance(blocks, list))
    check('context prepended as its own text block',
          blocks[0]['type'] == 'text' and 'View: Level 1' in blocks[0]['text'])
    check('image block preserved', blocks[-1]['type'] == 'image')
    check('no context = content untouched',
          apply_context_block(u"hi", u"") == u"hi")


def test_knowledge_prompt_is_single_language():
    """The knowledge prompt was written in Vietnamese with an
    "always answer in English" line bolted on."""
    from Intelligence.knowledge.knowledge_agent import get_system_prompt
    vi, en = get_system_prompt(True), get_system_prompt(False)
    check('vi knowledge prompt does not demand English',
          'in English' not in vi)
    check('en knowledge prompt is English', 'Always answer in English' in en)
    check('en knowledge prompt has no Vietnamese rules',
          'NGUYÊN TẮC' not in en)


def test_assistant_cards_are_bilingual():
    """Every visible card string had a single Vietnamese form, which is why
    the window still mixed languages after the English lock."""
    from GUI import AssistantCards as AC
    for key, pair in AC._TEXT.items():
        check('card string "{}" has both languages'.format(key),
              len(pair) == 2 and pair[0] and pair[1] and pair[0] != pair[1],
              pair)
    check('action labels cover the same actions',
          set(AC._ACTION_LABELS_VI) == set(AC._ACTION_LABELS_EN))


def test_reply_language_setting_roundtrip():
    import config.settings as CS
    CS.T3LabAISettings._instance = None
    s = CS.T3LabAISettings()
    check('default is auto', s.get_reply_language() == 'auto')
    s.set_reply_language('vi')
    check('vi persists', s.get_reply_language() == 'vi')
    s.set_reply_language('klingon')
    check('unknown value falls back to auto', s.get_reply_language() == 'auto')


# ─────────────────────────────────────────────────────────────────────────────
# 4. Routing ladder
# ─────────────────────────────────────────────────────────────────────────────

_PREV = {'specialist': 'revit_action', 'skill': 'model-cleanup'}


def test_continuation_needs_a_trailing_question():
    """The old test accepted a '?' anywhere in the last 250 characters, so a
    question mark in a mid-message aside opened the continuation path."""
    from Intelligence import routing as R

    check('question at the end counts',
          R.ends_with_question(u"Bạn muốn áp dụng cho toàn bộ project?"))
    check('trailing markdown does not hide it',
          R.ends_with_question(u"Which scope do you mean?**"))
    check('mid-message question does not count',
          not R.ends_with_question(
              u"Bạn hỏi 'cái gì?' thì đây là kết quả.\nĐã tô đỏ 128 tường."))
    check('statement is not a question',
          not R.ends_with_question(u"Đã tô đỏ 128 tường."))
    check('empty is not a question', not R.ends_with_question(u""))


def test_continuation_accepts_real_answers():
    from Intelligence import routing as R
    q = u"Bạn muốn áp dụng cho toàn bộ project hay chỉ view hiện tại?"

    for reply in (u"1", u"2", u"b", u"ok", u"vâng", u"toàn bộ project",
                  u"chỉ view hiện tại", u"phương án 2", u"cả hai"):
        check(u'continues on "{}"'.format(reply),
              R.is_continuation(q, reply, _PREV), reply)


def test_continuation_rejects_new_commands():
    """"tô vàng sàn" after a clarifying question is a NEW command. It is 13
    characters, so the old length test passed it and only the keyword escape
    hatch stopped it — one check standing alone."""
    from Intelligence import routing as R
    q = u"Bạn muốn áp dụng cho toàn bộ project hay chỉ view hiện tại?"

    check('new colour command is not a continuation',
          not R.is_continuation(q, u"tô vàng sàn", _PREV))
    check('new export command is not a continuation',
          not R.is_continuation(q, u"xuất pdf toàn bộ G sheet", _PREV))
    check('delete command is not a continuation',
          not R.is_continuation(q, u"xóa hết tường tầng 2", _PREV))
    check('shape check stands without the keyword hatch',
          not R.looks_like_answer(u"tô vàng sàn"),
          'action verb must disqualify an answer')


def test_continuation_rejects_closing_questions():
    from Intelligence import routing as R
    for closer in (u"Bạn có muốn thực hiện một hành động khác không?",
                   u"Anything else I can help with?",
                   u"Cần gì thêm không?"):
        check(u'closing question does not carry over',
              not R.is_continuation(closer, u"1", _PREV), closer)


def test_small_talk_never_rides_the_previous_turn():
    """The bug: typing "hello" right after the assistant's own greeting took
    SECONDS on a local model. Both of the assistant's standard closers —
    "Bạn muốn làm gì tiếp?" and "Xin chào! Bạn có cần giúp đỡ gì không?" —
    end in "?" and were missing from _CLOSING_QUESTIONS, so "hello" looked
    like the answer to a clarifying question. Continuation skips the whole
    deterministic stage, so the offline NLU never got to reply `greet`
    instantly and the turn went to the LLM instead."""
    from Intelligence import routing as R

    for closer in (u"Bạn muốn làm gì tiếp?",
                   u"Xin chào! Bạn có cần giúp đỡ gì không?",
                   u"Hello! I'm T3Lab Assistant 👋\nWhat would you like to do today?"):
        check(u'"{}" is a closer'.format(closer.splitlines()[-1][:38]),
              R.is_closing_question(closer), closer)

    # Even after a REAL clarifying question, small talk answers nothing.
    q = u"Bạn muốn liệt kê level của model nào?"
    for greeting in (u"hello", u"hi", u"chào bạn", u"cảm ơn", u"bye"):
        check(u'"{}" does not carry over'.format(greeting),
              not R.is_continuation(q, greeting, _PREV), greeting)

    # The affirmations that ARE answers must keep continuing — this is why
    # nlu_engine.is_conversational() (which calls "ok"/"1"/"no" small talk)
    # cannot be reused here.
    for answer in (u"ok", u"vâng", u"all", u"toàn bộ project"):
        check(u'"{}" still carries over'.format(answer),
              R.is_continuation(q, answer, _PREV), answer)

    # A polite opener in front of a real request is not small talk.
    check('"hi, export the G sheets" is a request',
          not R.is_social_turn(u"hi, export the G sheets"))


def test_english_small_talk_is_instant():
    """English "how are you" / "what's up" / "you there" carried no VI trigger,
    so classify() fell through to a full LLM turn — seconds on a local model.
    They must now classify as instant-safe small talk (answered from a canned
    reply, no model). Emotional/complaint chat must NOT be instant."""
    from Intelligence import nlu_engine as N
    from Intelligence import routing as R

    # Recognised as instant small talk, answered without the LLM.
    for q in (u"how are you?", u"how are you today", u"what's up", u"whats up",
              u"sup man", u"you there?", u"you good", u"hows it going",
              u"how do you do", u"long time no see"):
        r = N.classify(q)
        check(u'"{}" classifies'.format(q), bool(r), q)
        check(u'"{}" is chat/greet'.format(q),
              r and r.get('intent') in ('chat', 'greet'), r)
        check(u'"{}" is _instant'.format(q), r and r.get('_instant') is True, r)
        check(u'"{}" is not a generic fallback'.format(q),
              r and not r.get('_generic_fallback'), r)

    # Already-recognised social chat is instant too (thanks / reaction / bye).
    for q in (u"thanks", u"thank you so much", u"great job", u"awesome",
              u"cảm ơn nhé", u"bye"):
        r = N.classify(q)
        check(u'"{}" is instant social'.format(q),
              r and r.get('_instant') is True, r)

    # Emotional / complaint / low-mood chat is NOT instant — it keeps the LLM.
    for q in (u"mệt quá", u"chán quá", u"it's broken", u"this is stupid",
              u"nó không chạy được"):
        r = N.classify(q)
        check(u'"{}" is not instant (stays LLM)'.format(q),
              not (r and r.get('_instant')), r)

    # A command that opens with a greeting is never swallowed as small talk.
    for q in (u"how are you exporting the walls to pdf",
              u"hi, export the G sheets"):
        check(u'"{}" is not small talk'.format(q),
              not R.is_social_turn(q) and not N._match_english_smalltalk(q), q)

    # And it does not ride a previous clarifying question either.
    q = u"Which model do you want to list levels for?"
    for greeting in (u"how are you?", u"what's up", u"you there"):
        check(u'"{}" does not carry over'.format(greeting),
              not R.is_continuation(q, greeting, _PREV), greeting)


def test_which_model_asks_about_the_llm_not_the_rvt():
    """"model" is the most overloaded word in this product — the .rvt file or
    the LLM behind the chat. Asked "model bạn đang dùng là gì" the assistant
    had no deterministic answer, fell through to the LLM, and a local model
    replied "Tôi chưa mở bất kỳ model Revit nào": the wrong noun, plus an
    ungrounded claim about the open document. The provider and model id are
    known exactly, so this must never reach the LLM."""
    from Intelligence import routing as R

    for q in (u"model bạn đang dùng là gì",
              u"bạn đang dùng model nào",
              u"what model are you using",
              u"which LLM are you using",
              u"bạn dùng AI gì",
              u"llm nào vậy",
              u"provider nào đang chạy",
              u"bạn chạy trên model gì"):
        check(u'"{}" asks about the LLM'.format(q), R.asks_which_llm(q), q)

    # Revit-document questions must stay out — an interrogative plus the word
    # "model" is not enough, and a document verb settles it the other way
    # even when a second-person pronoun is present.
    for q in (u"model có bao nhiêu tường",
              u"mở model này",
              u"so sánh 2 model",
              u"kiểm tra model",
              u"model nào đang mở",
              u"model bạn vừa mở tên gì",
              u"bạn mở file rvt nào",
              u"bạn kiểm tra model giúp tôi",
              u"dựng model nhà 3 tầng",
              u"tạo model mới",
              u"list levels",
              u"hello"):
        check(u'"{}" is not an LLM question'.format(q),
              not R.asks_which_llm(q), q)


def test_continuation_guards():
    from Intelligence import routing as R
    q = u"Which scope?"
    check('no previous decision → no carryover',
          not R.is_continuation(q, u"1", None))
    check('fresh keyword hit → no carryover',
          not R.is_continuation(q, u"1", _PREV, fresh_keyword_hit=True))
    check('empty reply → no carryover', not R.is_continuation(q, u"  ", _PREV))
    check('long reply → no carryover',
          not R.is_continuation(q, u"x" * 120, _PREV))


def test_learned_pattern_defers_to_nlu():
    """A learned pattern used to take the turn outright before the NLU ran, so
    a stale mapping outranked a confident read of the live tool catalog."""
    from Intelligence import routing as R
    learned = {'intent': 'open_batchout', 'params': {}}

    check('wins when the NLU has no opinion',
          R.learned_pattern_wins(learned, None))
    check('wins when the NLU says unknown',
          R.learned_pattern_wins(learned, {'intent': 'unknown'}))
    check('wins when both agree',
          R.learned_pattern_wins(learned, {'intent': 'open_batchout'}))
    check('loses when the NLU names another tool',
          not R.learned_pattern_wins(learned, {'intent': 'check_spelling'}))
    check('loses to an authoritative catalog answer',
          not R.learned_pattern_wins(
              learned, {'intent': 'help', '_authoritative': True}))
    check('nothing learned → never wins',
          not R.learned_pattern_wins(None, None))


def test_dispatcher_precedence_conflicts():
    """The dispatcher's keyword stage is ordering-sensitive and its comments
    document specific conflicts it had to be tuned for. None of them had a
    test, so a table edit could silently undo the tuning."""
    from Intelligence.agents.dispatcher import AgentDispatcher
    d = AgentDispatcher()

    def spec(text):
        return d.classify(text, allow_llm=False).get('specialist')

    cases = [
        # "đổi model" contains the action verb 'doi' but is a document switch
        (u"đổi model sang file kia", 'multi_doc'),
        (u"so sánh 2 model", 'multi_doc'),
        # 'xuat'/'export' are action words too; the export spec must win
        (u"xuất pdf toàn bộ sheet", 'export'),
        # 'tao tuong' would otherwise land in generic action
        (u"tạo tường tầng 2", 'modeling'),
        # audits get the QA role, not the data role
        (u"kiểm tra warning trong model", 'qa_check'),
        # a write verb wins over doc/count words
        (u"đổi chiều cao theo tiêu chuẩn", 'revit_action'),
        # colour phrases only match WITH diacritics
        (u"tô đỏ tường", 'revit_action'),
        # read-only questions
        (u"có bao nhiêu cửa", 'revit_data'),
        # document questions
        (u"theo tiêu chuẩn TCVN thì sao", 'knowledge'),
        # PDF markup workflow
        (u"xử lý comment bản vẽ", 'comment'),
    ]
    for text, want in cases:
        got = spec(text)
        check(u'"{}" → {}'.format(text, want), got == want,
              'got {}'.format(got))

    # "bản vẽ" folds to "ban ve" — its "ve" must not count as the verb "vẽ".
    check('"bản vẽ" alone is not an action',
          spec(u"bản vẽ này là gì") != 'revit_action',
          spec(u"bản vẽ này là gì"))
    # Diacritic-less "to do" must stay ambiguous rather than false-matching
    # English ("what to do...").
    check('"what to do" is not a colour command',
          spec(u"what to do next") != 'revit_action',
          spec(u"what to do next"))


def test_build_requests_reach_the_modeling_specialist():
    """The bug: "dựng hệ grid 5x5 với thông số là 12345 và abcde" matched no
    modeling phrase, fell through to `general`, and the model answered with
    the nearest tool its catalog DID contain — it wrote a create_text_note
    reading "Grid 5x5: 12345, abcde" into the model.

    Only `modeling` carries the geometry constructors; `general` and
    `revit_action` have none, so any build wording that misses this stage is
    a wrong-tool report waiting to happen."""
    from Intelligence.agents.dispatcher import AgentDispatcher
    d = AgentDispatcher()

    def spec(text):
        return d.classify(text, allow_llm=False).get('specialist')

    builds = [
        # the reported case — verb + classifier + noun, none of it literal
        u"dựng hệ grid 5x5 với thông số là 12345 và abcde",
        u"dựng hệ lưới trục 5x5",
        u"tạo hệ lưới trục A-E, 1-5",
        u"dựng grid",
        u"thêm 2 tầng nữa",
        u"add 3 levels",
        u"create a grid system",
    ]
    for text in builds:
        got = spec(text)
        check(u'"{}" → modeling'.format(text), got == 'modeling', got)

    # The pair rule is adjacency-bound so it cannot annex the action
    # specialist's work: only the noun the verb governs counts.
    for text, want in ((u"tạo sheet mới", 'revit_action'),
                       (u"tạo view mặt bằng", 'revit_action'),
                       (u"thêm tham số cho tường", 'revit_action'),
                       (u"đổi tên tường tầng 2", 'revit_action')):
        got = spec(text)
        check(u'"{}" stays {}'.format(text, want), got == want, got)


# (utterance, the tool the assistant must be able to call for it).
# One entry per user-facing registry tool, in the wording people actually
# type. This is the corpus the coverage invariant below runs on.
ROUTING_CORPUS = [
    # reads
    (u"có bao nhiêu bức tường", 'ai_element_filter'),
    (u"how many doors are there", 'ai_element_filter'),
    (u"thống kê cửa theo tầng", 'ai_element_filter'),
    (u"liệt kê tất cả sheet", 'revit_list_sheets'),
    (u"list all views", 'revit_list_views'),
    (u"liệt kê level", 'list_levels'),
    (u"danh sách workset", 'list_worksets'),
    (u"view hiện tại là gì", 'get_current_view_info'),
    (u"đang chọn gì", 'revit_get_selected_elements'),
    (u"thông tin dự án", 'revit_get_project_info'),
    (u"model có bao nhiêu warning", 'get_model_warnings'),
    (u"sức khỏe model thế nào", 'get_model_health'),
    (u"đọc schedule cửa", 'get_schedule_data'),
    (u"có những loại tường nào", 'get_available_family_types'),
    (u"lấy giá trị parameter Comments của element 12345", 'get_parameter'),
    (u"kích thước bao của element 12345", 'get_element_bounding_box'),
    (u"khối lượng bê tông trong model", 'get_material_quantities'),
    (u"lấy dầm trên tầng 3", 'get_elements_by_level'),
    # modifies
    (u"đổi tên sheet A-101 thành A-102", 'rename_element'),
    (u"set parameter Comments cho tường", 'bulk_set_parameter'),
    (u"tô đỏ tường", 'revit_override_color'),
    (u"tô màu cửa theo Type", 'color_elements'),
    (u"pin toàn bộ tường", 'operate_element'),
    (u"chọn hết cửa sổ", 'select_elements'),
    (u"đổi type tường sang Generic 200", 'edit_elements'),
    (u"đổi scale view thành 1:50", 'manage_view'),
    (u"di chuyển element 123 sang phải 2m", 'move_elements'),
    (u"xoay element 123 45 độ", 'rotate_element'),
    (u"copy element 123 lên 3m", 'copy_elements'),
    (u"xóa element 123", 'delete_element'),
    (u"tag tất cả phòng", 'tag_all_rooms'),
    (u"tag hết tường trong view", 'tag_all_walls'),
    (u"gán tag cho cửa", 'tag_elements'),
    (u"thêm ghi chú vào view", 'create_text_note'),
    (u"chuyển sang view Level 1", 'set_active_view'),
    # sheets / views
    (u"tạo sheet mới A-201", 'create_sheet'),
    (u"đặt view Level 1 lên sheet A-101", 'add_view_to_sheet'),
    (u"đặt các view lên sheet", 'place_views_on_sheets'),
    (u"nhân bản view Level 1", 'duplicate_view'),
    (u"tạo view mặt bằng tầng 2", 'create_view'),
    (u"tạo schedule cửa", 'create_schedule'),
    (u"áp view template cho các view mặt bằng", 'apply_view_template'),
    (u"đổi tên view template", 'manage_view_template'),
    (u"đánh số lại sheet", 'manage_sheet'),
    (u"tạo revision mới", 'manage_revision'),
    (u"tạo view filter ẩn tường", 'create_view_filter'),
    # modeling
    (u"dựng hệ grid 5x5 với thông số là 12345 và abcde", 'create_grid'),
    (u"tạo level tầng 3 cao độ 7m", 'create_level'),
    (u"dựng tường từ (0,0) đến (5,0)", 'place_wall'),
    (u"tạo sàn theo biên phòng", 'create_surface_based_element'),
    (u"đặt cửa vào tường", 'create_point_based_element'),
    (u"tạo dầm từ trục A đến trục B", 'create_line_based_element'),
    (u"dựng hệ dầm theo lưới trục", 'create_structural_framing_system'),
    (u"tạo phòng ở tọa độ 3,4", 'create_room'),
    (u"tạo sàn từ các phòng", 'room_to_floor'),
    (u"load family cửa vào project", 'load_family'),
    (u"tạo dimension giữa trục A và B", 'create_dimension'),
    (u"join tường với sàn", 'join_geometry'),
    (u"cắt tường này làm đôi", 'split_element'),
    (u"chia đường line thành 3 đoạn", 'split_curve'),
    (u"vẽ filled region trong view", 'create_detail_annotation'),
    # QA
    (u"audit model", 'audit_model'),
    (u"purge model", 'purge_unused'),
    (u"kiểm tra geometry lỗi trước khi export", 'check_bad_geometry'),
    (u"kiểm tra vật liệu của tường", 'manage_material'),
    (u"kiểm tra link model", 'manage_links'),
    # export
    (u"xuất pdf toàn bộ sheet", 'export_sheets_pdf'),
    (u"xuất dwg các sheet G", 'export_dwg'),
    (u"xuất ảnh view hiện tại", 'export_image'),
    (u"xuất ifc", 'export_model'),
    (u"xuất dữ liệu phòng ra file", 'export_room_data'),
    # worksets / parameters
    (u"tạo workset mới tên Kết cấu", 'create_workset'),
    (u"chuyển toàn bộ tường sang workset Kiến trúc", 'set_element_workset'),
    (u"tạo project parameter cho cửa", 'create_project_parameter'),
    # documents
    (u"có những model nào đang mở", 'list_open_documents'),
    (u"chuyển sang model kia", 'switch_active_document'),
    (u"mở file rvt này", 'open_document'),
    (u"đóng model hiện tại", 'close_document'),
    (u"lưu model", 'manage_document'),
    (u"model nào mở gần đây", 'list_recent_documents'),
]


def test_comments_parameter_is_not_a_pdf_markup():
    """"Comments" is a built-in Revit parameter as well as the word for a
    PDF markup, and the comment stage runs first — so "lấy giá trị parameter
    Comments của element 12345" was handed to the markup-resolution agent.
    A parameter context has to disarm the bare word."""
    from Intelligence.agents.dispatcher import AgentDispatcher
    d = AgentDispatcher()

    def spec(text, **kw):
        return d.classify(text, allow_llm=False, **kw).get('specialist')

    for text in (u"lấy giá trị parameter Comments của element 12345",
                 u"set parameter Comments cho tường",
                 u"điền thông số Comments cho cửa"):
        got = spec(text)
        check(u'"{}" is not the markup agent'.format(text[:34]),
              got != 'comment', got)

    # The real markup workflow must be untouched.
    for text in (u"xử lý comment bản vẽ", u"hoàn thiện cmt",
                 u"đọc markup bluebeam"):
        got = spec(text)
        check(u'"{}" still routes to comment'.format(text), got == 'comment',
              got)
    # An annotated PDF is decisive on its own, parameter wording or not.
    check('an annotated PDF still wins',
          spec(u"đọc giá trị parameter trong file này",
               attached_pdf_annotated=True) == 'comment')


def test_every_routed_request_can_reach_its_tool():
    """THE invariant behind the create_grid bug, stated once for every tool.

    A specialist that is routed a request it has no tool for does not say
    "I can't do that" — the model picks the nearest tool it CAN see and
    reports that as success ("dựng hệ grid 5x5" → a create_text_note reading
    "Grid 5x5"). So for every utterance, the tool it needs must be visible
    to the specialist it lands on. Cloud providers get the full registry;
    a local model sees the specialist subset, or ESSENTIAL_TOOL_NAMES when
    the specialist declares none."""
    from Intelligence.agents.dispatcher import AgentDispatcher
    from Intelligence.agents import specialists as S
    from Intelligence.tool_schema import ESSENTIAL_TOOL_NAMES

    d = AgentDispatcher()
    gaps = []
    for text, need in ROUTING_CORPUS:
        spec_name = d.classify(text, allow_llm=False).get('specialist')
        sp = S.SPECIALISTS.get(spec_name)
        # Mirrors _run_native_agent: a falsy subset means "provider default",
        # which for a local model is ESSENTIAL_TOOL_NAMES.
        subset = sp.tools_for(True) if sp else None
        visible = subset or ESSENTIAL_TOOL_NAMES
        if need not in visible:
            gaps.append(u'"{}" needs {} but landed on {}'.format(
                text, need, spec_name))
    check('every corpus request can reach its tool ({} cases)'.format(
        len(ROUTING_CORPUS)), not gaps, u' | '.join(gaps[:6]))


def test_every_tool_is_consciously_placed():
    """A tool nobody wired into a subset is invisible to local models, and
    an invisible tool is answered with the nearest visible one. Adding a
    tool to the registry must therefore be a decision: put it in a
    specialist subset, in ESSENTIAL_TOOL_NAMES, or declare it hidden."""
    from Intelligence.agents import specialists as S
    from Intelligence.tool_schema import (ESSENTIAL_TOOL_NAMES,
                                          LOCAL_HIDDEN_TOOLS,
                                          get_server_tools)
    registry = set(t['name'] for t in get_server_tools())
    check('registry is readable', bool(registry))
    placed = set(ESSENTIAL_TOOL_NAMES) | set(LOCAL_HIDDEN_TOOLS)
    for sp in S.SPECIALISTS.values():
        placed |= set(sp.tool_names or ())
    orphans = sorted(registry - placed)
    check('every registry tool is placed', not orphans,
          'unplaced: {}'.format(', '.join(orphans)))
    stale = sorted(placed - registry)
    check('no subset names a tool that no longer exists', not stale,
          'stale: {}'.format(', '.join(stale)))


def test_announcing_the_work_is_not_doing_it():
    """The bug: "list levels" was answered with "Đang liệt kê các mức trong
    dự án…" and nothing else — the model narrated, called no tool, and the
    loop accepted any text-only reply as the final answer. On a data
    question that is worse than a missing answer: prose produced with zero
    read tools is ungrounded by construction."""
    from Intelligence.agent_loop import _announces_work

    for text in (u"Đang liệt kê các mức trong dự án...",
                 u"Let me check the levels for you.",
                 u"I'll list the levels now",
                 u"Tôi sẽ tiến hành kiểm tra model",
                 u"Đang xử lý..."):
        check(u'"{}" is a promise, not an answer'.format(text[:34]),
              _announces_work(text), text)

    # A clarifying question and a real answer must never be nudged.
    for text in (u"Bạn muốn liệt kê level của model nào?",
                 u"Which sheets do you want to export?",
                 u"Model có 5 level: L1, L2, L3, L4, L5.",
                 u""):
        check(u'"{}" is left alone'.format(text[:34]),
              not _announces_work(text), text)

    # End to end: narrate first, then obey the nudge and answer for real.
    tools = [{'name': 'list_levels'}]
    result, _shown, ran, prov = _run_agent([
        {'text': u'Đang liệt kê các mức trong dự án...'},
        {'text': u'', 'calls': [{'id': '1', 'name': 'list_levels',
                                 'args': {}}]},
        {'text': u'Model có 2 level: L1, L2.'},
    ], tools=tools)
    check('the nudged turn actually called the tool', ran == ['list_levels'],
          ran)
    check('and the user gets the real answer',
          result.get('text') == u'Model có 2 level: L1, L2.',
          result.get('text'))
    fed = [m.get('content') for t in prov.transcripts for m in t
           if m.get('role') == 'user']
    check('the nudge tells the model it called nothing',
          any(u'called no tool' in u'{}'.format(c) for c in fed), fed[-1:])

    # Bounded: a model that keeps narrating is not retried forever, and its
    # text still reaches the user rather than vanishing.
    result2, _shown2, ran2, prov2 = _run_agent([
        {'text': u'Đang liệt kê...'},
        {'text': u'Đang liệt kê...'},
        {'text': u'Đang liệt kê...'},
    ], tools=tools)
    check('narration is nudged at most once', len(prov2.turns) == 1,
          '{} turns left'.format(len(prov2.turns)))
    check('no tool ran on a pure-narration turn', ran2 == [], ran2)
    check('and its text still reaches the user',
          result2.get('text') == u'Đang liệt kê...', result2.get('text'))


def test_tool_result_survives_non_utf8_bytes():
    """The bug: a byte string carrying Windows code-page bytes (a localised
    Revit message, an OS path) reached json, whose encoder decodes byte
    strings as UTF-8 — "'unknown' codec can't decode byte 0xe0 in position
    17" escaped every handler up to _route_input and ended the turn AFTER
    tools had already run. The `u"{}".format(result)` fallback failed on the
    same bytes under IronPython 2.7, so it guarded nothing.

    CPython 3 has no implicit decode, so this test pins the sanitising step
    itself: no bytes may survive into what is handed to json."""
    from Intelligence.agent_loop import _json_safe, _result_to_json, _call_key

    cp1252 = b'\xe0\xf4ng'          # "àông" in cp1252 — NOT valid UTF-8
    payload = {'name': cp1252, 'ids': [1, cp1252], 'nested': {'p': cp1252}}

    clean = _json_safe(payload)

    def has_bytes(o):
        if isinstance(o, bytes):
            return True
        if isinstance(o, dict):
            return any(has_bytes(k) or has_bytes(v) for k, v in o.items())
        if isinstance(o, (list, tuple)):
            return any(has_bytes(v) for v in o)
        return False

    check('no byte string reaches the encoder', not has_bytes(clean))
    check('valid UTF-8 bytes decode as UTF-8',
          _json_safe(u'lưới'.encode('utf-8')) == u'lưới')

    s = _result_to_json(payload)
    check('a non-UTF-8 result still serialises', isinstance(s, str) and s)
    check('the duplicate guard still gets a key',
          _call_key('create_grid', payload)[0] == 'create_grid')


def test_local_catalog_can_build_geometry():
    """A local model routed to `general` sees ESSENTIAL_TOOL_NAMES and
    nothing else. That list carried create_text_note but no constructor, so
    a build request had exactly one plausible-looking tool in it — which is
    how "dựng hệ grid" became a text note. A catalog that can annotate must
    also be able to create."""
    from Intelligence.tool_schema import ESSENTIAL_TOOL_NAMES
    for name in ('create_grid', 'create_level', 'create_room', 'place_wall',
                 'create_line_based_element', 'create_surface_based_element'):
        check(u'{} is in the local catalog'.format(name),
              name in ESSENTIAL_TOOL_NAMES)


def test_spellcheck_fix_detection():
    from Intelligence import routing as R
    for args in (u"fix them", u"apply the corrections", u"sửa hết",
                 u"sửa lỗi chính tả", u"cập nhật lại"):
        check(u'"{}" asks for a fix'.format(args),
              R.wants_spellcheck_fix(args), args)
    for args in (u"", u"scan the project", u"kiểm tra toàn bộ dự án"):
        check(u'"{}" is a scan'.format(args),
              not R.wants_spellcheck_fix(args), args)


def _cap_answer(q, viet):
    from Intelligence import nlu_engine as N
    return N.answer_capability_question(q, viet)['message']


def _is_overview(msg):
    return (u'directly on the Revit model' in msg
            or u'trực tiếp trên model Revit' in msg)


def test_capability_scope_is_not_a_predicate():
    """A capability question is FRAME(PREDICATE [SCOPE]). The scope names WHERE
    the work happens ("with this project", "trong model này") and must never
    select a tool on its own — "what can you do with this project" used to be
    answered with Family Loader, whose doc merely reads "…vào project"."""
    for q in (u"what can you do with this project",
              u"what can you do in this model",
              u"what can you do in revit",
              u"what can you do here",
              u"what can you do",
              u"what tools do you have"):
        check(u'"{}" → overview'.format(q), _is_overview(_cap_answer(q, False)))
    for q in (u"bạn làm được gì với dự án này",
              u"t3lab hỗ trợ gì trong model này",
              u"hiện tại bạn hỗ trợ gì",
              u"có tool nào không",
              u"bạn làm được gì"):
        check(u'"{}" → overview'.format(q), _is_overview(_cap_answer(q, True)))


def test_capability_predicate_still_finds_tools():
    """Stripping the scope must not blunt real asks — a question that carries a
    predicate still resolves against the catalog, in either language."""
    for q, viet, expect in (
            (u"is there a tool to load family",     False, u'Family Loader'),
            (u"is there a tool for dwg",            False, u'DWG'),
            (u"what can you do with dwg files",     False, u'DWG'),
            (u"do you have a tool for point cloud", False, u'Point Cloud'),
            (u"any tool for tile layout",           False, u'Tile Layout'),
            (u"có tool nào để xuất pdf không",      True,  u'BatchOut'),
            (u"có tool nào check model không",      True,  u'Model Auditor'),
            (u"có tool nào quản lý workset không",  True,  u'Workset'),
            # "in" is a homograph: Vietnamese print, English preposition
            (u"có tool nào để in sheet không",      True,  u'BatchOut')):
        msg = _cap_answer(q, viet)
        check(u'"{}" → {}'.format(q, expect),
              expect in msg and not _is_overview(msg), msg[:80])


def test_vietnamese_capability_verbs_reach_the_english_catalog():
    """The catalog is named in English, so a Vietnamese ask for the same
    capability used to find nothing ("đổi tên view" → "chưa có tool")."""
    for q, expect in ((u"có tool nào để đổi tên view không", u'View Manager'),
                      (u"có tool nào quản lý dwg không",     u'DWG Manager'),
                      (u"có tool nào quản lý workset không", u'Workset Manager'),
                      (u"có tool nào quản lý thư viện family không",
                       u'Family Loader')):
        msg = _cap_answer(q, True)
        check(u'"{}" → {}'.format(q, expect), expect in msg, msg[:80])


def test_ubiquitous_word_cannot_name_a_tool():
    """"manager" is in 16 of 44 tool names — it selects nothing. A request whose
    only evidence is such a word must not answer with three arbitrary managers
    (and must not claim no tool exists either): the honest reply is the
    overview. Rarity may damp evidence, never manufacture it."""
    msg = _cap_answer(u"có tool nào quản lý project không", True)
    check(u'"quản lý project" → overview, not 3 arbitrary managers',
          _is_overview(msg), msg[:80])
    # …while the same word plus a selective one still resolves precisely
    msg = _cap_answer(u"có tool nào quản lý workset không", True)
    check(u'"quản lý workset" still resolves precisely',
          u'Workset Manager' in msg and not _is_overview(msg), msg[:80])


def test_capability_ignores_implementation_jargon():
    """Docstrings are written for developers ("v2 - pyRevit WPFWindow
    modeless"). Those words describe the implementation, never a capability, so
    they must not name a tool to the user."""
    msg = _cap_answer(u"is there a tool for modeless windows", False)
    check(u'"modeless windows" names no tool',
          msg.startswith(u'❌'), msg[:80])


def test_abbrev_expansion_respects_word_boundaries():
    """A multi-word abbreviation must not eat into the following word.
    "what are you" → "capabilities query" used to fire inside "what are YOUR
    capabilities", producing the nonsense "capabilities queryr capabilities".
    Single-token stems keep substring semantics on purpose ("images" → "imgs").
    """
    from Intelligence import nlu_engine as N
    exp = lambda q: N._expand(N._norm(q))
    check(u'"what are your capabilities" survives expansion',
          exp(u"what are your capabilities") == u"what are your capabilities",
          exp(u"what are your capabilities"))
    check(u'"what can you do" still collapses',
          exp(u"what can you do") == u"capabilities query")
    check(u'single-token stem still expands plurals',
          exp(u"export images") == u"export imgs", exp(u"export images"))
    check(u'space-padded shorthand still expands',
          exp(u"bo sheet") == u"batchout sheet", exp(u"bo sheet"))


def test_capability_frames_cover_productive_forms():
    """The frame detector must recognise the ability question in its productive
    forms, and stay out of superficially similar questions that are not."""
    from Intelligence import nlu_engine as N
    ask = lambda q: N.is_capability_question(N._expand(N._norm(q)))
    for q in (u"what else can you do", u"what are your capabilities",
              u"list your features", u"show me all your tools",
              u"tôi có thể làm gì với t3lab", u"bạn xử lý được gì"):
        check(u'"{}" is a capability question'.format(q), ask(q))
    for q in (u"what do you think about this wall",
              u"what can you tell me about walls",
              u"tôi phải làm gì bây giờ", u"list all sheets",
              u"what can I do to fix it"):
        check(u'"{}" is not a capability question'.format(q), not ask(q))


def test_pronoun_vs_determiner():
    """"nó/it/this" is anaphora only when it stands IN PLACE OF the noun. As a
    determiner ("this project", "that view") it must not bind to the last tool
    in the history, or an unrelated request silently opens that tool."""
    from Intelligence import nlu_engine as N
    exp = lambda q: N._expand(N._norm(q))

    for q in (u"mở nó", u"nó là gì", u"cái này là gì", u"open it",
              u"what is that"):
        check(u'"{}" is anaphora'.format(q), N._is_pronoun_query(exp(q)))
    for q in (u"what can you do with this project", u"that view is wrong",
              u"this project needs cleanup",
              u"nó không quan trọng bằng việc kiểm tra toàn bộ sheet"):
        check(u'"{}" is not anaphora'.format(q),
              not N._is_pronoun_query(exp(q)))


def test_history_referent_is_a_real_tool_mention():
    """The referent for "nó" is the tool the conversation acted on — the
    assistant's own "Opening X..." line, or a message that IS a tool request.
    A name merely appearing inside prose must not bind it."""
    from Intelligence import nlu_engine as N

    def intent(hist, q):
        r = N.classify(q, history=[{'role': 'assistant', 'content': h}
                                   for h in hist])
        return (r or {}).get('intent')

    check(u'"Opening BatchOut..." + "nó là gì" → BatchOut',
          intent([u"Đang mở BatchOut..."], u"nó là gì") == 'open_batchout')
    check(u'spaced title resolves ("Đang mở DWG Manager...")',
          intent([u"Đang mở DWG Manager..."], u"cái này là gì") == 'open_manadwg')
    check(u'a name inside prose does not bind the pronoun',
          intent([u"thanks for the feedback"], u"mở nó") is None)
    check(u'small talk leaves the pronoun unresolved',
          intent([u"Xin chào!"], u"mở nó") is None)

    from Intelligence import nlu_engine as N2
    r = N2.classify(u"mở nó", history=[
        {'role': 'user', 'content': u"đang mở dwg management giúp tôi với"},
    ])
    check(u'an "Opening X..." phrase from the USER does not count as the '
          u'assistant having launched that tool',
          (r or {}).get('intent') is None, r)


def test_capability_question_beats_anaphora():
    """An explicit capability frame states its own subject — resolving its
    pronoun against the history would open a tool instead of answering."""
    from Intelligence import nlu_engine as N
    r = N.classify(u"what can you do with this project",
                   history=[{'role': 'assistant', 'content': u'Đang mở BatchOut...'}])
    check(u'capability frame is answered, not routed to the last tool',
          r and r.get('intent') == 'help' and _is_overview(r.get('message', '')),
          (r or {}).get('intent'))


# ─────────────────────────────────────────────────────────────────────────────
# 6. Agent loop never shows a tool call as the answer
# ─────────────────────────────────────────────────────────────────────────────
# Observed 2026-08-03: /lod-standard, /parameter-management and
# /annotation-standard each answered with the literal text
#   {"name": "apply_playbook", "parameters": {"playbook_name": "lod-standard"}}
# The model wrote a tool call as chat text naming a tool that does not exist;
# the loop's rescue path rejected the unknown name and then rendered the blob
# as the assistant's reply — and persisted it to the transcript.

_PHANTOM_BLOB = ('{"name": "apply_playbook", "parameters": '
                 '{"playbook_name": "lod-standard"}}')


class _ScriptedProvider(object):
    """Replays canned chat_agent turns and records the transcript it saw."""
    SUPPORTS_NATIVE_TOOLS = True

    def __init__(self, turns):
        self.turns = list(turns)
        self.transcripts = []

    def chat_agent(self, system_prompt, messages, tools,
                   on_delta=None, max_tokens=None):
        self.transcripts.append(list(messages))
        if not self.turns:
            return None
        t = self.turns.pop(0)
        text = t.get('text', '')
        if on_delta and text:
            on_delta(text)          # the blob streams live, as in production
        return {'text': text,
                'tool_calls': t.get('calls', []),
                'assistant_msg': {'role': 'assistant', 'content': text},
                'stop_reason': 'end_turn'}

    def agent_tool_results(self, calls, results):
        return [{'role': 'user', 'content': r} for r in results]


def _run_agent(turns, tools=None):
    """(result, shown, tool_names, provider) for a scripted agent run.

    `shown` is every on_turn_text payload — exactly what reaches a chat
    bubble and self._add_to_history in script.py.
    """
    from Intelligence.agent_loop import AgentLoop
    shown, ran = [], []
    cb = {'on_turn_text':  lambda t, final: shown.append(t),
          'on_text_delta': lambda c: None,
          'on_tool_start': lambda n, a, i: ran.append(n),
          'on_tool_done':  lambda n, r, ok, s: None}
    prov = _ScriptedProvider(turns)
    loop = AgentLoop(prov, lambda n, a: {'ok': True},
                     tools if tools is not None else [{'name': 'ai_element_filter'}],
                     callbacks=cb, max_iterations=6)
    return loop.run([], 'sys', 'hi'), shown, ran, prov


def test_phantom_tool_call_is_never_shown():
    result, shown, _ran, prov = _run_agent([
        {'text': _PHANTOM_BLOB},
        {'text': u'LOD 300 for structure — here is the matrix.'},
    ])
    check('phantom blob never reaches a chat bubble',
          not any('apply_playbook' in s for s in shown), shown)
    check('phantom blob is not the returned answer',
          'apply_playbook' not in (result.get('text') or ''), result.get('text'))
    check('the real answer still lands',
          u'LOD 300 for structure — here is the matrix.' in shown, shown)
    corrections = [m.get('content', '') for m in prov.transcripts[-1]
                   if m.get('role') == 'user']
    check('model is told apply_playbook is not a tool',
          any('`apply_playbook` is not a tool' in c for c in corrections),
          corrections)


def test_phantom_correction_is_bounded():
    """A model that keeps emitting the blob must still not print it — and
    must not spin: the loop gives up and ends the turn with no text, which
    script.py routes to the legacy JSON-intent path for a real reply."""
    result, shown, _ran, _p = _run_agent([{'text': _PHANTOM_BLOB}] * 6)
    check('blob suppressed on every retry',
          not any('apply_playbook' in s for s in shown), shown)
    check('gives up instead of looping to max_iterations',
          result.get('status') == 'done'
          and result.get('iterations', 99) <= 3, result)
    check('ends with empty text so the legacy fallback engages',
          not (result.get('text') or '').strip(), result.get('text'))


def test_phantom_keeps_the_prose_around_it():
    _result, shown, _ran, _p = _run_agent([
        {'text': u'Applying the LOD standard now.\n' + _PHANTOM_BLOB},
        {'text': u'Done.'},
    ])
    check('prose survives, blob does not',
          u'Applying the LOD standard now.' in shown
          and not any('apply_playbook' in s for s in shown), shown)


_HINT_TOOLS_ANTHROPIC = [{
    'name': 'set_parameter',
    'description': 'Set a parameter value',
    'input_schema': {
        'type': 'object',
        'properties': {
            'element_id': {'type': 'string'},
            'name': {'type': 'string'},
            'value': {'type': 'string'},
            'scope': {'type': 'string', 'enum': ['instance', 'type']},
        },
        'required': ['element_id', 'name', 'value'],
    },
}]
_HINT_TOOLS_OPENAI = [{
    'type': 'function',
    'function': {
        'name': 'set_parameter',
        'description': 'Set a parameter value',
        'parameters': _HINT_TOOLS_ANTHROPIC[0]['input_schema'],
    },
}]


def test_param_hint_reads_both_schema_shapes():
    """The schema hint must work for Claude (input_schema) AND Ollama/OpenAI
    (function.parameters) tool shapes."""
    from Intelligence.agent_loop import _param_hint
    for shape, tools in (('anthropic', _HINT_TOOLS_ANTHROPIC),
                         ('openai', _HINT_TOOLS_OPENAI)):
        h = _param_hint(tools, 'set_parameter')
        check(u'{}: names the tool'.format(shape), 'set_parameter' in h, h)
        check(u'{}: marks required args'.format(shape),
              'element_id (required' in h, h)
        check(u'{}: marks optional args'.format(shape),
              'scope (optional' in h, h)
        check(u'{}: lists the enum'.format(shape), 'instance|type' in h, h)
    check('unknown tool → empty hint',
          _param_hint(_HINT_TOOLS_ANTHROPIC, 'no_such_tool') == u'')


def test_tool_error_carries_the_schema_hint():
    """A failing tool call hands the model the tool's expected schema so a small
    model can self-correct instead of repeating the bad call."""
    from Intelligence.agent_loop import AgentLoop
    calls_seen = {'n': 0}

    def _exec(name, args):
        calls_seen['n'] += 1
        if calls_seen['n'] == 1:
            return {'error': 'missing required argument: element_id'}
        return {'ok': True}

    prov = _ScriptedProvider([
        {'text': u'', 'calls': [{'id': '1', 'name': 'set_parameter',
                                 'args': {'name': 'Comments'}}]},
        {'text': u'Done — parameter set.'},
    ])
    loop = AgentLoop(prov, _exec, _HINT_TOOLS_ANTHROPIC,
                     callbacks={'on_turn_text': lambda t, f: None,
                                'on_text_delta': lambda c: None,
                                'on_tool_start': lambda n, a, i: None,
                                'on_tool_done': lambda n, r, ok, s: None},
                     max_iterations=6)
    loop.run([], 'sys', 'set the comments parameter')

    # The messages the model saw on its SECOND turn carry the tool result.
    fed_back = u' '.join(m.get('content', '') for m in prov.transcripts[-1]
                         if m.get('role') == 'user')
    check('error result carries the expected schema',
          'expected_parameters' in fed_back, fed_back)
    check('schema names the missing required arg',
          'element_id (required' in fed_back, fed_back)


def test_real_tool_written_as_text_runs_without_printing_json():
    """The rescue path already executed these; it also used to print the raw
    JSON into the chat first."""
    _result, shown, ran, _p = _run_agent([
        {'text': '{"name": "ai_element_filter", "parameters": {"category": "Walls"}}'},
        {'text': u'Found 12 walls.'},
    ])
    check('rescued call still executes', ran == ['ai_element_filter'], ran)
    check('its JSON is not printed as an answer',
          not any('ai_element_filter' in s for s in shown), shown)


def test_json_data_answer_is_not_mistaken_for_a_tool_call():
    """A JSON object that merely has a "name" key is DATA — suppressing it
    would silently eat a legitimate answer."""
    payload = '{"name": "Level 1", "elevation": 0, "id": 1234}'
    result, shown, _ran, _p = _run_agent([{'text': u'The level is: ' + payload}])
    check('data payload is shown verbatim', payload in (result.get('text') or ''),
          result.get('text'))
    check('data payload reaches the bubble', any(payload in s for s in shown), shown)


def test_fenced_json_example_is_not_suppressed():
    """```json fences mean the model is SHOWING json on purpose."""
    fenced = u'Example call:\n```json\n' + _PHANTOM_BLOB + u'\n```'
    result, _shown, _ran, _p = _run_agent([{'text': fenced}])
    check('fenced example survives', 'apply_playbook' in (result.get('text') or ''),
          result.get('text'))


def test_plain_reply_is_untouched():
    result, shown, ran, _p = _run_agent([{'text': u'Hello, how can I help?'}])
    check('prose reply unchanged', shown == [u'Hello, how can I help?'], shown)
    check('no tool ran', ran == [], ran)
    check('one iteration only', result.get('iterations') == 1, result)


def test_saved_blob_is_pruned_on_load():
    """Transcripts written before the fix still carry the blob, and
    _restore_history replays them into the model's context — teaching it that
    answering with a fake tool call is what this assistant does."""
    from Intelligence.agent_loop import is_bare_tool_call_text as stale

    check('bare phantom blob is pruned', stale(_PHANTOM_BLOB))
    check('bare real-tool blob is pruned too',
          stale('{"intent": "ai_element_filter", "params": {"category": "Walls"}}'))
    check('a normal answer is kept', not stale(u'LOD 300 for structure.'))
    check('a JSON data answer is kept',
          not stale('{"name": "Level 1", "elevation": 0}'))
    check('an answer that merely quotes json is kept',
          not stale(u'The call was ' + _PHANTOM_BLOB + u' — ignore it.'))
    check('empty content is kept', not stale(u'') and not stale(None))


def test_skills_contract_says_a_skill_is_not_a_tool():
    """Prompt-side half of the same fix — both the native agent path and the
    legacy JSON-intent path inject build_skills_block()."""
    from Intelligence.skills_engine import build_skills_block
    block = build_skills_block(['lod-standard'])
    check('skill block was built', bool(block))
    check('contract forbids apply_playbook',
          'apply_playbook' in block and 'A SKILL IS NOT A TOOL' in block,
          block[:300])


# ─────────────────────────────────────────────────────────────────────────────
# Skills: bare "/skill" invocation
#
# A user typed "/annotation-standard" with nothing after it. The skill is a
# CONVENTION document that declares create_dimension/tag_* so a real "dim mặt
# bằng này" has tools to hand — but "declares tools" was read as "is an
# executable job", so the bare invocation got the "act NOW, do NOT ask about
# scope" boilerplate and the model started dimensioning every floor plan in
# the project. 17 of 25 shipped skills sit in that same class.
# ─────────────────────────────────────────────────────────────────────────────

def _engine():
    from Intelligence.skills_engine import SkillsEngine
    eng = SkillsEngine()
    eng.scan()
    return eng


def test_convention_skill_is_not_treated_as_an_executable_job():
    eng = _engine()
    check('annotation-standard is flagged as model-modifying',
          eng.modifies_model('annotation-standard'))
    check('...and is NOT a tool-less reference either',
          not eng.is_reference_skill('annotation-standard'))


def test_read_only_skills_may_still_act_immediately():
    """The fix must not turn every playbook into a question. A skill that can
    only read has nothing to confirm — /qa-checklist still scans on sight."""
    eng = _engine()
    for sid in ('qa-checklist', 'lod-standard', 'shared-coordinates'):
        check('{} may run immediately'.format(sid),
              not eng.modifies_model(sid))


def test_every_writing_skill_is_flagged():
    """Mechanical sweep: any skill declaring a tool that edits the model or
    writes a file must plan before acting."""
    eng = _engine()
    from Intelligence.tool_schema import is_model_modifying
    wrong = []
    for meta in eng.all_skills():
        sid = meta['id']
        expect = any(is_model_modifying(t) for t in eng.tools_for(sid))
        if eng.modifies_model(sid) != expect:
            wrong.append(sid)
    check('modifies_model agrees with the tool registry on all 25 skills',
          not wrong, wrong)


def test_slash_boilerplate_has_three_distinct_modes():
    """Source-level lock on the caller. The three wordings must stay distinct:
    collapsing modifying skills back onto the read-only text is exactly the
    regression this whole section exists for."""
    import io as _io
    _t3_path = os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py')
    if not os.path.exists(_t3_path):
        _t3_path = os.path.join(
            TAB, 'Support.panel',
            'T3LabAssistant.pushbutton', 'script.py')
    with _io.open(_t3_path, encoding='utf-8') as f:
        src = f.read()
    check('reference mode still exists', 'is_reference_skill(_sid)' in src)
    check('modifying mode is consulted', 'modifies_model(_sid)' in src)
    check('modifying mode forbids acting this turn',
          'create, modify, delete, rename, tag, ' in src
          and 'wait for my confirmation' in src)
    check('read-only mode still scans immediately',
          'only reads the model' in src)


def test_a_noun_cannot_activate_the_build_playbook():
    """image-to-model builds an entire building and its body demands a tool
    call every turn "cho tới khi dựng xong". It used to trigger on the nouns
    `phong ngu` / `can ho` / `phong khach`, so an ordinary counting question
    activated it; `dung lai` folds to the same ASCII as "dùng lại" (= reuse),
    which did the same for "dùng lại family cũ"."""
    eng = _engine()
    for msg in (u'có bao nhiêu phòng ngủ trong model?',
                u'diện tích phòng khách là bao nhiêu',
                u'căn hộ A có mấy phòng',
                u'dùng lại family cũ được không'):
        hits = [sid for sid, _ in eng.match_scored(msg)]
        check('build playbook stays out of: {}'.format(msg),
              'image-to-model' not in hits, hits)


def test_build_requests_still_reach_the_build_playbook():
    eng = _engine()
    for msg in (u'dựng model từ ảnh này', u'dựng lại model theo bản vẽ',
                u'phân tích bản vẽ rồi dựng nhà'):
        hits = [sid for sid, _ in eng.match_scored(msg)]
        check('build playbook fires on: {}'.format(msg),
              'image-to-model' in hits, hits)


def test_catalog_questions_reach_the_deterministic_answer():
    """"liệt kê các tool" / "chức năng của assistant" asked for the catalog and
    missed the detector, so the LLM answered from memory instead — the exact
    path that turns a truncated tool list into a confident claim of full
    coverage. Same answer, so the detector has to catch the phrasing too."""
    from Intelligence import nlu_engine as N
    for q in (u'liệt kê các tool', u'danh sách tool',
              u'chức năng của assistant', u'giới thiệu các tính năng',
              u'extension này có gì', u'bạn làm được gì',
              u'có tool nào check chính tả không'):
        exp = N._expand(N._norm(q))
        check('catalog question detected: {}'.format(q),
              N.is_capability_question(exp), exp)


def test_a_data_query_is_not_a_catalog_question():
    """The widened patterns must not swallow ordinary list requests — "liệt kê"
    and "danh sách" are the assistant's most common data verbs."""
    from Intelligence import nlu_engine as N
    for q in (u'liệt kê các sheet', u'danh sách phòng tầng 2',
              u'liệt kê view chưa lên sheet', u'list all doors',
              u'cho tôi danh sách level', u'model này có gì lạ'):
        exp = N._expand(N._norm(q))
        check('stays a data query: {}'.format(q),
              not N.is_capability_question(exp), exp)


def test_operate_element_verbs_reach_the_action_specialist():
    """"pin toàn bộ cột" scored no action keyword and landed on `general`,
    arriving without the ACTION role prompt — the one that spells out that a
    pin is not a colour. That prompt is the guard against the original
    pin→override_color substitution, so the verb has to route there."""
    from Intelligence.agents.dispatcher import AgentDispatcher
    disp = AgentDispatcher()
    for q in (u'pin toàn bộ cột trong view', u'ghim hết grid lại',
              u'bỏ ghim các cột'):
        dec = disp.classify(q, allow_llm=False) or {}
        check('action specialist for: {}'.format(q),
              dec.get('specialist') == 'revit_action', dec.get('specialist'))


def test_a_3d_request_is_never_answered_with_the_plan_tool():
    """"create 3d view" used to come back suggesting **Create Plan Views**.

    The fuzzy score rewards overlap and was blind to conflict: {create, view}
    matched, while `3d` and `plan` cancelled out as merely "unmatched" rather
    than "these are two different drawings". Being handed the wrong tool is
    worse than being told nothing matches.
    """
    from Intelligence.nlu_engine import resolve_tool
    match, cands = resolve_tool(u'create 3d view')
    titles = [(match or {}).get('title')] + [c.get('title') for c in cands]
    check('no plan tool is offered for a 3D request',
          not any(t and 'Plan' in t for t in titles), titles)


def test_the_plan_tool_is_still_reachable():
    """The guard must only remove WRONG candidates."""
    from Intelligence.nlu_engine import resolve_tool
    match, _ = resolve_tool(u'create plan view')
    check('"create plan view" still resolves',
          (match or {}).get('title') == 'Create Plan Views', match)
    for q in (u'open batchout', u'workset manager', u'dwg management',
              u'load family', u'mcp control'):
        m, _c = resolve_tool(q)
        check('"{}" still resolves'.format(q), m is not None,
              [c.get('title') for c in _c])


def test_tagging_and_export_phrasings_reach_the_right_skill():
    eng = _engine()
    tag = [sid for sid, _ in eng.match_scored(u'tag tất cả tường trong view')]
    check('"tag tất cả tường" reaches annotation-standard',
          'annotation-standard' in tag, tag)
    exp = [sid for sid, _ in eng.match_scored(u'xuất pdf bộ bản vẽ')]
    check('"xuất pdf bộ bản vẽ" prefers export-standard',
          exp[:1] == ['export-standard'], exp)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    ('launcher', [
        test_launcher_runs_main_guard,
        test_launcher_reports_failure_honestly,
        test_launcher_treats_exitscript_as_success,
        test_api_context_runner_normalises_every_launcher_shape,
        test_raise_failure_only_discards_its_own_task,
        test_tools_are_launched_through_the_api_context,
    ]),
    ('catalog', [
        test_every_registered_tool_exists_on_disk,
        test_every_ribbon_button_is_openable,
        test_every_tool_resolves_by_every_name_it_shows,
        test_propertyline_is_reachable,
        test_registry_prunes_vanished_buttons,
        test_skipped_buttons_are_pruned_from_an_old_registry,
        test_skip_list_only_hides_real_buttons,
        test_builtin_tools_are_installed,
        test_renamed_tools_resolve_to_real_tools,
        test_no_dead_intent_advertised_anywhere,
    ]),
    ('language', [
        test_agent_prompt_language_follows_setting,
        test_specialist_prompt_forwards_language,
        test_knowledge_prompt_is_single_language,
        test_assistant_cards_are_bilingual,
        test_reply_language_setting_roundtrip,
    ]),
    ('prompt cache', [
        test_system_prompt_is_static_for_prompt_cache,
    ]),
    ('routing', [
        test_continuation_needs_a_trailing_question,
        test_continuation_accepts_real_answers,
        test_continuation_rejects_new_commands,
        test_continuation_rejects_closing_questions,
        test_small_talk_never_rides_the_previous_turn,
        test_english_small_talk_is_instant,
        test_which_model_asks_about_the_llm_not_the_rvt,
        test_continuation_guards,
        test_learned_pattern_defers_to_nlu,
        test_dispatcher_precedence_conflicts,
        test_build_requests_reach_the_modeling_specialist,
        test_local_catalog_can_build_geometry,
        test_every_routed_request_can_reach_its_tool,
        test_every_tool_is_consciously_placed,
        test_comments_parameter_is_not_a_pdf_markup,
        test_announcing_the_work_is_not_doing_it,
        test_tool_result_survives_non_utf8_bytes,
        test_spellcheck_fix_detection,
    ]),
    ('semantics', [
        test_abbrev_expansion_respects_word_boundaries,
        test_capability_frames_cover_productive_forms,
        test_capability_scope_is_not_a_predicate,
        test_capability_predicate_still_finds_tools,
        test_vietnamese_capability_verbs_reach_the_english_catalog,
        test_ubiquitous_word_cannot_name_a_tool,
        test_capability_ignores_implementation_jargon,
        test_pronoun_vs_determiner,
        test_history_referent_is_a_real_tool_mention,
        test_capability_question_beats_anaphora,
    ]),
    ('agent-loop', [
        test_phantom_tool_call_is_never_shown,
        test_phantom_correction_is_bounded,
        test_phantom_keeps_the_prose_around_it,
        test_param_hint_reads_both_schema_shapes,
        test_tool_error_carries_the_schema_hint,
        test_real_tool_written_as_text_runs_without_printing_json,
        test_json_data_answer_is_not_mistaken_for_a_tool_call,
        test_fenced_json_example_is_not_suppressed,
        test_plain_reply_is_untouched,
        test_saved_blob_is_pruned_on_load,
        test_skills_contract_says_a_skill_is_not_a_tool,
    ]),
    ('skills / bare slash', [
        test_convention_skill_is_not_treated_as_an_executable_job,
        test_read_only_skills_may_still_act_immediately,
        test_every_writing_skill_is_flagged,
        test_slash_boilerplate_has_three_distinct_modes,
        test_a_noun_cannot_activate_the_build_playbook,
        test_build_requests_still_reach_the_build_playbook,
        test_catalog_questions_reach_the_deterministic_answer,
        test_a_data_query_is_not_a_catalog_question,
        test_operate_element_verbs_reach_the_action_specialist,
        test_a_3d_request_is_never_answered_with_the_plan_tool,
        test_the_plan_tool_is_still_reachable,
        test_tagging_and_export_phrasings_reach_the_right_skill,
    ]),
]


def main():
    for group, tests in TESTS:
        print('\n{}'.format(group))
        for t in tests:
            try:
                t()
            except Exception as ex:
                FAILURES.append(t.__name__)
                print('  ERROR {}  {}'.format(t.__name__, ex))

    print('')
    if FAILURES:
        print('{} FAILED: {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
