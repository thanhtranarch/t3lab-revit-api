# -*- coding: utf-8 -*-
"""csv.writer needs a TEXT file under Python 3.

`open(path, 'wb')` + `csv.writer` is the Python 2 idiom; under the CPython 3
engine it raises "a bytes-like object is required" on the first row (Tile
Layout's Export CSV, 2026-10-02). This guard scans every module that uses the
csv module and fails on a binary-mode open in the same function.

Run: python3 dev/test_csv_text_mode.py
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')


def _binary_csv_opens(source, path):
    tree = ast.parse(source, filename=path)
    hits = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.dump(fn)
        if 'csv' not in body or 'writer' not in body:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'open'
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and 'b' in str(node.args[1].value)):
                hits.append('%s:%d %s()' % (os.path.relpath(path, REPO), node.lineno, fn.name))
    return hits


def main():
    hits = []
    for root, _dirs, files in os.walk(EXT):
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(root, name)
            with open(path, encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            if 'csv' not in source:
                continue
            try:
                hits.extend(_binary_csv_opens(source, path))
            except SyntaxError:
                continue
    if hits:
        print('FAILED: csv.writer on a binary-mode file:\n  ' + '\n  '.join(hits))
        return 1
    print('csv text-mode guard: clean')
    return 0


if __name__ == '__main__':
    sys.exit(main())
