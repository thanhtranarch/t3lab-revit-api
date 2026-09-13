# -*- coding: utf-8 -*-
"""
CPython 3 tests for the Assistant's chat-history persistence and restore.

Run:  python3 dev/test_assistant_history.py
Exit 0 = all pass. Plain asserts, mirroring dev/test_assistant_ui.py.

T3LabAssistant.pushbutton/script.py cannot be imported here (it pulls in clr /
pyrevit / Autodesk.Revit), so this follows the same approach as
dev/test_assistant_ui.py: parse the module with `ast`, then either exec the
pure helpers in a stub namespace and test their behaviour, or assert the
structural rule directly on the source.

What these guard, and why each one exists:
  * save_chat_history must NOT use json.dumps(ensure_ascii=True). That call
    raises on IronPython 2.7 for any non-ASCII, the surrounding `except`
    swallowed it, and chat history was therefore never written at all once a
    conversation contained Vietnamese. Every history file on disk was
    English-only and no error was ever shown.
  * _get_doc_key must resolve the document through Snippets._host.resolve_doc,
    not `revit.doc` — the latter yields None in this modeless pane's engine, so
    the key was always "default" and every model shared one conversation.
  * The restore banner must be emitted with actions=False and before the
    replayed turns, or a restarted pane looks like the assistant's latest
    utterance is a notice about itself, carrying copy/retry/rate buttons.
"""
from __future__ import unicode_literals

import ast
import io
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_dialog_path = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'T3LabAssistantDialog.py')
SCRIPT = _dialog_path if os.path.exists(_dialog_path) else os.path.join(
    REPO, 'T3Lab.extension', 'T3Lab.tab', 'Support.panel',
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


def _func(name, tree=None):
    """Top-level or method FunctionDef node called `name`, or None."""
    for node in ast.walk(tree or TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _src(node):
    return ast.get_source_segment(SRC, node) or ''


def _ensure_ascii_true_lines(node):
    """Line numbers of real `ensure_ascii=True` CALL arguments under `node`.

    Matched on the AST, not on the text: the source deliberately *names* the
    broken pattern in comments explaining why it must not be used, and a
    substring check would flag those and force the explanation to be deleted.
    """
    hits = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        for kw in n.keywords:
            if (kw.arg == 'ensure_ascii'
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True):
                hits.append(n.lineno)
    return hits


def _revit_doc_lines(node):
    """Line numbers of real `revit.doc` attribute reads under `node`."""
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr == 'doc'
            and isinstance(n.value, ast.Name) and n.value.id == 'revit']


def _load_pure(names, extra_globals=None):
    """exec the named top-level helpers in an isolated namespace."""
    ns = {}
    ns.update(extra_globals or {})
    for n in names:
        node = _func(n)
        if node is None:
            raise AssertionError('helper not found: {}'.format(n))
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     '<{}>'.format(n), 'exec'), ns)
    return ns


# ─── Behaviour: _relative_time ──────────────────────────────────────────────────

def test_relative_time():
    print('[history: _relative_time]')
    ns = _load_pure(['_relative_time'])
    rt = ns['_relative_time']

    import datetime as dt
    now = dt.datetime.now()

    def stamp(delta):
        return (now - delta).strftime('%Y-%m-%d %H:%M:%S')

    check('empty for missing stamp', rt(None, True) == '' and rt('', False) == '')
    check('empty for junk', rt('not a timestamp', True) == '',
          rt('not a timestamp', True))
    check('empty for wrong format', rt('2026/08/10', True) == '')

    # Use midday so "today" cannot roll over the date boundary mid-test.
    today_noon = now.replace(hour=12, minute=34, second=0)
    check('today, Vietnamese',
          rt(today_noon.strftime('%Y-%m-%d %H:%M:%S'), True)
          == 'hôm nay lúc 12:34',
          rt(today_noon.strftime('%Y-%m-%d %H:%M:%S'), True))
    check('today, English',
          rt(today_noon.strftime('%Y-%m-%d %H:%M:%S'), False)
          == 'today at 12:34')

    y = (now - dt.timedelta(days=1)).replace(hour=9, minute=5, second=0)
    check('yesterday, Vietnamese',
          rt(y.strftime('%Y-%m-%d %H:%M:%S'), True) == 'hôm qua lúc 09:05',
          rt(y.strftime('%Y-%m-%d %H:%M:%S'), True))

    check('a few days ago', rt(stamp(dt.timedelta(days=3)), True)
          == '3 ngày trước', rt(stamp(dt.timedelta(days=3)), True))
    check('a few days ago, English', rt(stamp(dt.timedelta(days=3)), False)
          == '3 days ago')
    check('older than a week falls back to a date',
          '/' in rt(stamp(dt.timedelta(days=30)), True),
          rt(stamp(dt.timedelta(days=30)), True))


# ─── Behaviour: the restored banner ─────────────────────────────────────────────

def test_restored_banner():
    print('[history: restored banner text]')
    node = _func('_restored_banner')
    check('_restored_banner exists', node is not None)
    if node is None:
        return

    # It is a method; rebind as a plain function with a dummy self.
    ns = _load_pure(['_relative_time'])
    src = _src(node).replace('def _restored_banner(self, saved, viet):',
                             'def _restored_banner(saved, viet):', 1)
    exec(compile(src, '<banner>', 'exec'), ns)
    banner = ns['_restored_banner']

    # Relative to now, so the assertions do not rot as the clock moves.
    import datetime as dt
    recent = (dt.datetime.now() - dt.timedelta(days=2)).strftime(
        '%Y-%m-%d %H:%M:%S')
    old = (dt.datetime.now() - dt.timedelta(days=40)).strftime(
        '%Y-%m-%d %H:%M:%S')

    saved = [{'role': 'user', 'content': 'a', 'ts': recent},
             {'role': 'assistant', 'content': 'b', 'ts': recent}]
    vi = banner(saved, True)
    en = banner(saved, False)

    check('Vietnamese banner is Vietnamese', 'khôi phục' in vi, vi)
    check('English banner is English', 'restored' in en, en)
    check('reports the turn count', '1' in vi.split('\n')[0], vi)
    check('says how old the session is', '2 ngày trước' in vi, vi)
    check('tells the user how to start fresh',
          'hội thoại mới' in vi and 'new-conversation' in en)
    check('singular/plural handled',
          '1 turn,' in en and '1 turns' not in en, en)

    stale = banner([{'role': 'user', 'content': 'a', 'ts': old}], True)
    check('an old session shows a date, not "N days ago"',
          '/' in stale and 'ngày trước' not in stale, stale)

    many = [{'role': 'user', 'content': 'x', 'ts': recent}] * 4
    check('plural for several turns', '4 turns' in banner(many, False),
          banner(many, False))

    # A history with no timestamps must not produce a dangling separator.
    bare = [{'role': 'user', 'content': 'a'}]
    check('no trailing comma without a timestamp',
          ', ' not in banner(bare, False).split('\n')[0],
          banner(bare, False))


# ─── Structure: the regressions that hid for months ─────────────────────────────

def test_history_is_written_through_jsonsafe():
    print('[history: persistence uses jsonsafe, not the broken escaper]')
    node = _func('save_chat_history')
    check('save_chat_history exists', node is not None)
    if node is None:
        return
    check('does not use ensure_ascii=True', not _ensure_ascii_true_lines(node),
          'the IronPython escaper raises on Vietnamese')
    check('serializes via jsonsafe', 'jsonsafe.dumps' in _src(node),
          _src(node)[:200])
    check('still writes utf-8 in one shot',
          "io.open(path, 'w', encoding='utf-8')" in _src(node))

    # Nothing else in the file may reintroduce it.
    everywhere = _ensure_ascii_true_lines(TREE)
    check('no ensure_ascii=True anywhere in the pane', not everywhere,
          'still at line(s) {}'.format(everywhere))


def test_doc_key_resolves_a_real_document():
    print('[history: per-document key, not one shared "default"]')
    node = _func('_get_doc_key')
    check('_get_doc_key exists', node is not None)
    if node is None:
        return
    check('uses the resolve_doc fallback chain', 'resolve_doc' in _src(node))
    stale = _revit_doc_lines(node)
    check('does not read revit.doc directly', not stale,
          'revit.doc is None in this engine; still read at {}'.format(stale))

    check('a legacy-key constant exists', 'LEGACY_DOC_KEY' in SRC)
    read = _func('_history_path_for_read')
    check('reads fall back to the legacy file', read is not None
          and 'LEGACY_DOC_KEY' in _src(read))
    for fn in ('load_chat_history', 'load_chat_summary'):
        n = _func(fn)
        check('{} goes through the fallback'.format(fn),
              n is not None and '_history_path_for_read' in _src(n))


def test_banner_is_a_notice_not_a_reply():
    print('[history: banner has no action row and precedes the replay]')
    node = _func('_restore_history')
    check('_restore_history exists', node is not None)
    if node is None:
        return
    body = _src(node)

    check('banner is emitted without actions', 'actions=False' in body, body)
    check('session language is re-learned from the restored turns',
          '_note_user_language' in body,
          'otherwise the first reply after a restart is in the wrong language')

    i_banner = body.find('_restored_banner')
    i_replay = body.find('_append_user_message')
    check('banner comes before the replayed turns',
          i_banner != -1 and i_replay != -1 and i_banner < i_replay,
          'banner at {}, replay at {}'.format(i_banner, i_replay))


def main():
    test_relative_time()
    test_restored_banner()
    test_history_is_written_through_jsonsafe()
    test_doc_key_resolves_a_real_document()
    test_banner_is_a_notice_not_a_reply()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant-history tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
