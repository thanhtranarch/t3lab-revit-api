# -*- coding: utf-8 -*-
"""
MCPService — Shared MCP control backend

Single source of truth for all MCP-related operations:
  • MCP HTTP server (start / stop / status)
  • File-based task watcher (start / stop / status)
  • MCP client config snippet generation (JSON + Codex TOML)
  • Auto-configure Claude Desktop / ChatGPT (Codex) / Antigravity
  • Data directory management

Import this from any dialog or tool that needs MCP control:
    from Services.mcp_service import MCPService
"""

from __future__ import unicode_literals

import os
import re
import sys

# ─── Path helper ───────────────────────────────────────────────────────────────
def _ensure_lib_in_path():
    here    = os.path.dirname(os.path.abspath(__file__))   # lib/Services
    lib_dir = os.path.dirname(here)                        # lib
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)


# ─── Lazy imports (avoid crashing if running outside Revit) ────────────────────
def _get_server():
    _ensure_lib_in_path()
    from core.server import get_t3labai_server
    return get_t3labai_server()


def _get_watcher():
    _ensure_lib_in_path()
    from core.file_watcher import get_task_watcher
    return get_task_watcher()


def _get_paths_module():
    _ensure_lib_in_path()
    from core import paths
    return paths


def _get_data_dir():
    _ensure_lib_in_path()
    try:
        from core.file_watcher import T3LAB_DATA_DIR
        return T3LAB_DATA_DIR
    except Exception:
        try:
            return _get_paths_module().get_setting(
                'data_dir',
                lambda: os.path.join(os.path.expanduser('~'), 'T3Lab_AI_Data'))
        except Exception:
            return os.path.join(os.path.expanduser('~'), 'T3Lab_AI_Data')


def _source_bridge_path():
    """Absolute path to the bridge.py shipped inside this extension."""
    here    = os.path.dirname(os.path.abspath(__file__))   # lib/Services
    lib_dir = os.path.dirname(here)                        # lib
    return os.path.join(lib_dir, 'core', 'bridge.py')


def _get_bridge_path():
    """
    Bridge path to embed in Claude Desktop / Cursor configs.

    Always the machine-stable deployed copy (%APPDATA%/T3LabAI/bridge.py,
    see MCPService.deploy_bridge) — never a path inside the extension
    folder, so the config keeps working when the extension is moved,
    re-downloaded or updated. Falls back to the in-extension copy only if
    deployment fails (e.g. APPDATA not writable).
    """
    deployed, _err = MCPService.deploy_bridge()
    return (deployed or _source_bridge_path()).replace('\\', '/')


def _find_python_executable():
    """
    Locate a CPython 3 interpreter to run core/bridge.py.

    bridge.py needs f-strings and urllib.request (Python 3.6+) — the
    IronPython interpreter running this Revit process can't run it, and a
    bare "python" command isn't guaranteed to resolve on every machine
    (e.g. python.org installs that only register "python3", or a PATH
    that hasn't picked up a fresh install yet). Search PATH first, then
    fall back to common per-user/system install locations, and only use
    the bare command name as a last resort so Claude Desktop still gets
    something to try.

    The resolved path is read from / written to mcp_paths.json (see
    core/paths.py) so the scan only runs once per machine and the result
    is hand-editable afterwards; the cached value is revalidated on every
    call in case Python was reinstalled elsewhere.
    """
    paths = _get_paths_module()
    cached_path = paths.load_settings().get('python_executable')
    if cached_path and os.path.isfile(cached_path):
        return cached_path

    is_windows = os.name == 'nt'
    exe_names = ['python.exe', 'python3.exe'] if is_windows else ['python3', 'python']
    found = None

    # 1) Search PATH directories for a real interpreter.
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    for d in path_dirs:
        for name in exe_names:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                found = candidate
                break
        if found:
            break

    # 2) Common install locations not always present on PATH.
    if not found:
        home = os.path.expanduser('~')
        fallback_globs = []
        if is_windows:
            fallback_globs.append(os.path.join(home, 'AppData', 'Local', 'Programs', 'Python', 'Python*', 'python.exe'))
            fallback_globs.append(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps', 'python.exe'))
            fallback_globs.append('C:/Python*/python.exe')
            fallback_globs.append('C:/Program Files/Python*/python.exe')
        else:
            fallback_globs.append('/usr/local/bin/python3')
            fallback_globs.append('/usr/bin/python3')
            fallback_globs.append(os.path.join(home, '.pyenv', 'shims', 'python3'))

        try:
            import glob
            for pattern in fallback_globs:
                matches = sorted(glob.glob(pattern), reverse=True)
                if matches:
                    found = matches[0]
                    break
        except Exception:
            pass

    if found:
        paths.set_setting('python_executable', found)
        return found

    # 3) Give up — return the bare command name and let the OS PATH try.
    # Not cached: it isn't a real resolved path, so nothing to reuse.
    return 'python' if is_windows else 'python3'


# ─── Cross-engine server detection ─────────────────────────────────────────────
# pyRevit runs startup.py and `#! python3` pushbuttons in DIFFERENT Python
# engines inside ONE Revit process (IronPython vs CPython). core/server.py
# anchors its singleton on the AppDomain as a Python object, which cannot cross
# that boundary: the other engine receives an opaque CLR object it cannot call
# ("'Object_1$1' object has no attribute 'get_server_stats'"), then happily
# binds a SECOND port in the same process — one Revit was seen LISTENING on
# both 48884 and 48885.
#
# /health is engine-independent and reports the owning pid, so probing it is
# the only reliable way to answer "does THIS Revit process already host a
# server?" regardless of which engine started it.

SERVER_PORT_MIN, SERVER_PORT_MAX = 48884, 48894


def _probe_health(port, timeout=0.35):
    """Return the /health payload for `port`, or None if nothing answers."""
    try:
        import json as _json
        try:
            from urllib.request import urlopen
        except ImportError:                       # IronPython 2
            from urllib2 import urlopen
        raw = urlopen('http://127.0.0.1:{}/health'.format(port), timeout=timeout).read()
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', 'replace')
        return _json.loads(raw)
    except Exception:
        return None


def _local_server_ports():
    """
    Ports already hosting a T3Lab server inside THIS Revit process.

    Matched by pid, so a server in another Revit window is correctly ignored —
    those are legitimate and the bridge switches between them on purpose.
    """
    try:
        mypid = os.getpid()
    except Exception:
        return []
    found = []
    for port in range(SERVER_PORT_MIN, SERVER_PORT_MAX + 1):
        info = _probe_health(port)
        if info and info.get('status') == 'ok' and info.get('pid') == mypid:
            found.append(port)
    return found


# ─── AI client registry ────────────────────────────────────────────────────────
# Every MCP client that can be auto-configured launches the SAME stdio bridge
# (%APPDATA%/T3LabAI/bridge.py). Only the config file location and its syntax
# differ:
#
#   Claude Desktop   claude_desktop_config.json          JSON  "mcpServers"
#   ChatGPT (Codex)  ~/.codex/config.toml                TOML  [mcp_servers.X]
#   Antigravity      ~/.gemini/antigravity-ide/
#                        mcp_config.json                 JSON  "mcpServers"
#
# Cursor, Windsurf and Claude Code use the same JSON shape as Claude Desktop,
# so config_snippet(fmt='json') can be pasted into those by hand as well.
#
# ChatGPT note: the ChatGPT app's own "Connectors" (developer mode) only accept
# a PUBLIC https MCP endpoint — a local stdio bridge cannot be registered there.
# The supported local path is Codex (the CLI and the Codex panel inside the
# ChatGPT desktop app), which reads ~/.codex/config.toml. That is what we write.

MCP_SERVER_NAME = 't3lab-revit'


def _home():
    return os.path.expanduser('~')


def _appdata():
    return os.environ.get('APPDATA', os.path.join(_home(), 'AppData', 'Roaming'))


def _claude_candidates():
    import platform
    system = platform.system()
    if system == 'Windows':
        return [os.path.join(_appdata(), 'Claude', 'claude_desktop_config.json')]
    if system == 'Darwin':
        return [os.path.join(_home(), 'Library', 'Application Support', 'Claude',
                             'claude_desktop_config.json')]
    return [os.path.join(_home(), '.config', 'Claude', 'claude_desktop_config.json')]


def _codex_candidates():
    """Codex reads $CODEX_HOME/config.toml, defaulting to ~/.codex/config.toml."""
    out = []
    codex_home = os.environ.get('CODEX_HOME')
    if codex_home:
        out.append(os.path.join(codex_home, 'config.toml'))
    out.append(os.path.join(_home(), '.codex', 'config.toml'))
    return out


def _antigravity_candidates():
    """
    Antigravity keeps its MCP registry outside the VS Code-style user folder —
    it inherits Cascade's layout, so the live file is
    ~/.gemini/antigravity-ide/mcp_config.json. The other entries cover
    alternative layouts; whichever exists first wins.
    """
    return [
        os.path.join(_home(), '.gemini', 'config', 'mcp_config.json'),
        os.path.join(_home(), '.gemini', 'antigravity', 'mcp_config.json'),
        os.path.join(_home(), '.gemini', 'antigravity-ide', 'mcp_config.json'),
        os.path.join(_home(), '.antigravity', 'mcp_config.json'),
        os.path.join(_appdata(), 'Antigravity', 'User', 'globalStorage', 'mcp_config.json'),
    ]


# key -> descriptor. `setting` is the mcp_paths.json key that overrides the
# auto-detected path, so a non-standard install stays hand-editable.
AI_CLIENTS = [
    {'key': 'claude',
     'label': 'Claude Desktop',
     'fmt': 'json',
     'setting': 'claude_desktop_config',
     'candidates': _claude_candidates},
    {'key': 'chatgpt',
     'label': 'ChatGPT (Codex)',
     'fmt': 'toml',
     'setting': 'codex_config',
     'candidates': _codex_candidates},
    {'key': 'antigravity',
     'label': 'Antigravity',
     'fmt': 'json',
     'setting': 'antigravity_config',
     'candidates': _antigravity_candidates},
]


def _client_spec(key):
    for spec in AI_CLIENTS:
        if spec['key'] == key:
            return spec
    raise KeyError('Unknown MCP client: {}'.format(key))


def _resolve_client_path(spec):
    """
    Config path for one client.

    mcp_paths.json wins if the user set it; otherwise the first candidate that
    already exists on disk is chosen, falling back to the primary candidate so
    Configure can create it. The result is persisted, so it becomes editable.
    """
    def _default():
        candidates = spec['candidates']()
        for c in candidates:
            if os.path.isfile(c):
                return c
        return candidates[0]

    try:
        return _get_paths_module().get_setting(spec['setting'], _default)
    except Exception:
        return _default()


def _backup_once(path):
    """
    Keep a pristine copy of a user config the first time we touch it.

    These files hold the user's other MCP servers, model providers and plugins;
    `<path>.t3lab-backup` is written only when it does not already exist, so the
    very first pre-T3Lab state stays recoverable. Best-effort — a failed backup
    never blocks the write.
    """
    try:
        backup = path + '.t3lab-backup'
        if os.path.isfile(path) and not os.path.isfile(backup):
            with open(path, 'rb') as f:
                data = f.read()
            with open(backup, 'wb') as f:
                f.write(data)
    except Exception:
        pass


def _server_entry(python, bridge, port):
    """The JSON object describing our stdio server (Claude / Antigravity)."""
    return {
        'type': 'stdio',
        'command': python,
        'args': [bridge, str(port)],
    }


# ── JSON clients (Claude Desktop, Antigravity) ────────────────────────────────

def _read_json_config(path):
    import json as _json
    import codecs
    if not os.path.isfile(path):
        return {}
    with codecs.open(path, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    if not raw:
        return {}
    return _json.loads(raw)


def _json_is_configured(path):
    config = _read_json_config(path)
    servers = config.get('mcpServers') if isinstance(config, dict) else None
    return MCP_SERVER_NAME in (servers or {})


def _write_json_config(path, python, bridge, port):
    import json as _json
    import codecs

    try:
        config = _read_json_config(path)
    except Exception:
        config = {}          # unparseable -> start clean (backup keeps original)
    if not isinstance(config, dict):
        config = {}

    servers = config.get('mcpServers')
    if not isinstance(servers, dict):
        servers = {}
        config['mcpServers'] = servers

    entry = _server_entry(python, bridge, port)
    # Antigravity stamps its own entries with a protobuf type marker; keep it
    # if a previous entry had one so the client still recognises the record.
    previous = servers.get(MCP_SERVER_NAME)
    if isinstance(previous, dict) and '$typeName' in previous:
        entry['$typeName'] = previous['$typeName']
    servers[MCP_SERVER_NAME] = entry

    cfg_dir = os.path.dirname(path)
    if cfg_dir and not os.path.isdir(cfg_dir):
        os.makedirs(cfg_dir)

    _backup_once(path)
    with codecs.open(path, 'w', encoding='utf-8') as f:
        f.write(_json.dumps(config, indent=2, ensure_ascii=False))


# ── TOML client (Codex / ChatGPT) ─────────────────────────────────────────────

# A TOML table header alone on its line: [x], [x.y], [[x]] — deliberately
# stricter than "starts with [" so a line of an inline array is never mistaken
# for the start of the next table.
_TOML_HEADER_RE = re.compile(r'^\s*\[\[?[^\[\]]+\]\]?\s*(?:#.*)?$')


def _toml_section_re(name):
    """Matches `[mcp_servers.<name>]` and any of its sub-tables (.env etc.)."""
    return re.compile(
        r'^\s*\[\s*mcp_servers\s*\.\s*["\']?' + re.escape(name) +
        r'["\']?\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$')


def _toml_quote(value):
    """Basic TOML string — backslashes and quotes escaped (Windows paths)."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def _codex_block(python, bridge, port):
    return (
        '[mcp_servers.{}]\n'.format(MCP_SERVER_NAME) +
        'type = "stdio"\n' +
        'command = "{}"\n'.format(_toml_quote(python)) +
        'args = ["{}", "{}"]\n'.format(_toml_quote(bridge), port) +
        # Revit can be slow to answer the first probe on a cold start; Codex
        # kills a server that has not handshaken within 10s by default.
        'startup_timeout_sec = 60\n'
    )


def _toml_upsert(text, block):
    """
    Replace the `[mcp_servers.t3lab-revit]` table (and its sub-tables) in
    `text` with `block`, or append it when absent.

    Everything outside our own table is preserved line-for-line — the file
    holds the user's model providers, other MCP servers and plugins.
    """
    section_re = _toml_section_re(MCP_SERVER_NAME)
    lines = text.splitlines()
    out = []
    i, n = 0, len(lines)
    replaced = False

    while i < n:
        if section_re.match(lines[i]):
            if not replaced:
                out.extend(block.rstrip('\n').split('\n'))
                replaced = True
            i += 1
            while i < n and not _TOML_HEADER_RE.match(lines[i]):
                i += 1
            continue
        out.append(lines[i])
        i += 1

    if not replaced:
        if out and out[-1].strip():
            out.append('')
        out.extend(block.rstrip('\n').split('\n'))

    return '\n'.join(out) + '\n'


def _read_toml_text(path):
    import codecs
    if not os.path.isfile(path):
        return ''
    with codecs.open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _toml_is_configured(path):
    section_re = _toml_section_re(MCP_SERVER_NAME)
    for line in _read_toml_text(path).splitlines():
        if section_re.match(line):
            return True
    return False


def _write_toml_config(path, python, bridge, port):
    import codecs
    try:
        text = _read_toml_text(path)
    except Exception:
        text = ''

    cfg_dir = os.path.dirname(path)
    if cfg_dir and not os.path.isdir(cfg_dir):
        os.makedirs(cfg_dir)

    _backup_once(path)
    with codecs.open(path, 'w', encoding='utf-8') as f:
        f.write(_toml_upsert(text, _codex_block(python, bridge, port)))


# ─── MCPService ────────────────────────────────────────────────────────────────

class MCPService(object):
    """
    Stateless helper that wraps MCP server + file watcher operations.

    All methods return simple dicts so callers don't need to handle exceptions
    — failures come back as {'error': '<message>'}.

    Typical usage in a dialog:
        status = MCPService.server_status()
        if not status.get('error'):
            print(status['running'], status['port'])

        ok, err = MCPService.start_server()
        ok, err = MCPService.stop_server()

        ok, err = MCPService.start_watcher()
        ok, err = MCPService.stop_watcher()

        snippet = MCPService.config_snippet()
    """

    # ── MCP HTTP server ────────────────────────────────────────────────────────

    @staticmethod
    def server_status():
        """
        Return status of the MCP HTTP server.

        Returns:
            dict with keys: running (bool), port (int), tools_count (int),
                            commands_processed (int), foreign (bool),
                            error (str|None)

        `foreign` is True when the live server belongs to ANOTHER pyRevit engine
        in this same Revit process (see the cross-engine note above): it is
        running and usable over HTTP, but its Python object cannot be called
        from here, so Stop is not available without restarting Revit.
        """
        try:
            server = _get_server()
            stats  = server.get_server_stats()
            if stats.get('running'):
                return {
                    'running':              True,
                    'port':                 stats.get('port', 48884),
                    'tools_count':          stats.get('tools_count', 0),
                    'commands_processed':   stats.get('commands_processed', 0),
                    'external_event_ready': stats.get('external_event_ready', False),
                    'foreign':              False,
                    'error':                None,
                }
            own_error = None
        except Exception as ex:
            # An uncallable cross-engine anchor lands here. Never surface the
            # raw AttributeError: the server may well be up and serving.
            own_error = str(ex)

        ports = _local_server_ports()
        if ports:
            return {'running': True, 'port': ports[0], 'tools_count': 0,
                    'commands_processed': 0, 'external_event_ready': False,
                    'foreign': True, 'error': None}

        return {'running': False, 'port': 48884, 'tools_count': 0,
                'commands_processed': 0, 'external_event_ready': False,
                'foreign': False, 'error': own_error}

    @staticmethod
    def ensure_external_event():
        """
        Create the Revit ExternalEvent that marshals model-editing tools onto
        Revit's main thread.

        MUST be called from Revit's UI thread (e.g. a pushbutton's main body),
        NOT from a background worker — ExternalEvent.Create throws outside a
        Revit API context. Without this, every create/modify/rename MCP tool
        fails with "transaction outside API context". Safe and idempotent.

        Returns:
            (success: bool, error_message: str|None)
        """
        try:
            server = _get_server()
            return server.ensure_external_event()
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def start_server(port=None):
        """
        Start the MCP HTTP server.

        Args:
            port (int|None): Override port. Uses server default if None.

        Returns:
            (success: bool, error_message: str)
        """
        # One Revit process must expose exactly one port. A server started by
        # another engine in this process is invisible to the object above, so
        # without this check start_server() binds a second port — which is how
        # one Revit ended up LISTENING on 48884 AND 48885.
        existing = _local_server_ports()
        if existing:
            return True, None

        try:
            server = _get_server()
            if port:
                server.port = int(port)
            ok = server.start_server()
            if ok:
                return True, None
            reason = getattr(server, '_start_error', None)
            return False, ('start_server() failed: {}'.format(reason)
                           if reason else 'start_server() returned False')
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def stop_server():
        """
        Stop the MCP HTTP server.

        Returns:
            (success: bool, error_message: str)
        """
        try:
            server = _get_server()
            ok = server.stop_server()
            if ok:
                return True, None
            err = 'stop_server() returned False'
        except Exception as ex:
            err = str(ex)

        # The object we hold could not stop it. If a server in this process is
        # still answering, it belongs to another pyRevit engine (startup.py runs
        # under IronPython, `#! python3` buttons under CPython) and only a Revit
        # restart releases it. Say so instead of repeating a raw AttributeError.
        if _local_server_ports():
            return False, ('The running server was started by another pyRevit '
                           'engine in this Revit session and cannot be stopped '
                           'from here — restart Revit to release it.')
        return False, err

    @staticmethod
    def toggle_server(current_port=None):
        """
        Start server if stopped, stop it if running.

        Returns:
            (new_state: 'running'|'stopped', error_message: str|None)
        """
        status = MCPService.server_status()
        if status.get('error'):
            return 'unknown', status['error']
        if status['running']:
            ok, err = MCPService.stop_server()
            return ('stopped' if ok else 'running'), err
        else:
            ok, err = MCPService.start_server(port=current_port)
            return ('running' if ok else 'stopped'), err

    # ── Open documents ─────────────────────────────────────────────────────────

    @staticmethod
    def list_open_documents():
        """
        List documents open in this Revit instance.

        Tool calls always target the ACTIVE document (what the user sees) —
        switching is done through the switch_active_document / open_document
        MCP tools, which activate the target's window for real.

        Returns:
            (documents: list[dict] with keys title/path/is_active, error: str|None)
        """
        try:
            server = _get_server()
            return server.get_open_documents(), None
        except Exception as ex:
            return [], str(ex)

    # ── Teaching capture (Opus distils via MCP) ────────────────────────────────

    @staticmethod
    def teaching_status():
        """Return the server's teaching-capture status dict.

        Keys: enabled (bool), sandbox (str|None), session_open (bool),
        sessions_recorded (int), error (str|None).
        """
        try:
            server = _get_server()
            info = server.get_teaching_status()
            info['error'] = None
            return info
        except Exception as ex:
            return {'enabled': False, 'sandbox': None, 'session_open': False,
                    'sessions_recorded': 0, 'error': str(ex)}

    @staticmethod
    def set_teaching_mode(on):
        """Enable/disable teaching capture. Returns (new_state: bool, err|None)."""
        try:
            server = _get_server()
            return bool(server.set_teaching_mode(bool(on))), None
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def toggle_teaching_mode():
        """Flip teaching capture. Returns (new_state: bool, err|None)."""
        status = MCPService.teaching_status()
        if status.get('error'):
            return False, status['error']
        return MCPService.set_teaching_mode(not status.get('enabled'))

    @staticmethod
    def mark_active_document_as_sandbox():
        """Designate the ACTIVE document as the teaching sandbox — the only doc
        model-modifying tools may touch while teaching mode is on.

        Resolves the active document via the server's open-documents list (safe,
        already used by the dialog). Returns (info: dict|None, error: str|None).
        """
        try:
            server = _get_server()
            docs = server.get_open_documents() or []
            active = None
            for d in docs:
                if d.get('is_active'):
                    active = d
                    break
            if active is None and docs:
                active = docs[0]
            if active is None:
                return None, 'No open document to mark as sandbox.'
            info = {'title': active.get('title') or '',
                    'path':  active.get('path') or ''}
            server.set_sandbox_document(info)
            return info, None
        except Exception as ex:
            return None, str(ex)

    @staticmethod
    def clear_sandbox():
        """Clear the designated sandbox document. Returns (ok, err|None)."""
        try:
            server = _get_server()
            server.set_sandbox_document(None)
            return True, None
        except Exception as ex:
            return False, str(ex)

    # ── File watcher ───────────────────────────────────────────────────────────

    @staticmethod
    def watcher_status():
        """
        Return status of the file-based task watcher.

        Returns:
            dict with keys: running (bool), data_dir (str),
                            has_ext_event (bool), error (str|None)
        """
        try:
            watcher = _get_watcher()
            info    = watcher.get_status()
            info['error'] = None
            return info
        except Exception as ex:
            return {'running': False, 'data_dir': _get_data_dir(),
                    'has_ext_event': False, 'error': str(ex)}

    @staticmethod
    def start_watcher():
        """
        Start the file task watcher.

        Returns:
            (success: bool, error_message: str)
        """
        try:
            watcher = _get_watcher()
            ok = watcher.start()
            return (True, None) if ok else (False, 'start() returned False')
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def stop_watcher():
        """
        Stop the file task watcher.

        Returns:
            (success: bool, error_message: str)
        """
        try:
            watcher = _get_watcher()
            watcher.stop()
            return True, None
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def toggle_watcher():
        """
        Start watcher if stopped, stop it if running.

        Returns:
            (new_state: 'running'|'stopped', error_message: str|None)
        """
        status = MCPService.watcher_status()
        if status.get('error'):
            return 'unknown', status['error']
        if status['running']:
            ok, err = MCPService.stop_watcher()
            return ('stopped' if ok else 'running'), err
        else:
            ok, err = MCPService.start_watcher()
            return ('running' if ok else 'stopped'), err

    # ── Config & paths ─────────────────────────────────────────────────────────

    @staticmethod
    def deploy_bridge():
        """
        Copy the extension's core/bridge.py to the machine-stable location
        %APPDATA%/T3LabAI/bridge.py (next to mcp_token.txt / mcp_paths.json).

        The Claude Desktop config points at the deployed copy, never inside
        the extension folder — anyone can download the extension anywhere on
        their machine, and updating/moving/re-cloning it never breaks the MCP
        connection. Content-compared before writing, so it is safe (and
        cheap) to call on every Revit startup; extension updates propagate
        to the deployed copy automatically.

        Returns:
            (deployed_path: str|None, error: str|None)
        """
        try:
            src = _source_bridge_path()
            with open(src, 'rb') as f:
                data = f.read()

            dst = os.path.join(_get_paths_module().settings_dir(), 'bridge.py')
            try:
                with open(dst, 'rb') as f:
                    if f.read() == data:
                        return dst, None          # already up to date
            except Exception:
                pass                              # missing/unreadable → write

            with open(dst, 'wb') as f:
                f.write(data)
            return dst, None
        except Exception as ex:
            return None, str(ex)

    @staticmethod
    def config_snippet(port=None, fmt='json'):
        """
        Return a ready-to-paste MCP server entry for this machine.

        Args:
            port (int|None): Port number to embed. Reads from running server if None.
            fmt (str): 'json' for the "mcpServers" block used by Claude Desktop,
                Antigravity, Cursor, Windsurf and Claude Code; 'toml' for the
                `[mcp_servers.t3lab-revit]` table used by ChatGPT's Codex.

        Returns:
            str: Formatted snippet.
        """
        if port is None:
            try:
                server = _get_server()
                port   = server.port
            except Exception:
                port = 48884

        bridge = _get_bridge_path()

        if str(fmt).lower() == 'toml':
            # Codex reads real TOML — Windows backslashes must stay escaped.
            return _codex_block(_find_python_executable(), bridge, port).rstrip('\n')

        python = _find_python_executable().replace('\\', '/')
        return (
            '{\n'
            '  "mcpServers": {\n'
            '    "' + MCP_SERVER_NAME + '": {\n'
            '      "type": "stdio",\n'
            '      "command": "' + python + '",\n'
            '      "args": [\n'
            '        "' + bridge + '",\n'
            '        "' + str(port) + '"\n'
            '      ]\n'
            '    }\n'
            '  }\n'
            '}'
        )

    @staticmethod
    def data_dir():
        """Return the T3Lab_AI_Data directory path (created if absent)."""
        d = _get_data_dir()
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
        except OSError:
            pass
        return d

    @staticmethod
    def open_data_dir():
        """Open the T3Lab_AI_Data directory in the system file explorer."""
        d = MCPService.data_dir()
        try:
            import subprocess
            import platform
            if platform.system() == 'Windows':
                subprocess.Popen(['explorer', d])
            elif platform.system() == 'Darwin':
                subprocess.Popen(['open', d])
            else:
                subprocess.Popen(['xdg-open', d])
            return True, None
        except Exception as ex:
            return False, str(ex)

    # ── AI client auto-configure (Claude Desktop · ChatGPT/Codex · Antigravity) ─

    @staticmethod
    def clients():
        """Return [(key, label, fmt)] for every auto-configurable MCP client."""
        return [(c['key'], c['label'], c['fmt']) for c in AI_CLIENTS]

    @staticmethod
    def find_client_config(key):
        """Config file path for one client key ('claude'/'chatgpt'/'antigravity')."""
        return _resolve_client_path(_client_spec(key))

    @staticmethod
    def client_status(key):
        """
        Check one client's configuration status.

        Returns:
            dict: key, label, fmt, path, file_exists, configured, error
        """
        label, fmt = key, 'json'
        try:
            spec  = _client_spec(key)
            label = spec['label']
            fmt   = spec['fmt']
            path  = _resolve_client_path(spec)
        except Exception as ex:
            return {'key': key, 'label': label, 'fmt': fmt, 'path': '',
                    'file_exists': False, 'configured': False, 'error': str(ex)}

        base = {'key': key, 'label': label, 'fmt': fmt, 'path': path}
        if not os.path.isfile(path):
            base.update({'file_exists': False, 'configured': False, 'error': None})
            return base
        try:
            configured = (_toml_is_configured(path) if fmt == 'toml'
                          else _json_is_configured(path))
            base.update({'file_exists': True, 'configured': configured, 'error': None})
        except Exception as ex:
            base.update({'file_exists': True, 'configured': False,
                         'error': 'Parse error: {}'.format(ex)})
        return base

    @staticmethod
    def clients_status():
        """Status dicts for every client, in registry order."""
        return [MCPService.client_status(c['key']) for c in AI_CLIENTS]

    @staticmethod
    def configure_client(key, port=None):
        """
        Write the t3lab-revit entry into one client's config file.

        Creates the file and its directory if absent, merges with existing
        entries, and keeps a one-time `<path>.t3lab-backup` of the original.

        Returns:
            (success: bool, message: str) — config path on success, error text
            on failure.
        """
        try:
            spec = _client_spec(key)
        except Exception as ex:
            return False, str(ex)

        try:
            if port is None:
                try:
                    port = _get_server().port
                except Exception:
                    port = 48884

            bridge = _get_bridge_path()
            python = _find_python_executable()
            path   = _resolve_client_path(spec)

            if spec['fmt'] == 'toml':
                _write_toml_config(path, python, bridge, port)
            else:
                _write_json_config(path, python, bridge, port)
            return True, path
        except Exception as ex:
            return False, str(ex)

    @staticmethod
    def configure_all_clients(port=None):
        """
        Configure every registered client in one go.

        Returns:
            list[dict]: key, label, ok, message — one entry per client.
        """
        results = []
        for spec in AI_CLIENTS:
            ok, msg = MCPService.configure_client(spec['key'], port=port)
            results.append({'key': spec['key'], 'label': spec['label'],
                            'ok': ok, 'message': msg})
        return results

    # ── Backwards-compatible Claude Desktop wrappers ──────────────────────────

    @staticmethod
    def find_claude_desktop_config():
        """Deprecated — use find_client_config('claude')."""
        return MCPService.find_client_config('claude')

    @staticmethod
    def claude_desktop_status():
        """Deprecated — use client_status('claude')."""
        return MCPService.client_status('claude')

    @staticmethod
    def configure_claude_desktop(port=None):
        """Deprecated — use configure_client('claude', port)."""
        return MCPService.configure_client('claude', port=port)

    # ── Combined snapshot (for dashboard widgets) ──────────────────────────────

    @staticmethod
    def full_status():
        """
        Return a combined status dict for both server and watcher.

        Useful for status-bar indicators or dashboards that need a single call.

        Returns:
            {
              'server':  {running, port, tools_count, commands_processed, error},
              'watcher': {running, data_dir, has_ext_event, error},
              'config':  '<snippet string>',
              'clients': [{key, label, fmt, path, file_exists, configured, error}],
            }
        """
        srv = MCPService.server_status()
        wat = MCPService.watcher_status()
        try:
            clients = MCPService.clients_status()
        except Exception:
            clients = []
        return {
            'server':  srv,
            'watcher': wat,
            'config':  MCPService.config_snippet(port=srv.get('port')),
            'clients': clients,
        }
