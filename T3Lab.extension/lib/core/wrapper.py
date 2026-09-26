# -*- coding: utf-8 -*-
"""
Tool Wrapper
------------
Provides a common pattern for pyRevit scripts to support both UI and 
headless execution via JSON arguments.

Author: Tran Tien Thanh
"""

import sys
import json
import traceback

class ToolWrapper:
    def __init__(self, ui_class):
        self.ui_class = ui_class

    def run(self):
        """Runs the tool in either UI or Headless mode."""
        if len(sys.argv) > 1:
            self._run_headless(sys.argv[1])
        else:
            self._run_ui()

    def _run_ui(self):
        """Launches the WPF UI."""
        try:
            window = self.ui_class()
            window.ShowDialog()
        except Exception as ex:
            print("UI Error: {}".format(ex))

    def _run_headless(self, args_json):
        """Parses JSON and executes the core logic."""
        try:
            args = json.loads(args_json)
            # Each tool must implement a 'headless_execute' method in its UI class
            # or we can pass a separate logic class.
            # For simplicity, we'll look for a 'run_logic' static method or similar.
            
            # Implementation detail: The script should define how to handle headless.
            # We'll use a convention: if a 'run_headless' function exists in the module, use it.
            pass
        except Exception as ex:
            print(json.dumps({"status": "error", "message": str(ex)}))


def respond(status="success", message="", data=None):
    """Standardized response format for Agent."""
    response = {"status": status, "message": message}
    if data:
        response["data"] = data
    print(json.dumps(response))
