# -*- coding: utf-8 -*-
"""
Shared helpers for the T3Lab Assistant surfaces.

The chat window (T3Lab.tab/.../T3LabAssistant.pushbutton/script.py) and the
LLMs Setting dialog (GUI/LLMSettingDialog.py) had grown independent copies of
the same few things: the provider brand-colour table, an open-folder helper,
and the wording used to describe a project's scope. The copies had already
drifted — most consequentially, the chat window's folder opener lacked the
.NET 8 shell fallback, so "open folder" buttons died silently under Revit 2025+
while the identical button in the settings dialog worked.

Pure Python + guarded .NET imports, so it stays importable under CPython 3 for
the headless tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import os

try:
    from pyrevit import script as _script
    _logger = _script.get_logger()
except Exception:                                    # CPython test runs
    class _NullLogger(object):
        def debug(self, *a, **kw):
            pass

        def error(self, *a, **kw):
            pass
    _logger = _NullLogger()


# ─── Provider brand colours ───────────────────────────────────────────────────
# One table. The chat window's badge, the settings dialog's provider dot and the
# model popup all render from this, so a provider can never show one colour in
# one window and a different colour in the other.
PROVIDER_COLORS = {
    "claude":    (217, 119,  87),   # Anthropic orange
    "openai":    ( 16, 163, 127),   # OpenAI green
    "deepseek":  ( 37,  99, 235),   # DeepSeek blue
    "ollama":    ( 59, 130, 246),   # Ollama blue
    "lmstudio":  (124,  58, 237),   # LM Studio purple
}
PROVIDER_GRAY = (161, 161, 170)     # #A1A1AA — no provider / offline


# ─── Open a folder / file in the shell ────────────────────────────────────────

def open_in_explorer(path, create=True):
    """Open a folder in Explorer. Returns True on success.

    NEVER use a bare Process.Start(folder): Revit 2025+ runs on .NET 8, where
    ProcessStartInfo.UseShellExecute defaults to FALSE, so handing it a
    directory (or any non-executable) raises Win32Exception instead of asking
    the shell to open it. Under Revit 2023 (.NET Framework 4.8) the same call
    works, which is why this looks machine-specific — and combined with an
    `except Exception: pass` it made the button look simply dead.

    explorer.exe always exists and takes a quoted path on both runtimes; the
    explicit UseShellExecute=True path is the last resort.
    """
    if not path:
        return False
    try:
        import System.Diagnostics as _diag
    except Exception:
        return False
    if not os.path.isdir(path):
        if not create:
            return False
        try:
            os.makedirs(path)
        except Exception:
            return False
    try:
        _diag.Process.Start(u"explorer.exe", u'"{}"'.format(path))
        return True
    except Exception as ex:
        _logger.debug("open_in_explorer({}) failed: {}".format(path, ex))
    try:
        psi = _diag.ProcessStartInfo(path)
        psi.UseShellExecute = True
        _diag.Process.Start(psi)
        return True
    except Exception as ex:
        _logger.debug("open_in_explorer fallback failed: {}".format(ex))
    return False


def open_file_with_shell(path):
    """Open a FILE with its default application (activity logs, reports)."""
    if not path or not os.path.isfile(path):
        return False
    try:
        import System.Diagnostics as _diag
    except Exception:
        return False
    try:
        psi = _diag.ProcessStartInfo(path)
        psi.UseShellExecute = True
        _diag.Process.Start(psi)
        return True
    except Exception as ex:
        _logger.debug("open_file_with_shell({}) failed: {}".format(path, ex))
    try:
        _diag.Process.Start(u"notepad.exe", u'"{}"'.format(path))
        return True
    except Exception as ex:
        _logger.debug("open_file_with_shell fallback failed: {}".format(ex))
    return False


# ─── Project scope wording ────────────────────────────────────────────────────

def project_scope_lines(meta, doc_counts=None):
    """Markdown bullets describing what a project scope actually covers.

    Kept here because the same explanation was written three different ways
    across the two windows, and one of them was simply wrong: it claimed
    attachments and activity logs are "separate per Revit document". Only CHATS
    are per-document (ProjectStore.history_path); attachments and logs are
    per-DAY (attachments/<YYYY-MM-DD>/, logs/<YYYY-MM-DD>.md).
    """
    meta = meta or {}
    lines = []

    instr = (meta.get('instructions') or u'').strip()
    if instr:
        preview = u" ".join(instr.split())
        if len(preview) > 160:
            preview = preview[:159] + u"…"
        lines.append(u"- **Instructions:** {}".format(preview))
    else:
        lines.append(u"- **Instructions:** none yet.")

    if doc_counts is not None:
        lines.append(u"- **Knowledge:** {}".format(doc_counts))

    prov = meta.get('provider')
    if prov:
        model = meta.get('model')
        lines.append(u"- **AI provider:** {}{}".format(
            prov, u" · {}".format(model) if model else u""))
    else:
        lines.append(u"- **AI provider:** follows the global setting.")

    lines.append(u"- **Chats:** stored in this project, separate per Revit "
                 u"document.")
    lines.append(u"- **Attachments & activity logs:** stored in this project, "
                 u"filed by date.")
    return lines
