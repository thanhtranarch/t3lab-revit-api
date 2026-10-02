# -*- coding: utf-8 -*-
"""
CPython 3 tests for the assistant's Claude-Projects-style workspace backend.

Run:  python3 dev/test_assistant_workspace.py
Exit code 0 = all pass. Plain asserts, mirroring dev/test_assistant_llm.py.

Covers, headless (no Revit, no WPF):
  * personal instructions  — config.settings get/set_user_instructions,
                             build_user_instructions_block, the 2000-char cap
  * project description    — ProjectStore create/update/get/list, old files
  * project delete cleanup — delete_summary counts; delete_project removes the
                             project's memory + tagged archived sessions and
                             nothing else
  * project knowledge files — list_project_files / remove_project_file
  * archived sessions      — config.chat_sessions: sessions_dir, list_sessions
                             (scope, doc filter, search, order), rename_session,
                             delete_session, tag_session, path guard

%APPDATA% is pointed at a fresh temp dir per test, so nothing real is touched.
"""
from __future__ import unicode_literals

import io
import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ['APPDATA'] = tempfile.mkdtemp(prefix='t3lab_ws_')

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def _sandbox():
    """Fresh APPDATA + fresh singletons. Returns (settings, project store)."""
    os.environ['APPDATA'] = tempfile.mkdtemp(prefix='t3lab_ws_')
    import config.settings as CS
    import config.project_store as PS
    from config import chat_sessions as CHS
    from Intelligence import assistant_memory as M
    CS.T3LabAISettings._instance = None
    PS.ProjectStore._instance = None
    with CHS._CACHE_LOCK:
        CHS._CACHE.clear()
    mem = os.path.join(os.environ['APPDATA'], 'mem.json')
    M._memory_file = lambda: mem
    return CS.T3LabAISettings(), PS.ProjectStore()


def _settings_file():
    return os.path.join(os.environ['APPDATA'], 'T3LabAI', 'settings.json')


def _write_json(path, data):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False))


def _read_json(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)


# ─── personal instructions ────────────────────────────────────────────────────

def test_user_instructions_roundtrip():
    print('[settings: personal instructions]')
    s, _ps = _sandbox()
    import config.settings as CS
    check('default is empty', s.get_user_instructions() == '')
    check('no block when empty', s.build_user_instructions_block() == '')

    text = 'Trả lời ngắn gọn.\r\nUse millimetres.\n\n\n\nAlways list element IDs.  '
    check('set returns True', s.set_user_instructions(text) is True)
    got = s.get_user_instructions()
    check('CRLF normalised, outer space trimmed, blank runs folded',
          got == 'Trả lời ngắn gọn.\nUse millimetres.\n\nAlways list element IDs.',
          repr(got))
    on_disk = _read_json(_settings_file())
    check('stored under profile.instructions',
          on_disk.get('profile', {}).get('instructions') == got, on_disk.get('profile'))

    CS.T3LabAISettings._instance = None
    check('survives a new session (reload from disk)',
          CS.T3LabAISettings().get_user_instructions() == got)

    block = CS.T3LabAISettings().build_user_instructions_block()
    check('prompt block has a header and the text',
          block.startswith('## Personal instructions') and got in block, block)

    s = CS.T3LabAISettings()
    s.set_user_instructions('x' * (CS.MAX_USER_INSTRUCTIONS_CHARS + 500))
    check('capped at MAX_USER_INSTRUCTIONS_CHARS',
          len(s.get_user_instructions()) == CS.MAX_USER_INSTRUCTIONS_CHARS)
    check('cap is 2000', CS.MAX_USER_INSTRUCTIONS_CHARS == 2000)

    s.set_user_instructions('   ')
    check('whitespace-only clears', s.get_user_instructions() == '')
    s.set_user_instructions(None)
    check('None clears', s.get_user_instructions() == '')

    # other settings are untouched by the setter (merge-on-write)
    s.set_api_key('Claude', 'sk-ant-keep')
    s.set_user_instructions('hello there')
    check('setter keeps unrelated keys',
          _read_json(_settings_file())['api_keys'].get('Claude') == 'sk-ant-keep')


def test_user_instructions_old_and_odd_files():
    print('[settings: personal instructions on old / hand-edited files]')
    _sandbox()
    import config.settings as CS
    _write_json(_settings_file(), {'username': 'T', 'agents': {}})
    CS.T3LabAISettings._instance = None
    s = CS.T3LabAISettings()
    check('file without a profile block reads as empty',
          s.get_user_instructions() == '')
    check('and can be written', s.set_user_instructions('Use mm.')
          and s.get_user_instructions() == 'Use mm.')
    check('old keys preserved', _read_json(_settings_file()).get('username') == 'T')

    _write_json(_settings_file(), {'profile': 'not a dict'})
    CS.T3LabAISettings._instance = None
    s = CS.T3LabAISettings()
    check('non-dict profile reads as empty', s.get_user_instructions() == '')
    check('non-dict profile is repaired on write',
          s.set_user_instructions('Fixed') and
          _read_json(_settings_file())['profile'] == {'instructions': 'Fixed'})

    _write_json(_settings_file(), {'profile': {'instructions': 'y' * 5000}})
    CS.T3LabAISettings._instance = None
    check('hand-edited oversize value is capped on read',
          len(CS.T3LabAISettings().get_user_instructions()) == 2000)


def test_reply_language_values():
    print('[settings: reply language values]')
    s, _ps = _sandbox()
    import config.settings as CS
    check('REPLY_LANGUAGES', CS.REPLY_LANGUAGES == ('auto', 'en', 'vi'))
    for lang in CS.REPLY_LANGUAGES:
        s.set_reply_language(lang)
        check('{} persists'.format(lang), s.get_reply_language() == lang)
    check('stored in agents.reply_language',
          _read_json(_settings_file())['agents']['reply_language'] == 'vi')


# ─── project description ──────────────────────────────────────────────────────

def test_project_description():
    print('[project: description]')
    _s, ps = _sandbox()
    import config.project_store as PS
    meta = ps.create_project('Tower A', '  Residential   tower, 32 floors ')
    check('created with a cleaned description',
          meta['description'] == 'Residential tower, 32 floors', meta)
    check('get_project returns it',
          ps.get_project(meta['id'])['description'] == 'Residential tower, 32 floors')
    listed = [p for p in ps.list_projects() if p['id'] == meta['id']][0]
    check('list_projects carries it', listed['description'] ==
          'Residential tower, 32 floors', listed)

    plain = ps.create_project('No Desc')
    check('default description is empty', plain['description'] == '')

    ps.update_project(meta['id'], {'description': 'line one\nline two ' + 'z' * 900})
    d = ps.get_project(meta['id'])['description']
    check('update collapses newlines and caps at MAX_DESCRIPTION_CHARS',
          '\n' not in d and len(d) == PS.MAX_DESCRIPTION_CHARS, len(d))

    # A project.json written before descriptions existed.
    old_pid = 'p_oldproj'
    path = os.path.join(ps.project_dir(old_pid), 'project.json')
    _write_json(path, {'id': old_pid, 'name': 'Legacy', 'created': '2026-01-01T00:00:00',
                       'instructions': 'Old rule', 'knowledge_dirs': []})
    ps.invalidate_meta()
    old = ps.get_project(old_pid)
    check('old project.json loads with description ""',
          old is not None and old['description'] == '' and
          old['instructions'] == 'Old rule', old)
    check('old project listed with description ""',
          [p for p in ps.list_projects() if p['id'] == old_pid][0]['description'] == '')
    check('old file not rewritten by a read', 'description' not in _read_json(path))
    ps.update_project(old_pid, {'description': 'Now described'})
    check('old project can be given one',
          ps.get_project(old_pid)['description'] == 'Now described'
          and _read_json(path)['instructions'] == 'Old rule')


# ─── archived sessions ────────────────────────────────────────────────────────

def _session(folder, doc_key, sid, title, messages, **extra):
    data = {'id': sid, 'doc_key': doc_key, 'title': title,
            'timestamp': '{}-{}-{} {}:{}'.format(sid[:4], sid[4:6], sid[6:8],
                                                 sid[9:11], sid[11:13]),
            'message_count': len(messages),
            'messages': [{'role': r, 'content': c, 'ts': ''} for r, c in messages],
            'summary': ''}
    data.update(extra)
    path = os.path.join(folder, '{}_{}.json'.format(doc_key, sid))
    _write_json(path, data)
    return path


def test_sessions_dir():
    print('[sessions: where new archives go]')
    _s, ps = _sandbox()
    from config import chat_sessions as CHS
    g = CHS.sessions_dir(None)
    check('no project → global chat_history/sessions',
          g.replace('\\', '/').endswith('T3LabAI/chat_history/sessions')
          and os.path.isdir(g), g)
    pid = ps.create_project('S')['id']
    p = CHS.sessions_dir(pid)
    check('active project → projects/<pid>/sessions',
          os.path.normcase(p) == os.path.normcase(
              os.path.join(ps.project_dir(pid), 'sessions')) and os.path.isdir(p), p)
    stale = CHS.sessions_dir('p_deleted1')
    check('stale project id falls back to global', stale == g, stale)
    check('no ghost project folder created',
          not os.path.exists(os.path.join(ps.project_dir('p_deleted1'))))


def test_list_sessions_scopes_and_search():
    print('[sessions: list / filter / search]')
    _s, ps = _sandbox()
    from config import chat_sessions as CHS
    pid = ps.create_project('Tower')['id']
    other = ps.create_project('Villa')['id']
    g = CHS.global_sessions_dir()
    pdir = CHS.sessions_dir(pid)
    odir = CHS.sessions_dir(other)

    old_global = _session(g, 'ModelA', '20260101_090000', 'Old global chat',
                          [('user', 'count the walls'), ('assistant', '120 walls')])
    new_global = _session(g, 'ModelB', '20260301_101500', 'Newer global chat',
                          [('user', 'Kiểm tra tường bao'), ('assistant', 'ok')])
    tagged = _session(g, 'ModelA', '20260201_120000', 'Tagged to Tower',
                      [('user', 'sheet prefix WH-')], pid=pid)
    in_proj = _session(pdir, 'ModelA', '20260401_080000', 'Project chat',
                       [('user', 'renumber sheets'), ('assistant', 'done')])
    _session(odir, 'ModelC', '20260402_080000', 'Villa chat', [('user', 'doors')])
    # a pre-doc_key file: identified by its name prefix only
    legacy = os.path.join(g, 'ModelA_20251201_070000.json')
    _write_json(legacy, {'title': 'Legacy file', 'messages': [{'role': 'user',
                                                                'content': 'hi there'}]})
    with open(os.path.join(g, 'broken_20260101_000000.json'), 'w') as f:
        f.write('{ not json')

    none = CHS.list_sessions()
    paths = [m['path'] for m in none]
    check('"No project" lists untagged global archives only',
          new_global in paths and old_global in paths and legacy in paths
          and tagged not in paths and in_proj not in paths, [m['title'] for m in none])
    check('corrupt file skipped, never raises', len(none) == 3, len(none))
    check('newest first', paths[0] == new_global and paths[-1] == legacy, paths)
    check('pid is None for No project', all(m['pid'] is None for m in none))
    check('legacy file gets defaults',
          [m for m in none if m['path'] == legacy][0]['message_count'] == 1)

    proj = CHS.list_sessions(pid=pid)
    check('project listing = its folder + tagged global files',
          [m['path'] for m in proj] == [in_proj, tagged], [m['title'] for m in proj])
    check('project items carry the pid', all(m['pid'] == pid for m in proj))

    every = CHS.list_sessions(all_projects=True)
    check('all_projects lists everything readable', len(every) == 6, len(every))
    check('all_projects keeps newest-first order',
          [m['title'] for m in every][:2] == ['Villa chat', 'Project chat'],
          [m['title'] for m in every])

    by_doc = CHS.list_sessions(doc_key='ModelA', all_projects=True)
    check('doc_key filter (field or legacy file name)',
          sorted(m['title'] for m in by_doc) ==
          ['Legacy file', 'Old global chat', 'Project chat', 'Tagged to Tower'],
          [m['title'] for m in by_doc])

    hits = CHS.list_sessions(query='walls')
    check('search matches message text', [m['path'] for m in hits] == [old_global])
    hits = CHS.list_sessions(query='tuong bao')
    check('search folds Vietnamese diacritics',
          [m['path'] for m in hits] == [new_global], [m['title'] for m in hits])
    hits = CHS.list_sessions(query='NEWER global')
    check('search matches title, case-insensitive, all terms',
          [m['path'] for m in hits] == [new_global])
    check('all terms must match', CHS.list_sessions(query='walls tuong') == [])
    check('limit', len(CHS.list_sessions(all_projects=True, limit=2)) == 2)

    keys = set(every[0])
    check('item fields', {'path', 'title', 'created', 'doc_key', 'pid',
                          'message_count'} <= keys and '_epoch' not in keys, keys)
    check('count_project_sessions', CHS.count_project_sessions(pid) == 2)


def test_rename_delete_tag_sessions():
    print('[sessions: rename / delete / tag]')
    _s, ps = _sandbox()
    from config import chat_sessions as CHS
    pid = ps.create_project('R')['id']
    g = CHS.global_sessions_dir()
    path = _session(g, 'Doc', '20260505_050505', 'auto title',
                    [('user', 'Nhịp dầm 6m')])
    CHS.list_sessions()                    # warm the cache

    check('rename ok', CHS.rename_session(path, '  Beam   spans ') is True)
    data = _read_json(path)
    check('title stored + flagged as user-renamed',
          data['title'] == 'Beam spans' and data.get('renamed') is True, data)
    check('messages untouched', data['messages'][0]['content'] == 'Nhịp dầm 6m')
    check('listing shows the new title (cache invalidated)',
          CHS.list_sessions()[0]['title'] == 'Beam spans')
    check('search finds the new title', CHS.list_sessions(query='beam spans'))
    check('empty title refused', CHS.rename_session(path, '   ') is False)
    long_t = CHS.rename_session(path, 'T' * 500) and _read_json(path)['title']
    check('title capped', len(long_t) == CHS.MAX_TITLE_CHARS, len(long_t or ''))

    outside = os.path.join(os.environ['APPDATA'], 'T3LabAI', 'settings.json')
    _write_json(outside, {'keep': True})
    check('rename refuses files outside a sessions folder',
          CHS.rename_session(outside, 'x') is False)
    check('delete refuses files outside a sessions folder',
          CHS.delete_session(outside) is False and os.path.exists(outside))
    pj = os.path.join(ps.project_dir(pid), 'project.json')
    check('delete refuses project.json', CHS.delete_session(pj) is False
          and os.path.exists(pj))

    check('tag ok', CHS.tag_session(path, pid))
    check('tagged session moves to the project listing',
          [m['path'] for m in CHS.list_sessions(pid=pid)] == [path]
          and CHS.list_sessions() == [])
    check('untag ok', CHS.tag_session(path, None) and CHS.list_sessions())

    check('delete ok', CHS.delete_session(path) is True)
    check('file gone', not os.path.exists(path))
    check('listing empty', CHS.list_sessions() == [])
    check('second delete is False', CHS.delete_session(path) is False)


# ─── project delete cleanup + knowledge files ─────────────────────────────────

def test_project_files():
    print('[project: knowledge files list / remove]')
    _s, ps = _sandbox()
    pid = ps.create_project('Files')['id']
    files = ps.files_dir(pid)
    for rel, data in (('BEP.docx', b'PK..'), ('notes.md', b'# x'),
                      (os.path.join('sub', 'Register.xlsx'), b'PK'),
                      ('drawing.dwg', b'AC1027'), ('PROJECT_CONTEXT.md', b'gen'),
                      ('~$BEP.docx', b'lock')):
        p = os.path.join(files, rel)
        if not os.path.isdir(os.path.dirname(p)):
            os.makedirs(os.path.dirname(p))
        with open(p, 'wb') as f:
            f.write(data)
    got = ps.list_project_files(pid)
    rels = [f['rel'].replace('\\', '/') for f in got]
    check('lists user files incl. sub-folders, sorted',
          rels == ['BEP.docx', 'drawing.dwg', 'notes.md', 'sub/Register.xlsx'], rels)
    check('generated summary and Office lock files hidden',
          'PROJECT_CONTEXT.md' not in rels and '~$BEP.docx' not in rels)
    flags = dict((f['name'], f['indexable']) for f in got)
    check('indexable flag: docx/xlsx/md yes, dwg no',
          flags == {'BEP.docx': True, 'drawing.dwg': False, 'notes.md': True,
                    'Register.xlsx': True}, flags)
    check('size + modified present', got[0]['size'] == 4 and got[0]['modified'])

    own_before = ps.count_documents(pid)[0]
    check('remove by relative path',
          ps.remove_project_file(pid, os.path.join('sub', 'Register.xlsx')))
    check('remove by absolute path',
          ps.remove_project_file(pid, os.path.join(files, 'drawing.dwg')))
    check('count cache refreshed after removal (sub-folder file)',
          ps.count_documents(pid)[0] == own_before - 2,
          (own_before, ps.count_documents(pid)[0]))
    escape = os.path.join(ps.project_dir(pid), 'project.json')
    check('path outside files/ refused', not ps.remove_project_file(pid, escape)
          and os.path.exists(escape))
    check('.. traversal refused',
          not ps.remove_project_file(pid, os.path.join('..', 'project.json'))
          and os.path.exists(escape))
    check('missing file is False', not ps.remove_project_file(pid, 'nope.pdf'))
    check('no project → empty list', ps.list_project_files(None) == [])


def test_delete_project_cleanup():
    print('[project: delete removes memory + sessions, nothing else]')
    _s, ps = _sandbox()
    from config import chat_sessions as CHS
    from Intelligence import assistant_memory as M
    pid = ps.create_project('Doomed', 'to be deleted')['id']
    keep = ps.create_project('Keeper')['id']
    ps.set_active_project(pid)

    M.add_fact('doomed fact one', scope='project', project_id=pid)
    M.add_fact('doomed fact two', scope='project', project_id=pid)
    M.add_fact('keeper fact', scope='project', project_id=keep)
    M.add_fact('global fact', scope='global')

    with open(os.path.join(ps.files_dir(pid), 'a.pdf'), 'wb') as f:
        f.write(b'%PDF')
    _write_json(ps.history_path(pid, 'ModelA'), {'messages': []})
    g = CHS.global_sessions_dir()
    tagged = _session(g, 'ModelA', '20260601_010101', 'tagged', [('user', 'x')], pid=pid)
    untagged = _session(g, 'ModelA', '20260601_020202', 'untagged', [('user', 'y')])
    kept_tag = _session(g, 'ModelA', '20260601_030303', 'keeper', [('user', 'z')], pid=keep)
    in_proj = _session(CHS.sessions_dir(pid), 'ModelA', '20260601_040404', 'p', [('user', 'w')])
    ps.add_knowledge_dir(pid, os.path.join(os.environ['APPDATA'], 'share'))

    summ = ps.delete_summary(pid)
    check('summary counts', summ == {'name': 'Doomed', 'files': 1, 'chats': 1,
                                     'sessions': 2, 'memory': 2, 'linked_dirs': 1},
          summ)

    check('delete returns True', ps.delete_project(pid) is True)
    check('project folder gone', not os.path.exists(ps.project_dir(pid)))
    check('active project cleared', ps.get_active_project_id() is None)
    check('its memory facts gone (and key dropped)',
          M.count_scope(M.PROJECT_SCOPE, pid) == 0
          and pid not in M._load()['projects'])
    check('other project memory kept', M.count_scope(M.PROJECT_SCOPE, keep) == 1)
    check('global memory kept', M.count_scope(M.GLOBAL_SCOPE) == 1)
    check('tagged global session deleted', not os.path.exists(tagged))
    check('project-folder session deleted', not os.path.exists(in_proj))
    check('untagged + other-project sessions kept',
          os.path.exists(untagged) and os.path.exists(kept_tag))
    check('keeper project intact', ps.get_project(keep) is not None)
    check('summary of a missing project is all zero',
          ps.delete_summary('p_missing')['files'] == 0)
    check('delete without pid is False', ps.delete_project(None) is False)


def main():
    test_user_instructions_roundtrip()
    test_user_instructions_old_and_odd_files()
    test_reply_language_values()
    test_project_description()
    test_sessions_dir()
    test_list_sessions_scopes_and_search()
    test_rename_delete_tag_sessions()
    test_project_files()
    test_delete_project_cleanup()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant workspace tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
