# -*- coding: utf-8 -*-
"""FamiGen family generation shared by the FamiGen dialog and the MCP server.

* ``preview_mesh`` - pure tessellation of a family schema for the review pane.
* ``proposals``    - pure store for schemas proposed by an external AI (MCP).
* ``builder``      - Revit API: build, save and load the family. No WPF.

The schema contract itself lives in ``Intelligence.family_schema``.
"""
