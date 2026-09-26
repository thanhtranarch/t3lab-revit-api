# -*- coding: utf-8 -*-
"""
Tool Registry
-------------
Centralized registry for T3Lab tools accessible by the AI Agent.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

import os

class ToolRegistry:
    def __init__(self, extension_path=None):
        if extension_path is None:
            # Assume we are in lib/core/registry.py
            # Go up 3 levels to reach T3Lab.extension
            self.base_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        else:
            self.base_path = extension_path
            
        self.tab_path = os.path.join(self.base_path, "T3Lab.tab")
        self.tools = self._initialize_tools()

    def _initialize_tools(self):
        """Returns a dictionary of available tools with their metadata."""
        return {
            "cad_to_beam": {
                "name": "CAD to Beam",
                "description": "Converts CAD lines to Revit beams with AI dimension detection from nearby TextNotes.",
                "rel_path": "Modeling & Datum.panel/Create.stack/Create Elements.pulldown/Beam.pushbutton/script.py"
            },
            "annotation_manager": {
                "name": "Annotation Manager",
                "description": "Unified tool for managing Dimensions and Text Notes — find, delete, and auto-rename types and instances.",
                "rel_path": "Annotation & Select.panel/Text.stack/AnnotationManager.pushbutton/script.py"
            },
            "workset_manager": {
                "name": "Workset Manager",
                "description": "List, rename, and manage user worksets; remove unused worksets via a checklist.",
                "rel_path": "Standards & Settings.panel/WorksetManager.pushbutton/script.py"
            },
            "batch_export": {
                "name": "BatchOut",
                "description": "Batch export sheets to PDF, DWG, NWD, and IFC formats with custom naming and revision tracking.",
                "rel_path": "Views & Sheets.panel/BatchOut.pushbutton/script.py"
            },
            "load_family": {
                "name": "Load Family",
                "description": "Browse and load Revit families from local disk or Cloud (Vercel API).",
                "rel_path": "Modeling & Datum.panel/Family Work 2.stack/Load Family.pushbutton/script.py"
            },
            "room_to_area": {
                "name": "Room to Area",
                "description": "Automatically convert room boundaries to area boundaries in the active area plan.",
                "rel_path": "Modeling & Datum.panel/Areas.stack/Room to Area.pushbutton/script.py"
            },
            "create_plan_views": {
                "name": "Create Plan Views",
                "description": "Batch-generate individual floor plan views for each room with custom naming and template assignment.",
                "rel_path": "Views & Sheets.panel/Create Room Plan/script.py"
            }
        }

