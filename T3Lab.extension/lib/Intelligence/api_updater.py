# -*- coding: utf-8 -*-
"""
API Updater

Auto-checks for Revit API updates and notifies users of changes.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "API Updater"

import os
import json
import re
import clr
from datetime import datetime, timedelta

# Try to import web client
try:
    clr.AddReference('System.Net')
    from System.Net import WebClient
    from System import Uri
    from System.Text import Encoding
    HAS_WEB_CLIENT = True
except:
    HAS_WEB_CLIENT = False

__author__ = "T3Lab"
__version__ = "1.0.0"


class RevitAPIUpdater(object):
    """Auto-updater for Revit API information."""

    def __init__(self, cache_dir=None):
        """Initialize API updater.

        Args:
            cache_dir: Directory to store cached API information
        """
        # Set cache directory
        if cache_dir is None:
            cache_dir = os.path.join(os.path.expanduser('~'), '.t3lab', 'api_cache')
        self.cache_dir = cache_dir

        # Create cache directory if it doesn't exist
        if not os.path.exists(self.cache_dir):
            try:
                os.makedirs(self.cache_dir)
            except:
                pass

        # Update tracker file
        self.update_tracker_file = os.path.join(self.cache_dir, 'update_tracker.json')

        # Load update tracker
        self.update_tracker = self._load_update_tracker()

        # API documentation base URL
        self.api_docs_base = 'https://www.revitapidocs.com'

    def _load_update_tracker(self):
        """Load update tracker information."""
        if os.path.exists(self.update_tracker_file):
            try:
                with open(self.update_tracker_file, 'r') as f:
                    return json.load(f)
            except:
                pass

        # Default update tracker
        return {
            'last_check': None,
            'last_update': None,
            'known_versions': [2022, 2023, 2024, 2025, 2026],
            'update_schedule': 'friday',  # Check on Fridays
            'auto_update_enabled': True,
        }

    def _save_update_tracker(self):
        """Save update tracker information."""
        try:
            with open(self.update_tracker_file, 'w') as f:
                json.dump(self.update_tracker, f, indent=2)
            return True
        except:
            return False

    def should_check_for_updates(self):
        """Determine if we should check for updates.

        Returns:
            bool: True if we should check, False otherwise
        """
        # Check if auto-update is enabled
        if not self.update_tracker.get('auto_update_enabled', True):
            return False

        # Get current day of week (0 = Monday, 4 = Friday)
        today = datetime.now()
        is_friday = today.weekday() == 4

        # Check if last check was today
        last_check = self.update_tracker.get('last_check')
        if last_check:
            try:
                last_check_date = datetime.strptime(last_check, '%Y-%m-%d')
                if last_check_date.date() == today.date():
                    # Already checked today
                    return False
            except:
                pass

        # Check on Fridays or if never checked before
        if is_friday or last_check is None:
            return True

        # Also check if last check was more than 7 days ago
        if last_check:
            try:
                last_check_date = datetime.strptime(last_check, '%Y-%m-%d')
                if today - last_check_date > timedelta(days=7):
                    return True
            except:
                pass

        return False

    def check_for_updates(self):
        """Check for API updates from revitapidocs.com.

        Returns:
            dict: Update information
        """
        result = {
            'checked': False,
            'new_versions_found': [],
            'api_changes_detected': False,
            'success': False,
            'error': None,
        }

        if not HAS_WEB_CLIENT:
            result['error'] = 'WebClient not available'
            return result

        try:
            # Update last check time
            self.update_tracker['last_check'] = datetime.now().strftime('%Y-%m-%d')
            self._save_update_tracker()

            result['checked'] = True

            # Try to fetch the main page
            client = WebClient()
            client.Encoding = Encoding.UTF8

            main_page = client.DownloadString(Uri(self.api_docs_base))

            if not main_page:
                result['error'] = 'Failed to download main page'
                return result

            # Parse HTML to find available Revit versions
            # Look for version links like /2023/, /2024/, etc.
            version_pattern = r'href="/(20\d{2})/"'
            matches = re.findall(version_pattern, main_page)

            if matches:
                # Extract unique versions
                available_versions = sorted(set([int(v) for v in matches]))

                # Check for new versions
                known_versions = self.update_tracker.get('known_versions', [])
                new_versions = [v for v in available_versions if v not in known_versions]

                if new_versions:
                    result['new_versions_found'] = new_versions

                    # Update known versions
                    self.update_tracker['known_versions'] = sorted(set(known_versions + new_versions))
                    self.update_tracker['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    self._save_update_tracker()

                    result['api_changes_detected'] = True

                result['success'] = True

        except Exception as ex:
            result['error'] = str(ex)

        return result

    def fetch_api_info_for_version(self, version):
        """Fetch API information for a specific Revit version.

        Args:
            version: Revit version number (e.g., 2024)

        Returns:
            dict: API information or None if failed
        """
        if not HAS_WEB_CLIENT:
            return None

        try:
            client = WebClient()
            client.Encoding = Encoding.UTF8

            # Fetch the main page for this version
            version_url = '{}/{}/'.format(self.api_docs_base, version)
            version_page = client.DownloadString(Uri(version_url))

            if not version_page:
                return None

            # Parse the page to extract API information
            # This is a simplified implementation - in production you'd want more robust parsing

            api_info = {
                'version': version,
                'fetched_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': version_url,
                'api_available': True,
            }

            # Look for "What's New" link
            whats_new_pattern = r'href="(/{}[^"]*news[^"]*)">'.format(version)
            whats_new_match = re.search(whats_new_pattern, version_page, re.IGNORECASE)

            if whats_new_match:
                whats_new_url = self.api_docs_base + whats_new_match.group(1)
                api_info['whats_new_url'] = whats_new_url

                try:
                    # Fetch What's New page
                    whats_new_page = client.DownloadString(Uri(whats_new_url))

                    # Extract changes (simplified - you'd want better parsing)
                    if 'Document.Export' in whats_new_page:
                        api_info['export_changes'] = True

                    if 'DWGExportOptions' in whats_new_page:
                        api_info['dwg_options_changes'] = True

                    if 'PDFExportOptions' in whats_new_page:
                        api_info['pdf_options_changes'] = True

                except:
                    pass

            # Save to cache
            cache_file = os.path.join(self.cache_dir, 'version_{}_info.json'.format(version))
            try:
                with open(cache_file, 'w') as f:
                    json.dump(api_info, f, indent=2)
            except:
                pass

            return api_info

        except Exception as ex:
            return None


class APIUpdateNotifier(object):
    """Notifier for API updates."""

    def __init__(self):
        """Initialize update notifier."""
        self.notifications = []

    def add_notification(self, title, message, severity='info'):
        """Add a notification.

        Args:
            title: Notification title
            message: Notification message
            severity: Severity level ('info', 'warning', 'critical')
        """
        self.notifications.append({
            'title': title,
            'message': message,
            'severity': severity,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })

    def get_notifications(self):
        """Get all notifications.

        Returns:
            list: List of notifications
        """
        return self.notifications


def auto_check_and_update():
    """Automatically check for updates if needed.

    This function should be called when the tool starts.
    It will check for updates on Fridays or if never checked before.

    Returns:
        dict: Update result
    """
    updater = RevitAPIUpdater()
    notifier = APIUpdateNotifier()

    result = {
        'checked': False,
        'updates_found': False,
        'notifications': [],
    }

    # Check if we should check for updates
    if updater.should_check_for_updates():
        # Perform update check
        update_result = updater.check_for_updates()

        result['checked'] = True
        result['update_result'] = update_result

        # Process results
        if update_result.get('success'):
            new_versions = update_result.get('new_versions_found', [])

            if new_versions:
                result['updates_found'] = True

                # Add notification for new versions
                for version in new_versions:
                    notifier.add_notification(
                        'New Revit Version Detected',
                        'Revit {} API documentation is now available. The tool will automatically adapt to this version.'.format(version),
                        'info'
                    )

                    # Fetch API info for new version
                    api_info = updater.fetch_api_info_for_version(version)
                    if api_info:
                        # Check for breaking changes
                        if api_info.get('export_changes'):
                            notifier.add_notification(
                                'API Changes Detected',
                                'Document.Export API changes detected in Revit {}. Please review export functionality.'.format(version),
                                'warning'
                            )

        elif update_result.get('error'):
            # Only notify about errors if this is a manual check
            pass

        result['notifications'] = notifier.get_notifications()

    return result
