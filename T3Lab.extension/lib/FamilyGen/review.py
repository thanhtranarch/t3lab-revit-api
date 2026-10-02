# -*- coding: utf-8 -*-
"""Open the FamiGen review window for an MCP proposal.

The MCP server stays UI-agnostic: it calls ``open_review`` and gets back
'opened' or 'updated'. Must run on Revit's main thread inside API context (the
server's ExternalEvent handler); the window is modeless, so this returns at
once and never waits for the user.
"""


def open_review(doc, app, schema, proposal_id, note=u''):
    try:
        import _cpython_bootstrap  # noqa: F401  (installs the pyrevit.forms shim under CPython)
    except Exception:
        pass
    from GUI.FamiGenDialog import show_proposal
    return show_proposal(doc, app, schema, proposal_id, note)
