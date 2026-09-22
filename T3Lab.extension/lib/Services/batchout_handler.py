# -*- coding: utf-8 -*-
"""
BatchOut External Event Handler
===============================
Provides an IExternalEventHandler implementation for BatchOut (ExportManagerWindow).
Defined in lib/Services to ensure the CLR type is registered only once in PythonNet,
preventing 'Duplicate type name within an assembly' while fully satisfying
IExternalEventHandler on both CPython 3 and IronPython 2.
"""

import sys
import clr
clr.AddReference('RevitAPIUI')
from Autodesk.Revit.UI import IExternalEventHandler


class BatchOutEventHandler(IExternalEventHandler):
    __namespace__ = "T3Lab.BatchOut"
    """Runs queued window actions inside a valid Revit API context.

    Required because the window is modeless (Show, not ShowDialog): ALL Revit
    API access — reads included, not just transactions — must happen in here.
    Touching the API from WPF events or Dispatcher callbacks after the command
    has returned runs outside API context and hard-crashes Revit on workshared
    models.
    """

    def __init__(self, logger):
        self._logger = logger
        self._queue = []

    def add(self, action):
        self._queue.append(action)

    def remove(self, action):
        """Discard only the request whose Raise failed."""
        for index in range(len(self._queue) - 1, -1, -1):
            if self._queue[index] is action:
                del self._queue[index]
                break

    def clear(self):
        self._queue = []

    def Execute(self, uiapp):
        try:
            actions = self._queue
            self._queue = []
            for action in actions:
                try:
                    action()
                except Exception as ex:
                    try:
                        self._logger.error(
                            "BatchOut external event failed: {}".format(ex))
                    except Exception:
                        pass
        except Exception:
            pass

    def GetName(self):
        return "T3Lab BatchOut Handler"
