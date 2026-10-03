# -*- coding: utf-8 -*-
"""Where the extension's own folders are.

The ribbon tab folder name is the tab title Revit shows, so it changes
(``T3Lab.tab`` -> ``T3Lab_Dev.tab``, 2026-09-26), and since 2026-10-03 the
ribbon has more than one tab. A hardcoded tab name broke BG Theme settings,
ImageToDrafting's potrace, FamiGen prompts, ManaLoca sessions and the
Assistant's tool list the moment the folder was renamed; a hardcoded panel
path breaks the same way the moment a panel moves to another tab.

So a bundle is found by its folder NAME (``find_bundle('FamiGen.pushbutton')``),
never by the panel path it happens to sit in. Bundle names are unique across
the extension (dev/test_extension_paths.py checks it).
"""

import io
import os
import re

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

# Folder suffixes of the ribbon bundles find_bundle() indexes.
BUNDLE_SUFFIXES = ('.panel', '.stack', '.pulldown', '.splitbutton', '.splitpushbutton',
                   '.pushbutton', '.smartbutton', '.urlbutton', '.linkbutton',
                   '.invokebutton', '.panelbutton', '.content')

_INDEX = {}   # extension dir -> {bundle folder name: path}


def _layout(extension_dir):
    """Tab names listed under ``layout:`` in the extension's bundle.yaml.

    pyRevit builds only the tabs listed there, in that order. A line scanner,
    not a YAML parse: this module also loads under IronPython 2.7.
    """
    path = os.path.join(extension_dir, 'bundle.yaml')
    names, inside = [], False
    try:
        with io.open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                if re.match(r'layout\s*:', line):
                    inside = True
                    continue
                if not inside or not line.strip() or line.lstrip().startswith('#'):
                    continue
                item = re.match(r'\s+-\s*(.+?)\s*$', line)
                if not item:
                    break                      # next top-level key
                names.append(item.group(1).strip('"\''))
    except (IOError, OSError):
        pass
    return names


def tab_dirs(extension_dir=None):
    """Every ``*.tab`` folder of the extension, in ribbon order.

    Tabs listed in the extension's ``layout:`` come first, in that order; any
    other tab follows by name, ``T3Lab*`` first.
    """
    root = extension_dir or EXTENSION_DIR
    try:
        tabs = sorted(n for n in os.listdir(root)
                      if n.endswith('.tab') and os.path.isdir(os.path.join(root, n)))
    except OSError:
        tabs = []
    order = dict((name + '.tab', i) for i, name in enumerate(_layout(root)))
    tabs.sort(key=lambda n: (order.get(n, len(order)), not n.startswith('T3Lab'), n))
    return [os.path.join(root, n) for n in tabs]


def tab_dir(extension_dir=None):
    """The first ribbon tab folder. Kept for old callers: a path into a panel
    goes through ``find_bundle`` / ``bundle_path``, never through this."""
    root = extension_dir or EXTENSION_DIR
    tabs = tab_dirs(root)
    return tabs[0] if tabs else os.path.join(root, 'T3Lab.tab')


def _build_index(root):
    index = {}
    for tab in tab_dirs(root):
        for dirpath, dirs, _files in os.walk(tab):
            dirs.sort()
            for d in dirs:
                if d.endswith(BUNDLE_SUFFIXES):
                    index.setdefault(d, os.path.join(dirpath, d))
    _INDEX[root] = index
    return index


def find_bundle(name, extension_dir=None):
    """Path of the ribbon bundle folder called ``name`` (e.g.
    ``'FamiGen.pushbutton'``, ``'Support.panel'``) in any tab, or None."""
    root = extension_dir or EXTENSION_DIR
    path = _INDEX.get(root, {}).get(name)
    if path and os.path.isdir(path):
        return path
    # Not indexed yet, or the folder moved since: rebuild once.
    return _build_index(root).get(name)


def bundle_path(name, *parts):
    """``os.path.join(find_bundle(name), *parts)``.

    Always returns a path, so callers keep their usual ``os.path.isfile``
    checks: when the bundle is missing it points under the first tab, where
    nothing exists.
    """
    folder = find_bundle(name) or os.path.join(tab_dir(), name)
    return os.path.join(folder, *parts)
