# -*- coding: utf-8 -*-
"""Where the extension's own folders are.

The ribbon tab folder name is the tab title Revit shows, so it changes
(``T3Lab.tab`` -> ``T3Lab_Dev.tab``, 2026-09-26). Every path into it goes
through ``tab_dir()`` instead of a hardcoded name; a hardcoded name broke
BG Theme settings, ImageToDrafting's potrace, FamiGen prompts, ManaLoca
sessions and the Assistant's tool list the moment the folder was renamed.
"""

import os

EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')


def tab_dir(extension_dir=None):
    """The extension's ``*.tab`` folder (a ``T3Lab*`` one first if several)."""
    root = extension_dir or EXTENSION_DIR
    try:
        tabs = sorted(n for n in os.listdir(root)
                      if n.endswith('.tab') and os.path.isdir(os.path.join(root, n)))
    except OSError:
        tabs = []
    tabs.sort(key=lambda n: not n.startswith('T3Lab'))
    return os.path.join(root, tabs[0] if tabs else 'T3Lab.tab')


def tab_path(*parts):
    """``os.path.join(tab_dir(), *parts)``."""
    return os.path.join(tab_dir(), *parts)
