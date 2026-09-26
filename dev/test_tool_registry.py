# -*- coding: utf-8 -*-
"""
CPython 3 drift locks for the MCP tool registry in lib/core/server.py.

Run:  python3 dev/test_tool_registry.py
Exit code 0 = all pass. Plain asserts, same conventions as
dev/test_assistant_routing.py. No Revit required.

WHY THIS FILE EXISTS
--------------------
A user asked the assistant to "pin all walls in view". No pin tool existed, and
`operate_element` — the tool that should have carried it — had been left out of
the specialist tool subsets, so the model picked the nearest thing it could see
(`revit_override_color`) and painted 54 walls red while reporting success.

Silently doing a different thing is the worst failure this assistant has. Every
test here mechanises one step of the "registering a tool" checklist that, when
skipped, produces exactly that class of bug:

    registry ↔ dispatch     a tool advertised but not implemented returns None,
                            and the model reports success on it
    enum coverage           a free-text operation field means an unrecognised
                            value silently falls through to some default branch
    enum ↔ dispatch         an advertised operation with no branch is a promise
                            the server cannot keep
    _WRITE_TOOLS            a transactional tool missing here throws
                            "outside of API context" on every single call
    tool subsets            a name missing from a specialist subset is invisible
                            to that specialist — the original pin bug
    prompt rules            a multi-op tool the prompt never names is a tool the
                            model reaches for by guesswork

core.server can't be imported headlessly (it pulls the Revit API), but the
registry is a pure dict literal, so ast.literal_eval reads the whole thing.
"""
from __future__ import unicode_literals

import ast
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
EXT = os.path.join(REPO, 'T3Lab.extension')
LIB = os.path.join(EXT, 'lib')
SERVER = os.path.join(LIB, 'core', 'server.py')
SPECIALISTS = os.path.join(LIB, 'Intelligence', 'agents', 'specialists.py')
TOOL_SCHEMA = os.path.join(LIB, 'Intelligence', 'tool_schema.py')
NLU = os.path.join(LIB, 'Intelligence', 'nlu_engine.py')
AGENT_LOOP = os.path.join(LIB, 'Intelligence', 'agent_loop.py')
ASSISTANT = os.path.join(TAB, 'Support.panel',
                         'T3LabAssistant.pushbutton', 'script.py')

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# Source extraction
# ─────────────────────────────────────────────────────────────────────────────

_SERVER_SRC = _read(SERVER)


def _registry():
    """The `self._tools = {...}` literal inside _register_default_tools()."""
    tree = ast.parse(_SERVER_SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != '_register_default_tools':
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not isinstance(stmt.value, ast.Dict):
                continue
            return ast.literal_eval(stmt.value)
    raise AssertionError('could not locate the _tools registry literal')


REGISTRY = _registry()


def _frozenset_literal(src, name):
    """Read a module- or class-level `NAME = frozenset([...])` / `= {...}`."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == name):
                continue
            value = node.value
            # frozenset([...]) / set([...])
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in ('frozenset', 'set')):
                if not value.args:
                    return set()
                return set(ast.literal_eval(value.args[0]))
            if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                return set(ast.literal_eval(value))
            # READ_TOOLS | frozenset([...]) — union expressions
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                return _union_names(src, value)
    raise AssertionError('could not find {}'.format(name))


def _union_names(src, node):
    """Flatten `A | frozenset([...]) | {...}` into a set of strings."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _union_names(src, node.left) | _union_names(src, node.right)
    if isinstance(node, ast.Name):
        return _frozenset_literal(src, node.id)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ('frozenset', 'set')):
        return set(ast.literal_eval(node.args[0])) if node.args else set()
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return set(ast.literal_eval(node))
    raise AssertionError('unsupported union expression')


def _class_attr_set(src, class_name, attr):
    """Read a class-level `attr = frozenset([...])` (e.g. _WRITE_TOOLS)."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == attr:
                    value = stmt.value
                    if (isinstance(value, ast.Call)
                            and isinstance(value.func, ast.Name)
                            and value.func.id in ('frozenset', 'set')):
                        return set(ast.literal_eval(value.args[0]))
                    return set(ast.literal_eval(value))
    raise AssertionError('could not find {}.{}'.format(class_name, attr))


def _dispatch_branches():
    """{tool_name: source_text} for each `elif tool_name == 'x':` branch."""
    lines = _SERVER_SRC.splitlines()
    starts = []
    attr_map = {}
    for m in re.finditer(r"^\s*(_[A-Z0-9_]+)\s*=\s*'([a-z0-9_]+)'", _SERVER_SRC, re.M):
        attr_map[m.group(1)] = m.group(2)

    pat = re.compile(r"^\s*(?:el)?if\s+tool_name\s*==\s*(?:'([a-z0-9_]+)'|self\.(_[A-Z0-9_]+))\s*:")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            name = m.group(1) or attr_map.get(m.group(2))
            if name:
                starts.append((i, len(m.group(0)) - len(m.group(0).lstrip()), name))
    out = {}
    for idx, (line_no, indent, name) in enumerate(starts):
        next_start = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        end = next_start
        for j in range(line_no + 1, next_start):
            l = lines[j]
            if l.strip() and (len(l) - len(l.lstrip())) <= indent:
                end = j
                break
        out[name] = '\n'.join(lines[line_no:end])
    return out


BRANCHES = _dispatch_branches()

# Names that are deliberately not public registry entries: pseudo-tools the
# assistant calls directly through srv._execute_tool, never advertised to a
# model. Adding a name here is a decision, not a workaround — an unlisted
# branch means a tool that no provider can ever reach.
INTERNAL_TOOLS = {
    '__begin_action_group',      # withdrawn request TransactionGroup — the
    '__end_action_group',        # dispatch keeps inert no-op branches only
    'collect_spellcheck_text',   # feeds the /english-spellcheck pipeline
}

# Fields whose value the dispatch switches on. A free-text value here is how
# "asked for a section, silently got a 3D view" happens.
SWITCH_FIELDS = {'operation', 'mode', 'action', 'view_type', 'element_type',
                 'data_type', 'format', 'scope', 'match', 'detail_level',
                 'discipline', 'link_kind'}


# ─────────────────────────────────────────────────────────────────────────────
# Registry ↔ dispatch
# ─────────────────────────────────────────────────────────────────────────────

def test_every_registry_tool_has_a_dispatch_branch():
    """Advertised but unimplemented: the branch chain falls through, the tool
    returns 'Tool not implemented' or None, and the model narrates a success
    that never happened."""
    missing = sorted(n for n in REGISTRY if n not in BRANCHES)
    check('every registered tool has a dispatch branch', not missing, missing)


def test_every_dispatch_branch_is_registered():
    """The converse — dead code no provider can reach."""
    extra = sorted(n for n in BRANCHES
                   if n not in REGISTRY and n not in INTERNAL_TOOLS)
    check('every dispatch branch is registered', not extra, extra)


def test_no_duplicate_tool_names():
    """A tool whose 'name' disagrees with its registry key is unroutable: the
    schema goes out under one name and the dispatch keys off the other."""
    mismatched = sorted(k for k, v in REGISTRY.items()
                        if v.get('name') != k)
    check('registry key matches the schema name', not mismatched, mismatched)


def test_every_schema_description_is_non_empty():
    """tool_schema.to_anthropic_tools falls back to the bare tool NAME when the
    description is empty — the model then has to guess from the name alone."""
    blank = sorted(k for k, v in REGISTRY.items()
                   if not (v.get('description') or '').strip())
    check('every tool has a description', not blank, blank)


# ─────────────────────────────────────────────────────────────────────────────
# Enum coverage
# ─────────────────────────────────────────────────────────────────────────────

def _switch_props():
    """Yield (tool, field, schema_prop) for every dispatch-switch field."""
    for tool, spec in sorted(REGISTRY.items()):
        props = (spec.get('inputSchema') or {}).get('properties') or {}
        for field, prop in sorted(props.items()):
            if field in SWITCH_FIELDS and prop.get('type') == 'string':
                yield tool, field, prop


def test_every_switch_field_has_an_enum():
    """Without an enum the model can send anything, and the dispatch's else
    branch quietly does something else. This is the whole Gap-C bug class."""
    bare = ['{}.{}'.format(t, f) for t, f, p in _switch_props()
            if not p.get('enum')]
    check('every dispatch-switch field declares an enum', not bare, bare)


_IMPORT_IN_BRANCH = re.compile(
    r'from\s+((?:core|Snippets|Services|Intelligence)\.[A-Za-z_.]+)\s+import')


def _branch_and_delegates(tool):
    """Branch source, plus the source of any repo module it delegates to.

    Several branches are thin wrappers — `create_view` hands the whole view-type
    table to core.advanced_view_manager.create_single_view so the ribbon tool
    and the MCP tool can't drift apart. Searching only the branch text would
    punish exactly the sharing this refactor was for.
    """
    branch = BRANCHES.get(tool, '')
    text = [branch]
    for module in set(_IMPORT_IN_BRANCH.findall(branch)):
        path = os.path.join(LIB, *module.split('.')) + '.py'
        if os.path.isfile(path):
            text.append(_read(path))
    return _strip_comments('\n'.join(text))


def _strip_comments(src):
    """Drop whole-line comments — a value named in prose is not an implementation.

    (Learned the hard way: a comment explaining that view types are ".NET names
    like FloorPlan" made the checker believe FloorPlan had a branch.)
    """
    return '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))


def test_every_enum_value_appears_in_its_dispatch():
    """PARTIALLY implemented enums are the danger: `edit_elements` advertising
    mirror/group/ungroup/array while only three have branches means the model
    calls array and the server does nothing useful.

    A field where NO value appears in the source is a different animal — a data
    filter matched against runtime values (revit_list_views.view_type compares
    to str(view.ViewType)), which has no per-value branch by design. Requiring
    branches there would be wrong, so the rule is "all or nothing": once a field
    branches on ANY of its values, it must branch on all of them.
    """
    orphans = []
    for tool, field, prop in _switch_props():
        src = _branch_and_delegates(tool)
        values = prop.get('enum') or []
        found = [v for v in values
                 if "'{}'".format(v) in src or '"{}"'.format(v) in src]
        if not found:
            continue                      # runtime-matched filter, not a switch
        missing = [v for v in values if v not in found]
        orphans.extend('{}.{}={}'.format(tool, field, v) for v in missing)
    check('every advertised enum value is implemented',
          not orphans, orphans)


# ─────────────────────────────────────────────────────────────────────────────
# Threading / transactions
# ─────────────────────────────────────────────────────────────────────────────

def test_transactional_tools_are_write_tools():
    """A tool that opens a Transaction but isn't in _WRITE_TOOLS never reaches
    Revit's main thread: it throws "Starting a transaction ... outside of API
    context is not allowed" on every call."""
    write_tools = _class_attr_set(_SERVER_SRC, 'T3LabAIServer', '_WRITE_TOOLS')
    missing = []
    for name, src in sorted(BRANCHES.items()):
        if name in INTERNAL_TOOLS or name not in REGISTRY:
            continue
        if 'Transaction(doc' in src and name not in write_tools:
            missing.append(name)
    check('every transactional tool is in _WRITE_TOOLS', not missing, missing)


def test_write_tools_are_real_tools():
    write_tools = _class_attr_set(_SERVER_SRC, 'T3LabAIServer', '_WRITE_TOOLS')
    unknown = sorted(n for n in write_tools
                     if n not in REGISTRY and n not in INTERNAL_TOOLS)
    check('_WRITE_TOOLS names only real tools', not unknown, unknown)


def test_docless_tools_are_real_tools():
    docless = _class_attr_set(_SERVER_SRC, 'T3LabAIServer', '_DOCLESS_TOOLS')
    unknown = sorted(n for n in docless if n not in REGISTRY)
    check('_DOCLESS_TOOLS names only real tools', not unknown, unknown)


def test_every_argument_read_is_declared():
    """A dispatch that reads `arguments.get('x')` while the schema never
    declares `x` makes a working feature unreachable — the model can only find
    it by guessing (create_text_note's sheet_number sat like that).

    Since _reject_unknown_arguments() now refuses undeclared keys outright,
    this drift is no longer merely undiscoverable: it is a dead branch.
    """
    redirects = _read(SERVER)
    start = redirects.find('_ARGUMENT_REDIRECTS = {')
    block = redirects[start:redirects.find('}', start)] if start != -1 else ''
    exempt = set(re.findall(r"'([a-z_0-9]+)'", block))
    undeclared = {}
    for name, schema in sorted(REGISTRY.items()):
        props = set((schema.get('inputSchema') or {}).get('properties') or {})
        props |= exempt if name in exempt else set()
        src = BRANCHES.get(name, '')
        read = set(re.findall(
            r"arguments\.get\(\s*['\"]([a-zA-Z_0-9]+)['\"]", src))
        read |= set(re.findall(
            r"arguments\[\s*['\"]([a-zA-Z_0-9]+)['\"]", src))
        extra = sorted(read - props)
        if extra:
            undeclared[name] = extra
    check('every argument a dispatch reads is declared in its schema',
          not undeclared, undeclared)


def test_unknown_arguments_are_rejected_not_dropped():
    """Source lock on the guard itself. Without it a tool silently ignores
    what it cannot do and still answers {'success': True} — which is how
    "3D views of rooms with 100mm margin" became one isometric of the site."""
    src = _SERVER_SRC
    check('the guard exists', 'def _reject_unknown_arguments' in src)
    check('the guard runs before dispatch',
          src.find('rejected = self._reject_unknown_arguments') <
          src.find('if self._external_event:'))
    check('the guard names what was refused', "'accepted_arguments'" in src)


def test_read_only_tools_are_real_tools():
    names = _frozenset_literal(_read(TOOL_SCHEMA), 'READ_ONLY_TOOL_NAMES')
    unknown = sorted(n for n in names if n not in REGISTRY)
    check('READ_ONLY_TOOL_NAMES names only real tools', not unknown, unknown)


def test_read_only_tools_open_no_transaction():
    """A tool that opens a Transaction changes the document, full stop.

    is_model_modifying() is the inverse of this set, and it decides whether a
    bare "/skill" invocation may act immediately. Misfiling one transactional
    tool as read-only puts a whole playbook back on the path that made
    "/annotation-standard" dimension every floor plan in the project.
    """
    names = _frozenset_literal(_read(TOOL_SCHEMA), 'READ_ONLY_TOOL_NAMES')
    liars = sorted(n for n in names
                   if 'Transaction(doc' in BRANCHES.get(n, ''))
    check('no READ_ONLY tool opens a Transaction', not liars, liars)


# ─────────────────────────────────────────────────────────────────────────────
# Version safety
# ─────────────────────────────────────────────────────────────────────────────

def test_bic_map_is_built_defensively():
    """_BIC_NAMES holds MEMBER NAME STRINGS resolved through getattr, never
    `B.OST_x` attribute access. BuiltInCategory members come and go between
    Revit releases (OST_Toposolid is 2024+); one missing member in a literal
    raises while BUILDING the map, which takes down all nine category-accepting
    tools at once on Revit 2023."""
    tree = ast.parse(_SERVER_SRC)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == '_BIC_NAMES'
                        for t in node.targets)):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Attribute) and sub.attr.startswith('OST_'):
                bad.append(sub.attr)
    check('_BIC_NAMES uses guarded member-name strings', not bad, bad)


def test_bic_map_resolves_through_getattr():
    src = _SERVER_SRC
    start = src.find('def _bic_map(self)')
    body = src[start:start + 1400]
    check('_bic_map resolves members with getattr',
          'getattr(B, member' in body, 'guard missing from _bic_map')


# ─────────────────────────────────────────────────────────────────────────────
# Tool subsets — the original pin bug
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_subsets_reference_real_tools():
    """A typo here is INVISIBLE at runtime: tool_schema._registry_tools falls
    back to the full list when a subset filters to empty, and a specialist just
    silently loses the tool — which is how "pin the walls" became "paint them
    red"."""
    spec_src = _read(SPECIALISTS)
    schema_src = _read(TOOL_SCHEMA)
    bad = {}
    subsets = [(SPECIALISTS, spec_src, n) for n in
               ('READ_TOOLS', 'MODIFY_TOOLS', 'EXPORT_TOOLS', 'ACTION_TOOLS',
                'MULTIDOC_TOOLS', 'MODELING_TOOLS', 'QA_TOOLS')]
    subsets.append((TOOL_SCHEMA, schema_src, 'ESSENTIAL_TOOL_NAMES'))
    for path, src, name in subsets:
        try:
            names = _frozenset_literal(src, name)
        except AssertionError as ex:
            bad[name] = str(ex)
            continue
        unknown = sorted(n for n in names if n not in REGISTRY)
        if unknown:
            bad[name] = unknown
    check('every tool subset names only real tools', not bad, bad)


def test_multiop_tools_are_visible_to_the_action_specialist():
    """Any tool with an `operation` enum is a verb the user will ask for
    directly. If the ACTION specialist can't see it, it substitutes."""
    spec_src = _read(SPECIALISTS)
    action = _frozenset_literal(spec_src, 'ACTION_TOOLS')
    multiop = set()
    for tool, field, prop in _switch_props():
        if field == 'operation':
            multiop.add(tool)
    missing = sorted(multiop - action)
    check('action specialist sees every multi-operation tool',
          not missing, missing)


def test_cap_map_references_real_tools():
    """_CAP_MAP drives the user-facing "what can you do" answer."""
    nlu_src = _read(NLU)
    start = nlu_src.find('_CAP_MAP')
    end = nlu_src.find('\n_CAP_HIDE', start)
    if end == -1:
        end = start + 20000
    block = nlu_src[start:end]
    named = set(re.findall(r'u"([a-z0-9_]+)"', block))
    # Only judge names that look like tool names we actually know about.
    unknown = sorted(n for n in named
                     if n.count('_') >= 1 and n not in REGISTRY
                     and not n.startswith('open_'))
    check('_CAP_MAP names only real tools', not unknown, unknown)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt rules
# ─────────────────────────────────────────────────────────────────────────────

def test_prompt_names_every_multiop_tool():
    """The step whose omission caused the pin incident: a tool exists, is
    registered, is in the subsets — and the prompt never tells the model which
    verbs map to it, so it reaches for whatever it recognises."""
    prompt = _read(AGENT_LOOP)
    multiop = sorted({t for t, f, p in _switch_props() if f == 'operation'})
    missing = [t for t in multiop if '`{}`'.format(t) not in prompt]
    check('agent prompt names every multi-operation tool', not missing, missing)


def test_destructive_declaration_is_real():
    """_DESTRUCTIVE_TOOLS / _DESTRUCTIVE_OPS gate the confirmation dialog. A
    typo there means an irreversible action runs with no prompt."""
    bad = {}
    tools = _class_attr_set(_SERVER_SRC, 'T3LabAIServer', '_DESTRUCTIVE_TOOLS')
    unknown = sorted(n for n in tools if n not in REGISTRY)
    if unknown:
        bad['_DESTRUCTIVE_TOOLS'] = unknown

    tree = ast.parse(_SERVER_SRC)
    ops_map = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == '_DESTRUCTIVE_OPS'
                        for t in node.targets)):
            ops_map = {}
            for k, v in zip(node.value.keys, node.value.values):
                key = ast.literal_eval(k)
                if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                        and v.func.id in ('frozenset', 'set')):
                    ops_map[key] = set(ast.literal_eval(v.args[0]))
                else:
                    ops_map[key] = set(ast.literal_eval(v))
    if ops_map is None:
        bad['_DESTRUCTIVE_OPS'] = 'not found'
    else:
        for tool, ops in sorted(ops_map.items()):
            if tool not in REGISTRY:
                bad.setdefault('unknown tools', []).append(tool)
                continue
            props = (REGISTRY[tool].get('inputSchema') or {}).get('properties') or {}
            declared = set((props.get('operation') or {}).get('enum') or [])
            stray = sorted(ops - declared)
            if stray:
                bad.setdefault('unknown ops', []).append(
                    '{}: {}'.format(tool, stray))
    check('destructive declaration names real tools and operations', not bad, bad)


def test_no_request_wide_transaction_group():
    """The assistant must NOT open a TransactionGroup around a request.

    A TransactionGroup cannot outlive the ExternalEvent.Execute that starts it
    — Revit force-discards the phase and the leftover managed handle, once
    Assimilate()d, kills the process on the next request. This is what crashed
    Revit on multi-step requests (2026-08-11 journal 0139). The feature never
    worked; the guard is here so it does not come back.
    """
    src = _read(ASSISTANT)
    revived = [m for m in ('__begin_action_group', '_group_exempt')
               if '"{}"'.format(m) in src or "'{}'".format(m) in src]
    check('assistant opens no request-wide TransactionGroup', not revived,
          revived)

    srv = _read(SERVER)
    check('server does not Start() a request TransactionGroup',
          'tg.Start()' not in srv and 'self._action_group = tg' not in srv,
          'TransactionGroup revived in server.py')


TESTS = [
    ('registry ↔ dispatch', [
        test_every_registry_tool_has_a_dispatch_branch,
        test_every_dispatch_branch_is_registered,
        test_no_duplicate_tool_names,
        test_every_schema_description_is_non_empty,
    ]),
    ('enum coverage', [
        test_every_switch_field_has_an_enum,
        test_every_enum_value_appears_in_its_dispatch,
    ]),
    ('transactions', [
        test_transactional_tools_are_write_tools,
        test_write_tools_are_real_tools,
        test_docless_tools_are_real_tools,
        test_read_only_tools_are_real_tools,
        test_read_only_tools_open_no_transaction,
    ]),
    ('argument contract', [
        test_every_argument_read_is_declared,
        test_unknown_arguments_are_rejected_not_dropped,
    ]),
    ('version safety', [
        test_bic_map_is_built_defensively,
        test_bic_map_resolves_through_getattr,
    ]),
    ('tool subsets', [
        test_tool_subsets_reference_real_tools,
        test_multiop_tools_are_visible_to_the_action_specialist,
        test_cap_map_references_real_tools,
    ]),
    ('prompt rules', [
        test_prompt_names_every_multiop_tool,
        test_destructive_declaration_is_real,
        test_no_request_wide_transaction_group,
    ]),
]


def main():
    print('registry: {} tools, {} dispatch branches'.format(
        len(REGISTRY), len(BRANCHES)))
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
