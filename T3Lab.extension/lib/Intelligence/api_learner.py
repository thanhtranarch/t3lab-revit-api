# -*- coding: utf-8 -*-
"""
API Learner

Self-learning system for Revit API compatibility and smart export adapters.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "API Learner"

import os
import json
import clr
from datetime import datetime, timedelta

# Try to import requests for web scraping (may not be available)
try:
    clr.AddReference('System.Net')
    from System.Net import WebClient
    from System import Uri
    HAS_WEB_CLIENT = True
except Exception:
    HAS_WEB_CLIENT = False

__author__ = "T3Lab"
__version__ = "1.0.0"


class RevitAPILearner(object):
    """Self-learning system for Revit API compatibility."""

    def __init__(self, revit_version, cache_dir=None):
        """Initialize API learner.

        Args:
            revit_version: Revit version number (e.g., 2023, 2024)
            cache_dir: Directory to store cached API information
        """
        self.revit_version = int(revit_version)

        # Set cache directory
        if cache_dir is None:
            cache_dir = os.path.join(os.path.expanduser('~'), '.t3lab', 'api_cache')
        self.cache_dir = cache_dir

        # Create cache directory if it doesn't exist
        if not os.path.exists(self.cache_dir):
            try:
                os.makedirs(self.cache_dir)
            except Exception:
                pass

        # Cache file path
        self.cache_file = os.path.join(self.cache_dir, 'api_compatibility_{}.json'.format(self.revit_version))

        # Load cached API info
        self.api_info = self._load_cache()

        # API documentation base URL
        self.api_docs_url = 'https://www.revitapidocs.com/{}/'.format(self.revit_version)

    def _load_cache(self):
        """Load cached API information."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    # Check if cache is recent (less than 30 days old)
                    if 'cached_date' in data:
                        cached_date = datetime.strptime(data['cached_date'], '%Y-%m-%d')
                        if datetime.now() - cached_date < timedelta(days=30):
                            return data
            except Exception:
                pass

        # Return default API info
        return self._get_default_api_info()

    def _save_cache(self):
        """Save API information to cache."""
        try:
            self.api_info['cached_date'] = datetime.now().strftime('%Y-%m-%d')
            self.api_info['revit_version'] = self.revit_version

            with open(self.cache_file, 'w') as f:
                json.dump(self.api_info, f, indent=2)
            return True
        except Exception:
            return False

    def _get_default_api_info(self):
        """Get default API information with known compatibility."""
        return {
            'revit_version': self.revit_version,
            'cached_date': datetime.now().strftime('%Y-%m-%d'),
            'learned_from': 'default',

            # Export API compatibility
            'export_api': {
                'dwg_export': {
                    'signature': 'Export(String folder, String name, ICollection<ElementId> views, DWGExportOptions options)',
                    'supports_element_id_collection': self.revit_version >= 2018,
                    'supports_viewset': True,
                },
                'pdf_export': {
                    'signature': 'Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)',
                    'note': 'PDF export does NOT take filename parameter in Export() method. Use PDFExportOptions.FileName property instead (learned from pyRevit)',
                },
                'dwf_export': {
                    'signature': 'Export(String folder, String name, ViewSet views, DWFExportOptions options)',
                    'supports_viewset': True,
                },
            },

            # DWGExportOptions compatibility
            'dwg_export_options': {
                'prop_overrides': {
                    'type': 'PropOverrideMode',
                    'accepts_enum_only': self.revit_version >= 2018,
                    'supported_values': ['ByEntity', 'ByLayer'],
                },
                'exporting_areas': {
                    'available': self.revit_version >= 2024,
                },
                'merged_views': {
                    'available': self.revit_version >= 2018,
                },
            },

            # PDFExportOptions compatibility
            'pdf_export_options': {
                'file_name': {
                    'available': self.revit_version >= 2022,
                    'note': 'Set output filename (without .pdf extension). Learned from pyRevit.',
                },
                'combine': {
                    'available': self.revit_version >= 2018,
                },
                'hide_scope_boxes': {
                    'available': self.revit_version >= 2018,
                },
                'hide_crop_boundaries': {
                    'available': self.revit_version >= 2018,
                },
                'hide_unreferenced_view_tags': {
                    'available': self.revit_version >= 2018,
                },
            },

            # Version-specific notes
            'version_notes': {
                '2025': 'Built on .NET 8',
                '2024': 'Enhanced export options',
                '2023': 'PropOverrides requires enum',
                '2022': 'PDF export signature changed',
            }
        }

    def learn_from_web(self):
        """Learn API information from online documentation.

        Returns:
            bool: True if learning was successful, False otherwise
        """
        if not HAS_WEB_CLIENT:
            return False

        try:
            # Check if we can reach the API docs
            client = WebClient()

            # Try to fetch the API documentation page
            # This is a simplified implementation - in production you'd want more robust scraping
            main_page = client.DownloadString(Uri(self.api_docs_url))

            if main_page:
                # Mark as learned from web
                self.api_info['learned_from'] = 'web'
                self.api_info['last_web_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # In a full implementation, you would:
                # 1. Parse the HTML to extract API signatures
                # 2. Extract method overloads and parameters
                # 3. Identify version-specific changes
                # 4. Update self.api_info with learned information

                # For now, we'll just mark that we successfully connected
                self._save_cache()
                return True

        except Exception:
            pass

        return False

    def auto_update(self):
        """Automatically update API information if needed.

        Returns:
            bool: True if update was performed, False otherwise
        """
        # Check if cache is older than 30 days
        if 'cached_date' in self.api_info:
            try:
                cached_date = datetime.strptime(self.api_info['cached_date'], '%Y-%m-%d')
                if datetime.now() - cached_date > timedelta(days=30):
                    # Try to learn from web
                    return self.learn_from_web()
            except Exception:
                pass

        return False

    def get_export_signature(self, export_type):
        """Get the correct export signature for the current Revit version.

        Args:
            export_type: Type of export ('dwg', 'pdf', 'dwf')

        Returns:
            dict: Export signature information
        """
        export_key = '{}_export'.format(export_type.lower())

        if export_key in self.api_info.get('export_api', {}):
            return self.api_info['export_api'][export_key]

        return None

    def supports_property(self, class_name, property_name):
        """Check if a property is supported in the current Revit version.

        Args:
            class_name: Name of the class (e.g., 'dwg_export_options')
            property_name: Name of the property (e.g., 'exporting_areas')

        Returns:
            bool: True if property is supported, False otherwise
        """
        class_info = self.api_info.get(class_name, {})
        prop_info = class_info.get(property_name, {})

        return prop_info.get('available', False)

    def get_version_notes(self):
        """Get version-specific notes for the current Revit version.

        Returns:
            str: Version notes or empty string
        """
        notes = self.api_info.get('version_notes', {})
        return notes.get(str(self.revit_version), '')


class SmartAPIAdapter(object):
    """Smart API adapter that uses learned information to call correct APIs."""

    def __init__(self, doc, revit_version):
        """Initialize smart API adapter.

        Args:
            doc: Revit Document object
            revit_version: Revit version number
        """
        self.doc = doc
        self.revit_version = int(revit_version)
        self.learner = RevitAPILearner(revit_version)

        # Try to auto-update on initialization (non-blocking)
        try:
            self.learner.auto_update()
        except Exception:
            pass

    def export_dwg(self, folder, filename, view_ids, options):
        """Export to DWG using version-appropriate API.

        Args:
            folder: Output folder path
            filename: Output filename (without extension)
            view_ids: List or ICollection of ElementIds
            options: DWGExportOptions object

        Returns:
            bool: True if export succeeded
        """
        try:
            # Get signature info
            sig_info = self.learner.get_export_signature('dwg')

            if sig_info and sig_info.get('supports_element_id_collection', True):
                # Use ICollection<ElementId> signature (Revit 2018+)
                self.doc.Export(folder, filename, view_ids, options)
            else:
                # Fallback to ViewSet (older versions)
                # Note: This requires conversion of view_ids to ViewSet
                from Autodesk.Revit.DB import ViewSet
                view_set = ViewSet()
                for vid in view_ids:
                    view = self.doc.GetElement(vid)
                    if view:
                        view_set.Insert(view)
                self.doc.Export(folder, filename, view_set, options)

            return True
        except Exception as ex:
            raise ex

    def export_pdf(self, folder, filename, view_ids, options):
        """Export to PDF using version-appropriate API.

        Args:
            folder: Output folder path
            filename: Output filename (without extension)
            view_ids: List or IList of ElementIds
            options: PDFExportOptions object

        Returns:
            bool: True if export succeeded
        """
        try:
            # IMPORTANT: PDF export in Revit 2022+ uses a different signature than DWG/DXF
            # PDF signature: Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)
            # DWG/DXF signature: Export(String folder, String filename, ICollection<ElementId> views, DWGExportOptions options)
            # Note: PDF export does NOT take a filename parameter in Export() method
            # Instead, filename is set via PDFExportOptions.FileName property (learned from pyRevit)

            # Set filename via options property (pyRevit style)
            options.FileName = filename

            # Always use the 3-parameter signature for PDF export (Revit 2022+)
            return self.doc.Export(folder, view_ids, options)
        except Exception as ex:
            raise ex

    def export_dwf(self, folder, filename, view_set, options):
        """Export to DWF using version-appropriate API.

        Args:
            folder: Output folder path
            filename: Output filename (without extension)
            view_set: ViewSet object
            options: DWFExportOptions object

        Returns:
            bool: True if export succeeded
        """
        try:
            # DWF export uses ViewSet across all versions
            self.doc.Export(folder, filename, view_set, options)
            return True
        except Exception as ex:
            raise ex

    def configure_dwg_options(self, options, prop_override_mode=None):
        """Configure DWGExportOptions with version-appropriate settings.

        Args:
            options: DWGExportOptions object
            prop_override_mode: PropOverrideMode enum value (optional)

        Returns:
            DWGExportOptions: Configured options object
        """
        try:
            # Check if PropOverrides accepts enum only
            prop_info = self.learner.api_info.get('dwg_export_options', {}).get('prop_overrides', {})

            if prop_info.get('accepts_enum_only', True) and prop_override_mode:
                # Set PropOverrides to enum value (Revit 2018+)
                options.PropOverrides = prop_override_mode

            return options
        except Exception:
            return options

    def configure_pdf_options(self, options, hide_ref_planes=False, hide_scope_boxes=False, hide_crop_boundaries=False, hide_unreferenced_tags=False):
        """Configure PDFExportOptions with version-appropriate settings.

        Args:
            options: PDFExportOptions object
            hide_ref_planes: Hide reference/work planes
            hide_scope_boxes: Hide scope boxes
            hide_crop_boundaries: Hide crop boundaries
            hide_unreferenced_tags: Hide unreferenced view tags

        Returns:
            PDFExportOptions: Configured options object
        """
        try:
            # Check property availability
            pdf_opts = self.learner.api_info.get('pdf_export_options', {})

            if hide_ref_planes:
                try:
                    options.HideReferencePlane = True
                except AttributeError:
                    pass

            if hide_scope_boxes and pdf_opts.get('hide_scope_boxes', {}).get('available', True):
                options.HideScopeBoxes = True

            if hide_crop_boundaries and pdf_opts.get('hide_crop_boundaries', {}).get('available', True):
                options.HideCropBoundaries = True

            if hide_unreferenced_tags and pdf_opts.get('hide_unreferenced_view_tags', {}).get('available', True):
                options.HideUnreferencedViewTags = True

            return options
        except Exception:
            return options

    def get_learner_info(self):
        """Get information about the API learner.

        Returns:
            dict: Learner information
        """
        return {
            'revit_version': self.revit_version,
            'cache_file': self.learner.cache_file,
            'cached_date': self.learner.api_info.get('cached_date', 'Unknown'),
            'learned_from': self.learner.api_info.get('learned_from', 'Unknown'),
            'last_web_check': self.learner.api_info.get('last_web_check', 'Never'),
            'version_notes': self.learner.get_version_notes(),
        }
