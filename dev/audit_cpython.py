#! python3
# -*- coding: utf-8 -*-
"""audit_cpython.py — cong tac vien kiem tra migration IronPython -> CPython.

Moi check o day tuong ung MOT loi that da lam chet tool trong Revit, khong phai
suy doan. Chay:  python3 dev/audit_cpython.py [--quiet]

  C1  P0  CLR generic nhan Python builtin        -> TypeError: type(s) expected
  C2  P0  revit.doc / revit.uidoc o module scope -> AttributeError luc import
  C3  P0  pyrevit.forms.<api> chua duoc shim     -> PyRevitCPythonNotSupported
  C4  P0  __namespace__ trong pushbutton script  -> Duplicate type name (click 2)
  C5  P1  DB.<Enum> qua pyrevit.DB               -> attribute missing tren stub
  C6  P1  print() ngoai except                   -> ScriptIO has no attribute write
  C8  P0  ElementId.IntegerValue khong guard     -> AttributeError tren Revit 2024+
  C9  P0  reload() tran                          -> NameError tren CPython 3
  C10 P0  `with DB.Transaction(...)` tran        -> TypeError: does not support the
          context manager protocol (pythonnet 3 khong bien IDisposable thanh
          context manager). Boc bang Snippets._compat.disposing(...).
  C11 P0  `.ItemsSource = <list Python>`        -> TypeError: 'list' value cannot
          be converted to System.Collections.IEnumerable (WallCutProfile,
          2026-10-02). Dung GUI.WPF_Base.set_items_source(ctrl, items) hoac
          to_items_source(items). Chi bat RHS ro rang la list: literal [...],
          list comprehension, sorted()/list()/tuple(), hoac ten bien ma lan
          gan gan nhat trong cung ham la mot trong cac dang do.
"""

import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, 'T3Lab.extension')

# pyrevit.forms API da duoc _cpython_bootstrap shim -> an toan
SHIMMED = {'alert', 'pick_file', 'save_file', 'pick_folder', 'WPFWindow',
           'SelectFromList', 'CommandSwitchWindow', 'ask_for_string',
           'ProgressBar', 'WarningBar', 'MessageBox', 'toast',
           'Reactive', 'IRONPY', 'Visibility', '_cpy',
           'to_items_source', 'set_items_source'}

GENERIC = re.compile(
    r'\b(List|Array|Dictionary|ObservableCollection|IList|HashSet|IEnumerable|'
    r'Queue|Stack|KeyValuePair)\[\s*(object|str|int|float|bool|bytes|dict|list|tuple)\s*[,\]]')
MODULE_DOC = re.compile(
    r'^(doc|uidoc|app)\s*=\s*(revit\.(doc|uidoc|app)'
    r'|__revit__\.(ActiveUIDocument|Application)'
    r'|__revit__\.ActiveUIDocument\.Document)\s*$')
FORMS_API = re.compile(r'\bforms\.([A-Za-z_][A-Za-z0-9_]*)')
DB_ENUM = re.compile(r'\bDB\.(BuiltInCategory|BuiltInParameter|UnitType|SpecTypeId)\b')
PRINT_CALL = re.compile(r'^\s*print\s*\(')
# `with DB.Transaction(...)` - IronPython only. `revit.Transaction` (pyRevit's
# Python wrapper) is a real context manager and stays allowed.
DOTNET_WITH = re.compile(
    r'^\s*with\s+(?!revit\.)((?:\w+\.)*(?:Transaction|TransactionGroup|'
    r'SubTransaction|FilteredElementCollector))\s*\(')
# C11: `<ctrl>.ItemsSource = <rhs>` (khong phai `==`).
ITEMS_SOURCE = re.compile(r'^\s*[\w.\[\]]+\.ItemsSource\s*=(?!=)\s*(.*)$')
# RHS chac chan la sequence Python thuan.
PY_SEQ_EXPR = re.compile(r'^(\[|(sorted|list|tuple)\s*\()')
PY_NAME = re.compile(r'^((?:self\.)?[A-Za-z_]\w*)$')


def iter_py():
    for base, dirs, files in os.walk(EXT):
        dirs[:] = [d for d in dirs if d not in ('_archive', '__pycache__')]
        for fn in files:
            if fn.endswith('.py'):
                yield os.path.join(base, fn)


def rel(path):
    return os.path.relpath(path, ROOT).replace('\\', '/')


def audit():
    findings = []

    def eid_scope_is_safe(lines, idx):
        """True neu ham bao quanh dong idx (0-based) co nhanh .Value du phong.

        Helper doi phien ban (`eid.Value if hasattr(...) else eid.IntegerValue`,
        hoac try `.Value` / except `.IntegerValue`) la hop le - chi bao dong
        goi `.IntegerValue` tran, vi Revit 2024+ da bo han member nay.
        """
        start, indent = 0, None
        for j in range(idx, -1, -1):
            ln = lines[j]
            st = ln.lstrip()
            if st.startswith('def ') or st.startswith('async def '):
                start, indent = j, len(ln) - len(st)
                break
        else:
            start, indent = 0, -1
        end = len(lines)
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
                end = j
                break
        body = chr(10).join(lines[start:end])
        return '.Value' in body or "'Value'" in body or '"Value"' in body

    def last_assign_is_py_seq(lines, idx, name):
        """True neu lan gan gan nhat cua `name` truoc dong idx (0-based), trong
        cung ham, la list/sorted()/list()/tuple() Python. Dung o `def` bao quanh."""
        assign = re.compile(r'^\s*' + re.escape(name) + r'\s*=(?!=)\s*(.*)$')
        for j in range(idx - 1, -1, -1):
            st = lines[j].lstrip()
            if st.startswith('def ') or st.startswith('async def '):
                return False
            m = assign.match(lines[j])
            if m:
                return bool(PY_SEQ_EXPR.match(m.group(1).strip()))
        return False

    def add(sev, code, path, line, msg):
        findings.append((sev, code, rel(path), line, msg))

    for path in iter_py():
        try:
            src = io.open(path, encoding='utf-8').read()
        except Exception:
            continue
        lines = src.split('\n')
        is_pushbutton = path.replace('\\', '/').endswith('.pushbutton/script.py')

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue

            m = GENERIC.search(line)
            if m:
                add('P0', 'C1', path, i,
                    '%s[%s] - dung System.Object / CLR type' % (m.group(1), m.group(2)))

            m_oc = re.search(r'\bObservableCollection\[\s*([A-Za-z0-9_]+)\s*\]', line.split('#')[0])
            if m_oc and m_oc.group(1) not in ('Object', 'System'):
                add('P0', 'C1', path, i,
                    'ObservableCollection[%s] - phai dung ObservableCollection[Object]() tren CPython 3' % m_oc.group(1))

            if MODULE_DOC.match(line):
                add('P0', 'C2', path, i,
                    '%s o module scope - boc try/except, dung Snippets._host.resolve_doc()'
                    % stripped)

            for name in FORMS_API.findall(line):
                if name not in SHIMMED:
                    add('P0', 'C3', path, i,
                        'forms.%s chua shim - dung GUI.T3Dialog hoac them vao '
                        '_cpython_bootstrap.install_forms_shim()' % name)

            if is_pushbutton and '__namespace__' in line and '=' in line:
                # An giai neu dung uuid/exec_id (unique moi click) hoac da boc "if sys.version_info[0] < 3:"
                guarded = (
                    any(('version_info[0] < 3' in prev or 'uuid' in prev or 'exec_id' in prev)
                        for prev in lines[max(0, i - 15):i])
                    or 'uuid' in line
                    or 'exec_id' in line
                )
                if not guarded:
                    add('P0', 'C4', path, i,
                        '__namespace__ trong script.py chay lai moi click -> '
                        'Duplicate type name; dung uuid hoac dua class vao lib/')

            if 'script.get_output()' in line and '=' in line:
                # An giai neu da di qua _cpython_bootstrap.safe_output()
                if not any('safe_output' in prev for prev in lines[max(0, i - 5):i]):
                    add('P0', 'C7', path, i,
                        'script.get_output() truc tiep -> ScriptOutput.GetDefault '
                        'khong ton tai; dung _cpython_bootstrap.safe_output()')

            m = DB_ENUM.search(line)
            if m:
                add('P1', 'C5', path, i,
                    'DB.%s - import truc tiep tu Autodesk.Revit.DB' % m.group(1))

            if '.IntegerValue' in line.split('#')[0]:
                if not eid_scope_is_safe(lines, i - 1):
                    add('P0', 'C8', path, i,
                        '.IntegerValue tran - Revit 2024+ da bo; dung '
                        'Snippets._compat.eid_value()')

            if PRINT_CALL.match(line):
                add('P1', 'C6', path, i, 'print() - bo hoac dung logger')

            m = DOTNET_WITH.match(line)
            if m:
                add('P0', 'C10', path, i,
                    'with %s(...) tran - pythonnet 3 nem TypeError; dung '
                    '`with disposing(%s(...)) as t:` (Snippets._compat)'
                    % (m.group(1), m.group(1)))

            m = ITEMS_SOURCE.match(line.split('#')[0])
            if m:
                rhs = m.group(1).strip()
                bad = bool(PY_SEQ_EXPR.match(rhs))
                if not bad:
                    m_name = PY_NAME.match(rhs)
                    if m_name and rhs != 'None':
                        bad = last_assign_is_py_seq(lines, i - 1, m_name.group(1))
                if bad:
                    add('P0', 'C11', path, i,
                        'ItemsSource = list Python - pythonnet 3 khong doi sang '
                        'IEnumerable; dung set_items_source(ctrl, items) (GUI.WPF_Base)')

        # C9: bare reload() call (CPython 3 requires importlib.reload)
        try:
            tree = ast.parse(src)
            has_reload_def = any(
                (isinstance(n, ast.ImportFrom) and any(alias.name == 'reload' for alias in n.names))
                or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'reload' for t in n.targets))
                or (isinstance(n, ast.FunctionDef) and n.name == 'reload')
                for n in ast.walk(tree)
            )
            if not has_reload_def:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'reload':
                        add('P0', 'C9', path, node.lineno,
                            'reload() tran - tren CPython 3 reload khong phai builtin; dung importlib.reload')
        except Exception:
            pass

    return findings


def main():
    quiet = '--quiet' in sys.argv
    findings = audit()
    p0 = [f for f in findings if f[0] == 'P0']
    p1 = [f for f in findings if f[0] == 'P1']

    if not quiet:
        for code in sorted(set(f[1] for f in findings)):
            rows = [f for f in findings if f[1] == code]
            if not rows:
                continue
            print('\n%s  (%d)' % (code, len(rows)))
            shown = {}
            for sev, _c, path, line, msg in rows:
                shown.setdefault(path, []).append((line, msg))
            for path in sorted(shown):
                hits = shown[path]
                print('  %s' % path)
                for line, msg in hits[:4]:
                    print('      %-5d %s' % (line, msg))
                if len(hits) > 4:
                    print('      ... +%d dong nua' % (len(hits) - 4))

    print('\nCPYTHON AUDIT: %d P0 · %d P1  (%d file quet)'
          % (len(p0), len(p1), len(list(iter_py()))))
    return 1 if p0 else 0


if __name__ == '__main__':
    sys.exit(main())
