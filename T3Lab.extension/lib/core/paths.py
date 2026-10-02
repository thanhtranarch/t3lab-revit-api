# -*- coding: utf-8 -*-
"""
Shared, user-editable path settings for the MCP layer.

Every machine-specific path used by the MCP server / Control dialog — the
task-watcher data directory, the Python interpreter used to launch
bridge.py, the Claude Desktop config file — is resolved through a single
JSON file:

    %APPDATA%\\T3LabAI\\mcp_paths.json

That file lives outside the repo (same folder as mcp_token.txt), so it is
never committed and never carries any developer's local paths. The first
time a path is needed, a sensible per-OS default is computed and written
back to the file; from then on the file is the source of truth and can be
hand-edited to relocate any of these paths without touching code.
"""

from __future__ import unicode_literals

import os


def settings_dir():
    """The %APPDATA%\\T3LabAI directory, created on first use."""
    app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
    d = os.path.join(app_data, 'T3LabAI')
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
    except OSError:
        pass
    return d


def user_data_path(*parts, **kwargs):
    """Path of a per-user T3Lab data file under %APPDATA%\\T3LabAI.

    Tools keep their state HERE, never inside the extension folder: the clone
    is updated by git and shared, and state written next to the scripts was
    committed to the public repo (client names, machine paths — 2026-10-02).

    legacy=[old paths]: when the new file does not exist yet, the first old
    file that does is copied over once, so moving a writer never loses the
    user's settings. The old file is left untouched.
    """
    legacy = kwargs.get('legacy') or ()
    path = os.path.join(settings_dir(), *parts)
    folder = os.path.dirname(path)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError:
        pass
    if not os.path.exists(path):
        for old in legacy:
            if old and os.path.isfile(old):
                try:
                    import shutil
                    shutil.copyfile(old, path)
                    break
                except Exception:
                    pass
    return path


def settings_file():
    """Path to the shared path-settings JSON file."""
    return os.path.join(settings_dir(), 'mcp_paths.json')


def load_settings():
    """Return the settings dict, or {} if the file is missing/unreadable."""
    try:
        import json
        path = settings_file()
        if not os.path.isfile(path):
            return {}
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    """Persist the settings dict, best-effort."""
    try:
        import json
        with open(settings_file(), 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_setting(key, default_fn):
    """
    Read `key` from mcp_paths.json.

    If absent (first run, or the user cleared it), compute a default via
    default_fn() and persist it — so the JSON file becomes the editable
    source of truth for every call after this one.
    """
    data = load_settings()
    value = data.get(key)
    if value:
        return value
    value = default_fn()
    data[key] = value
    save_settings(data)
    return value


def set_setting(key, value):
    """Explicitly set/override `key` in mcp_paths.json."""
    data = load_settings()
    data[key] = value
    save_settings(data)
