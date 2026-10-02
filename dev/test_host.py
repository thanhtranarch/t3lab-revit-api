# -*- coding: utf-8 -*-
"""
Tests for Snippets._host (resolve_doc, resolve_uidoc, get_revit_version).

Run:  python dev/test_host.py
Exit 0 = all tests pass.
"""
from __future__ import unicode_literals

import os
import sys
import types
from unittest.mock import MagicMock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'T3Lab.extension', 'lib'))
sys.path.insert(0, os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI'))

from Snippets._host import (
    DocResult,
    get_revit_version,
    resolve_doc,
    resolve_uidoc,
    FALLBACK_VERSION,
)

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def test_doc_result():
    print('[test_doc_result: DocResult unpacking and attribute proxying]')
    class DummyDoc(object):
        Application = "MockApplication"
        ActiveView = "MockActiveView"
        Title = "MockProject.rvt"

    doc_obj = DummyDoc()
    res = DocResult(doc_obj, None)

    # 1. Unpacking pattern
    d, err = res
    check('unpacking yields original doc', d is doc_obj)
    check('unpacking yields None error', err is None)

    # 2. Single variable assignment & attribute access
    check('truthiness is True when doc is present', bool(res) is True)
    check('hasattr works for Application', hasattr(res, 'Application') is True)
    check('attribute proxy for Application', res.Application == "MockApplication")
    check('attribute proxy for ActiveView', res.ActiveView == "MockActiveView")
    check('res.doc returns doc_obj', res.doc is doc_obj)
    check('res.error returns None', res.error is None)
    check('isinstance tuple', isinstance(res, tuple) is True)
    check('tuple indexing res[0]', res[0] is doc_obj)
    check('tuple indexing res[1]', res[1] is None)

    # 3. None doc behavior
    res_none = DocResult(None, "No model open")
    d_none, err_none = res_none
    check('unpacking none doc yields None', d_none is None)
    check('unpacking none doc yields error message', err_none == "No model open")
    check('truthiness is False when doc is None', bool(res_none) is False)
    check('res_none.doc is None', res_none.doc is None)
    check('res_none.error is msg', res_none.error == "No model open")


def test_resolve_uidoc_candidate():
    print('[test_resolve_uidoc: candidate argument and probes]')
    class DummyUIDoc(object):
        Document = "MockDoc"

    uidoc_obj = DummyUIDoc()
    # Candidate passed
    resolved = resolve_uidoc(uidoc_obj)
    check('resolve_uidoc returns valid candidate', resolved is uidoc_obj)

    # None candidate when no Revit context
    resolved_none = resolve_uidoc(None)
    check('resolve_uidoc returns None when no context', resolved_none is None)


def test_resolve_doc_candidate():
    print('[test_resolve_doc: candidate argument and error message]')
    class DummyDoc(object):
        Application = "MockApp"

    doc_obj = DummyDoc()
    # Candidate doc passed
    res = resolve_doc(doc_obj)
    check('resolve_doc returns DocResult when candidate provided', res.doc is doc_obj)
    check('resolve_doc has no error when candidate provided', res.error is None)

    # Candidate tuple passed (defensive)
    res_from_tuple = resolve_doc((doc_obj, None))
    check('resolve_doc unpacks tuple candidate', res_from_tuple.doc is doc_obj)

    # None candidate when no Revit context
    res_none = resolve_doc(None)
    check('resolve_doc returns None doc when no context', res_none.doc is None)
    check('resolve_doc returns error text when no context', isinstance(res_none.error, str))


def test_get_revit_version():
    print('[test_get_revit_version: candidate doc and defaults]')
    class DummyDoc(object):
        class Application(object):
            VersionNumber = "2026"

    check('get_revit_version with doc candidate', get_revit_version(DummyDoc()) == 2026)
    check('get_revit_version with int default', get_revit_version(2025) == 2025)
    check('get_revit_version with fallback default', get_revit_version() == FALLBACK_VERSION)


def test_module_imports():
    print('[test_module_imports: GUI dialogs importing resolve_uidoc]')
    mock_modules = [
        'clr', 'Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB',
        'Autodesk.Revit.DB.Architecture', 'Autodesk.Revit.UI',
        'Autodesk.Revit.UI.Selection', 'Autodesk.Revit.Exceptions', 'System', 'System.Collections',
        'System.Collections.Generic', 'System.Collections.ObjectModel',
        'System.ComponentModel', 'System.Diagnostics', 'System.Drawing', 'System.Drawing.Imaging',
        'System.IO', 'System.Net', 'System.Text',
        'System.Windows', 'System.Windows.Controls', 'System.Windows.Forms',
        'System.Windows.Media', 'System.Windows.Media.Imaging',
        'System.Windows.Markup', 'System.Windows.Threading',
        'System.Windows.Input', 'System.Data',
        'pyrevit', 'pyrevit.forms', 'pyrevit.script', 'pyrevit.revit',
    ]
    for mod in mock_modules:
        if mod not in sys.modules:
            m = MagicMock()
            m.__path__ = []
            sys.modules[mod] = m
            if '.' in mod:
                parent, child = mod.rsplit('.', 1)
                if parent in sys.modules:
                    setattr(sys.modules[parent], child, m)

    sys.modules['Autodesk.Revit.DB'].IFailuresPreprocessor = object
    sys.modules['Autodesk.Revit.DB'].BuiltInCategory = MagicMock()
    sys.modules['Autodesk.Revit.UI.Selection'].ISelectionFilter = object

    try:
        import ManaAnnoDialog
        check('import ManaAnnoDialog succeeded', True)
    except Exception as e:
        check('import ManaAnnoDialog succeeded', False, str(e))

    try:
        import ManaSelectDialog
        check('import ManaSelectDialog succeeded', True)
    except Exception as e:
        check('import ManaSelectDialog succeeded', False, str(e))

    try:
        import ManaFamiDialog
        check('import ManaFamiDialog succeeded', True)
    except Exception as e:
        check('import ManaFamiDialog succeeded', False, str(e))

    try:
        import PropertyLineDialog
        check('import PropertyLineDialog succeeded', True)
    except Exception as e:
        check('import PropertyLineDialog succeeded', False, str(e))

    try:
        from Selection import select_similar_family
        check('import select_similar_family succeeded', True)
    except Exception as e:
        check('import select_similar_family succeeded', False, str(e))

    try:
        from Selection import super_select
        check('import super_select succeeded', True)
    except Exception as e:
        check('import super_select succeeded', False, str(e))


def test_stale_module_recovery():
    print('[test_stale_module_recovery: recovering from stale sys.modules without resolve_uidoc]')
    import Snippets._host
    # Simulate a stale cached module that was loaded before resolve_uidoc existed
    real_resolve = getattr(Snippets._host, 'resolve_uidoc', None)
    if hasattr(Snippets._host, 'resolve_uidoc'):
        delattr(Snippets._host, 'resolve_uidoc')

    for k in ('ManaAnnoDialog', 'GUI.ManaAnnoDialog'):
        if k in sys.modules:
            del sys.modules[k]

    try:
        import ManaAnnoDialog
        check('ManaAnnoDialog imports despite stale Snippets._host', hasattr(ManaAnnoDialog, 'resolve_uidoc'))
    except Exception as e:
        check('ManaAnnoDialog imports despite stale Snippets._host', False, str(e))
    finally:
        # Restore real resolve_uidoc if needed
        if real_resolve is not None and not hasattr(Snippets._host, 'resolve_uidoc'):
            Snippets._host.resolve_uidoc = real_resolve


if __name__ == '__main__':
    test_doc_result()
    test_resolve_uidoc_candidate()
    test_resolve_doc_candidate()
    test_get_revit_version()
    test_module_imports()
    test_stale_module_recovery()

    if FAILURES:
        print('\nFAILED {} test(s): {}'.format(len(FAILURES), FAILURES))
        sys.exit(1)
    else:
        print('\nAll Snippets._host tests PASSED cleanly.')
        sys.exit(0)
