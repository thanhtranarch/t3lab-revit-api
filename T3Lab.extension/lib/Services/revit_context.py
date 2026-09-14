# -*- coding: utf-8 -*-
"""
Revit API Context Runner

Runs a callable inside a *standard Revit API execution* — the context every
pyRevit pushbutton script is written for.

Why this exists
---------------
Revit only allows ExternalEvent.Create(), Transaction(), and most other
document operations from inside an API context: an external command's
Execute(), an IExternalEventHandler.Execute(), or an event callback.

The T3Lab Assistant is a MODELESS window. Its WPF dispatcher callbacks run on
Revit's main thread while Revit sits IDLE, and idle is *not* an API context.
Launching a tool script straight from there died at the tool's first
API-context operation:

    Attempting to create an ExternalEvent outside of a standard API execution

BCF Reader, ManaLoca and BatchOut all create an ExternalEvent while building
their window, so "open BCF Reader" printed "BCF Reader đã được mở" and then
threw — the assistant could not actually open them at all.

This module owns ONE ExternalEvent whose handler drains a queue of callables.
Because IExternalEventHandler.Execute IS an API context, a tool launched
through here behaves exactly as if the user had clicked its ribbon button.

Usage
-----
    from Services.revit_context import ensure_api_context, run_in_api_context

    ensure_api_context()                 # ONCE, from an API context
    run_in_api_context(launcher, on_done)  # from anywhere afterwards

`ensure_api_context()` must itself be called from an API context (the
assistant's pushbutton startup) — creating the event is the very operation
that idle context forbids.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Revit API Context Runner"

import threading

try:
    import queue as _queue_mod          # CPython 3
except ImportError:
    import Queue as _queue_mod          # IronPython 2.7


# ── State ─────────────────────────────────────────────────────────────────────
# Module-level, not instance state: the assistant window can be closed and
# reopened within one session, and the ExternalEvent must survive that (Revit
# leaks one per Create). A pyRevit reload reloads this module and the next
# ensure_api_context() makes a fresh one.
_LOCK    = threading.Lock()
_TASKS   = _queue_mod.Queue()
_EVENT   = None
_HANDLER = None


def _err_text(exc):
    """Exception message as unicode. Never raises.

    `str(exc)` is unsafe under IronPython 2.7 — an error carrying non-ASCII
    text (a Vietnamese family name, a localised Windows string) throws
    UnicodeEncodeError from inside the error handler itself.
    """
    try:
        return u"{}".format(exc)
    except Exception:
        pass
    try:
        r = repr(exc)
        return r.decode('utf-8', 'replace') if isinstance(r, bytes) else u"{}".format(r)
    except Exception:
        return u"<unprintable error>"


def _normalise(res):
    """Fold a launcher's return value into (ok, error_text).

    Launchers are inconsistent by design: the generic ones return
    (ok, err), the older special ones return a bare bool, and a plain
    callable returns None. All three mean "it ran" unless they say otherwise.
    """
    if res is None:
        return True, u""
    if isinstance(res, tuple):
        ok  = bool(res[0]) if res else False
        err = res[1] if len(res) > 1 else u""
        return ok, (err or u"")
    return bool(res), u""


def _call(func):
    """Run func(), returning (ok, error_text). Never raises."""
    try:
        return _normalise(func())
    except Exception as ex:
        return False, _err_text(ex)


def _remove_task(token):
    """Pull the queued entry tagged `token` out, leaving every other entry in
    the queue untouched and in order. Used only on the Raise()-failed path."""
    remaining = []
    found = False
    while True:
        try:
            item = _TASKS.get_nowait()
        except _queue_mod.Empty:
            break
        if not found and item[0] is token:
            found = True
            continue
        remaining.append(item)
    for item in remaining:
        _TASKS.put(item)
    return found


# ── Revit binding ─────────────────────────────────────────────────────────────
HAS_REVIT_UI = False
try:
    import clr
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

    class _ApiContextHandler(IExternalEventHandler):
        __namespace__ = "T3Lab.Services"
        """Drains the task queue inside Revit's API context."""

        def Execute(self, app):
            # Drain EVERYTHING queued: Revit coalesces several Raise() calls
            # into a single Execute, so a second tool queued in the same idle
            # window would otherwise sit there until something raised again.
            while True:
                try:
                    _token, func, on_done = _TASKS.get_nowait()
                except _queue_mod.Empty:
                    break
                ok, err = _call(func)
                if on_done:
                    # A failing reporter must not swallow the rest of the queue.
                    try:
                        on_done(ok, err)
                    except Exception:
                        pass

        def GetName(self):
            return "T3Lab API Context Runner"

    HAS_REVIT_UI = True
except Exception:
    pass


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_api_context():
    """Create the ExternalEvent. Returns (ok, error_text).

    MUST be called from a Revit API context — i.e. the pushbutton script's own
    execution, not a background thread and not a WPF callback. Idempotent.
    """
    global _EVENT, _HANDLER
    if not HAS_REVIT_UI:
        return False, u"Revit UI API not available (running outside Revit)"
    with _LOCK:
        if _EVENT is not None:
            return True, u""
        try:
            _HANDLER = _ApiContextHandler()
            _EVENT = ExternalEvent.Create(_HANDLER)
            return True, u""
        except Exception as ex:
            _EVENT, _HANDLER = None, None
            return False, _err_text(ex)


def has_api_context():
    """True when a callable can be marshalled into a real API context."""
    return _EVENT is not None


def run_in_api_context(func, on_done=None, require_api_context=False):
    """Run `func` inside Revit's API context.

    Args:
        func:    zero-argument callable. Its return value is folded into
                 (ok, error_text) by _normalise.
        on_done: optional callable(ok, error_text), invoked on Revit's main
                 thread once `func` has run.
        require_api_context: refuse inline execution when an ExternalEvent
                 cannot be queued. Use for modeless document operations.

    Returns 'queued' when the call was handed to the ExternalEvent, or
    'inline' when it ran immediately (no event available), or 'rejected' when
    a strict request could not be queued. Strict rejection still calls on_done.

    Deliberately ASYNCHRONOUS. The caller is normally the WPF dispatcher —
    i.e. Revit's main thread — and Revit only fires external events from that
    same thread's message loop, so blocking here for the result would
    deadlock the whole application.
    """
    if _EVENT is None and HAS_REVIT_UI:
        # Late retry: succeeds when this call happens to already be inside an
        # API context (e.g. driven from the MCP ExternalEvent handler), or
        # when the module was reloaded after the assistant started. Fails
        # harmlessly on the WPF dispatcher, which is the normal caller.
        ensure_api_context()

    if _EVENT is None:
        if require_api_context:
            if on_done:
                try:
                    on_done(False, "Revit API event is unavailable. Reload pyRevit and reopen the Assistant before retrying.")
                except Exception:
                    pass
            return 'rejected'
        # No event: outside Revit (tests), or ensure_api_context() failed /
        # was never called. Running inline is the best available context; if
        # the tool needs a real one it fails with its own honest error rather
        # than silently doing nothing.
        ok, err = _call(func)
        if on_done:
            try:
                on_done(ok, err)
            except Exception:
                pass
        return 'inline'

    token = object()
    _TASKS.put((token, func, on_done))
    try:
        request = _EVENT.Raise()
        # Pending means this shared handler was already queued; it drains all
        # tasks on Execute. Denied/TimedOut cannot promise that execution.
        if require_api_context and str(request) not in ('Accepted', 'Pending'):
            raise RuntimeError("Revit API event request was not accepted: {}".format(request))
    except Exception as ex:
        # Raise() failed — the task would sit in the queue forever. Take it
        # back out and run it here so the user still gets an outcome. With
        # more than one assistant window this queue can hold other callers'
        # tasks too, so pull OUR entry out by identity rather than whatever
        # happens to be at the front — a plain get_nowait() could silently
        # steal and discard someone else's still-good, still-queued task.
        _remove_task(token)
        if require_api_context:
            if on_done:
                try:
                    on_done(False, _err_text(ex))
                except Exception:
                    pass
            return 'rejected'
        ok, err = _call(func)
        if not ok and not err:
            err = _err_text(ex)
        if on_done:
            try:
                on_done(ok, err)
            except Exception:
                pass
        return 'inline'
    return 'queued'
