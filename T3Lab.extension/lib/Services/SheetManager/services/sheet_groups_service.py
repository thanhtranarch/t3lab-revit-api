# -*- coding: utf-8 -*-
"""
Sheet Manager - Custom Sheet Groups Service
Manage custom sheet groups with JSON persistence

Copyright © Dang Quoc Truong (DQT)
"""

import json
import os

from Snippets._compat import eid_value, make_eid


class SheetGroupsService(object):
    """Manage custom sheet groups with JSON storage"""
    
    def __init__(self, doc):
        self.doc = doc
        self.groups = {}  # {group_name: [sheet_ids]}
        self.json_file = self._get_json_file_path()
        self.load_groups()
    
    def _get_json_file_path(self):
        """Get JSON file path for current document"""
        try:
            doc_path = self.doc.PathName
            if doc_path:
                # Save next to document
                base_name = os.path.splitext(doc_path)[0]
                json_path = base_name + "_SheetGroups.json"
            else:
                # Document not saved - use temp location
                import tempfile
                temp_dir = tempfile.gettempdir()
                json_path = os.path.join(temp_dir, "SheetManager_SheetGroups.json")
            
            return json_path
        except:
            # Fallback
            import tempfile
            temp_dir = tempfile.gettempdir()
            return os.path.join(temp_dir, "SheetManager_SheetGroups.json")
    
    def load_groups(self):
        """Load groups from JSON file"""
        try:
            if os.path.exists(self.json_file):
                with open(self.json_file, 'r') as f:
                    data = json.load(f)
                    # Convert string IDs back to ElementId
                    self.groups = {}
                    for group_name, sheet_id_strings in data.items():
                        sheet_ids = [make_eid(int(id_str)) for id_str in sheet_id_strings if id_str]
                        self.groups[group_name] = sheet_ids
                    
                print("DEBUG: Loaded {} groups from {}".format(len(self.groups), self.json_file))
            else:
                self.groups = {}
                print("DEBUG: No existing groups file")
        except Exception as e:
            print("ERROR loading groups: {}".format(str(e)))
            self.groups = {}
    
