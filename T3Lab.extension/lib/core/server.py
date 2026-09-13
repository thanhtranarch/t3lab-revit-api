# -*- coding: utf-8 -*-
"""
MCP Server

Local MCP server implementation for AI-to-Revit communication.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "MCP Server"

import os
import sys
import threading
import time
import json
import uuid
import io

from core import jsonsafe

try:
    import queue as _queue_mod            # CPython 3
except ImportError:
    import Queue as _queue_mod            # IronPython 2.7

# Process-wide anchor for the server singleton. Stored on
# AppDomain.CurrentDomain — the ONLY store that is truly per-Revit-process.
# The previous anchor was an attribute on `sys`, but each IronPython engine
# has its OWN `sys` module: a pyRevit reload (or any command running in a
# fresh engine) got a blank `sys`, missed the anchor, built a second server
# and bound the next port while the orphaned old server thread kept its port
# LISTENING. Six reloads in a day = six live servers = the whole 48884-48894
# range exhausted ("No usable port"). AppDomain data is shared by every
# engine in the process, so the live server (port, HTTP thread,
# ExternalEvent) is found and reused across reloads. `sys` is kept only as a
# fallback for non-.NET runs (dev tooling under CPython).
# One Revit process must expose exactly one port.
_PROCESS_SINGLETON_KEY = '_t3lab_mcp_server_singleton'


def _anchor_usable(inst):
    """
    True only when `inst` is a server object THIS engine can actually call.

    pyRevit runs startup.py and a `#! python3` pushbutton in DIFFERENT Python
    engines inside one Revit process (IronPython vs CPython). AppDomain data is
    shared by all of them, so an instance anchored by one engine comes back to
    the other as an opaque CLR object — IronPython surfaces its Python classes
    as `IronPython.NewTypes.System.Object_1$1`, whose Python attributes are not
    reachable from Python.NET. Handing such an object to a caller produced
    "'Object_1$1' object has no attribute 'get_server_stats'" and, worse, left
    the other engine free to bind a SECOND port in the same process.
    """
    if inst is None:
        return False
    for name in ('get_server_stats', 'start_server', 'stop_server'):
        if not callable(getattr(inst, name, None)):
            return False
    return True


def _get_process_anchor():
    try:
        from System import AppDomain
        existing = AppDomain.CurrentDomain.GetData(_PROCESS_SINGLETON_KEY)
        if _anchor_usable(existing):
            return existing
    except Exception:
        pass
    fallback = getattr(sys, _PROCESS_SINGLETON_KEY, None)
    return fallback if _anchor_usable(fallback) else None


def _set_process_anchor(inst):
    try:
        from System import AppDomain
        AppDomain.CurrentDomain.SetData(_PROCESS_SINGLETON_KEY, inst)
    except Exception:
        pass
    setattr(sys, _PROCESS_SINGLETON_KEY, inst)

from Snippets._compat import eid_value, make_eid
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from urlparse import urlparse, parse_qs
try:
    from socketserver import ThreadingMixIn, TCPServer
except ImportError:
    from SocketServer import ThreadingMixIn, TCPServer

# External Event Handler for thread-safe Revit API calls
HAS_REVIT_UI = False


class _ToolTask(object):
    """One tool call marshalled onto Revit's UI thread via ExternalEvent.

    claim() decides who executes the task — the ExternalEvent handler ('ui')
    or the read-tool fallback on the calling thread ('fallback') — so a call
    can never run twice even if Revit fires the event at the same moment the
    fallback grace period expires.
    """

    def __init__(self, tool_name, arguments):
        self.tool_name = tool_name
        self.arguments = arguments
        self.result = None
        self.exception = None
        self.done = threading.Event()
        self._claim_lock = threading.Lock()
        self._claimed_by = None

    def claim(self, who):
        with self._claim_lock:
            if self._claimed_by is None:
                self._claimed_by = who
                return True
            return False


try:
    import clr
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

    class MCPExternalEventHandler(IExternalEventHandler):
        __namespace__ = "T3Lab.Server"
        def __init__(self, server):
            self.server = server
            self.tasks = _queue_mod.Queue()

        def Execute(self, app):
            # `app` is the live UIApplication Revit hands to every external
            # event — the ONLY document source that works regardless of
            # which IronPython engine created this server. The engine-bound
            # pyrevit.HOST_APP.uiapp is None when the AppDomain-anchored
            # singleton was built in a startup/hook engine, which made every
            # tool fail with "No active document" while a model was open.
            # Cache it so fallback reads (executed off this event) can use
            # it too.
            try:
                self.server._cached_uiapp = app
            except Exception:
                pass
            # Drain everything queued: ExternalEvent coalesces multiple
            # Raise() calls into one Execute, and several callers (HTTP
            # worker + assistant thread) may be waiting at once.
            while True:
                try:
                    task = self.tasks.get_nowait()
                except _queue_mod.Empty:
                    break
                if not task.claim('ui'):
                    continue  # read fallback or timeout already consumed it
                is_write = task.tool_name in self.server._WRITE_TOOLS
                if is_write:
                    self.server._write_in_progress = True
                try:
                    task.result = self.server._execute_tool_in_context(
                        task.tool_name, task.arguments, uiapp=app)
                    task.exception = None
                except Exception as e:
                    task.exception = e
                    task.result = None
                finally:
                    if is_write:
                        self.server._write_in_progress = False
                        # Revit keeps regenerating and refreshing views for a
                        # while after a write commits. Stamp the finish time so
                        # the read fallback stays off the worker thread during
                        # that window — see _READ_FALLBACK_WAIT.
                        try:
                            self.server._last_write_done = time.time()
                        except Exception:
                            pass
                    task.done.set()

        def GetName(self):
            return "T3Lab MCP External Event Handler"

    HAS_REVIT_UI = True
except Exception as e:
    pass



class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """One thread per request, so /health probes and the bridge's parallel
    instance scans answer instantly even while a slow tools/call is in
    flight (a single-threaded HTTPServer queues EVERYTHING behind the slow
    request, which made instances flap dead/alive during model loads).
    /mcp handling itself stays serialized through _MCP_REQUEST_LOCK — the
    Revit API is not thread-safe, and one-tool-at-a-time per instance is
    exactly what the ExternalEvent architecture assumes."""
    daemon_threads = True

    # On Windows, SO_REUSEADDR lets a second process bind an already-listening
    # port SILENTLY (two T3Lab/Routes listeners ended up sharing 48884). Fail
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        """Suppress default SocketServer traceback printing on MTA worker threads.
        Without this, connection resets or socket aborts during pyRevit reload
        print to sys.stderr, triggering WPF MetroWindow instantiation on a non-STA thread."""
        pass

    def server_bind(self):
        TCPServer.server_bind(self)
        # HTTPServer.server_bind would now call socket.getfqdn() — a reverse
        # DNS lookup that can block for seconds on corporate DNS/VPN, making
        # server startup look failed while it was merely slow.
        host, port = self.socket.getsockname()[:2]
        self.server_name = host
        self.server_port = port


# Module-level (not instance state): survives pyRevit reloads, where the
# pre-reload server singleton found via the AppDomain anchor skips __init__
# entirely.
_MCP_REQUEST_LOCK = threading.Lock()


class MCPRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for MCP Protocol"""

    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        """Override to suppress default logging"""
        pass

    def _send_response(self, status_code, content_type, body):
        """Send HTTP response"""
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status_code=200):
        """Send JSON response.

        Serialized through jsonsafe, NOT plain json.dumps: IronPython 2.7's
        ASCII escaper raises on non-ASCII, and 35 tool descriptions in the
        registry below contain an em dash. That made every tools/list response
        die mid-handler — the client saw a bare connection reset and bridge.py
        silently fell back to its stale tools_cache.json, so newly registered
        tools (the t3lab_* teaching set among them) never reached Claude.
        """
        body = jsonsafe.dumps(data)
        self._send_response(status_code, 'application/json', body)

    def _send_sse_event(self, event_type, data):
        """Send SSE event"""
        message = "event: {}\ndata: {}\n\n".format(event_type,
                                                   jsonsafe.dumps(data))
        self.wfile.write(message.encode('utf-8'))
        self.wfile.flush()

    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/':
            # Server info
            self._send_json({
                'name': 'T3LabAI MCP Server',
                'version': '1.0.0',
                'protocol': 'mcp',
                'status': 'running'
            })

        elif path == '/sse':
            # SSE endpoint for MCP communication
            self._handle_sse()

        elif path == '/health':
            # Health check. pid + port let external diagnostics attribute a
            # listener to its Revit process — the same pid answering on
            # SEVERAL ports in the range means orphaned duplicate servers
            # (broken singleton anchor), not several Revit windows.
            self._send_json({'status': 'ok', 'pid': os.getpid(),
                             'port': self.server.server_port})

        elif path in ('/v1/models', '/models'):
            # Tolerate OpenAI-compatible clients that probe for a model list,
            # so they don't repeatedly hit an "unexpected endpoint" 404.
            self._send_json({'object': 'list', 'data': []})

        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        """Handle POST requests (MCP messages)"""
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'

        try:
            request = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({'error': 'Invalid JSON'}, 400)
            return

        if path == '/message' or path == '/mcp':
            self._handle_mcp_message(request)
        else:
            self._send_json({'error': 'Not found'}, 404)

    def _handle_sse(self):
        """Handle SSE connection for MCP"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        # Register client
        server = self.server.mcp_server
        client_id = str(uuid.uuid4())
        server._register_client(client_id)

        # Send endpoint event for MCP protocol
        endpoint_url = "http://127.0.0.1:{}/message".format(server.port)
        self._send_sse_event('endpoint', endpoint_url)

        try:
            # Keep connection alive
            while server.is_running:
                # Send keep-alive ping every 30 seconds
                import time
                time.sleep(30)
                self._send_sse_event('ping', {'timestamp': time.time()})
        except Exception:
            pass
        finally:
            server._unregister_client(client_id)

    def _handle_mcp_message(self, request):
        """Handle MCP JSON-RPC message.

        Serialized through _MCP_REQUEST_LOCK: the HTTP server is threaded
        (so /health never starves behind a slow call), but tool execution
        must stay one-at-a-time — concurrent Revit API access off the main
        thread can crash Revit natively.
        """
        with _MCP_REQUEST_LOCK:
            self._handle_mcp_message_locked(request)

    def _handle_mcp_message_locked(self, request):
        server = self.server.mcp_server

        method = request.get('method', '')
        params = request.get('params', {})
        request_id = request.get('id')

        # ── Auth ────────────────────────────────────────────────────────
        # Reject anything that doesn't carry the shared-secret token (see
        # T3LabAIServer._get_or_create_token). Without this, any local
        # process could reach tools/call — including send_code_to_revit,
        # which executes arbitrary IronPython with full Revit API access.
        expected = 'Bearer ' + server._token
        if self.headers.get('Authorization', '') != expected:
            if 'id' not in request:
                self._send_json({'status': 'unauthorized'}, 401)
            else:
                self._send_json({
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': {'code': -32001, 'message': 'Unauthorized: missing or invalid token'}
                }, 401)
            return

        # If it's a notification (no 'id' in request), do not send JSON-RPC response
        if 'id' not in request:
            try:
                if method == 'notifications/initialized':
                    # Handle initialized notification if needed
                    pass
            except Exception:
                pass
            self._send_json({'status': 'ok'})
            return

        response = {
            'jsonrpc': '2.0',
            'id': request_id
        }

        try:
            if method == 'initialize':
                response['result'] = server._handle_initialize(params)
            elif method == 'tools/list':
                response['result'] = server._handle_tools_list()
            elif method == 'tools/call':
                response['result'] = server._handle_tool_call(params)
            else:
                response['error'] = {
                    'code': -32601,
                    'message': 'Method not found: {}'.format(method)
                }
        except Exception as e:
            response['error'] = {
                'code': -32603,
                'message': str(e)
            }

        server._commands_processed += 1
        self._send_json(response)


class T3LabAIServer(object):
    """MCP Server for AI communication with Revit"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # A prior instance anchored on the AppDomain (from before a pyRevit
        # reload, or from another engine) wins over a fresh class-level
        # _instance. Returning an instance of a DIFFERENT (old) class means
        # Python skips __init__, so the already-running server — its port,
        # HTTP thread and ExternalEvent — is reused untouched instead of a
        # second one being spun up. A full Revit restart (required to load
        # edited server code anyway) starts clean.
        existing = _get_process_anchor()
        if existing is not None:
            cls._instance = existing
            return existing
        if cls._instance is None:
            with cls._lock:
                existing = _get_process_anchor()
                if existing is not None:
                    cls._instance = existing
                    return existing
                if cls._instance is None:
                    inst = super(T3LabAIServer, cls).__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
                    _set_process_anchor(inst)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._port = 48884
        self._is_running = False
        self._client_count = 0
        self._total_clients = 0
        self._commands_processed = 0
        self._server_thread = None
        self._http_server = None
        self._clients = {}
        self._tools = {}
        self._external_event = None
        self._event_handler = None
        # UIApplication captured from ExternalEvent.Execute — the engine-
        # independent document source (read via getattr: an anchored
        # pre-update instance may predate this attribute).
        self._cached_uiapp = None
        self._write_in_progress = False
        self._start_error = None
        self._token = self._get_or_create_token()
        # Legacy slot for the withdrawn B4 request-wide TransactionGroup. It is
        # never populated any more (see __begin_action_group) — kept only so an
        # instance anchored from before a pyRevit reload still has the
        # attribute for _release_stale_action_group to clear.
        self._action_group = None
        # Monotonic time the last write tool finished on the Revit main thread.
        # Gates the read fallback: see _READ_FALLBACK_WAIT.
        self._last_write_done = 0.0
        # Saved element-id sets built by ai_element_filter(store_as=...), keyed
        # by document title. Let a caller say element_ids="@name" instead of
        # ferrying thousands of ids through the model's context — see
        # _expand_id_handles.
        self._selection_sets = {}

        # ── Teaching capture (Opus distils via MCP) ──────────────────────────
        # When ON, the EXTERNAL MCP path (_handle_tool_call, i.e. Claude Desktop
        # via bridge.py) records every tool call into a trajectory the local
        # Qwen is later fine-tuned on, and — on the Revit main thread — every
        # model-MODIFYING tool is restricted to a designated sandbox document so
        # the teacher can never touch the real project. State persisted in
        # mcp_paths.json so it survives a restart. See set_teaching_mode /
        # _sandbox guard in _execute_tool_in_context / the recorder below.
        self._teaching_enabled = False
        self._sandbox_doc = None          # {'title':..., 'path':...} or None
        self._teach_session = None        # {'id','goal','steps':[...]} or None
        self._restore_teaching_state()

        self._initialized = True

        # Register default Revit tools
        self._register_default_tools()

    def _get_or_create_token(self):
        """Return the shared-secret token every /message and /mcp request
        must carry, creating and persisting one on first run.

        Without this, any local process could hit the HTTP server and call
        tools/call — including send_code_to_revit, which runs arbitrary
        IronPython with full Revit API access. Persisting the token to the
        same %APPDATA%\\T3LabAI directory used by settings.py lets a
        locally-spawned bridge.py (launched by Claude Desktop/Cursor per
        the mcpServers config) read it without any manual setup.
        """
        try:
            app_data = os.environ.get('APPDATA', '')
            token_dir = os.path.join(app_data, 'T3LabAI')
            if not os.path.exists(token_dir):
                os.makedirs(token_dir)
            token_path = os.path.join(token_dir, 'mcp_token.txt')
            if os.path.exists(token_path):
                with open(token_path, 'r') as f:
                    existing = f.read().strip()
                if existing:
                    return existing
            token = uuid.uuid4().hex
            with open(token_path, 'w') as f:
                f.write(token)
            return token
        except Exception:
            # Can't persist (e.g. no APPDATA) — still refuse unauthenticated
            # requests rather than silently accepting everything, just with
            # a token that only this process knows (external bridges won't
            # be able to authenticate until this succeeds).
            return uuid.uuid4().hex

    @property
    def token(self):
        return self._token

    def _register_default_tools(self):
        """Register default Revit tools for MCP"""
        self._tools = {
            # ── Teaching capture boundary tools (Opus distils via MCP) ───────
            # Call these to frame ONE training trajectory when the user has
            # turned teaching capture on in MCP Control. They record nothing
            # about the Revit model itself — only the task framing — and are
            # no-ops when teaching mode is off.
            't3lab_begin_teaching': {
                'name': 't3lab_begin_teaching',
                'description': ('Start recording a T3Lab teaching trajectory. '
                                'Call this FIRST, before the Revit tool calls '
                                'for one task, passing the task goal in your own '
                                'words. Everything you do until t3lab_end_teaching '
                                'is captured as one example to fine-tune the local '
                                'assistant. Only records when the user enabled '
                                'teaching capture; safe to call regardless.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'goal': {
                            'type': 'string',
                            'description': ('The task you are about to perform, '
                                            'phrased as a user request (e.g. '
                                            '"tag all walls on level 1").'),
                        },
                    },
                    'required': ['goal'],
                },
            },
            't3lab_end_teaching': {
                'name': 't3lab_end_teaching',
                'description': ('Finish the current T3Lab teaching trajectory '
                                'started with t3lab_begin_teaching. Call this '
                                'LAST, after the task is done, with a short '
                                'summary of the outcome. Persists the trajectory '
                                'as one training example.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'summary': {
                            'type': 'string',
                            'description': ('One-sentence summary of what was '
                                            'accomplished (optional).'),
                        },
                    },
                    'required': [],
                },
            },
            't3lab_set_teaching_mode': {
                'name': 't3lab_set_teaching_mode',
                'description': ('Turn T3Lab teaching capture on or off. While ON, '
                                'the external MCP session is recorded as training '
                                'data AND model-modifying tools are restricted to '
                                'the sandbox document (protects the real project). '
                                'Call with enabled=true before teaching a task.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'enabled': {
                            'type': 'boolean',
                            'description': 'True to start capturing, false to stop.',
                        },
                    },
                    'required': ['enabled'],
                },
            },
            't3lab_mark_sandbox': {
                'name': 't3lab_mark_sandbox',
                'description': ('Designate a document as the teaching sandbox — '
                                'the ONLY document model-modifying tools may touch '
                                'while teaching mode is on. Pass the title or path '
                                'of a SCRATCH .rvt (get it from list_open_documents). '
                                'NEVER mark a real project model.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'document': {
                            'type': 'string',
                            'description': ('Title or file path of the scratch '
                                            'document, as shown by list_open_documents.'),
                        },
                    },
                    'required': ['document'],
                },
            },
            't3lab_training_status': {
                'name': 't3lab_training_status',
                'description': ('Report the local training corpus (example count '
                                'by source), the last fine-tune, whether a run is '
                                'in progress, and the current teaching/sandbox '
                                'state. Call before t3lab_train_model.'),
                'inputSchema': {
                    'type': 'object', 'properties': {}, 'required': [],
                },
            },
            't3lab_train_model': {
                'name': 't3lab_train_model',
                'description': ('Launch a background LoRA fine-tune of the local '
                                'model on the captured training data. Runs OUTSIDE '
                                'Revit (CPython + GPU), detached, can take hours. '
                                'Refuses below the minimum example count unless '
                                'force=true. Check t3lab_training_status first.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'force': {
                            'type': 'boolean',
                            'description': ('Train even if the dataset is below '
                                            'the recommended minimum.'),
                        },
                    },
                    'required': [],
                },
            },
            't3lab_build_exemplars': {
                'name': 't3lab_build_exemplars',
                'description': ('Distil the captured teaching data into a small, '
                                'PORTABLE few-shot file (teacher_exemplars.json) '
                                'that makes the local model answer in the taught '
                                'style on ANY machine — no GPU or fine-tune needed. '
                                'Commit that file to share it. Call after teaching '
                                'when you want portability without training.'),
                'inputSchema': {
                    'type': 'object', 'properties': {}, 'required': [],
                },
            },
            'revit_get_active_view': {
                'name': 'revit_get_active_view',
                'description': 'Get information about the currently active view in Revit',
                'inputSchema': {
                    'type': 'object',
                    'properties': {},
                    'required': []
                }
            },
            'revit_get_selected_elements': {
                'name': 'revit_get_selected_elements',
                'description': 'Get information about currently selected elements in Revit',
                'inputSchema': {
                    'type': 'object',
                    'properties': {},
                    'required': []
                }
            },
            'revit_get_project_info': {
                'name': 'revit_get_project_info',
                'description': 'Get project information from the current Revit document',
                'inputSchema': {
                    'type': 'object',
                    'properties': {},
                    'required': []
                }
            },
            'revit_list_views': {
                'name': 'revit_list_views',
                'description': 'List all views in the current Revit document, optionally filtered by view type.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_type': {
                            'type': 'string',
                            'description': "Optional filter on Revit's view type. Spelling is forgiving — \"floor_plan\", \"Floor Plan\" and \"FloorPlan\" all work. An unknown value returns an error listing the view types actually present, never a silently empty list.",
                            'enum': ['FloorPlan', 'CeilingPlan', 'ThreeD',
                                     'Section', 'Elevation', 'Detail',
                                     'DraftingView', 'Legend', 'Schedule',
                                     'AreaPlan', 'EngineeringPlan', 'Rendering',
                                     'Walkthrough', 'ColumnSchedule',
                                     'PanelSchedule', 'Report']
                        }
                    },
                    'required': []
                }
            },
            'revit_list_sheets': {
                'name': 'revit_list_sheets',
                'description': 'List all sheets in the current Revit document',
                'inputSchema': {
                    'type': 'object',
                    'properties': {},
                    'required': []
                }
            },
            'revit_get_element_info': {
                'name': 'revit_get_element_info',
                'description': 'Get detailed information about a specific Revit element by ID',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {
                            'type': 'integer',
                            'description': 'The Revit element ID'
                        }
                    },
                    'required': ['element_id']
                }
            },
            'revit_override_color': {
                'name': 'revit_override_color',
                'description': 'Apply ONE specific color override to elements in the active view — the right tool for "color/tô walls red", "highlight X in green". For a WHOLE category pass `category` directly: the server collects ALL matching elements itself (no ids needed, no count limit). For coloring BY a parameter\'s values use color_elements instead.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'color': {
                            'type': 'string',
                            'description': 'Hex color code (e.g. #FF0000) or CSS color name (e.g. red, green, blue)'
                        },
                        'category': {
                            'type': 'string',
                            'description': 'Optional category name (Walls, Floors, Doors, Windows, Columns, Ceilings, Roofs, ...). When given and element_ids is omitted, EVERY element of this category visible in the active view is colored — preferred for whole-category requests, no count limit.'
                        },
                        'element_ids': {
                            'type': 'array',
                            'items': {
                                'type': ['integer', 'string']
                            },
                            'description': 'Optional list of Revit element IDs for a specific subset, or ["@name"] naming a set saved by ai_element_filter(store_as="name"). If both this and category are omitted, applies to the currently selected elements.'
                        },
                        'halftone': {
                            'type': 'boolean',
                            'description': 'Also draw the elements halftone (faded). Optional.'
                        },
                        'transparency': {
                            'type': 'integer',
                            'description': 'Also set surface transparency, 0 (opaque) to 100 (invisible). Optional.'
                        },
                        'line_weight': {
                            'type': 'integer',
                            'description': 'Also set projection/cut line weight, 1-16. Optional.'
                        }
                    },
                    'required': ['color']
                }
            },
            'create_level': {
                'name': 'create_level',
                'description': 'Create a new Level in the Revit model at the specified elevation',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'elevation': {
                            'type': 'number',
                            'description': 'Elevation in meters above project base point'
                        },
                        'name': {
                            'type': 'string',
                            'description': 'Level name (e.g. "Level 3", "Ground Floor")'
                        }
                    },
                    'required': ['elevation']
                }
            },
            'place_wall': {
                'name': 'place_wall',
                'description': 'Place a wall between two points on a specified level',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'start_x': {
                            'type': 'number',
                            'description': 'Start point X coordinate in meters'
                        },
                        'start_y': {
                            'type': 'number',
                            'description': 'Start point Y coordinate in meters'
                        },
                        'end_x': {
                            'type': 'number',
                            'description': 'End point X coordinate in meters'
                        },
                        'end_y': {
                            'type': 'number',
                            'description': 'End point Y coordinate in meters'
                        },
                        'level_name': {
                            'type': 'string',
                            'description': 'Level name to place wall on (default: first level)'
                        },
                        'height': {
                            'type': 'number',
                            'description': 'Wall height in meters (default: 3.0)'
                        },
                        'wall_type_name': {
                            'type': 'string',
                            'description': 'Wall type name (default: first available)'
                        }
                    },
                    'required': ['start_x', 'start_y', 'end_x', 'end_y']
                }
            },
            'get_parameter': {
                'name': 'get_parameter',
                'description': 'Get the value of a named parameter from a Revit element by its ID',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {
                            'type': 'integer',
                            'description': 'Revit element ID'
                        },
                        'parameter_name': {
                            'type': 'string',
                            'description': 'Parameter name to retrieve'
                        }
                    },
                    'required': ['element_id', 'parameter_name']
                }
            },
            'get_elements_by_level': {
                'name': 'get_elements_by_level',
                'description': 'Get all elements of a given category on a specified level (e.g. get all beams on Level 3)',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'level_name': {
                            'type': 'string',
                            'description': 'Level name (e.g. "Level 3", "Ground Floor")'
                        },
                        'category': {
                            'type': 'string',
                            'description': 'Revit category name filter: Walls, Beams, Floors, Columns, Doors, Windows. If omitted, returns all elements on the level.'
                        }
                    },
                    'required': ['level_name']
                }
            },
            # ── Query / read tools ────────────────────────────────────────────
            'get_current_view_info': {
                'name': 'get_current_view_info',
                'description': 'Get detailed info about the current active view including scale, view type, crop region, and discipline',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'get_current_view_elements': {
                'name': 'get_current_view_elements',
                'description': 'Get elements visible in the current active view, optionally filtered by category. Returns total_count (EXACT, uncapped — use it for any statistics) plus up to `limit` element entries.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Optional category filter: Walls, Floors, Doors, Windows, Rooms, Columns, Beams, etc.'
                        },
                        'limit': {
                            'type': 'integer',
                            'description': 'Max element entries listed in the reply (default 100). total_count in the result is always the exact uncapped count.'
                        }
                    },
                    'required': []
                }
            },
            'get_available_family_types': {
                'name': 'get_available_family_types',
                'description': 'Get available family types in the current project, optionally filtered by category',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Category to filter by (e.g. Doors, Windows, Furniture, Structural Framing)'
                        }
                    },
                    'required': []
                }
            },
            'get_material_quantities': {
                'name': 'get_material_quantities',
                'description': 'Calculate material quantities and takeoffs for elements in the model',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Category to calculate quantities for: Walls, Floors, Roofs, Ceilings. Default: Walls'
                        },
                        'level_name': {
                            'type': 'string',
                            'description': 'Optional: limit to a specific level'
                        }
                    },
                    'required': []
                }
            },
            'ai_element_filter': {
                'name': 'ai_element_filter',
                'description': (
                    'THE element finder. Combine any of category/categories, group_name, '
                    'workset, level_name, type_name, name_contains and a parameter filter in '
                    'ONE call — the server narrows with Revit\'s own filters, so this is far '
                    'faster than fetching a category and sifting it yourself, and it answers '
                    '"elements inside the groups named X" and "elements on workset Y", which '
                    'no other tool can. Returns total_count = the TRUE uncapped match count. '
                    'THREE ways to keep the reply small: `group_by` returns counts per '
                    'type/level/workset with NO element rows (use it for every "how many" / '
                    '"thống kê" / breakdown question); `fields` trims each row (fields=["id"] '
                    'returns a bare id list); `store_as="x"` keeps the FULL matched id set on '
                    'the server and returns just the handle "@x" — pass "@x" as element_ids to '
                    'set_element_workset, select_elements, bulk_set_parameter, '
                    'revit_override_color, color_elements, edit_elements, operate_element or '
                    'tag_elements instead of ferrying ids. At least one filter is required.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Element category to search: Walls, Floors, Doors, Windows, Rooms, Columns, Beams, Ceilings, Roofs, Grids, Levels, Sheets, TextNotes, Dimensions, Stairs, Railings, Pipes, Ducts, Furniture, CurtainPanels... (unknown names return the closest matches)'
                        },
                        'categories': {
                            'type': 'array', 'items': {'type': 'string'},
                            'description': 'Several categories in ONE call (e.g. ["Walls","Floors","Columns"]) instead of one call each. Combines with category.'
                        },
                        'group_name': {
                            'type': 'string',
                            'description': 'Return the elements INSIDE the model/detail groups whose instance name or group-type name contains this text (case-insensitive substring, e.g. "_F1_"). Nested groups are included. This is the only way to query group membership — there is no group filter anywhere else.'
                        },
                        'workset': {
                            'type': 'string',
                            'description': 'Only elements on worksets whose name contains this text (case-insensitive substring). Workshared models only.'
                        },
                        'level_name': {
                            'type': 'string',
                            'description': 'Only elements whose level name contains this text (case-insensitive substring). Works for host-based elements that report their level through a parameter.'
                        },
                        'type_name': {
                            'type': 'string',
                            'description': 'Only elements whose TYPE name contains this text (case-insensitive substring, e.g. "Generic - 200mm").'
                        },
                        'name_contains': {
                            'type': 'string',
                            'description': 'Only elements whose own name contains this text (case-insensitive substring).'
                        },
                        'parameter_name': {
                            'type': 'string',
                            'description': 'Optional parameter name to filter on'
                        },
                        'parameter_value': {
                            'type': 'string',
                            'description': 'Optional value to match (partial match supported)'
                        },
                        'group_by': {
                            'type': 'array',
                            'items': {'type': 'string',
                                      'enum': ['category', 'name', 'type',
                                               'level', 'workset', 'group']},
                            'description': 'Return COUNTS per combination of these keys instead of element rows — e.g. ["name","level"] for a Type x Level breakdown, ["workset"] for "how many elements per workset". total_count stays exact. Use this for any counting/statistics question: it costs a handful of rows instead of thousands.'
                        },
                        'fields': {
                            'type': 'array',
                            'items': {'type': 'string',
                                      'enum': ['id', 'name', 'category', 'level',
                                               'type', 'workset', 'group',
                                               'param_value', 'text', 'view']},
                            'description': 'Which fields each element row carries. Default ["id","name","category","level","param_value"]. Pass fields=["id"] to get a bare "ids" array — the cheapest listing there is.'
                        },
                        'store_as': {
                            'type': 'string',
                            'description': 'Save the FULL matched id set on the server under this name (no cap, no paging) and return the handle "@<name>" instead of the ids. Then call any element_ids tool with element_ids="@<name>". Sets last for this Revit session and this document.'
                        },
                        'limit': {
                            'type': 'integer',
                            'description': 'Max element rows to return (default 50). Ignored when group_by is used. Never raise this just to count — read total_count.'
                        },
                        'offset': {
                            'type': 'integer',
                            'description': 'Skip the first N matches (paging). When the result says truncated=true, call again with offset=offset+count until offset+count reaches total_count — never conclude from a truncated list. Prefer store_as or group_by over paging.'
                        }
                    },
                    'required': []
                }
            },
            'analyze_model_statistics': {
                'name': 'analyze_model_statistics',
                'description': 'Analyze model complexity with element counts by category and warnings summary',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            # ── Create tools ──────────────────────────────────────────────────
            'create_point_based_element': {
                'name': 'create_point_based_element',
                'description': 'Create a point-based element (door, window, furniture, fixture) at a specified XYZ location',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'family_type': {
                            'type': 'string',
                            'description': 'Family type name (e.g. "Single-Flush", "36x84")'
                        },
                        'x': {'type': 'number', 'description': 'X coordinate in meters'},
                        'y': {'type': 'number', 'description': 'Y coordinate in meters'},
                        'z': {'type': 'number', 'description': 'Z coordinate in meters (default 0)'},
                        'level_name': {'type': 'string', 'description': 'Level to place element on'},
                        'host_wall_id': {'type': 'integer', 'description': 'Optional host wall element ID for hosted families (doors/windows)'}
                    },
                    'required': ['family_type', 'x', 'y']
                }
            },
            'create_line_based_element': {
                'name': 'create_line_based_element',
                'description': 'Create a line-based element (beam, pipe, duct, structural framing) between two points',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'family_type': {'type': 'string', 'description': 'Family type name (e.g. "W-Wide Flange-Column:W10x33")'},
                        'start_x': {'type': 'number', 'description': 'Start X in meters'},
                        'start_y': {'type': 'number', 'description': 'Start Y in meters'},
                        'start_z': {'type': 'number', 'description': 'Start Z in meters (default 0)'},
                        'end_x': {'type': 'number', 'description': 'End X in meters'},
                        'end_y': {'type': 'number', 'description': 'End Y in meters'},
                        'end_z': {'type': 'number', 'description': 'End Z in meters (default 0)'},
                        'level_name': {'type': 'string', 'description': 'Reference level name'}
                    },
                    'required': ['family_type', 'start_x', 'start_y', 'end_x', 'end_y']
                }
            },
            'create_surface_based_element': {
                'name': 'create_surface_based_element',
                'description': 'Create a surface-based element (floor, ceiling, roof) from a list of boundary points',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_type': {
                            'type': 'string',
                            'description': 'What to create from the boundary.',
                            'enum': ['floor', 'ceiling', 'roof']
                        },
                        'boundary_points': {
                            'type': 'array',
                            'description': 'List of [x, y] pairs in meters defining the boundary polygon',
                            'items': {
                                'type': 'array',
                                'items': {'type': 'number'}
                            }
                        },
                        'level_name': {'type': 'string', 'description': 'Level name'},
                        'type_name': {'type': 'string', 'description': 'Floor/Ceiling/Roof type name (optional)'}
                    },
                    'required': ['element_type', 'boundary_points']
                }
            },
            'create_grid': {
                'name': 'create_grid',
                'description': 'Create a grid system with smart spacing — generates named horizontal and vertical grid lines',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'x_spacings': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'List of X-direction spacings in meters (e.g. [6, 6, 6] creates 4 vertical lines)'
                        },
                        'y_spacings': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'List of Y-direction spacings in meters'
                        },
                        'origin_x': {'type': 'number', 'description': 'Origin X in meters (default 0)'},
                        'origin_y': {'type': 'number', 'description': 'Origin Y in meters (default 0)'},
                        'x_labels': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Labels for vertical grid lines (e.g. ["A","B","C"]). Auto-generated if omitted.'
                        },
                        'y_labels': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Labels for horizontal grid lines (e.g. ["1","2","3"]). Auto-generated if omitted.'
                        }
                    },
                    'required': ['x_spacings', 'y_spacings']
                }
            },
            'create_room': {
                'name': 'create_room',
                'description': 'Create and place a room at a specified XY location on a level',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'x': {'type': 'number', 'description': 'X coordinate of room placement point in meters'},
                        'y': {'type': 'number', 'description': 'Y coordinate of room placement point in meters'},
                        'level_name': {'type': 'string', 'description': 'Level to place the room on'},
                        'name': {'type': 'string', 'description': 'Room name (e.g. "Office", "Kitchen")'},
                        'number': {'type': 'string', 'description': 'Room number (e.g. "101", "A-01")'}
                    },
                    'required': ['x', 'y']
                }
            },
            'create_structural_framing_system': {
                'name': 'create_structural_framing_system',
                'description': 'Create a structural beam framing system on a grid — places beams at specified bay spacings',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'x_bays': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'Bay spacings in X direction in meters'
                        },
                        'y_bays': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'Bay spacings in Y direction in meters'
                        },
                        'level_name': {'type': 'string', 'description': 'Level to place beams on'},
                        'beam_type': {'type': 'string', 'description': 'Structural framing family type name (optional)'},
                        'origin_x': {'type': 'number', 'description': 'Origin X in meters (default 0)'},
                        'origin_y': {'type': 'number', 'description': 'Origin Y in meters (default 0)'}
                    },
                    'required': ['x_bays', 'y_bays']
                }
            },
            # ── Modify / delete tools ─────────────────────────────────────────
            'delete_element': {
                'name': 'delete_element',
                'description': ('Delete one or more Revit elements by their IDs. Deletion cascades '
                                '(deleting a wall removes its hosted doors/windows), so prefer a '
                                'dry_run=true preview first to see everything that would be removed.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'List of element IDs to delete'
                        },
                        'dry_run': {
                            'type': 'boolean',
                            'description': 'If true, do NOT delete — return the full list of elements that WOULD be removed (including cascade deletions). Default false.'
                        }
                    },
                    'required': ['element_ids']
                }
            },
            'operate_element': {
                'name': 'operate_element',
                'description': 'Act on elements IN THE ACTIVE VIEW without changing the model itself: select, hide, isolate, unhide, reset_color, pin, unpin, halftone, unhalftone, transparency, hide_category, unhide_category, reset_temporary, select_similar. This is the ONLY tool that pins/locks elements ("pin", "ghim", "khoá tường") and the ONLY way back out of isolate ("reset_temporary") — none of these are color operations. Pass category to act on ALL elements of that category (no ids needed, no count limit), or element_ids for a specific subset. To CHANGE the model (mirror, swap type, group) use edit_elements instead.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'description': (
                                'What to do: "select" · "hide" · "isolate" (temporary) · '
                                '"unhide" · "reset_temporary" (exit temporary hide/isolate — '
                                'the way back from isolate, needs no target) · '
                                '"hide_category"/"unhide_category" (whole category in the '
                                'view V/G, needs `category`) · "reset_color" (clear graphic '
                                'overrides) · "pin"/"unpin" (lock elements in place) · '
                                '"halftone"/"unhalftone" · "transparency" (needs '
                                '`transparency` 0-100) · "select_similar" (select everything '
                                'matching the seed elements, see `match`/`scope`)'),
                            'enum': ['select', 'hide', 'isolate', 'unhide',
                                     'reset_color', 'pin', 'unpin',
                                     'halftone', 'unhalftone', 'transparency',
                                     'hide_category', 'unhide_category',
                                     'reset_temporary', 'select_similar']
                        },
                        'category': {
                            'type': 'string',
                            'description': 'Optional category name (Walls, Floors, Doors, StructuralFoundations, Ducts, ...). When given and element_ids is omitted, the operation applies to EVERY element of this category visible in the active view — no count limit. REQUIRED for hide_category / unhide_category.'
                        },
                        'element_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs for a specific subset (omit when using category). For select_similar these are the SEED elements; omit to seed from the current selection.'
                        },
                        'transparency': {
                            'type': 'integer',
                            'description': 'For operation "transparency": 0 (opaque) to 100 (invisible).'
                        },
                        'match': {
                            'type': 'string',
                            'description': 'For select_similar: what counts as "similar". Default "type".',
                            'enum': ['category', 'family', 'type']
                        },
                        'scope': {
                            'type': 'string',
                            'description': 'For select_similar: search the active view only or the whole model. Default "view".',
                            'enum': ['view', 'model']
                        }
                    },
                    'required': ['operation']
                }
            },
            'color_elements': {
                'name': 'color_elements',
                'description': 'Color-CODE elements in the active view BY a parameter\'s values — each unique value gets a distinct auto-assigned color (legend style). NOT for applying one specific color: for "color/tô walls red" use revit_override_color instead.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Element category to color — any supported category name (Walls, Floors, Doors, Windows, Rooms, Columns, Ceilings, Roofs, ...)'
                        },
                        'parameter_name': {
                            'type': 'string',
                            'description': 'Parameter name to group colors by (e.g. "Type Name", "Level"). Must be a real existing parameter — never an empty string, never a color name.'
                        }
                    },
                    'required': ['category', 'parameter_name']
                }
            },
            'edit_elements': {
                'name': 'edit_elements',
                'description': 'Structurally EDIT elements in the model: mirror, change_type (swap to another family type), group, ungroup. Unlike operate_element (which only changes how things look in the active view) these change the model itself. Pass category to act on every element of that category in the active view, or element_ids for a specific subset.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'description': (
                                '"mirror" (needs `axis`; set `copy` true to keep the '
                                'original) · "change_type" (needs `type_name`, swaps '
                                'the elements to that type — "đổi tất cả cửa sang '
                                'type X") · "group" (make one Revit group of the '
                                'targets) · "ungroup" (dissolve the targeted groups; '
                                'NOT undoable by re-grouping)'),
                            'enum': ['mirror', 'change_type', 'group', 'ungroup']
                        },
                        'category': {
                            'type': 'string',
                            'description': 'Category name — acts on EVERY element of it in the active view. Omit when passing element_ids.'
                        },
                        'element_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs for a specific subset. Omit to use `category`, or omit both to use the current Revit selection.'
                        },
                        'axis': {
                            'type': 'string',
                            'description': 'For mirror: which vertical plane to mirror across. "x" flips left/right, "y" flips front/back.',
                            'enum': ['x', 'y']
                        },
                        'origin': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'For mirror: [x, y] in METERS the mirror plane passes through. Default [0, 0].'
                        },
                        'copy': {
                            'type': 'boolean',
                            'description': 'For mirror: true keeps the originals and creates mirrored copies. Default false (move).'
                        },
                        'type_name': {
                            'type': 'string',
                            'description': 'For change_type: the target type name, as reported by get_available_family_types.'
                        },
                        'family_name': {
                            'type': 'string',
                            'description': 'For change_type: optional family name, to disambiguate when the same type name exists in several families.'
                        },
                        'group_name': {
                            'type': 'string',
                            'description': 'For group: optional name for the new group.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_view': {
                'name': 'manage_view',
                'description': 'Read or set view PROPERTIES: scale, detail level, discipline, crop box. Targets the active view when view_ids is omitted. To create a view use create_view; to apply a template use apply_view_template.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'description': 'What to do. "get_properties" reads them back.',
                            'enum': ['get_properties', 'set_scale',
                                     'set_detail_level', 'set_discipline',
                                     'set_crop']
                        },
                        'view_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'View element IDs. Omit to target the ACTIVE view.'
                        },
                        'scale': {
                            'type': 'integer',
                            'description': 'For set_scale: the denominator, e.g. 100 for 1:100.'
                        },
                        'detail_level': {
                            'type': 'string',
                            'enum': ['coarse', 'medium', 'fine'],
                            'description': 'For set_detail_level.'
                        },
                        'discipline': {
                            'type': 'string',
                            'enum': ['architectural', 'structural', 'mechanical',
                                     'electrical', 'plumbing', 'coordination'],
                            'description': 'For set_discipline.'
                        },
                        'crop_active': {
                            'type': 'boolean',
                            'description': 'For set_crop: turn the crop region on/off.'
                        },
                        'crop_visible': {
                            'type': 'boolean',
                            'description': 'For set_crop: show/hide the crop boundary.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_view_template': {
                'name': 'manage_view_template',
                'description': 'Curate the view TEMPLATE library: list, rename, duplicate, delete, and check usage counts. To APPLY a template to views use apply_view_template instead — this tool manages the templates themselves.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'description': '"usage" reports how many views use each template — run it before delete.',
                            'enum': ['list', 'usage', 'rename', 'duplicate', 'delete']
                        },
                        'template_name': {
                            'type': 'string',
                            'description': 'Template to act on (rename / duplicate / delete).'
                        },
                        'template_id': {
                            'type': 'integer',
                            'description': 'Template element ID, as an alternative to template_name.'
                        },
                        'new_name': {
                            'type': 'string',
                            'description': 'For rename / duplicate: the new template name.'
                        },
                        'force': {
                            'type': 'boolean',
                            'description': 'For delete: allow deleting a template that views still use. Default false — deleting an in-use template silently changes every view that referenced it.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_links': {
                'name': 'manage_links',
                'description': 'Inspect and manage linked models and CAD links: list, reload, unload, delete, pin, unpin. "list" is the right tool for "có bao nhiêu link", "link nào chưa load".',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'description': '"unload" keeps the link but drops its geometry; "delete" REMOVES the link and everything that depends on it (tags, dimensions).',
                            'enum': ['list', 'reload', 'unload', 'delete',
                                     'pin', 'unpin']
                        },
                        'link_kind': {
                            'type': 'string',
                            'description': 'Which links to consider. Default "all".',
                            'enum': ['revit', 'cad', 'all']
                        },
                        'link_names': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Link names (as reported by "list"). Required for every operation except list.'
                        },
                        'link_ids': {
                            'type': 'array',
                            'items': {'type': 'integer'},
                            'description': 'Link type element IDs, as an alternative to link_names.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_revision': {
                'name': 'manage_revision',
                'description': 'Manage project revisions: list them, create one, assign a revision to sheets, or mark it issued.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'enum': ['list', 'create', 'assign_to_sheets',
                                     'set_issued'],
                            'description': 'What to do.'
                        },
                        'revision_id': {
                            'type': 'integer',
                            'description': 'Revision element ID (assign_to_sheets / set_issued). Omit to use the most recent revision.'
                        },
                        'description': {
                            'type': 'string',
                            'description': 'For create: the revision description.'
                        },
                        'date': {
                            'type': 'string',
                            'description': 'For create: the revision date, free text as Revit stores it.'
                        },
                        'issued_by': {'type': 'string', 'description': 'For create / set_issued.'},
                        'issued_to': {'type': 'string', 'description': 'For create / set_issued.'},
                        'issued': {
                            'type': 'boolean',
                            'description': 'For set_issued: true marks the revision issued (Revit then locks its clouds). Default true.'
                        },
                        'sheet_numbers': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'For assign_to_sheets: sheet numbers, e.g. ["A-101", "A-102"].'
                        },
                        'sheet_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'For assign_to_sheets: sheet element IDs, as an alternative to sheet_numbers.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_sheet': {
                'name': 'manage_sheet',
                'description': 'Sheet operations beyond creation: duplicate a sheet, renumber sheets, and list the saved print sets (ViewSheetSets). To create a sheet use create_sheet; to place views use add_view_to_sheet / place_views_on_sheets.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'enum': ['duplicate', 'renumber', 'list_sets'],
                            'description': 'What to do.'
                        },
                        'sheet_numbers': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Sheets to act on, by sheet number.'
                        },
                        'sheet_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'Sheets to act on, by element ID.'
                        },
                        'new_number': {
                            'type': 'string',
                            'description': 'For renumber with exactly ONE sheet: its new sheet number.'
                        },
                        'prefix': {
                            'type': 'string',
                            'description': 'For renumber of several sheets: new number = prefix + running index.'
                        },
                        'start_at': {
                            'type': 'integer',
                            'description': 'For renumber with a prefix: first index. Default 1.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'export_model': {
                'name': 'export_model',
                'description': 'Export the model to IFC, NWC (Navisworks), DWF or DGN. For PDF use export_sheets_pdf, for DWG use export_dwg, for PNG use export_image. IFC and NWC need their Autodesk exporter add-in installed — the tool says so plainly if it is missing.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'format': {
                            'type': 'string',
                            'enum': ['ifc', 'nwc', 'dwf', 'dgn'],
                            'description': 'Export format. IFC and NWC cover the whole model; DWF and DGN export sheets or views.'
                        },
                        'output_folder': {
                            'type': 'string',
                            'description': 'Destination folder. Defaults to the folder the model lives in.'
                        },
                        'filename': {
                            'type': 'string',
                            'description': 'Base file name without extension. Defaults to the model name.'
                        },
                        'sheet_numbers': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'For dwf / dgn: which sheets to export. Defaults to the active view.'
                        },
                        'ifc_version': {
                            'type': 'string',
                            'enum': ['IFC2x3', 'IFC4'],
                            'description': 'For ifc: schema version. Default IFC2x3.'
                        }
                    },
                    'required': ['format']
                }
            },
            'check_bad_geometry': {
                'name': 'check_bad_geometry',
                'description': 'Scan a view for DEGENERATE GEOMETRY that crashes Revit during PDF/DWG export: zero-area faces, slivers, pathological UV domains, zero normals and singular points. This is the tool for "vì sao export PDF crash", "tìm hình học lỗi". Read-only.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'Views to scan. Omit to scan the ACTIVE view.'
                        },
                        'category': {
                            'type': 'string',
                            'description': 'Optional: only probe this category, to narrow a big scan.'
                        },
                        'deep_probe': {
                            'type': 'boolean',
                            'description': 'DANGEROUS. Also calls Face.Triangulate(), which is the exact code path that crashes Revit — it can take the session down. Default false; only set it when a normal scan came back clean and the export still crashes.'
                        },
                        'limit': {
                            'type': 'integer',
                            'description': 'Maximum number of suspect elements to report. Default 40.'
                        }
                    },
                    'required': []
                }
            },
            'manage_material': {
                'name': 'manage_material',
                'description': 'Read materials: list the materials in the project, or report which materials given elements use. Read-only — to ASSIGN a material, take the material id from "list" and use bulk_set_parameter on the relevant material parameter.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'enum': ['list', 'get_element_materials'],
                            'description': 'What to read.'
                        },
                        'name_filter': {
                            'type': 'string',
                            'description': 'For list: only materials whose name contains this text (case-insensitive).'
                        },
                        'element_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'For get_element_materials: which elements to inspect.'
                        },
                        'category': {
                            'type': 'string',
                            'description': 'For get_element_materials: inspect every element of this category in the active view instead of passing ids.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'create_detail_annotation': {
                'name': 'create_detail_annotation',
                'description': 'Draw view-specific 2D detail annotation: a filled region or a detail line. All coordinates are in METERS. These are drafting elements — they exist only in the view they are drawn in.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'enum': ['filled_region', 'detail_line'],
                            'description': 'What to draw.'
                        },
                        'view_id': {
                            'type': 'integer',
                            'description': 'View to draw in. Omit to use the ACTIVE view.'
                        },
                        'boundary_points': {
                            'type': 'array',
                            'items': {'type': 'array', 'items': {'type': 'number'}},
                            'description': 'For filled_region: [[x, y], ...] in METERS, at least 3 points. The loop is closed automatically.'
                        },
                        'start': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'For detail_line: [x, y] start point in METERS.'
                        },
                        'end': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'For detail_line: [x, y] end point in METERS.'
                        },
                        'type_name': {
                            'type': 'string',
                            'description': 'For filled_region: filled region type name. Defaults to the project default.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'manage_document': {
                'name': 'manage_document',
                'description': 'Save the model, save it under a new name, or synchronise with central. sync_with_central PUBLISHES YOUR WORK TO THE WHOLE TEAM, can run for minutes and relinquishes worksets — it is disabled by default and the user must enable it in settings. Never call save_as or sync_with_central without the user asking for it in their current message.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'operation': {
                            'type': 'string',
                            'enum': ['save', 'save_as', 'sync_with_central'],
                            'description': '"save" writes the current file. "save_as" needs `path`. "sync_with_central" needs `comment` and only works on a workshared model.'
                        },
                        'path': {
                            'type': 'string',
                            'description': 'For save_as: full destination path ending in .rvt.'
                        },
                        'overwrite': {
                            'type': 'boolean',
                            'description': 'For save_as: allow replacing an existing file. Default false.'
                        },
                        'comment': {
                            'type': 'string',
                            'description': 'For sync_with_central: the synchronisation comment. Required, and it goes into the team\'s history.'
                        },
                        'relinquish': {
                            'type': 'boolean',
                            'description': 'For sync_with_central: relinquish the worksets you own afterwards. Default true.'
                        }
                    },
                    'required': ['operation']
                }
            },
            'tag_all_walls': {
                'name': 'tag_all_walls',
                'description': 'Automatically tag all walls in the current active view',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'tag_type': {
                            'type': 'string',
                            'description': 'Wall tag family type name (optional, uses first available)'
                        },
                        'leader': {
                            'type': 'boolean',
                            'description': 'Add tag leader line (default false)'
                        }
                    },
                    'required': []
                }
            },
            'tag_all_rooms': {
                'name': 'tag_all_rooms',
                'description': 'Automatically tag all rooms in the current active view',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'tag_type': {
                            'type': 'string',
                            'description': 'Room tag family type name (optional)'
                        }
                    },
                    'required': []
                }
            },
            # ── Data / storage tools ──────────────────────────────────────────
            'export_room_data': {
                'name': 'export_room_data',
                'description': 'Export all room data from the project (number, name, area, level, department) as structured JSON',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'level_name': {
                            'type': 'string',
                            'description': 'Optional: filter rooms by level name'
                        }
                    },
                    'required': []
                }
            },
            'store_project_data': {
                'name': 'store_project_data',
                'description': 'Store current project metadata (name, number, client, address, status) to a local JSON file for later querying',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'store_room_data': {
                'name': 'store_room_data',
                'description': 'Store all room metadata to a local JSON file in the project folder',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'query_stored_data': {
                'name': 'query_stored_data',
                'description': 'Query previously stored project and room data from local JSON files',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'data_type': {
                            'type': 'string',
                            'description': 'Which stored dataset to read back.',
                            'enum': ['project', 'rooms']
                        }
                    },
                    'required': ['data_type']
                }
            },
            # ── Utility tools ─────────────────────────────────────────────────
            'send_code_to_revit': {
                'name': 'send_code_to_revit',
                'description': ('Execute IronPython code directly in the Revit context. Use with care — full Revit API access. '
                                'Return data by assigning to `result` (or output.append(...)); print() is captured into the '
                                'tool result, and code must NEVER open dialogs (TaskDialog/MessageBox/forms).'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'code': {
                            'type': 'string',
                            'description': ('IronPython 2.7 code to execute. Has access to doc, uidoc, app, and the variables '
                                            '`result` / `output` (a list). Assign your answer to `result` or output.append(...) — '
                                            'do NOT print for display and do NOT show any dialog/window.')
                        }
                    },
                    'required': ['code']
                }
            },
            'say_hello': {
                'name': 'say_hello',
                'description': 'Display a greeting TaskDialog in Revit — use to verify MCP connection is working',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'message': {
                            'type': 'string',
                            'description': 'Message to display (default: "Hello from T3Lab AI!")'
                        }
                    },
                    'required': []
                }
            },
            # ── Parameter writing ─────────────────────────────────────────────
            'set_parameter': {
                'name': 'set_parameter',
                'description': 'Set the value of a named parameter on a Revit element',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {'type': 'integer', 'description': 'Revit element ID'},
                        'parameter_name': {'type': 'string', 'description': 'Parameter name to set'},
                        'value': {'type': 'string', 'description': 'New value as string (will be converted to appropriate type)'}
                    },
                    'required': ['element_id', 'parameter_name', 'value']
                }
            },
            'get_all_parameters': {
                'name': 'get_all_parameters',
                'description': 'Get all parameters (name, value, type, read-only flag) for a given element',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {'type': 'integer', 'description': 'Revit element ID'}
                    },
                    'required': ['element_id']
                }
            },
            # ── Spatial transforms ────────────────────────────────────────────
            'move_elements': {
                'name': 'move_elements',
                'description': 'Move one or more elements by a delta XYZ vector',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_ids': {
                            'type': 'array', 'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs to move'
                        },
                        'dx': {'type': 'number', 'description': 'Delta X in meters'},
                        'dy': {'type': 'number', 'description': 'Delta Y in meters'},
                        'dz': {'type': 'number', 'description': 'Delta Z in meters (default 0)'}
                    },
                    'required': ['element_ids', 'dx', 'dy']
                }
            },
            'copy_elements': {
                'name': 'copy_elements',
                'description': 'Copy elements and translate the copies by a delta XYZ vector',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_ids': {
                            'type': 'array', 'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs to copy'
                        },
                        'dx': {'type': 'number', 'description': 'Delta X in meters'},
                        'dy': {'type': 'number', 'description': 'Delta Y in meters'},
                        'dz': {'type': 'number', 'description': 'Delta Z in meters (default 0)'}
                    },
                    'required': ['element_ids', 'dx', 'dy']
                }
            },
            'rotate_element': {
                'name': 'rotate_element',
                'description': 'Rotate elements around a Z-axis through a given origin point',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_ids': {
                            'type': 'array', 'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs to rotate'
                        },
                        'angle_degrees': {'type': 'number', 'description': 'Rotation angle in degrees (counter-clockwise)'},
                        'origin_x': {'type': 'number', 'description': 'Rotation axis origin X in meters (default 0)'},
                        'origin_y': {'type': 'number', 'description': 'Rotation axis origin Y in meters (default 0)'}
                    },
                    'required': ['element_ids', 'angle_degrees']
                }
            },
            'get_element_bounding_box': {
                'name': 'get_element_bounding_box',
                'description': 'Get the bounding box (min/max XYZ in meters) of a Revit element',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {'type': 'integer', 'description': 'Revit element ID'}
                    },
                    'required': ['element_id']
                }
            },
            # ── View management ───────────────────────────────────────────────
            'create_view': {
                'name': 'create_view',
                'description': 'Create a new view: plan (floor / ceiling / structural / area), 3D isometric, section, elevation, drafting view or legend.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_type': {
                            'type': 'string',
                            'description': 'What kind of view to create. "legend" duplicates an existing legend (Revit cannot create one from scratch); "area_plan" needs an Area Scheme in the project.',
                            'enum': ['floor_plan', 'ceiling_plan', 'structural_plan',
                                     'drafting', '3d', 'section', 'elevation',
                                     'area_plan', 'legend']
                        },
                        'level_name': {
                            'type': 'string',
                            'description': 'Level name — used by floor / ceiling / structural / area plans, and to pick the host plan for an elevation. Defaults to the first level.'
                        },
                        'name': {'type': 'string', 'description': 'Name for the new view'},
                        'room_ids': {
                            'type': 'array',
                            'items': {'type': ['integer', 'string']},
                            'description': 'Room element ids. Creates ONE 3D view per room, each cropped to that room by a section box (view_type must be "3d"; `name` is ignored, views are named "3D - <number> <room name>"). There is deliberately NO "all rooms" default — get the ids from ai_element_filter(category="Rooms") or revit_get_selected_elements first, and confirm the count with the user: one view per room in a large model means dozens of views.'
                        },
                        'margin_mm': {
                            'type': 'number',
                            'description': 'Clearance added around each room bounding box, in MILLIMETRES (default 0). Only used together with room_ids.'
                        }
                    },
                    'required': ['view_type']
                }
            },
            'set_active_view': {
                'name': 'set_active_view',
                'description': 'Switch the active view in Revit by view name or element ID',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_name': {'type': 'string', 'description': 'View name to activate'},
                        'view_id': {'type': 'integer', 'description': 'View element ID (alternative to view_name)'}
                    },
                    'required': []
                }
            },
            'rename_element': {
                'name': 'rename_element',
                'description': 'Rename a view, sheet, level, or any named Revit element',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {'type': 'integer', 'description': 'Element ID to rename'},
                        'new_name': {'type': 'string', 'description': 'New name for the element'}
                    },
                    'required': ['element_id', 'new_name']
                }
            },
            # ── Sheet management ──────────────────────────────────────────────
            'create_sheet': {
                'name': 'create_sheet',
                'description': 'Create a new drawing sheet with a title block',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sheet_number': {'type': 'string', 'description': 'Sheet number (e.g. "A-101")'},
                        'sheet_name': {'type': 'string', 'description': 'Sheet title (e.g. "Ground Floor Plan")'},
                        'title_block': {'type': 'string', 'description': 'Title block family type name (optional, uses first available)'}
                    },
                    'required': ['sheet_number', 'sheet_name']
                }
            },
            'add_view_to_sheet': {
                'name': 'add_view_to_sheet',
                'description': 'Place a view as a viewport on an existing sheet',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sheet_id': {'type': 'integer', 'description': 'Sheet element ID'},
                        'view_id': {'type': 'integer', 'description': 'View element ID to place'},
                        'x': {'type': 'number', 'description': 'Viewport center X on sheet in mm (default 297 = center A3)'},
                        'y': {'type': 'number', 'description': 'Viewport center Y on sheet in mm (default 210)'}
                    },
                    'required': ['sheet_id', 'view_id']
                }
            },
            # ── Annotation ────────────────────────────────────────────────────
            'create_text_note': {
                'name': 'create_text_note',
                'description': 'Add a text annotation to the active view at a specified location',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'text': {'type': 'string', 'description': 'Text content'},
                        'x': {'type': 'number', 'description': 'X position in meters (model coordinates)'},
                        'y': {'type': 'number', 'description': 'Y position in meters'},
                        'font_size': {'type': 'number', 'description': 'Text height in mm (default 3.5)'},
                        'text_type': {'type': 'string', 'description': 'Text note type name (optional)'},
                        # The dispatch has honoured this since it was written,
                        # but it was never declared — so the only way to reach
                        # it was to guess. Undeclared = unreachable.
                        'sheet_number': {'type': 'string', 'description': 'Place the note on this sheet (e.g. "A-101") instead of the active view.'}
                    },
                    'required': ['text', 'x', 'y']
                }
            },
            # ── Model quality ─────────────────────────────────────────────────
            'get_model_warnings': {
                'name': 'get_model_warnings',
                'description': 'Get all Revit model warnings with descriptions and affected element IDs',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'limit': {'type': 'integer', 'description': 'Max warnings to return (default 50)'}
                    },
                    'required': []
                }
            },
            'get_model_health': {
                'name': 'get_model_health',
                'description': 'Get model health summary: warning count, element count, linked files, unused families',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            # ── Collaboration / worksets ──────────────────────────────────────
            'list_worksets': {
                'name': 'list_worksets',
                'description': 'List user worksets in a workshared model with their open/close status. Pass name_contains to get only the ones you are looking for instead of the whole list.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'name_contains': {
                            'type': 'string',
                            'description': 'Only worksets whose name contains this text (case-insensitive substring, e.g. "_F1_"). total_count still reports how many worksets the model has.'
                        }
                    },
                    'required': []
                }
            },
            'set_element_workset': {
                'name': 'set_element_workset',
                'description': 'Move elements to a specified workset. Pass category to move ALL elements of that category (no ids needed, no count limit), or element_ids for a subset. For any other selection — the elements of a named group, a level, a type — call ai_element_filter with store_as="x" first and pass element_ids="@x" here.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {
                            'type': 'string',
                            'description': 'Optional category name (Walls, Floors, ...). When given and element_ids is omitted, EVERY element of this category in the project is moved — no count limit.'
                        },
                        'element_ids': {
                            'type': 'array', 'items': {'type': ['integer', 'string']},
                            'description': 'Element IDs for a specific subset (omit when using category). Also accepts a one-entry list ["@name"] naming a set saved by ai_element_filter(store_as="name") — that moves every element in the set, with no id list and no count limit.'
                        },
                        'workset_name': {'type': 'string', 'description': 'Target workset name'}
                    },
                    'required': ['workset_name']
                }
            },
            # ── Datum / navigation ────────────────────────────────────────────
            'list_levels': {
                'name': 'list_levels',
                'description': 'List all levels in the project with their elevations in meters',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            # ── Family management ─────────────────────────────────────────────
            'load_family': {
                'name': 'load_family',
                'description': 'Load a .rfa family file into the current project from a local file path',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'file_path': {
                            'type': 'string',
                            'description': 'Absolute path to the .rfa family file'
                        }
                    },
                    'required': ['file_path']
                }
            },
            # ── Geometry editing ──────────────────────────────────────────────
            'split_curve': {
                'name': 'split_curve',
                'description': ('Split a model or detail curve (line, arc, ellipse or spline) into '
                                'segments while preserving the EXACT original geometry — arcs stay '
                                'arcs, splines stay splines (never flattened to straight lines). '
                                'Provide either "segments" for equal-length division or '
                                '"split_at_ratios" for explicit cut positions.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {
                            'type': 'integer',
                            'description': 'Element ID of the model curve or detail curve to split'
                        },
                        'segments': {
                            'type': 'integer',
                            'description': 'Number of equal-length pieces to split into (default 2, min 2, max 200)'
                        },
                        'split_at_ratios': {
                            'type': 'array',
                            'items': {'type': 'number'},
                            'description': 'Optional explicit cut positions as normalized fractions 0..1 along the curve (e.g. [0.25, 0.5]). Overrides "segments".'
                        }
                    },
                    'required': ['element_id']
                }
            },
            'split_element': {
                'name': 'split_element',
                'description': ('Split a location-curve element (wall, beam, pipe, duct, model line) '
                                'into two at a point or ratio, preserving the exact curve geometry.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id': {'type': 'integer', 'description': 'Element ID with a location curve (wall/beam/pipe/etc.)'},
                        'at_ratio': {'type': 'number', 'description': 'Split position as a fraction 0..1 along the curve (default 0.5)'},
                        'x': {'type': 'number', 'description': 'Optional split point X in meters (overrides at_ratio)'},
                        'y': {'type': 'number', 'description': 'Optional split point Y in meters (used with x)'}
                    },
                    'required': ['element_id']
                }
            },
            'join_geometry': {
                'name': 'join_geometry',
                'description': 'Join (or unjoin) the geometry of two elements, e.g. wall-to-floor or wall-to-column.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_id_a': {'type': 'integer', 'description': 'First element ID'},
                        'element_id_b': {'type': 'integer', 'description': 'Second element ID'},
                        'unjoin': {'type': 'boolean', 'description': 'If true, unjoin instead of join (default false)'}
                    },
                    'required': ['element_id_a', 'element_id_b']
                }
            },
            # ── Bulk parameter / selection / tagging ──────────────────────────
            'bulk_set_parameter': {
                'name': 'bulk_set_parameter',
                'description': ('Set one parameter to the same value across MANY elements of a category, '
                                'optionally narrowed by a filter parameter/value substring match.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {'type': 'string', 'description': 'Category to target (e.g. Walls, Doors, Rooms)'},
                        'parameter_name': {'type': 'string', 'description': 'Parameter to set on each element'},
                        'value': {'type': 'string', 'description': 'New value (string; coerced to the parameter storage type)'},
                        'filter_parameter': {'type': 'string', 'description': 'Optional parameter to filter elements by before setting'},
                        'filter_value': {'type': 'string', 'description': 'Optional value substring the filter_parameter must contain'},
                        'element_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'Optional explicit element IDs (overrides category collection)'},
                        'limit': {'type': 'integer', 'description': 'Optional cap on elements to modify. Omit (default) for NO limit — every matching element is modified.'}
                    },
                    'required': ['parameter_name', 'value']
                }
            },
            'select_elements': {
                'name': 'select_elements',
                'description': 'Set the Revit selection to elements matched by category + optional parameter filter, or explicit IDs.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {'type': 'string', 'description': 'Category to select (e.g. Walls, Doors)'},
                        'parameter_name': {'type': 'string', 'description': 'Optional parameter to filter on'},
                        'parameter_value': {'type': 'string', 'description': 'Optional value substring to match'},
                        'element_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'Optional explicit IDs to select (overrides category)'},
                        'add_to_selection': {'type': 'boolean', 'description': 'Add to the current selection instead of replacing (default false)'},
                        'limit': {'type': 'integer', 'description': 'Optional cap on selection size. Omit (default) for NO limit — every match is selected.'},
                        'show': {'type': 'boolean', 'description': 'Also zoom the view onto the selected elements (default false)'}
                    },
                    'required': []
                }
            },
            'tag_elements': {
                'name': 'tag_elements',
                'description': 'Tag all elements of a category in the active view (generic version of tag_all_walls/rooms).',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {'type': 'string', 'description': 'Category to tag (e.g. Doors, Windows, Walls, Rooms)'},
                        'tag_type': {'type': 'string', 'description': 'Tag family type name (optional, uses first available)'},
                        'leader': {'type': 'boolean', 'description': 'Add tag leader line (default false)'}
                    },
                    'required': ['category']
                }
            },
            'create_dimension': {
                'name': 'create_dimension',
                'description': ('Create an aligned dimension across a set of grids (or elements with a '
                                'straight location curve) in the active view.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'element_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'IDs of grids (or line-based elements) to dimension between — at least 2'},
                        'offset': {'type': 'number', 'description': 'Perpendicular offset of the dimension line in meters (default 1.0)'}
                    },
                    'required': ['element_ids']
                }
            },
            # ── Schedules ─────────────────────────────────────────────────────
            'get_schedule_data': {
                'name': 'get_schedule_data',
                'description': 'Read a schedule (ViewSchedule) as a JSON table. Returns EXACT row_count and column_totals computed over the full schedule — always use those for counts/sums instead of adding up rows.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'schedule_name': {'type': 'string', 'description': 'Schedule name (partial match allowed)'},
                        'schedule_id': {'type': 'integer', 'description': 'Schedule element ID (alternative to name)'},
                        'limit': {'type': 'integer', 'description': 'Max data rows to return (default 200)'}
                    },
                    'required': []
                }
            },
            'create_schedule': {
                'name': 'create_schedule',
                'description': 'Create a new schedule for a category with an optional explicit field list.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'category': {'type': 'string', 'description': 'Category to schedule (e.g. Walls, Doors, Rooms)'},
                        'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Parameter names to add as columns (optional; adds a sensible default set if omitted)'},
                        'name': {'type': 'string', 'description': 'Schedule name (optional)'}
                    },
                    'required': ['category']
                }
            },
            # ── View / sheet automation ───────────────────────────────────────
            'duplicate_view': {
                'name': 'duplicate_view',
                'description': 'Duplicate a view (plain, with detailing, or as a dependent view).',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_id': {'type': 'integer', 'description': 'View element ID to duplicate'},
                        'mode': {'type': 'string',
                                 'description': 'How to duplicate. Default "plain".',
                                 'enum': ['plain', 'with_detailing', 'dependent']},
                        'name': {'type': 'string', 'description': 'Name for the new view (optional)'}
                    },
                    'required': ['view_id']
                }
            },
            'apply_view_template': {
                'name': 'apply_view_template',
                'description': 'Apply a view template to one or more views.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'View IDs to apply the template to'},
                        'template_name': {'type': 'string', 'description': 'View template name (partial match allowed)'}
                    },
                    'required': ['view_ids', 'template_name']
                }
            },
            'create_view_filter': {
                'name': 'create_view_filter',
                'description': ('Create a rule-based view filter and apply it to a view, optionally hiding '
                                'matching elements or overriding their color.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Filter name'},
                        'categories': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Categories the filter applies to (e.g. ["Walls"])'},
                        'parameter_name': {'type': 'string', 'description': 'Optional parameter to build a "contains" rule on'},
                        'parameter_value': {'type': 'string', 'description': 'Optional value the parameter must contain'},
                        'view_id': {'type': 'integer', 'description': 'View to apply the filter to (default active view)'},
                        'hide': {'type': 'boolean', 'description': 'Hide matching elements (default false)'},
                        'color': {'type': 'string', 'description': 'Optional hex/CSS color to override matching elements'}
                    },
                    'required': ['name', 'categories']
                }
            },
            'place_views_on_sheets': {
                'name': 'place_views_on_sheets',
                'description': 'Place one or more views onto sheets as viewports (one view per sheet by default).',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'View IDs to place'},
                        'title_block': {'type': 'string', 'description': 'Title block family type name (optional)'},
                        'sheet_id': {'type': 'integer', 'description': 'Existing sheet to place all views on (optional; otherwise a sheet is created per view)'}
                    },
                    'required': ['view_ids']
                }
            },
            'export_dwg': {
                'name': 'export_dwg',
                'description': 'Export sheets or views to DWG files in a folder.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sheet_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'Sheet IDs to export'},
                        'view_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'View IDs to export (used if sheet_ids omitted)'},
                        'output_folder': {'type': 'string', 'description': 'Destination folder (default: same folder as the .rvt)'}
                    },
                    'required': []
                }
            },
            'export_image': {
                'name': 'export_image',
                'description': 'Export a view (or the active view) to a PNG image.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'view_id': {'type': 'integer', 'description': 'View to export (default active view)'},
                        'output_folder': {'type': 'string', 'description': 'Destination folder (default: same folder as the .rvt)'},
                        'width': {'type': 'integer', 'description': 'Pixel width of the image (default 1600)'}
                    },
                    'required': []
                }
            },
            # ── Standards / model management ──────────────────────────────────
            'create_project_parameter': {
                'name': 'create_project_parameter',
                'description': ('Create a project parameter bound to one or more categories using a shared '
                                'parameter file (created automatically if absent).'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Parameter name'},
                        'categories': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Categories to bind to (e.g. ["Walls","Doors"])'},
                        'type': {'type': 'string', 'description': 'Parameter data type: Text (default), Integer, Number, Length, Area, YesNo'},
                        'group': {'type': 'string', 'description': 'Parameter group name (optional, default "Data")'},
                        'instance': {'type': 'boolean', 'description': 'Instance binding (default true) vs type binding'}
                    },
                    'required': ['name', 'categories']
                }
            },
            'room_to_floor': {
                'name': 'room_to_floor',
                'description': 'Create a floor matching the boundary of one or more rooms.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'room_ids': {'type': 'array', 'items': {'type': ['integer', 'string']}, 'description': 'Room element IDs to build floors from'},
                        'room_id': {'type': 'integer', 'description': 'Single room element ID (alternative to room_ids)'},
                        'floor_type': {'type': 'string', 'description': 'Floor type name (optional)'}
                    },
                    'required': []
                }
            },
            'purge_unused': {
                'name': 'purge_unused',
                'description': ('Report (and optionally delete) unused family symbols and view templates. '
                                'Defaults to a safe dry run that only reports counts.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'dry_run': {'type': 'boolean', 'description': 'If true (default) only report; if false, delete unused items'}
                    },
                    'required': []
                }
            },
            'audit_model': {
                'name': 'audit_model',
                'description': ('Detailed model audit: warnings by type, imported CAD, groups, in-place '
                                'families, unused types/templates, and basic naming issues.'),
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'create_workset': {
                'name': 'create_workset',
                'description': 'Create a new user workset (workshared models only).',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Workset name'}
                    },
                    'required': ['name']
                }
            },
            # ── Utility / assistant control ───────────────────────────────────
            'file_watcher_status': {
                'name': 'file_watcher_status',
                'description': 'Check whether the T3Lab file-based task watcher is running and get its data directory path',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'get_revit_context': {
                'name': 'get_revit_context',
                'description': 'Get current Revit context: active view, selected elements, open document info',
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'list_open_documents': {
                'name': 'list_open_documents',
                'description': ('List all documents open in this Revit instance (title, file path, '
                                'which one is active). Tool calls always target the ACTIVE document '
                                '— use switch_active_document to activate another one.'),
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'switch_active_document': {
                'name': 'switch_active_document',
                'description': ('Activate another project: brings the matched document\'s window to '
                                'the front so it becomes the ACTIVE document that all tool calls '
                                'target — the model on screen is the model being edited. Matches an '
                                'open document by title or file path. '
                                'If nothing matches but path_or_title is an existing .rvt file path, '
                                'the file is OPENED from disk in this Revit window (large models may '
                                'exceed the 120s tool timeout — the file keeps opening in Revit; '
                                'verify with list_open_documents). Documents open in another Revit '
                                'window (separate instance) are reachable too — the T3Lab bridge '
                                're-routes the connection automatically. '
                                'Use list_open_documents first to see what is open.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'path_or_title': {
                            'type': 'string',
                            'description': ('Document title (e.g. "Project1"), file name '
                                            '(e.g. "Tower_A.rvt") or full .rvt file path. '
                                            'A full path to a not-yet-open file opens it from disk.')
                        }
                    },
                    'required': ['path_or_title']
                }
            },
            'open_document': {
                'name': 'open_document',
                'description': ('Open a Revit model (.rvt / .rfa) from a file path and activate it '
                                '— it becomes the document all tool calls target. If the file is '
                                'already open its window is activated instead. Large models may '
                                'exceed the 120s tool timeout — the file keeps opening in Revit; '
                                'verify with list_open_documents. '
                                'Use list_recent_documents to find project paths quickly.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'path': {
                            'type': 'string',
                            'description': 'Full file path to the .rvt / .rfa file to open'
                        }
                    },
                    'required': ['path']
                }
            },
            'close_document': {
                'name': 'close_document',
                'description': ('Close an open document by title or file path. Set save=true to '
                                'save before closing (unsaved changes are discarded otherwise). '
                                'The active document can only be closed when another document is '
                                'open — Revit activates the other one first. Use list_open_documents '
                                'to see what is open.'),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'path_or_title': {
                            'type': 'string',
                            'description': 'Title, file name or full path of the open document to close'
                        },
                        'save': {
                            'type': 'boolean',
                            'description': 'Save the document before closing (default false — discard changes)'
                        }
                    },
                    'required': ['path_or_title']
                }
            },
            'list_recent_documents': {
                'name': 'list_recent_documents',
                'description': ('List recently opened Revit projects (from Revit\'s recent-files '
                                'list) with an exists-on-disk flag. The fastest way to switch to a '
                                'project that is not open yet: pick a path here, then call '
                                'open_document with it.'),
                'inputSchema': {'type': 'object', 'properties': {}, 'required': []}
            },
            'show_assistant_pane': {
                'name': 'show_assistant_pane',
                'description': 'Show or hide the T3Lab Assistant dockable pane',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'action': {
                            'type': 'string',
                            'description': 'Show or hide the pane. Default "show".',
                            'enum': ['show', 'hide']
                        },
                        'message': {
                            'type': 'string',
                            'description': ('Optional message to inject into the pane chat. '
                                            'Delivery is NOT guaranteed — always check '
                                            '"message_injected" in the result, and repeat the '
                                            'text in your own reply when it is false.')
                        }
                    },
                    'required': []
                }
            },
            # ── Export ────────────────────────────────────────────────────────
            'export_sheets_pdf': {
                'name': 'export_sheets_pdf',
                'description': 'Export one or more sheets to PDF files in a specified folder',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sheet_ids': {
                            'type': 'array', 'items': {'type': ['integer', 'string']},
                            'description': 'Sheet element IDs to export (omit to export all sheets)'
                        },
                        'output_folder': {
                            'type': 'string',
                            'description': 'Folder path to save PDF files. Default: same folder as .rvt file'
                        },
                        'combined': {
                            'type': 'boolean',
                            'description': 'Combine all sheets into one PDF (default false)'
                        }
                    },
                    'required': []
                }
            }
        }

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        self._port = value

    @property
    def is_running(self):
        return self._is_running

    @property
    def client_count(self):
        return len(self._clients)

    def _register_client(self, client_id):
        """Register a connected client"""
        self._clients[client_id] = {'connected': True}
        self._total_clients += 1

    def _unregister_client(self, client_id):
        """Unregister a disconnected client"""
        if client_id in self._clients:
            del self._clients[client_id]

    # ── Open documents ───────────────────────────────────────────────────
    # Every tool call targets pyrevit.revit.doc — the document/window Revit
    # itself has active, i.e. exactly what the user sees on screen. Project
    # switching is done through real window activation (switch_active_document,
    # open_document), never through hidden redirection: the old "pin" layer
    # that could silently retarget tools away from the visible document was
    # removed on user request.

    def get_open_documents(self):
        """List non-linked documents open in this Revit instance.

        Returns a list of {'title', 'path', 'is_active'} dicts. Used by the
        MCP Control dialog and the list_open_documents tool.
        """
        try:
            from pyrevit import HOST_APP, revit
            uiapp = None
            try:
                uiapp = HOST_APP.uiapp
            except Exception:
                pass
            if uiapp is None:
                # Startup/hook-engine singleton: HOST_APP has no uiapp —
                # use the one cached from ExternalEvent.Execute.
                uiapp = getattr(self, '_cached_uiapp', None)
            if uiapp is None:
                return []
            active_title = None
            try:
                active_title = revit.doc.Title
            except Exception:
                pass
            if active_title is None:
                try:
                    active_title = uiapp.ActiveUIDocument.Document.Title
                except Exception:
                    pass

            docs = []
            for d in uiapp.Application.Documents:
                if d.IsLinked:
                    continue
                docs.append({
                    'title': d.Title,
                    'path': d.PathName or '(unsaved)',
                    'is_active': d.Title == active_title,
                })
            return docs
        except Exception:
            return []

    def _get_uiapp(self, preferred=None):
        """UIApplication with engine fallback: prefer the one Revit passed
        into ExternalEvent.Execute (engine-independent), then the per-engine
        pyrevit HOST_APP, then the copy cached from a previous Execute.
        Returns None only when no tool has ever run through the event."""
        if preferred is not None:
            return preferred
        try:
            from pyrevit import HOST_APP
            if HOST_APP.uiapp is not None:
                return HOST_APP.uiapp
        except Exception:
            pass
        return getattr(self, '_cached_uiapp', None)

    def _recover_active_document(self, uidoc, uiapp=None):
        """Best-effort (doc, uidoc, error) when no active document resolved.

        pyrevit.revit.doc is None when Revit sits on the start page, when no
        document tab has focus yet, and sometimes on the read-fallback worker
        thread (ActiveUIDocument is only reliable inside an API context).
        Prefer the active view's document; failing that, the single open
        document when the choice is unambiguous; otherwise return an
        actionable error dict instead of letting every tool crash with
        "'NoneType' object has no attribute 'Title'".

        uiapp: the UIApplication Revit passed into ExternalEvent.Execute.
        The per-engine pyrevit.HOST_APP.uiapp is None when this server
        singleton was created in a startup/hook engine (AppDomain anchor),
        so the event-supplied/cached uiapp is the reliable source there.
        """
        try:
            if uiapp is None:
                try:
                    from pyrevit import HOST_APP
                    uiapp = HOST_APP.uiapp
                except Exception:
                    uiapp = None
            if uiapp is None:
                uiapp = getattr(self, '_cached_uiapp', None)
            if uiapp is None:
                return None, None, {
                    'error': ('Cannot reach the Revit UIApplication from '
                              'this engine yet (no tool has run through the '
                              'Revit ExternalEvent in this session). Retry '
                              'the call once — the first ExternalEvent '
                              'execution registers the Revit context.'),
                }

            active = None
            try:
                active = uiapp.ActiveUIDocument
            except Exception:
                pass
            if active is not None and active.Document is not None:
                return active.Document, active, None

            docs = [d for d in uiapp.Application.Documents if not d.IsLinked]
            if len(docs) == 1:
                target = docs[0]
                target_uidoc = uidoc
                try:
                    from Autodesk.Revit.UI import UIDocument
                    target_uidoc = UIDocument(target)
                except Exception:
                    pass
                return target, target_uidoc, None
            if not docs:
                return None, None, {
                    'error': ('No document is open in this Revit instance '
                              '(start page). Open a project in Revit, or call '
                              'switch_active_document with a full .rvt file '
                              'path to open one.'),
                    'open_documents': [],
                }
            return None, None, {
                'error': ('No active document — several documents are open in '
                          'this Revit instance but none has focus, so the '
                          'target is ambiguous. Click a document tab in Revit, '
                          'or call switch_active_document first.'),
                'open_documents': [d.Title for d in docs],
            }
        except Exception as e:
            return None, None, {'error': 'No active document: {}'.format(e)}

    def _handle_initialize(self, params):
        """Handle MCP initialize request"""
        return {
            'protocolVersion': '2024-11-05',
            'capabilities': {
                'tools': {}
            },
            'serverInfo': {
                'name': 'T3LabAI Revit MCP Server',
                'version': '1.0.0'
            }
        }

    def _handle_tools_list(self):
        """Handle tools/list request"""
        return {
            'tools': list(self._tools.values())
        }

    def _handle_tool_call(self, params):
        """Handle tools/call request.

        This is the EXTERNAL MCP entry (bridge.py / Claude Desktop). The in-app
        assistant calls _execute_tool directly and never reaches here, so the
        teaching recorder + session-boundary tools live here and only capture
        the external teacher's sessions.
        """
        tool_name = params.get('name', '')
        arguments = params.get('arguments', {})

        # Teaching session boundary tools are handled locally (no Revit) so the
        # teacher can frame each trajectory. No-op unless teaching mode is on.
        boundary = self._teach_handle_boundary(tool_name, arguments)
        if boundary is not None:
            return boundary

        if tool_name not in self._tools:
            return {
                'content': [{
                    'type': 'text',
                    'text': 'Error: Unknown tool: {}'.format(tool_name)
                }],
                'isError': True
            }

        # Execute tool and return result
        try:
            result = self._execute_tool(tool_name, arguments)
            self._teach_record_step(tool_name, arguments, result)
            return {
                'content': [{
                    'type': 'text',
                    # jsonsafe, not json.dumps: a single Revit name with an
                    # accent (a view called "Café Kitchen - Section") made the
                    # whole tool call fail with "'unknown' codec can't decode
                    # byte 0xe9" — IronPython's ensure_ascii escaper again.
                    'text': jsonsafe.dumps(result, indent=2)
                }]
            }
        except Exception as e:
            self._teach_record_step(
                tool_name, arguments, {'error': str(e), 'tool': tool_name})
            return {
                'content': [{
                    'type': 'text',
                    'text': 'Error executing tool: {}'.format(str(e))
                }],
                'isError': True
            }

    # ── Teaching capture (Opus distils via the external MCP path) ────────────

    # Session-boundary pseudo-tools the teacher (Opus in Claude Desktop) calls
    # to frame one training trajectory. Registered into tools/list, handled
    # entirely here — they never touch Revit.
    _TEACH_BEGIN_TOOL = 't3lab_begin_teaching'
    _TEACH_END_TOOL   = 't3lab_end_teaching'

    # Claude-callable teaching/training control tools — handled locally (no
    # Revit), so the external teacher can drive the whole "teach then train"
    # loop from Claude Desktop. Hidden from the in-app catalog (tool_schema).
    _TEACH_SET_MODE_TOOL = 't3lab_set_teaching_mode'
    _TEACH_SANDBOX_TOOL  = 't3lab_mark_sandbox'
    _TEACH_STATUS_TOOL   = 't3lab_training_status'
    _TEACH_TRAIN_TOOL    = 't3lab_train_model'
    _TEACH_EXEMPLARS_TOOL = 't3lab_build_exemplars'
    # Below this many examples, a fine-tune wastes time (the trainer aborts
    # anyway) unless the caller forces it. Mirrors tools/train validate_dataset.
    _TRAIN_MIN_EXAMPLES  = 30

    def _teach_data_dir(self):
        from core import teaching
        return teaching.session_dir()

    def _restore_teaching_state(self):
        """Load persisted teaching_enabled + sandbox_doc from mcp_paths.json."""
        try:
            from core import paths
            data = paths.load_settings() or {}
            self._teaching_enabled = bool(data.get('teaching_enabled', False))
            sb = data.get('sandbox_doc')
            self._sandbox_doc = sb if isinstance(sb, dict) else None
        except Exception:
            self._teaching_enabled = False
            self._sandbox_doc = None

    def set_teaching_mode(self, on):
        """Enable/disable teaching capture + sandbox write-lock. Persisted."""
        self._teaching_enabled = bool(on)
        try:
            from core import paths
            paths.set_setting('teaching_enabled', self._teaching_enabled)
        except Exception:
            pass
        return self._teaching_enabled

    def set_sandbox_document(self, info):
        """Mark a document as the teaching sandbox. `info` is {'title','path'}
        (resolved by the caller on the UI thread) or None to clear. Persisted."""
        self._sandbox_doc = info if isinstance(info, dict) else None
        try:
            from core import paths
            paths.set_setting('sandbox_doc', self._sandbox_doc)
        except Exception:
            pass
        return self._sandbox_doc

    def get_teaching_status(self):
        """{'enabled', 'sandbox', 'session_open', 'sessions_recorded'}."""
        sessions = 0
        try:
            sessions = len([f for f in os.listdir(self._teach_data_dir())
                            if f.endswith('.jsonl')])
        except Exception:
            pass
        sb = None
        if isinstance(self._sandbox_doc, dict):
            sb = self._sandbox_doc.get('title') or self._sandbox_doc.get('path')
        return {'enabled': bool(self._teaching_enabled),
                'sandbox': sb,
                'session_open': self._teach_session is not None,
                'sessions_recorded': sessions}

    def _is_sandbox_doc(self, doc):
        """True when `doc` is the designated teaching sandbox.

        Runs on the Revit main thread (valid doc) — reads two string props only,
        then delegates the (pure, tested) match to core.teaching.is_sandbox.
        """
        if doc is None:
            return False
        try:
            title = u'{}'.format(getattr(doc, 'Title', u'') or u'')
            path  = u'{}'.format(getattr(doc, 'PathName', u'') or u'')
        except Exception:
            return False
        from core import teaching
        return teaching.is_sandbox(title, path, self._sandbox_doc)

    def _teach_sandbox_reject(self, tool_name):
        """The error returned when a write is blocked by the sandbox guard."""
        return {
            'error': ('Teaching mode is ON: model-modifying tools are only '
                      'allowed on the sandbox document. Mark a scratch .rvt as '
                      'the sandbox in MCP Control (or name it with "sandbox"/'
                      '"nhap"), then retry — the real project is protected.'),
            'tool': tool_name,
            'teaching_blocked': True,
        }

    @staticmethod
    def _text_result(msg):
        return {'content': [{'type': 'text', 'text': u'{}'.format(msg)}]}

    def _teach_handle_boundary(self, tool_name, arguments):
        """Handle the Claude-callable teaching/training control tools locally
        (no Revit). Returns a result dict, or None when `tool_name` is not one
        of them so the normal execution path runs."""
        args = arguments or {}

        # ── Session boundaries ──────────────────────────────────────────────
        if tool_name == self._TEACH_BEGIN_TOOL:
            goal = u'{}'.format(args.get('goal') or u'').strip()
            if self._teaching_enabled:
                self._teach_begin(goal)
                msg = 'Teaching trajectory started.'
            else:
                msg = ('Teaching mode is OFF — call t3lab_set_teaching_mode('
                       'enabled=true) first, then retry.')
            return self._text_result(msg)
        if tool_name == self._TEACH_END_TOOL:
            summary = u'{}'.format(args.get('summary') or u'').strip()
            return self._text_result(self._teach_end(summary))

        # ── Teaching-mode toggle ────────────────────────────────────────────
        if tool_name == self._TEACH_SET_MODE_TOOL:
            enabled = bool(args.get('enabled', True))
            self.set_teaching_mode(enabled)
            return self._text_result(
                'Teaching capture {}. {}'.format(
                    'ON' if enabled else 'OFF',
                    'Model writes are now restricted to the sandbox document.'
                    if enabled else 'Sandbox write-lock lifted.'))

        # ── Mark sandbox (local: caller supplies the doc identifier) ────────
        if tool_name == self._TEACH_SANDBOX_TOOL:
            doc = u'{}'.format(args.get('document') or u'').strip()
            if not doc:
                return self._text_result(
                    'Pass document=<title or path of the scratch .rvt> — get it '
                    'from list_open_documents. Only ever mark a SCRATCH model.')
            self.set_sandbox_document({'title': doc, 'path': doc})
            return self._text_result(
                'Sandbox set to "{}". Model-modifying tools are allowed only on '
                'this document while teaching mode is on.'.format(doc))

        # ── Training status ─────────────────────────────────────────────────
        if tool_name == self._TEACH_STATUS_TOOL:
            return self._text_result(self._training_status_text())

        # ── Launch training ─────────────────────────────────────────────────
        if tool_name == self._TEACH_TRAIN_TOOL:
            force = bool(args.get('force', False))
            return self._text_result(self._launch_training(force))

        # ── Build portable exemplars (no GPU) ───────────────────────────────
        if tool_name == self._TEACH_EXEMPLARS_TOOL:
            return self._text_result(self._build_exemplars())

        return None

    def _build_exemplars(self):
        """Distil teacher data into the git-tracked portable few-shot file.

        Unlike training, this needs no GPU: it rewrites teacher_exemplars.json so
        a plain local model answers in the taught style on any machine once the
        file is committed. Never raises.
        """
        try:
            from Intelligence.learning import exemplars as _ex
            res = _ex.promote_from_dataset()
            n = res.get('count', 0)
            if res.get('status') == 'ok':
                return ('Rebuilt {} portable exemplars into '
                        'lib/Intelligence/config/teacher_exemplars.json. Commit '
                        'that file so every machine\'s local model answers in the '
                        'taught style — no GPU / re-train needed.'.format(n))
            return 'Could not build exemplars: {}'.format(res.get('status'))
        except Exception as ex:
            return 'Could not build exemplars: {}'.format(ex)

    def _training_status_text(self):
        """Human-readable dataset + last-train + teaching status. Never raises."""
        try:
            from Intelligence.learning import trainer as _t
            st = _t.dataset_stats()
            last = _t.last_train() or {}
            running = _t.is_running()
        except Exception as ex:
            return 'Training status unavailable: {}'.format(ex)
        by_src = ', '.join('{}: {}'.format(k, v)
                           for k, v in sorted((st.get('by_source') or {}).items()))
        teach = self.get_teaching_status()
        last_line = ('last trained {} on {} examples'.format(
            last.get('trained_at'), last.get('examples'))
            if last else 'never trained')
        return ('Training data: {} examples ({}). {}. Teaching {}, sandbox={}, '
                'sessions recorded={}. Training running: {}.'.format(
                    st.get('count', 0), by_src or 'empty', last_line,
                    'ON' if teach.get('enabled') else 'OFF',
                    teach.get('sandbox') or 'none',
                    teach.get('sessions_recorded', 0),
                    'yes' if running else 'no'))

    def _launch_training(self, force):
        """Decide + launch the detached fine-tune. Never raises."""
        try:
            from Intelligence.learning import trainer as _t
            from core import teaching
            count = _t.dataset_stats().get('count', 0)
            if not teaching.should_launch_training(count, force,
                                                   self._TRAIN_MIN_EXAMPLES):
                return ('Only {} training examples — need >= {} to train well. '
                        'Teach more tasks (t3lab_begin_teaching/…/'
                        't3lab_end_teaching) or call t3lab_train_model(force=true)'
                        ' to run anyway.'.format(count, self._TRAIN_MIN_EXAMPLES))
            if _t.is_running():
                return 'A training run is already in progress.'
            # Refresh the portable few-shot layer too, so machines without the
            # fine-tuned model still benefit once teacher_exemplars.json is
            # committed. Best-effort — never blocks the training launch.
            exemplar_note = ''
            try:
                from Intelligence.learning import exemplars as _ex
                ex_res = _ex.promote_from_dataset()
                exemplar_note = (' Also rebuilt {} portable exemplars '
                                 '(commit teacher_exemplars.json).'
                                 .format(ex_res.get('count', 0)))
            except Exception:
                pass
            ok, note = _t.launch(force=force)
            if ok:
                return ('Started background fine-tune on {} examples. It runs '
                        'OUTSIDE Revit (CPython + GPU) and can take hours; see '
                        'tools/train/README.md. The result is served as an '
                        'Ollama model the assistant can then point at.{}'
                        .format(count, exemplar_note))
            return 'Could not launch training: {}'.format(note)
        except Exception as ex:
            return 'Could not launch training: {}'.format(ex)

    def _teach_begin(self, goal):
        """Open a new trajectory buffer (flushing any orphan first)."""
        if self._teach_session is not None:
            self._teach_flush()
        import time as _t
        self._teach_session = {
            'id': _t.strftime('%Y%m%d-%H%M%S'),
            'goal': goal or u'',
            'steps': [],
        }

    def _teach_record_step(self, tool_name, arguments, result):
        """Append one executed tool call to the open trajectory + session file.

        No-op unless teaching mode is on. If the teacher never called
        begin_teaching, an implicit session is opened so nothing is lost.
        Never raises.
        """
        if not self._teaching_enabled:
            return
        if tool_name in (self._TEACH_BEGIN_TOOL, self._TEACH_END_TOOL):
            return
        try:
            from core import teaching
            if self._teach_session is None:
                self._teach_begin(u'')
            step = {'tool': tool_name, 'arguments': arguments,
                    'result': result,
                    'is_error': bool(teaching.is_error_result(result))}
            self._teach_session['steps'].append(step)
            path = os.path.join(self._teach_data_dir(),
                                self._teach_session['id'] + '.jsonl')
            teaching.append_step_line(
                path, self._teach_session.get('goal', u''), step)
        except Exception:
            pass

    def _teach_end(self, summary):
        """Close the open trajectory: convert to an SFT example, clear buffer."""
        if self._teach_session is None:
            return 'No teaching trajectory was open.'
        session = self._teach_session
        self._teach_session = None
        steps = session.get('steps') or []
        goal = session.get('goal') or u''
        if not steps:
            return 'Teaching trajectory had no tool calls — nothing recorded.'
        try:
            from Intelligence.learning import dataset as _ds
            rev = None
            try:
                from pyrevit import HOST_APP
                rev = int(HOST_APP.version)
            except Exception:
                rev = None
            if goal:
                ok, note = _ds.add_trajectory(
                    goal, steps, final=(summary or None),
                    source='mcp_teacher', quality='teacher', revit_version=rev)
                return ('Recorded teaching trajectory ({} steps): {}'
                        .format(len(steps), note))
            # No goal declared — leave the raw session file for the miner to
            # label later; do not fabricate a target here.
            return ('Recorded {} steps with no goal — call '
                    't3lab_begin_teaching(goal=...) first next time.'
                    .format(len(steps)))
        except Exception as ex:
            return 'Could not record trajectory: {}'.format(ex)

    def _teach_flush(self):
        """Flush an orphan open session (begin without end) to the dataset if it
        has a goal; the raw file survives regardless for the miner."""
        try:
            if self._teach_session and self._teach_session.get('goal') \
                    and self._teach_session.get('steps'):
                self._teach_end(u'')
        except Exception:
            pass
        self._teach_session = None

    # Tools that open a Transaction / mutate the model. These MUST run on
    # Revit's main thread via the ExternalEvent — starting a transaction from
    # the HTTP worker thread throws "outside of API context is not allowed".
    _WRITE_TOOLS = frozenset([
        'revit_override_color', 'create_level', 'place_wall', 'set_parameter',
        'create_point_based_element', 'create_line_based_element',
        'create_surface_based_element', 'create_grid', 'create_room',
        'create_structural_framing_system', 'delete_element', 'operate_element',
        'edit_elements', 'manage_view', 'manage_view_template',
        'manage_links', 'manage_revision', 'manage_sheet', 'export_model',
        'create_detail_annotation',
        # Save/SaveAs/SynchronizeWithCentral need the Revit main thread but
        # must NOT be inside any open transaction phase.
        'manage_document',
        # Read-only, but this set really means "must run on Revit's main
        # thread": check_bad_geometry evaluates surface derivatives and can
        # tessellate faces, which is exactly the work that takes the process
        # down. Never let it fall back onto the HTTP worker thread. It opens
        # no transaction of its own.
        'check_bad_geometry',
        'color_elements', 'tag_all_walls', 'tag_all_rooms', 'move_elements',
        'copy_elements', 'rotate_element', 'create_view', 'set_active_view',
        'rename_element', 'create_sheet', 'add_view_to_sheet', 'create_text_note',
        'set_element_workset', 'load_family', 'send_code_to_revit',
        'store_project_data', 'store_room_data', 'export_sheets_pdf', 'say_hello',
        'show_assistant_pane', 'split_curve', 'split_element', 'join_geometry',
        'bulk_set_parameter', 'select_elements', 'tag_elements', 'create_dimension',
        'create_schedule', 'duplicate_view', 'apply_view_template',
        'create_view_filter', 'place_views_on_sheets', 'export_dwg', 'export_image',
        'create_project_parameter', 'room_to_floor', 'purge_unused', 'create_workset',
        # These call UIApplication.OpenAndActivateDocument / Document.Close,
        # which throw "outside of API context" from the HTTP worker thread.
        'switch_active_document', 'open_document', 'close_document',
        # Internal pseudo-tools (agent request TransactionGroup) — NOT in the
        # public registry, but they open/close a TransactionGroup so they must
        # run on the Revit main thread like any other write tool.
        '__begin_action_group', '__end_action_group',
    ])

    # ── Destructive-action declaration ────────────────────────────────────────
    # The confirmation gate lives in the Assistant UI (it has to block on a
    # dialog), but WHAT is dangerous is a property of the tool, so it is
    # declared here next to the tool definitions. The UI calls is_destructive();
    # dev/test_tool_registry.py checks every name below is a real tool.
    _DESTRUCTIVE_TOOLS = frozenset([
        'delete_element',
    ])

    # Tools that are destructive only for certain operations.
    _DESTRUCTIVE_OPS = {
        'edit_elements':        frozenset(['ungroup']),
        'manage_view_template': frozenset(['delete']),
        'manage_links':         frozenset(['delete']),
        'manage_document':      frozenset(['save_as', 'sync_with_central']),
        # manage_sheet has no destructive op: print-set CRUD was deliberately
        # left out (PrintManager global state, too version-sensitive to ship
        # untested), so there is nothing here to gate yet.
    }

    def is_destructive(self, tool_name, arguments=None):
        """True when this call needs an explicit user confirmation first.

        Covers three shapes: always-destructive tools, tools destructive only
        for some operations, and the two argument-conditional legacy rules.
        """
        args = arguments or {}
        if tool_name in self._DESTRUCTIVE_TOOLS:
            return True
        ops = self._DESTRUCTIVE_OPS.get(tool_name)
        if ops:
            op = u'{}'.format(args.get('operation') or '').lower()
            if op in ops:
                return True
        # purge_unused only bites once it stops being a dry run.
        if tool_name == 'purge_unused' and not bool(args.get('dry_run', True)):
            return True
        # A deep geometry probe is read-only but CAN hard-crash Revit
        # (Face.Triangulate is the documented access-violation path).
        if tool_name == 'check_bad_geometry' and bool(args.get('deep_probe')):
            return True
        return False

    # Tools that never touch the target document — they must keep working
    # when this Revit instance has NO active document (start page, or no
    # document tab focused), because they are exactly what the AI client
    # needs to diagnose and fix that state (list what's open, switch/open a
    # document, ping the connection).
    _DOCLESS_TOOLS = frozenset([
        'say_hello', 'list_open_documents', 'switch_active_document',
        'open_document', 'close_document', 'list_recent_documents',
        'show_assistant_pane', 'file_watcher_status',
    ])

    def _show_elements_smart(self, uidoc, doc, ids):
        """Navigate to `ids` without Revit's "No good view could be found."

        uidoc.ShowElements() asks Revit to GUESS a view. For a model element
        (wall, door) that works, but a VIEW-SPECIFIC element — a text note in a
        legend, a tag or dimension in a drafting view — only exists in its
        owner view, and when that view isn't open Revit gives up and raises
        that modal dialog. The dialog comes from Revit itself, so wrapping
        ShowElements in try/except does NOT suppress it.

        So: resolve the element's OwnerViewId first and ACTIVATE that view,
        then zoom via the UIView. ShowElements is only used as a fallback for
        genuine model elements, where it behaves well.

        Safe to change the active view here: select_elements is in
        _WRITE_TOOLS, so this already runs on Revit's main thread via the
        ExternalEvent.

        Returns a small dict describing what happened (merged into the tool
        result) — never raises.
        """
        from Autodesk.Revit.DB import ElementId, View, ViewSheet
        from System.Collections.Generic import List as NetList

        info = {}
        try:
            owner_view = None
            for eid in ids:
                el = doc.GetElement(eid)
                if el is None:
                    continue
                ovid = getattr(el, 'OwnerViewId', None)
                if ovid is None or eid_value(ovid) <= 0:
                    continue                      # model element (not view-owned)
                v = doc.GetElement(ovid)
                if isinstance(v, View) and not v.IsTemplate:
                    owner_view = v
                    break

            if owner_view is not None:
                # Activating a view that is already active throws; skip it.
                try:
                    if eid_value(uidoc.ActiveView.Id) != eid_value(owner_view.Id):
                        uidoc.ActiveView = owner_view
                        info['activated_view'] = owner_view.Name
                except Exception as ex:
                    info['activate_error'] = str(ex)
                # Re-apply the selection: changing the active view clears it.
                try:
                    net = NetList[ElementId]()
                    for i in ids:
                        net.Add(i)
                    uidoc.Selection.SetElementIds(net)
                except Exception:
                    pass
                info['view'] = owner_view.Name
                info['view_type'] = str(getattr(owner_view, 'ViewType', ''))
                if self._zoom_to(uidoc, doc, owner_view, ids):
                    info['zoomed'] = True
                return info

            # Model element → ShowElements is appropriate. Only call it when
            # the element is actually visible somewhere, otherwise Revit pops
            # the modal dialog at the user.
            visible = self._first_view_showing(uidoc, doc, ids)
            if visible is None:
                info['show_skipped'] = ('element is not visible in any view '
                                        '(hidden, closed workset, or '
                                        'view-specific with no owner view)')
                return info
            try:
                net = NetList[ElementId]()
                for i in ids:
                    net.Add(i)
                uidoc.ShowElements(net)
                info['zoomed'] = True
            except Exception as ex:
                info['show_error'] = str(ex)
        except Exception as ex:
            info['show_error'] = str(ex)
        return info

    def _zoom_to(self, uidoc, doc, view, ids):
        """Zoom the open UIView of `view` onto the elements' bounding box."""
        try:
            from Autodesk.Revit.DB import XYZ
            bmin = bmax = None
            for eid in ids:
                el = doc.GetElement(eid)
                if el is None:
                    continue
                try:
                    bb = el.get_BoundingBox(view)
                except Exception:
                    bb = None
                if bb is None:
                    continue
                bmin = bb.Min if bmin is None else XYZ(
                    min(bmin.X, bb.Min.X), min(bmin.Y, bb.Min.Y),
                    min(bmin.Z, bb.Min.Z))
                bmax = bb.Max if bmax is None else XYZ(
                    max(bmax.X, bb.Max.X), max(bmax.Y, bb.Max.Y),
                    max(bmax.Z, bb.Max.Z))
            if bmin is None or bmax is None:
                return False
            pad = 2.0            # feet of breathing room around the target
            bmin = XYZ(bmin.X - pad, bmin.Y - pad, bmin.Z)
            bmax = XYZ(bmax.X + pad, bmax.Y + pad, bmax.Z)
            for uiview in uidoc.GetOpenUIViews():
                if eid_value(uiview.ViewId) == eid_value(view.Id):
                    uiview.ZoomAndCenterRectangle(bmin, bmax)
                    return True
        except Exception:
            pass
        return False

    def _first_view_showing(self, uidoc, doc, ids, max_views=40):
        """A view in which at least one of `ids` is visible, or None.

        Used to decide whether ShowElements can succeed, so the user never
        gets Revit's "No good view could be found." modal.

        Runs on Revit's MAIN thread, so cost matters: the search is ordered
        cheapest-first (active view, then already-open views) and every
        collector is narrowed to the elements' own categories before a
        bounded sweep of the remaining views. On a large model an unbounded
        all-views × all-elements scan would freeze the UI for seconds.
        """
        try:
            from Autodesk.Revit.DB import (FilteredElementCollector, View,
                                           ViewType, ElementMulticategoryFilter,
                                           ElementId)
            from System.Collections.Generic import List as NetList

            wanted = set(eid_value(i) for i in ids)
            if not wanted:
                return None

            cat_ids = NetList[ElementId]()
            seen_cat = set()
            for eid in ids:
                el = doc.GetElement(eid)
                if el is None or el.Category is None:
                    continue
                cv = eid_value(el.Category.Id)
                if cv not in seen_cat:
                    seen_cat.add(cv)
                    cat_ids.Add(el.Category.Id)
            cat_filter = (ElementMulticategoryFilter(cat_ids)
                          if cat_ids.Count else None)

            def _hits(view):
                try:
                    coll = FilteredElementCollector(doc, view.Id) \
                        .WhereElementIsNotElementType()
                    if cat_filter is not None:
                        coll = coll.WherePasses(cat_filter)
                    for e in coll:
                        if eid_value(e.Id) in wanted:
                            return True
                except Exception:
                    pass
                return False

            skip = (ViewType.Schedule, ViewType.ColumnSchedule,
                    ViewType.PanelSchedule, ViewType.DrawingSheet,
                    ViewType.Internal, ViewType.ProjectBrowser,
                    ViewType.SystemBrowser, ViewType.Undefined)

            # 1. the active view, 2. any already-open view — near-zero cost
            candidates, done = [], set()
            try:
                candidates.append(uidoc.ActiveView)
            except Exception:
                pass
            try:
                for uv in uidoc.GetOpenUIViews():
                    v = doc.GetElement(uv.ViewId)
                    if v is not None:
                        candidates.append(v)
            except Exception:
                pass
            for v in candidates:
                try:
                    key = eid_value(v.Id)
                    if key in done or v.IsTemplate or v.ViewType in skip:
                        continue
                    done.add(key)
                    if _hits(v):
                        return v
                except Exception:
                    continue

            # 3. bounded sweep of the remaining views
            n = 0
            for v in FilteredElementCollector(doc).OfClass(View):
                if n >= max_views:
                    break
                try:
                    key = eid_value(v.Id)
                    if key in done or v.IsTemplate or v.ViewType in skip:
                        continue
                    done.add(key)
                    n += 1
                    if _hits(v):
                        return v
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def ensure_external_event(self):
        """Create the ExternalEvent used to marshal tool execution onto Revit's
        main thread.

        This MUST be invoked from a valid Revit API context — i.e. the pyRevit
        pushbutton's main (UI) thread. ExternalEvent.Create() throws
        InvalidOperationException when called from a background worker thread,
        which is exactly why start_server() (run from the assistant's
        background startup probe) cannot create it: every write tool would then
        fall back to running its Transaction outside API context and fail.

        Returns (ok: bool, error: str|None).
        """
        if not HAS_REVIT_UI:
            return False, 'Revit UI API not available (RevitAPIUI not loaded)'
        if self._external_event is not None:
            return True, None
        try:
            self._event_handler = MCPExternalEventHandler(self)
            self._external_event = ExternalEvent.Create(self._event_handler)
            return True, None
        except Exception as e:
            self._event_handler = None
            self._external_event = None
            return False, str(e)

    # Grace period (s) a read-only tool waits for the ExternalEvent before
    # falling back to direct execution. When Revit is idle the event fires in
    # well under a second; when Revit is busy (modal dialog open, user
    # mid-command, window minimized) it may not fire for minutes — reads must
    # not hang behind that. Pure reads off the UI thread are the same path
    # already used when no ExternalEvent exists at all.
    _READ_FALLBACK_WAIT = 2.0

    # ...but only while the model is NOT being rewritten underneath us. Reading
    # the API off the main thread is safe only when the main thread is idle;
    # the moment a write commits, Revit spends seconds regenerating elements
    # and refreshing view caches, and a collector walking the same document
    # from a worker thread during that window is the documented modeless
    # hard-crash. A multi-step assistant request is exactly write-then-read, so
    # that window is where the fallback fired most and where Revit died.
    #
    # Inside this quiet period after a write the read WAITS for its turn on the
    # main thread instead (it still gets a 120s ceiling, same as a write). A
    # read-only session never enters it at all, so the anti-hang valve keeps
    # working where it was actually needed.
    _WRITE_QUIET_PERIOD = 8.0

    def _read_fallback_allowed(self):
        """True when a read may safely run on the CALLING thread.

        Blocked while a write is executing on the main thread, and for
        _WRITE_QUIET_PERIOD seconds after the last one finished.
        """
        if getattr(self, '_write_in_progress', False):
            return False
        try:
            since = time.time() - (getattr(self, '_last_write_done', 0.0) or 0.0)
        except Exception:
            return False
        return since > self._WRITE_QUIET_PERIOD

    # Undeclared arguments a dispatch traps ITSELF, because it can give a
    # better answer than the generic guard below. get_schedule_data reads an
    # EXISTING schedule; a model that passes `category` wants a takeoff, and
    # the branch redirects it to get_material_quantities / create_schedule by
    # name. Letting the generic "does not accept: category" fire first would
    # throw that redirect away. dev/test_tool_registry.py exempts exactly
    # these pairs and no others.
    _ARGUMENT_REDIRECTS = {
        'get_schedule_data': frozenset(['category']),
    }

    def _reject_unknown_arguments(self, tool_name, arguments):
        """Error dict when `arguments` carries keys the tool cannot honour.

        Every dispatch branch reads its inputs with `arguments.get('x')`, so a
        key nobody reads is silently dropped and the tool still reports
        success. A user asked for "3D views of rooms with 100mm margin"; the
        model called create_view(category='Room', view_type='3D', margin=100);
        `category` and `margin` fell on the floor, ONE plain isometric of the
        whole site came back as {'success': True} — and the model, believing
        it now had a room view, went on to invent a colour-coding step nobody
        asked for. Answering "I ignored two of your three arguments" with
        "Done" is the worst failure mode this server has.

        Returns None when the call is clean. Internal pseudo-tools
        (__begin_action_group, collect_spellcheck_text...) are not in the
        registry and are left alone.
        """
        schema = (self._tools.get(tool_name) or {}).get('inputSchema') or {}
        declared = set(schema.get('properties') or {})
        if not declared or not isinstance(arguments, dict):
            return None
        declared |= self._ARGUMENT_REDIRECTS.get(tool_name, frozenset())
        unknown = sorted(k for k in arguments if k not in declared)
        if not unknown:
            return None
        return {
            'error': "{} does not accept: {}.".format(
                tool_name, ', '.join(unknown)),
            'accepted_arguments': sorted(declared),
            'hint': ('Those arguments were NOT applied. Either drop them and '
                     'retry, or tell the user this tool cannot do what they '
                     'asked — do not report the call as done.'),
            'tool': tool_name,
        }

    def _release_stale_action_group(self):
        """Drop any TransactionGroup handle left over from the withdrawn B4
        grouping WITHOUT touching it.

        Revit already force-closed the group when the Execute that started it
        returned, so the managed wrapper points at a terminated native object:
        calling Assimilate() or RollBack() on it is precisely the fatal
        exception this code path exists to stop. Only the reference is cleared.

        Reached from the Revit main thread (the inert pseudo-tools) and from
        the pane at request start, so a group opened by a pre-reload,
        AppDomain-anchored server can never be operated on later.
        """
        try:
            if getattr(self, '_action_group', None) is not None:
                self._action_group = None
        except Exception:
            pass

    def _execute_tool(self, tool_name, arguments):
        """Execute a Revit tool in a thread-safe manner using External Events."""
        rejected = self._reject_unknown_arguments(tool_name, arguments)
        if rejected is not None:
            return rejected
        if self._external_event:
            task = _ToolTask(tool_name, arguments)
            self._event_handler.tasks.put(task)
            self._external_event.Raise()

            is_write = tool_name in self._WRITE_TOOLS
            # Wait for main UI thread execution. Large write operations
            # (framing systems, PDF export, bulk deletes) legitimately take
            # far longer than 10s, so allow up to 120s before giving up.
            # Reads only wait the short grace period, then run directly.
            finished = task.done.wait(120 if is_write else self._READ_FALLBACK_WAIT)
            if not finished and not is_write:
                # Never read concurrently with a write mutating the model on
                # the UI thread, nor while Revit is still regenerating after
                # one — in either case keep waiting like a write would.
                if self._read_fallback_allowed() and task.claim('fallback'):
                    # Revit is busy with something else — run the read directly
                    # on this thread instead of hanging until Revit next goes
                    # idle. The target is the active document either way.
                    #
                    # Wrapped: this is a WORKER thread, and a .NET exception
                    # escaping it is not a failed tool call, it is a dead
                    # Revit process.
                    try:
                        return self._execute_tool_in_context(
                            tool_name, arguments)
                    except Exception as e:
                        return {'error': str(e), 'tool': tool_name}
                finished = task.done.wait(120)
            if not finished:
                # If still unclaimed, mark it consumed so the UI thread skips
                # it when the event finally fires after this timeout report.
                task.claim('abandoned')
                return {'error': 'Execution timed out waiting for Revit thread context', 'tool': tool_name}
            if task.exception:
                return {'error': str(task.exception), 'tool': tool_name}
            return task.result
        else:
            # No ExternalEvent — we're stuck on the HTTP worker thread. Read
            # tools tolerate this, but any write tool would throw a cryptic
            # "transaction outside API context" error. Surface a clear,
            # actionable message instead so the failure is diagnosable.
            if tool_name in self._WRITE_TOOLS:
                return {
                    'error': ('Revit ExternalEvent is not initialised, so model-editing tools '
                              'cannot run on the Revit main thread. Open (or reopen) the T3Lab '
                              'Assistant window once so it can register the event on the UI '
                              'thread, then retry.'),
                    'tool': tool_name,
                    'external_event_ready': False,
                }
            # Read tool with no ExternalEvent at all — run directly on the
            # HTTP worker thread (reads don't touch the window). Same reason
            # for the guard as the fallback above: an escaping .NET exception
            # on a worker thread kills the process, not just the call.
            try:
                return self._execute_tool_in_context(tool_name, arguments)
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

    # ── Shared tool helpers ────────────────────────────────────────────────

    # Human-facing category name → BuiltInCategory MEMBER NAME (a string, or a
    # tuple of candidate names tried in order — Revit renames members between
    # releases, e.g. OST_StructuralFoundation vs ...Foundations).
    #
    # Deliberately NOT `B.OST_x` attribute access in a dict literal: Revit adds
    # and removes BuiltInCategory members between versions (OST_Toposolid is
    # 2024+; several *Tags moved in 2024), and one missing member in a literal
    # raises at MAP-CONSTRUCTION time — which would take down all nine
    # category-accepting tools at once on Revit 2023. _bic_map() resolves every
    # entry through getattr() and silently drops what this Revit doesn't have,
    # so the same source runs on 2023.1 and 2026.4 with different coverage.
    _BIC_NAMES = {
        # Architectural
        'Walls': 'OST_Walls', 'Floors': 'OST_Floors', 'Ceilings': 'OST_Ceilings',
        'Roofs': 'OST_Roofs', 'Doors': 'OST_Doors', 'Windows': 'OST_Windows',
        'Columns': 'OST_Columns', 'Stairs': 'OST_Stairs',
        'Railings': 'OST_StairsRailing', 'Ramps': 'OST_Ramps',
        'CurtainPanels': 'OST_CurtainWallPanels',
        'CurtainWallMullions': 'OST_CurtainWallMullions',
        'CurtainSystems': ('OST_Curtain_Systems', 'OST_CurtainSystems'),
        'Furniture': 'OST_Furniture', 'FurnitureSystems': 'OST_FurnitureSystems',
        'Casework': 'OST_Casework', 'GenericModel': 'OST_GenericModel',
        'SpecialityEquipment': 'OST_SpecialityEquipment',
        'Entourage': 'OST_Entourage', 'Planting': 'OST_Planting',
        'PlantingArea': 'OST_Planting',      # legacy alias — keep, was public
        'Parking': 'OST_Parking', 'Site': 'OST_Site',
        'Topography': 'OST_Topography',      # 2023 + 2026 (deprecated, present)
        'Toposolid': 'OST_Toposolid',        # 2024+ ONLY — the guard's reason
        'Mass': 'OST_Mass', 'Parts': 'OST_Parts', 'Assemblies': 'OST_Assemblies',
        'Roads': 'OST_Roads',
        # Structural
        'Beams': 'OST_StructuralFraming',
        'StructuralFraming': 'OST_StructuralFraming',
        'StructuralColumns': 'OST_StructuralColumns',
        'StructuralFoundations': ('OST_StructuralFoundation',
                                  'OST_StructuralFoundations'),
        'StructuralTrusses': ('OST_StructuralTruss', 'OST_StructuralTrusses'),
        'StructuralStiffeners': ('OST_StructuralStiffener',
                                 'OST_StructuralStiffeners'),
        'StructuralConnections': ('OST_StructConnections',
                                  'OST_StructuralConnections'),
        'Rebar': 'OST_Rebar',
        # MEP — piping
        'Pipes': 'OST_PipeCurves', 'FlexPipes': 'OST_FlexPipeCurves',
        'PipeFittings': 'OST_PipeFitting', 'PipeAccessories': 'OST_PipeAccessory',
        'PipeInsulations': 'OST_PipeInsulations',
        'PlumbingFixtures': 'OST_PlumbingFixtures', 'Sprinklers': 'OST_Sprinklers',
        # MEP — ducting
        'Ducts': 'OST_DuctCurves', 'FlexDucts': 'OST_FlexDuctCurves',
        'DuctFittings': 'OST_DuctFitting', 'DuctAccessories': 'OST_DuctAccessory',
        'DuctInsulations': 'OST_DuctInsulations',
        'AirTerminals': 'OST_DuctTerminal',
        'MechanicalEquipment': 'OST_MechanicalEquipment',
        # MEP — electrical
        'CableTrays': 'OST_CableTray', 'CableTrayFittings': 'OST_CableTrayFitting',
        'Conduits': 'OST_Conduit', 'ConduitFittings': 'OST_ConduitFitting',
        'Wires': 'OST_Wire', 'LightingFixtures': 'OST_LightingFixtures',
        'LightingDevices': 'OST_LightingDevices',
        'ElectricalFixtures': 'OST_ElectricalFixtures',
        'ElectricalEquipment': 'OST_ElectricalEquipment',
        'CommunicationDevices': 'OST_CommunicationDevices',
        'DataDevices': 'OST_DataDevices',
        'FireAlarmDevices': 'OST_FireAlarmDevices',
        'SecurityDevices': 'OST_SecurityDevices',
        'TelephoneDevices': 'OST_TelephoneDevices',
        'NurseCallDevices': 'OST_NurseCallDevices',
        # Spatial — NOTE these are SpatialElements: a collector returns UNPLACED
        # rooms/areas/spaces too (Area == 0). Colour/tag paths should skip those.
        'Rooms': 'OST_Rooms', 'Areas': 'OST_Areas', 'Spaces': 'OST_MEPSpaces',
        'RoomSeparationLines': 'OST_RoomSeparationLines',
        'AreaSchemeLines': 'OST_AreaSchemeLines',
        # Datum / view / organisation
        'Grids': 'OST_Grids', 'Levels': 'OST_Levels',
        'ReferencePlanes': 'OST_CLines',
        'ScopeBoxes': 'OST_VolumeOfInterest',
        'Sheets': 'OST_Sheets', 'Views': 'OST_Views', 'Viewports': 'OST_Viewports',
        'Materials': 'OST_Materials', 'RevitLinks': 'OST_RvtLinks',
        # Annotation
        'TextNotes': 'OST_TextNotes', 'Dimensions': 'OST_Dimensions',
        'GenericAnnotations': 'OST_GenericAnnotation',
        'RevisionClouds': 'OST_RevisionClouds',
        'DetailItems': 'OST_DetailComponents',
        'Lines': 'OST_Lines',
        'DoorTags': 'OST_DoorTags', 'WindowTags': 'OST_WindowTags',
        'RoomTags': 'OST_RoomTags', 'AreaTags': 'OST_AreaTags',
        'SpaceTags': 'OST_MEPSpaceTags', 'WallTags': 'OST_WallTags',
        'FloorTags': 'OST_FloorTags', 'CeilingTags': 'OST_CeilingTags',
        'StructuralFramingTags': 'OST_StructuralFramingTags',
        'StructuralColumnTags': 'OST_StructuralColumnTags',
        'GenericModelTags': 'OST_GenericModelTags',
        'MultiCategoryTags': 'OST_MultiCategoryTags',
        'KeynoteTags': 'OST_KeynoteTags',
    }

    # Vietnamese (and a few loose English) synonyms → canonical key above.
    # Kept OUT of _BIC_NAMES so the canonical list (and the `did_you_mean`
    # suggestions) stay clean, while "tô đỏ móng" / "ẩn hết dầm" still resolve.
    # Matched lowercase.
    _BIC_ALIASES = {
        'tường': 'Walls', 'tuong': 'Walls', 'sàn': 'Floors', 'san': 'Floors',
        'trần': 'Ceilings', 'tran': 'Ceilings', 'mái': 'Roofs', 'mai': 'Roofs',
        'cửa': 'Doors', 'cua': 'Doors', 'cửa đi': 'Doors',
        'cửa sổ': 'Windows', 'cua so': 'Windows',
        'cột': 'Columns', 'cot': 'Columns',
        'cột kết cấu': 'StructuralColumns', 'cot ket cau': 'StructuralColumns',
        'dầm': 'Beams', 'dam': 'Beams',
        'móng': 'StructuralFoundations', 'mong': 'StructuralFoundations',
        'thép': 'Rebar', 'thep': 'Rebar', 'cốt thép': 'Rebar',
        'phòng': 'Rooms', 'phong': 'Rooms',
        'diện tích': 'Areas', 'dien tich': 'Areas',
        'cầu thang': 'Stairs', 'cau thang': 'Stairs',
        'lan can': 'Railings', 'dốc': 'Ramps',
        'nội thất': 'Furniture', 'noi that': 'Furniture',
        'tủ': 'Casework', 'tu': 'Casework',
        'đèn': 'LightingFixtures', 'den': 'LightingFixtures',
        'thiết bị vệ sinh': 'PlumbingFixtures', 'tbvs': 'PlumbingFixtures',
        'ống': 'Pipes', 'ong': 'Pipes', 'ống nước': 'Pipes',
        'ống gió': 'Ducts', 'ong gio': 'Ducts', 'gió': 'Ducts',
        'lưới trục': 'Grids', 'luoi truc': 'Grids', 'trục': 'Grids',
        'cao độ': 'Levels', 'cao do': 'Levels', 'tầng': 'Levels',
        'vách kính': 'CurtainPanels', 'kính': 'CurtainPanels',
        'chú thích': 'TextNotes', 'ghi chú': 'TextNotes',
        'kích thước': 'Dimensions', 'kich thuoc': 'Dimensions',
        'link': 'RevitLinks', 'vật liệu': 'Materials',
        'column': 'Columns', 'wall': 'Walls', 'floor': 'Floors',
        'door': 'Doors', 'window': 'Windows', 'room': 'Rooms',
        'beam': 'Beams', 'foundation': 'StructuralFoundations',
        'foundations': 'StructuralFoundations',
    }

    def _bic_map(self):
        """Human-facing category name → BuiltInCategory, for THIS Revit version.

        Built once per server instance from _BIC_NAMES; entries whose member
        doesn't exist in the running Revit are dropped rather than raising.
        Used by every category-accepting tool (override_color, operate_element,
        color_elements, ai_element_filter, select_elements, bulk_set_parameter,
        tag_elements, set_element_workset, create_schedule) so they all speak
        one vocabulary.
        """
        cached = getattr(self, '_bic_cache', None)
        if cached is not None:
            return cached
        from Autodesk.Revit.DB import BuiltInCategory as B
        out = {}
        for label, members in self._BIC_NAMES.items():
            if isinstance(members, tuple):
                candidates = members
            else:
                candidates = (members,)
            for member in candidates:
                bic = getattr(B, member, None)
                if bic is not None:
                    out[label] = bic
                    break
        self._bic_cache = out
        return out

    def _resolve_bic(self, cat_arg):
        """Category name -> (bic, canonical_name, error_dict). Exactly one of
        bic / error_dict is non-None. Case-insensitive, and accepts the
        Vietnamese synonyms in _BIC_ALIASES ("móng" -> StructuralFoundations).
        Unknown names get the CLOSEST matches + retry hint. Shared by every tool
        that accepts a `category` argument so they all speak the same vocabulary
        and return the same unknown-category contract."""
        CATEGORY_MAP = self._bic_map()
        bic = CATEGORY_MAP.get(cat_arg)
        name = cat_arg
        raw = u'{}'.format(cat_arg or u'').strip()
        low = raw.lower()
        if bic is None and raw:
            for _k in CATEGORY_MAP:
                if _k.lower() == low:
                    bic = CATEGORY_MAP[_k]
                    name = _k
                    break
        if bic is None and low:
            # Synonym layer (Vietnamese / singular English) → canonical key.
            alias = self._BIC_ALIASES.get(low)
            if alias:
                bic = CATEGORY_MAP.get(alias)
                name = alias
        if bic is None:
            # The full list is ~90 names — dumping it on every typo costs
            # hundreds of tokens per retry, so send the near misses instead.
            keys = sorted(CATEGORY_MAP.keys())
            near = [k for k in keys if low and (low in k.lower()
                                                or k.lower().startswith(low[:4]))]
            return None, cat_arg, {
                'error': "Unknown category '{}'.".format(cat_arg),
                'did_you_mean': near[:8],
                'total_supported': len(keys),
                'hint': ('Retry with one of did_you_mean, or another standard '
                         'Revit category name (Walls, StructuralFoundations, '
                         'Ducts, Areas, ...).')}
        return bic, name, None

    # ── Element finding: saved id sets ("@handle") ───────────────────────────
    #
    # The biggest token cost in this server was never the answer — it was the
    # ids. "Move every element of the groups named _F1_ onto workset _F1_"
    # meant paging thousands of element ids OUT of ai_element_filter, through
    # the model's context, and back INTO set_element_workset: tens of thousands
    # of tokens, and silently truncated the moment the id list outgrew the
    # filter's page size.
    #
    # ai_element_filter(store_as="x") parks the FULL matched id set here, on
    # the server, and hands back the handle "@x". Every tool that takes
    # element_ids accepts that handle instead (expanded in _expand_id_handles
    # before dispatch), so the ids never enter the conversation at all.
    #
    # Sets live in memory for the Revit session, keyed by document title.
    # Nothing invalidates them: they are a token shortcut, not a cache of model
    # state, so a set is only ever as fresh as the filter that built it.
    _SELSET_MAX = 24

    def _selset_key(self, doc):
        try:
            return doc.Title
        except Exception:
            return '<doc>'

    def _selset_bucket(self, doc):
        sets = getattr(self, '_selection_sets', None)
        if sets is None:
            sets = {}
            self._selection_sets = sets
        return sets.setdefault(self._selset_key(doc), {})

    def _selset_store(self, doc, name, ids):
        bucket = self._selset_bucket(doc)
        bucket.pop(name, None)
        bucket[name] = [int(v) for v in ids]
        while len(bucket) > self._SELSET_MAX:
            bucket.pop(next(iter(bucket)))
        return len(bucket[name])

    def _selset_get(self, doc, name):
        return self._selset_bucket(doc).get(name)

    # Argument names across the registry that carry Revit element ids and may
    # therefore be given a "@name" handle in place of a literal list.
    _HANDLE_ARG_KEYS = ('element_ids', 'ids', 'room_ids', 'view_ids',
                        'sheet_ids', 'element_id')

    def _expand_id_handles(self, doc, tool_name, arguments):
        """Replace "@name" saved-set references in id arguments with real ids.

        Mutates `arguments` in place. Returns an error dict for an unknown
        handle, None otherwise — expanding a typo to an empty list would make
        the tool either do nothing or (for the tools that fall back to the
        active selection, or to the whole category) do something else
        entirely, and report success either way.
        """
        if doc is None or not isinstance(arguments, dict):
            return None
        for key in self._HANDLE_ARG_KEYS:
            if key not in arguments:
                continue
            raw = arguments.get(key)
            if raw is None:
                continue
            items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            out, used = [], False
            for item in items:
                text = item.strip() if hasattr(item, 'strip') else None
                if text and text.startswith('@'):
                    ids = self._selset_get(doc, text[1:])
                    if ids is None:
                        return {
                            'error': "No saved element set named '{}'.".format(
                                text[1:]),
                            'saved_sets': sorted(self._selset_bucket(doc)),
                            'hint': ('Saved sets last for this Revit session and '
                                     'this document only. Re-run ai_element_filter '
                                     'with store_as="{}" to rebuild it, or pass '
                                     'literal ids.').format(text[1:]),
                            'tool': tool_name,
                        }
                    out.extend(ids)
                    used = True
                else:
                    out.append(item)
            if not used:
                continue
            if key == 'element_id':
                # Singular argument: a handle only makes sense here when it
                # resolved to exactly one element.
                if len(out) != 1:
                    return {
                        'error': ('{} takes ONE element, but that saved set holds '
                                  '{}.').format(tool_name, len(out)),
                        'hint': ('Use a tool that accepts element_ids, or pass a '
                                 'single numeric id.'),
                        'tool': tool_name,
                    }
                arguments[key] = out[0]
            else:
                arguments[key] = out
        return None

    # ── Element finding: shared fast collector ───────────────────────────────
    _FILTER_FIELDS = ('id', 'name', 'category', 'level', 'type', 'workset',
                      'group', 'param_value', 'text', 'view')
    _FILTER_DEFAULT_FIELDS = ('id', 'name', 'category', 'level', 'param_value')
    _GROUP_BY_KEYS = ('category', 'name', 'type', 'level', 'workset', 'group')

    def _worksets_matching(self, doc, needle):
        """(WorksetId list, name list, error) for the user worksets whose name
        contains `needle`, case-insensitively."""
        from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
        if not doc.IsWorkshared:
            return None, None, {
                'error': 'This document is not workshared — it has no worksets.',
                'hint': 'Drop the workset filter.',
                'tool': 'ai_element_filter'}
        low = needle.lower()
        all_ws = list(FilteredWorksetCollector(doc)
                      .OfKind(WorksetKind.UserWorkset).ToWorksets())
        hits = [ws for ws in all_ws if low in (ws.Name or '').lower()]
        if not hits:
            return None, None, {
                'error': "No workset name contains '{}'.".format(needle),
                'worksets_in_model': sorted(ws.Name for ws in all_ws)[:40],
                'hint': 'Retry with one of worksets_in_model.',
                'tool': 'ai_element_filter'}
        return [ws.Id for ws in hits], [ws.Name for ws in hits], None

    def _group_member_ids(self, doc, needle):
        """(ids, matched-group info, error) for every element inside the model
        or detail groups whose instance name OR group-type name contains
        `needle`.

        There is no ElementGroupFilter in the Revit API, so "the elements
        inside the groups called _F1_" cannot be expressed as a collector
        filter at all — Group.GetMemberIds() is the only route, and it is also
        the fast one. Nested groups are walked (depth-capped) so a member that
        is itself a group contributes its own members; the matched group
        instances are part of the id set too, because a group instance carries
        a workset and a type of its own.
        """
        from Autodesk.Revit.DB import FilteredElementCollector, Group
        low = (needle or '').lower()
        groups = list(FilteredElementCollector(doc).OfClass(Group)
                      .WhereElementIsNotElementType())

        def _names(grp):
            try:
                inst = elem_name(grp) or ''
            except Exception:
                inst = ''
            try:
                gtype = doc.GetElement(grp.GetTypeId())
                typ = (elem_name(gtype) or '') if gtype is not None else ''
            except Exception:
                typ = ''
            return inst, typ

        hits = []
        for grp in groups:
            inst, typ = _names(grp)
            if low in inst.lower() or low in typ.lower():
                hits.append((grp, typ or inst))
        if not hits:
            catalogue = set()
            for grp in groups:
                inst, typ = _names(grp)
                if typ or inst:
                    catalogue.add(typ or inst)
            return None, None, {
                'error': "No group name contains '{}'.".format(needle),
                'groups_in_model': sorted(catalogue)[:30],
                'total_groups': len(groups),
                'hint': ('Retry with one of groups_in_model, or drop group_name. '
                         'The match is a case-insensitive substring of the group '
                         'instance name or of its group type name.'),
                'tool': 'ai_element_filter'}

        ids, seen, matched = [], set(), []

        def _walk(grp, depth):
            if depth > 8:
                return
            try:
                members = grp.GetMemberIds()
            except Exception:
                return
            for mid in members:
                val = eid_value(mid)
                if val in seen:
                    continue
                seen.add(val)
                ids.append(val)
                sub = doc.GetElement(mid)
                if isinstance(sub, Group):
                    _walk(sub, depth + 1)

        for grp, label in hits:
            val = eid_value(grp.Id)
            if val not in seen:
                seen.add(val)
                ids.append(val)
            matched.append({'id': val, 'name': label})
            _walk(grp, 0)
        return ids, matched, None

    def _find_elements(self, doc, arguments):
        """(elements, meta, error) — the shared element finder.

        Every narrowing Revit can do in native code is pushed into the
        collector: category, workset and level are quick filters, and group
        membership restricts the collector's id set up front. Only the checks
        Revit has no filter for (type name, element name, parameter substring)
        run per element — and only over whatever survived the quick filters,
        instead of over every element in the model as before.
        """
        from Autodesk.Revit.DB import (FilteredElementCollector, ElementId,
                                       ElementFilter, ElementWorksetFilter,
                                       LogicalOrFilter, BuiltInCategory, Level)
        try:
            from System.Collections.Generic import List as NetList
        except Exception:
            NetList = None

        # Name caches are per-query, not per-session: level / type / workset
        # ids are only meaningful inside one document, and this server switches
        # documents. A cache that outlived the query would answer a Level 3
        # lookup in model B with model A's level name.
        self._level_name_cache = {}
        self._type_name_cache = {}
        self._workset_name_cache = {}

        meta = {}
        # 1 · Categories — one or many.
        cat_args = []
        if arguments.get('category'):
            cat_args.append(arguments.get('category'))
        for extra in (arguments.get('categories') or []):
            if extra:
                cat_args.append(extra)
        bics, cat_names = [], []
        for raw in cat_args:
            bic, name, err = self._resolve_bic(raw)
            if err:
                err['tool'] = 'ai_element_filter'
                return None, None, err
            if name not in cat_names:
                cat_names.append(name)
                bics.append(bic)
        meta['categories'] = cat_names

        group_arg = (arguments.get('group_name') or '').strip()
        ws_arg    = (arguments.get('workset') or '').strip()
        lvl_arg   = (arguments.get('level_name') or '').strip()
        type_arg  = (arguments.get('type_name') or '').strip()
        name_arg  = (arguments.get('name_contains') or '').strip()

        if not (bics or group_arg or ws_arg or lvl_arg or type_arg or name_arg):
            return None, None, {
                'error': 'ai_element_filter needs at least one filter.',
                'hint': ('Pass category (or categories), and/or group_name, '
                         'workset, level_name, type_name, name_contains. For a '
                         'whole-model overview call analyze_model_statistics — '
                         'an unfiltered scan of every element is never the '
                         'right query.'),
                'tool': 'ai_element_filter'}

        # 2 · Group membership restricts the collector's id set up front.
        base_ids = None
        if group_arg:
            base_ids, matched_groups, gerr = self._group_member_ids(
                doc, group_arg)
            if gerr:
                return None, None, gerr
            meta['group_count'] = len(matched_groups)
            meta['groups_matched'] = matched_groups[:20]
            if not base_ids:
                return [], meta, None

        if base_ids is not None and NetList is not None:
            seed = NetList[ElementId]()
            for val in base_ids:
                seed.Add(make_eid(val))
            collector = FilteredElementCollector(doc, seed)
        else:
            collector = FilteredElementCollector(doc)
        collector = collector.WhereElementIsNotElementType()

        # 3 · Category quick filter.
        py_cats = None
        if len(bics) == 1:
            collector = collector.OfCategory(bics[0])
        elif bics:
            applied = False
            try:
                from Autodesk.Revit.DB import ElementMulticategoryFilter
                if NetList is not None:
                    net_cats = NetList[BuiltInCategory]()
                    for bic in bics:
                        net_cats.Add(bic)
                    collector = collector.WherePasses(
                        ElementMulticategoryFilter(net_cats))
                    applied = True
            except Exception:
                applied = False
            if not applied:
                py_cats = set(n.lower() for n in cat_names)

        # 4 · Workset quick filter.
        if ws_arg:
            ws_ids, ws_names, werr = self._worksets_matching(doc, ws_arg)
            if werr:
                return None, None, werr
            try:
                ws_filters = [ElementWorksetFilter(w) for w in ws_ids]
                if len(ws_filters) == 1:
                    collector = collector.WherePasses(ws_filters[0])
                elif NetList is not None:
                    net_f = NetList[ElementFilter]()
                    for one in ws_filters:
                        net_f.Add(one)
                    collector = collector.WherePasses(LogicalOrFilter(net_f))
                meta['worksets_matched'] = ws_names
            except Exception as exc:
                return None, None, {
                    'error': 'Workset filter failed: {}'.format(exc),
                    'tool': 'ai_element_filter'}

        # 5 · Level. Deliberately NOT ElementLevelFilter: that filter only sees
        #     elements carrying a LevelId, while host-based elements (doors,
        #     windows, most families) report their level through a parameter —
        #     so a quick-filtered miss is indistinguishable from "nothing on
        #     that level". Levels are resolved to names once and compared
        #     against each element's resolved level name, which is cached.
        py_level = None
        if lvl_arg:
            low = lvl_arg.lower()
            level_names = []
            all_names = []
            for lvl in FilteredElementCollector(doc).OfClass(Level):
                try:
                    lname = elem_name(lvl) or ''
                except Exception:
                    continue
                all_names.append(lname)
                if low in lname.lower():
                    level_names.append(lname)
            if not level_names:
                return None, None, {
                    'error': "No level name contains '{}'.".format(lvl_arg),
                    'levels_in_model': sorted(all_names)[:40],
                    'hint': 'Retry with one of levels_in_model.',
                    'tool': 'ai_element_filter'}
            meta['levels_matched'] = level_names
            py_level = set(n.lower() for n in level_names)

        # 6 · Type name — resolved to a set of type ids ONCE, then compared by
        #     id per element instead of reading a name off every instance.
        type_ids = None
        if type_arg:
            low = type_arg.lower()
            tcol = FilteredElementCollector(doc).WhereElementIsElementType()
            if len(bics) == 1:
                tcol = tcol.OfCategory(bics[0])
            type_ids, sample = set(), []
            for etype in tcol:
                try:
                    tname = elem_name(etype) or ''
                except Exception:
                    continue
                if low in tname.lower():
                    type_ids.add(eid_value(etype.Id))
                elif len(sample) < 400:
                    sample.append(tname)
            if not type_ids:
                return None, None, {
                    'error': "No element type name contains '{}'.".format(
                        type_arg),
                    'types_seen': sorted(set(sample))[:25],
                    'hint': ('Retry with one of types_seen, or call '
                             'get_available_family_types to list them.'),
                    'tool': 'ai_element_filter'}
            meta['type_matches'] = len(type_ids)

        elements = self._scan_collector(doc, collector, py_cats, py_level,
                                        type_ids, name_arg)
        return elements, meta, None

    def _scan_collector(self, doc, collector, py_cats, py_level, type_ids,
                        name_arg):
        """Apply the checks Revit has no native filter for, in one pass."""
        name_low = (name_arg or '').lower()
        out = []
        for elem in collector:
            try:
                if py_cats is not None:
                    cat = elem.Category
                    if cat is None or (cat.Name or '').lower() not in py_cats:
                        continue
                if type_ids is not None:
                    if eid_value(elem.GetTypeId()) not in type_ids:
                        continue
                if name_low:
                    try:
                        if name_low not in (elem_name(elem) or '').lower():
                            continue
                    except Exception:
                        continue
                if py_level is not None:
                    if (self._element_level_name(doc, elem) or '').lower() \
                            not in py_level:
                        continue
                out.append(elem)
            except Exception:
                continue
        return out

    def _element_level_name(self, doc, elem):
        """Level name of an element, cached by level id.

        Two-step, same as get_material_quantities: LevelId first, then a
        'Level' parameter for host-based elements that have no LevelId.
        """
        cache = getattr(self, '_level_name_cache', None)
        if cache is None:
            cache = {}
            self._level_name_cache = cache
        try:
            lid = eid_value(elem.LevelId)
        except Exception:
            lid = -1
        if lid > 0:
            if lid not in cache:
                try:
                    lvl = doc.GetElement(elem.LevelId)
                    cache[lid] = (elem_name(lvl) if lvl is not None else '')
                except Exception:
                    cache[lid] = ''
            if cache[lid]:
                return cache[lid]
        try:
            par = elem.LookupParameter('Level')
            if par:
                return par.AsValueString() or ''
        except Exception:
            pass
        return ''

    def _element_workset_name(self, doc, elem):
        cache = getattr(self, '_workset_name_cache', None)
        if cache is None:
            cache = {}
            self._workset_name_cache = cache
        try:
            wid = elem.WorksetId
            key = eid_value(wid)
        except Exception:
            return ''
        if key not in cache:
            try:
                cache[key] = doc.GetWorksetTable().GetWorkset(wid).Name
            except Exception:
                cache[key] = ''
        return cache[key]

    def _element_type_name(self, doc, elem):
        cache = getattr(self, '_type_name_cache', None)
        if cache is None:
            cache = {}
            self._type_name_cache = cache
        try:
            tid = eid_value(elem.GetTypeId())
        except Exception:
            return ''
        if tid <= 0:
            return ''
        if tid not in cache:
            try:
                etype = doc.GetElement(elem.GetTypeId())
                cache[tid] = (elem_name(etype) if etype is not None else '')
            except Exception:
                cache[tid] = ''
        return cache[tid]

    def _element_group_name(self, doc, elem):
        try:
            gid = elem.GroupId
            if eid_value(gid) <= 0:
                return ''
            grp = doc.GetElement(gid)
            if grp is None:
                return ''
            gtype = doc.GetElement(grp.GetTypeId())
            return (elem_name(gtype) if gtype is not None
                    else elem_name(grp)) or ''
        except Exception:
            return ''

    def _filter_row(self, doc, elem, fields, param_val):
        """One result row, carrying only the requested fields.

        Every field costs tokens on every row, so the caller picks. `level`,
        `type` and `workset` also cost a document lookup per element when they
        are not already cached — leaving them out is a speed win as well.
        """
        row = {}
        for field in fields:
            try:
                if field == 'id':
                    row['id'] = eid_value(elem.Id)
                elif field == 'name':
                    row['name'] = elem_name(elem)
                elif field == 'category':
                    row['category'] = elem.Category.Name if elem.Category else ''
                elif field == 'level':
                    row['level'] = self._element_level_name(doc, elem)
                elif field == 'type':
                    row['type'] = self._element_type_name(doc, elem)
                elif field == 'workset':
                    row['workset'] = self._element_workset_name(doc, elem)
                elif field == 'group':
                    row['group'] = self._element_group_name(doc, elem)
                elif field == 'param_value':
                    row['param_value'] = param_val
                elif field == 'text':
                    if hasattr(elem, 'Text'):
                        row['text'] = elem.Text
                elif field == 'view':
                    ov = doc.GetElement(elem.OwnerViewId)
                    if ov is not None:
                        row['view'] = elem_name(ov)
            except Exception:
                pass
        return row

    def _group_key_value(self, doc, elem, key, param_val):
        if key == 'category':
            try:
                return elem.Category.Name if elem.Category else ''
            except Exception:
                return ''
        if key == 'name':
            try:
                return elem_name(elem) or ''
            except Exception:
                return ''
        if key == 'type':
            return self._element_type_name(doc, elem)
        if key == 'level':
            return self._element_level_name(doc, elem)
        if key == 'workset':
            return self._element_workset_name(doc, elem)
        if key == 'group':
            return self._element_group_name(doc, elem)
        if key == 'param_value':
            return param_val
        return ''

    def _solid_fill_id(self, doc):
        """ElementId of a solid FillPatternElement, or InvalidElementId.

        Shared by revit_override_color and color_elements. NOTE the sentinel:
        ElementId(-1) is not version-safe — Revit 2025+ dropped the Int32
        constructor and IronPython dies with "Multiple targets could match".
        """
        try:
            from Autodesk.Revit.DB import (FilteredElementCollector,
                                           FillPatternElement, ElementId)
            for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
                pattern = fp.GetFillPattern()
                if pattern and pattern.IsSolidFill:
                    return fp.Id
        except Exception:
            pass
        from Autodesk.Revit.DB import ElementId
        return ElementId.InvalidElementId

    # Colour vocabulary shared by revit_override_color and create_view_filter.
    # Vietnamese names are in it because the office types "tô đỏ tường" and the
    # model forwards the word it was handed, accents and all.
    CSS_COLORS = {
        'red': (255, 0, 0), 'do': (255, 0, 0), u'đỏ': (255, 0, 0),
        'green': (0, 255, 0), 'xanh la': (0, 255, 0), u'xanh lá': (0, 255, 0),
        'blue': (0, 0, 255), 'xanh': (0, 0, 255), u'xanh dương': (0, 0, 255),
        'orange': (255, 165, 0), 'cam': (255, 165, 0),
        'cyan': (0, 255, 255),
        'yellow': (255, 255, 0), 'vang': (255, 255, 0), u'vàng': (255, 255, 0),
        'magenta': (255, 0, 255),
        'black': (0, 0, 0), 'den': (0, 0, 0), u'đen': (0, 0, 0),
        'white': (255, 255, 255), 'trang': (255, 255, 255), u'trắng': (255, 255, 255),
        'gray': (128, 128, 128), 'grey': (128, 128, 128),
        'xam': (128, 128, 128), u'xám': (128, 128, 128),
        'pink': (255, 192, 203), 'hong': (255, 192, 203), u'hồng': (255, 192, 203),
        'purple': (128, 0, 128), 'tim': (128, 0, 128), u'tím': (128, 0, 128),
        'violet': (238, 130, 238),
    }

    def _parse_color(self, color_str):
        """(r, g, b) for a colour, or None if unparseable.

        Accepts a CSS or Vietnamese name, #rrggbb, #rgb, an [r, g, b] sequence,
        or the STRING form of one ("[0, 0, 255]", "0,0,255"). The string form
        matters because the MCP schema declares `color` as a string, so a
        caller that sends an RGB array has it stringified before it arrives —
        which used to land here as an unknown value and, in
        revit_override_color, silently become red.
        """
        if color_str is None or color_str == u'':
            return None

        if isinstance(color_str, (list, tuple)):
            parts = list(color_str)
        else:
            s = u'{}'.format(color_str).lower().strip()
            if s in self.CSS_COLORS:
                return self.CSS_COLORS[s]
            if s.startswith('#'):
                h = s[1:]
                try:
                    if len(h) == 3:
                        h = u''.join(c * 2 for c in h)
                    if len(h) == 6:
                        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                except ValueError:
                    return None
                return None
            parts = [p for p in s.strip('[]() ').replace(';', ',').split(',')
                     if p.strip() != u'']

        if len(parts) != 3:
            return None
        try:
            rgb = tuple(int(float(u'{}'.format(p).strip())) for p in parts)
        except (TypeError, ValueError):
            return None
        return rgb if all(0 <= c <= 255 for c in rgb) else None

    def _color_error(self, color_str):
        """Error dict for a colour that _parse_color rejected."""
        return {
            'error': u"Unrecognised colour '{}'.".format(color_str),
            'supported_colors': sorted(set(
                k for k in self.CSS_COLORS if all(ord(c) < 128 for c in k))),
            'hint': ('Use a colour name, #rrggbb, or an [r,g,b] triple with '
                     'values 0-255. An unknown colour is refused rather than '
                     'silently painted red.'),
        }

    def _apply_param_value(self, param, value):
        """Coerce a string value onto a Revit parameter honouring its storage
        type. Returns (ok, error). Mirrors the single-element set_parameter tool
        so bulk_set_parameter behaves identically per element."""
        from Autodesk.Revit.DB import StorageType, ElementId
        if param is None:
            return False, 'parameter not found'
        if param.IsReadOnly:
            return False, 'read-only'
        try:
            st = param.StorageType
            if st == StorageType.String:
                param.Set(value)
            elif st == StorageType.Double:
                parsed = False
                try:
                    parsed = param.SetValueString(value)
                except Exception:
                    parsed = False
                if not parsed:
                    param.Set(float(value))
            elif st == StorageType.Integer:
                param.Set(int(float(value)))
            elif st == StorageType.ElementId:
                param.Set(make_eid(int(value)))
            else:
                return False, 'unsupported storage type'
            return True, None
        except Exception as e:
            return False, str(e)

    def _execute_tool_in_context(self, tool_name, arguments, uiapp=None):
        """Execute a Revit tool directly (must be inside Revit context thread).

        Target document resolution: 1) the active view's document
        (pyrevit.revit.doc — what the user sees on screen), 2) the uiapp
        Revit passed into ExternalEvent.Execute (engine-independent — the
        per-engine pyrevit.HOST_APP has no UIApplication when the AppDomain-
        anchored singleton was created in a startup/hook engine), 3) the
        single open document when unambiguous (_recover_active_document),
        4) an actionable error listing open documents — never a guess. Use
        switch_active_document / open_document to change the target.
        """
        try:
            from Autodesk.Revit.DB import (FilteredElementCollector, ViewSheet,
                                           BuiltInCategory, Level, ElementId,
                                           Transaction, ElementLevelFilter)
            from pyrevit import revit
            doc = revit.doc
            uidoc = revit.uidoc
            if doc is None and tool_name not in self._DOCLESS_TOOLS:
                _ui = uiapp or getattr(self, '_cached_uiapp', None)
                if _ui is not None:
                    try:
                        _ud = _ui.ActiveUIDocument
                        if _ud is not None and _ud.Document is not None:
                            doc, uidoc = _ud.Document, _ud
                    except Exception:
                        pass
            if doc is None and tool_name not in self._DOCLESS_TOOLS:
                doc, uidoc, no_doc_err = self._recover_active_document(
                    uidoc, uiapp)
                if no_doc_err is not None:
                    no_doc_err['tool'] = tool_name
                    return no_doc_err
        except ImportError:
            return {'error': 'Revit API not available', 'tool': tool_name}

        # ── Saved-set handles ("@name") ──────────────────────────────────────
        # Expanded once, here, for EVERY tool rather than in each id-consuming
        # branch: the branches below all read arguments['element_ids'] and must
        # see plain integers whether the caller passed ids or a handle.
        _handle_err = self._expand_id_handles(doc, tool_name, arguments)
        if _handle_err is not None:
            return _handle_err

        # ── Teaching sandbox write-guard ─────────────────────────────────────
        # When teaching mode is ON, a model-MODIFYING tool may only run against
        # the designated sandbox document — so the teacher (or the assistant)
        # can never damage the real project while capturing training data. Runs
        # HERE, on the Revit main thread, where `doc` is resolved (reading a
        # doc's Title/PathName off the HTTP thread would be unsafe). Read tools
        # are unaffected; teaching mode OFF is a no-op.
        if self._teaching_enabled and doc is not None:
            try:
                from Intelligence.tool_schema import is_model_modifying
                modifies = is_model_modifying(tool_name)
            except Exception:
                # Fail safe: if we can't tell, treat known writers as writers.
                modifies = tool_name in self._WRITE_TOOLS
            from core import teaching
            if teaching.should_block_write(
                    True, modifies, self._is_sandbox_doc(doc)):
                return self._teach_sandbox_reject(tool_name)

        # ── Agent request TransactionGroup (B4) — WITHDRAWN, DO NOT REINSTATE ──
        # These pseudo-tools used to open a TransactionGroup here and leave it
        # open so a whole assistant request would collapse into ONE undo entry.
        # That is not possible from an ExternalEvent: Revit force-closes every
        # transaction phase an event handler leaves open when Execute returns.
        # It says so in the journal, once per write:
        #
        #   "An API event handler left some transaction phases open for the
        #    active document. In effect of that, all uncommitted changes made
        #    by this event handler will be discarded."
        #
        # So the group never survived the very Execute that started it (5/5
        # writes in the 2026-08-11 crash journal, and NO other journal in that
        # folder carries the warning at all) — B4 delivered nothing. What it
        # DID deliver was a stale managed TransactionGroup handle pointing at a
        # group Revit had already terminated, which `__end_action_group` then
        # Assimilate()d, and which the next request's `__begin_action_group`
        # Assimilate()d a second time. Operating a dead native group is the
        # unhandled managed exception (0xe0434352) that took Revit down right
        # after the second request of a multi-step task.
        #
        # Kept as inert no-ops rather than deleted: an AppDomain-anchored
        # server instance from before a reload can still be called with these
        # names. Each tool keeps its own Transaction, so a multi-step request
        # is now N undo entries instead of the 1 it never actually produced.
        if tool_name in ('__begin_action_group', '__end_action_group'):
            self._release_stale_action_group()
            return {'success': False,
                    'note': ('Request-wide TransactionGroup is disabled: a '
                             'group cannot outlive the ExternalEvent that '
                             'opened it. Each tool commits its own '
                             'transaction.')}

        if tool_name == 'revit_get_active_view':
            view = doc.ActiveView
            if view is None:
                return {'error': 'No active view in Revit — open or activate a view first.'}
            return {
                'name': view.Name,
                'id': eid_value(view.Id),
                'type': str(view.ViewType)
            }

        elif tool_name == 'revit_get_selected_elements':
            selection = uidoc.Selection.GetElementIds()
            elements = []
            for eid in selection:
                elem = doc.GetElement(eid)
                elements.append({
                    'id': eid_value(eid),
                    'name': elem.Name if hasattr(elem, 'Name') else str(elem),
                    'category': elem.Category.Name if elem.Category else 'Unknown'
                })
            return {'selected_count': len(elements), 'elements': elements}

        elif tool_name == 'revit_get_project_info':
            info = doc.ProjectInformation
            return {
                'name': info.Name,
                'number': info.Number,
                'client': info.ClientName,
                'address': info.Address,
                'status': info.Status
            }

        elif tool_name == 'revit_list_views':
            from Autodesk.Revit.DB import View
            collector = FilteredElementCollector(doc).OfClass(View)
            raw_filter = arguments.get('view_type')
            # The filter used to be an EXACT, case-sensitive match against
            # str(view.ViewType) (".NET" names like "FloorPlan"), and a miss
            # returned an empty list rather than an error — so "floor_plan" made
            # the assistant announce the project had no floor plans. Normalise
            # both sides, and tell the caller what actually exists on a miss.
            def _norm(s):
                return u'{}'.format(s or u'').replace(u'_', u'').replace(
                    u' ', u'').replace(u'-', u'').lower()

            wanted = _norm(raw_filter) if raw_filter else None
            views = []
            present = set()
            for v in collector:
                if v.IsTemplate:
                    continue
                vtype = str(v.ViewType)
                present.add(vtype)
                if wanted is None or _norm(vtype) == wanted:
                    views.append({'name': v.Name, 'id': eid_value(v.Id),
                                  'type': vtype})
            if wanted is not None and not views:
                return {'error': "No views of type '{}' in this document.".format(
                            raw_filter),
                        'available_view_types': sorted(present),
                        'hint': ('Retry with one of available_view_types, or omit '
                                 'view_type to list every view.')}
            return {'count': len(views), 'views': views}

        elif tool_name == 'revit_list_sheets':
            collector = FilteredElementCollector(doc).OfClass(ViewSheet)
            sheets = []
            for s in collector:
                sheets.append({
                    'name': s.Name,
                    'number': s.SheetNumber,
                    'id': eid_value(s.Id)
                })
            return {'count': len(sheets), 'sheets': sheets}

        elif tool_name == 'collect_spellcheck_text':
            # One-shot collector for /english-spellcheck: gather EVERY
            # human-authored string in the model so the deterministic engine
            # can proofread it — not just Text Notes, but title-block labels,
            # the full Project Information, revision descriptions, view titles,
            # dimension text overrides, model text and schedule names. Read
            # only, runs on the Revit main thread (routed like any read tool).
            from Autodesk.Revit.DB import (BuiltInCategory as _B,
                                           BuiltInParameter as _BP, StorageType)
            from Snippets._compat import elem_name
            import re as _re
            items, counts = [], {}
            view_only = bool(arguments.get('view_only'))
            av_id = None
            try:
                if view_only and doc.ActiveView is not None:
                    av_id = doc.ActiveView.Id
            except Exception:
                av_id = None

            _vn_cache = {}

            def _view_name(vid):
                try:
                    k = eid_value(vid)
                    if k in _vn_cache:
                        return _vn_cache[k]
                    ov = doc.GetElement(vid)
                    nm = ov.Name if ov is not None else u''
                    _vn_cache[k] = nm
                    return nm
                except Exception:
                    return u''

            def _add(_id, _txt, _src, _view):
                try:
                    _txt = (_txt or u'').strip()
                except Exception:
                    return
                if not _txt or not any(c.isalpha() for c in _txt):
                    return
                items.append({'id': _id, 'text': _re.sub(r'[\r\n]+', u' / ', _txt),
                              'source': _src, 'view': _view or _src})
                counts[_src] = counts.get(_src, 0) + 1

            # 1. Text Notes (respects view_only)
            try:
                from Autodesk.Revit.DB import TextNote
                _c = (FilteredElementCollector(doc, av_id) if av_id is not None
                      else FilteredElementCollector(doc))
                for tn in _c.OfClass(TextNote).WhereElementIsNotElementType():
                    _add(eid_value(tn.Id), tn.Text, u'Text Note',
                         _view_name(tn.OwnerViewId))
            except Exception:
                pass

            # 2. Model Text (3D text)
            try:
                from Autodesk.Revit.DB import ModelText
                for mt in FilteredElementCollector(doc).OfClass(ModelText):
                    _add(eid_value(mt.Id), mt.Text, u'Model Text', u'Model Text')
            except Exception:
                pass

            # 3. Dimension text overrides ("VERFIY ON SITE" typos live here)
            try:
                from Autodesk.Revit.DB import Dimension
                _c = (FilteredElementCollector(doc, av_id) if av_id is not None
                      else FilteredElementCollector(doc))

                def _dim_add(_did, obj, vname):
                    for attr in ('ValueOverride', 'Above', 'Below',
                                 'Prefix', 'Suffix'):
                        try:
                            v = getattr(obj, attr, None)
                        except Exception:
                            v = None
                        if v:
                            _add(_did, v, u'Dimension text', vname)

                for dim in _c.OfClass(Dimension).WhereElementIsNotElementType():
                    vname = _view_name(dim.OwnerViewId)
                    _did = eid_value(dim.Id)
                    n = 0
                    try:
                        n = dim.NumberOfSegments
                    except Exception:
                        n = 0
                    if n and n > 0:
                        try:
                            for seg in dim.Segments:
                                _dim_add(_did, seg, vname)
                        except Exception:
                            pass
                    else:
                        _dim_add(_did, dim, vname)
            except Exception:
                pass

            if not view_only:
                # 4. Sheets — name + number
                try:
                    for s in FilteredElementCollector(doc).OfClass(ViewSheet):
                        _add(eid_value(s.Id), s.Name, u'Sheet name',
                             u'Sheet ' + (s.SheetNumber or u''))
                        _add(eid_value(s.Id), s.SheetNumber, u'Sheet number',
                             u'Sheet ' + (s.SheetNumber or u''))
                except Exception:
                    pass

                # 5. Views — name + "Title on Sheet"
                try:
                    from Autodesk.Revit.DB import View
                    for v in FilteredElementCollector(doc).OfClass(View):
                        if v.IsTemplate:
                            continue
                        _add(eid_value(v.Id), v.Name, u'View name', u'View')
                        try:
                            p = v.get_Parameter(_BP.VIEW_DESCRIPTION)
                            if p is not None:
                                _add(eid_value(v.Id), p.AsString(),
                                     u'Title on sheet', v.Name)
                        except Exception:
                            pass
                except Exception:
                    pass

                # 6. Rooms / Levels / Grids — names
                for _cat, _lbl in ((_B.OST_Rooms, u'Room name'),
                                   (_B.OST_Levels, u'Level name'),
                                   (_B.OST_Grids, u'Grid name')):
                    try:
                        for el in (FilteredElementCollector(doc).OfCategory(_cat)
                                   .WhereElementIsNotElementType()):
                            _add(eid_value(el.Id), elem_name(el), _lbl, _lbl)
                    except Exception:
                        pass

                # 7. Project Information — ALL string parameters
                try:
                    pinfo = doc.ProjectInformation
                    if pinfo is not None:
                        pid = eid_value(pinfo.Id)
                        for p in pinfo.Parameters:
                            try:
                                if p.StorageType != StorageType.String:
                                    continue
                                val = p.AsString()
                                if val:
                                    _add(pid, val,
                                         u'Project info: ' + p.Definition.Name,
                                         u'Project Information')
                            except Exception:
                                continue
                except Exception:
                    pass

                # 8. Title blocks — instance string-parameter values (labels)
                try:
                    for tb in (FilteredElementCollector(doc)
                               .OfCategory(_B.OST_TitleBlocks)
                               .WhereElementIsNotElementType()):
                        vname = _view_name(tb.OwnerViewId) or u'Title block'
                        tid = eid_value(tb.Id)
                        for p in tb.Parameters:
                            try:
                                if p.StorageType != StorageType.String:
                                    continue
                                val = p.AsString()
                                if val:
                                    _add(tid, val, u'Title block', vname)
                            except Exception:
                                continue
                except Exception:
                    pass

                # 9. Revisions — descriptions
                try:
                    from Autodesk.Revit.DB import Revision
                    for rv in FilteredElementCollector(doc).OfClass(Revision):
                        try:
                            _add(eid_value(rv.Id), rv.Description,
                                 u'Revision', u'Revision')
                        except Exception:
                            pass
                except Exception:
                    pass

                # 10. Schedule names
                try:
                    from Autodesk.Revit.DB import ViewSchedule
                    for sc in FilteredElementCollector(doc).OfClass(ViewSchedule):
                        try:
                            if sc.IsTemplate:
                                continue
                            _add(eid_value(sc.Id), sc.Name,
                                 u'Schedule name', u'Schedule')
                        except Exception:
                            pass
                except Exception:
                    pass

            return {'items': items, 'counts': counts, 'total': len(items)}

        elif tool_name == 'revit_get_element_info':
            from Autodesk.Revit.DB import ElementId
            eid = make_eid(int(arguments.get('element_id', 0)))
            elem = doc.GetElement(eid)
            if elem:
                params = {}
                for p in elem.Parameters:
                    try:
                        params[p.Definition.Name] = p.AsValueString() or p.AsString() or str(p.AsDouble())
                    except Exception:
                        pass
                return {
                    'id': eid_value(elem.Id),
                    'name': elem.Name if hasattr(elem, 'Name') else str(elem),
                    'category': elem.Category.Name if elem.Category else 'Unknown',
                    'parameters': params
                }
            return {'error': 'Element not found'}

        elif tool_name == 'revit_override_color':
            color_str = arguments.get('color')
            element_ids = arguments.get('element_ids')
            cat_arg = arguments.get('category')

            # Whole-category path — server-side collection, NO count cap.
            # The model used to ferry ids from ai_element_filter, whose
            # paging capped big requests at its `limit` (50 by default), so
            # "tô vàng sàn" on 500 floors colored only the first page.
            # Scoped to the active view: overrides are per-view, so coloring
            # elements invisible here would have no visible effect anyway.
            category_used = None
            if not element_ids and cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                element_ids = [eid_value(e.Id) for e in
                               FilteredElementCollector(doc, doc.ActiveView.Id)
                               .OfCategory(bic).WhereElementIsNotElementType()]
                if not element_ids:
                    return {'error': "No {} elements are visible in the "
                                     "active view.".format(cat_arg)}
                category_used = cat_arg

            # If element_ids is omitted or empty, use the active selection
            if not element_ids:
                selection = uidoc.Selection.GetElementIds()
                element_ids = [eid_value(eid) for eid in selection]

            if not element_ids:
                return {'error': 'No elements specified and no elements are selected in Revit.'}

            # Reject non-numeric entries up front. LLMs sometimes try to nest
            # a filter call as a string ("ai_element_filter(...)") — silently
            # skipping those in the loop below would return a fake success
            # (overridden_count=0) that teaches the model nothing.
            _clean_ids, _bad_ids = [], []
            for _v in element_ids:
                try:
                    _clean_ids.append(int(_v))
                except (TypeError, ValueError):
                    _bad_ids.append(u'{}'.format(_v))
            if _bad_ids:
                return {'error': 'element_ids must be plain numeric Revit element IDs, '
                                 'got: {}. Nested calls are not supported — call '
                                 'ai_element_filter first to get the IDs, then pass '
                                 'them here.'.format(u', '.join(_bad_ids[:3])),
                        'tool': tool_name}
            element_ids = _clean_ids

            # Parse color.
            #
            # An UNRECOGNISED colour must not fall through to the default.
            # It used to: anything that was neither a CSS name nor #rrggbb
            # silently became red, so a caller passing an RGB triple got
            # "success" with red paint while the reply said blue. Wrong
            # geometry colour is invisible in the transcript and only shows up
            # in the model. RGB triples are now accepted, and anything still
            # unparseable is an error that names what IS supported.
            r, g, b = 255, 0, 0  # default when no colour is given at all
            if color_str is not None and color_str != u'':
                rgb = self._parse_color(color_str)
                if rgb is None:
                    return self._color_error(color_str)
                r, g, b = rgb

            from Autodesk.Revit.DB import Color, OverrideGraphicSettings, ElementId, Transaction
            revit_color = Color(r, g, b)

            solid_pattern_id = self._solid_fill_id(doc)

            override_settings = OverrideGraphicSettings()
            override_settings.SetProjectionLineColor(revit_color)
            if solid_pattern_id != ElementId.InvalidElementId:
                try:
                    override_settings.SetSurfaceForegroundPatternId(solid_pattern_id)
                    override_settings.SetSurfaceForegroundPatternColor(revit_color)
                    override_settings.SetCutForegroundPatternId(solid_pattern_id)
                    override_settings.SetCutForegroundPatternColor(revit_color)
                except Exception:
                    pass

            # Optional extras on the same override, so "tô đỏ và làm mờ tường"
            # is one call. Each is independent of the colour and silently
            # skipped when out of range rather than failing the whole call.
            applied_extras = {}
            _halftone = arguments.get('halftone')
            if _halftone is not None:
                try:
                    override_settings.SetHalftone(bool(_halftone))
                    applied_extras['halftone'] = bool(_halftone)
                except Exception:
                    pass
            _transp = arguments.get('transparency')
            if _transp is not None:
                try:
                    _tv = int(_transp)
                    if 0 <= _tv <= 100:
                        override_settings.SetSurfaceTransparency(_tv)
                        applied_extras['transparency'] = _tv
                except (TypeError, ValueError):
                    pass
            _lw = arguments.get('line_weight')
            if _lw is not None:
                try:
                    _lwv = int(_lw)
                    if 1 <= _lwv <= 16:
                        override_settings.SetProjectionLineWeight(_lwv)
                        override_settings.SetCutLineWeight(_lwv)
                        applied_extras['line_weight'] = _lwv
                except (TypeError, ValueError):
                    pass

            view = doc.ActiveView
            t = Transaction(doc, "T3Lab AI Override Color")
            t.Start()
            overridden_count = 0
            for eid_val in element_ids:
                try:
                    eid = make_eid(eid_val)
                    view.SetElementOverrides(eid, override_settings)
                    overridden_count += 1
                except Exception:
                    pass
            t.Commit()
            
            result = {
                'success': True,
                'overridden_count': overridden_count,
                'color': color_str or 'red',
                'rgb': [r, g, b]
            }
            if applied_extras:
                result.update(applied_extras)
            if category_used:
                result['category'] = category_used
                result['scope'] = 'active_view'
            return result

        elif tool_name == 'create_level':
            from Autodesk.Revit.DB import Level, Transaction, ElementId
            elevation_m = float(arguments.get('elevation', 0.0))
            level_name = arguments.get('name')
            # Convert meters to internal Revit units (feet): 1 m = 3.28084 ft
            METERS_TO_FEET = 3.28084
            elevation_ft = elevation_m * METERS_TO_FEET
            t = Transaction(doc, "T3Lab AI: Create Level")
            t.Start()
            try:
                new_level = Level.Create(doc, elevation_ft)
                if level_name:
                    try:
                        new_level.Name = level_name
                    except Exception:
                        # Name already taken — uniquify instead of failing
                        for suffix in range(2, 100):
                            try:
                                new_level.Name = '{} ({})'.format(level_name, suffix)
                                break
                            except Exception:
                                continue
                t.Commit()
                return {
                    'success': True,
                    'level_id': eid_value(new_level.Id),
                    'name': new_level.Name,
                    'elevation_m': elevation_m
                }
            except Exception as ex:
                t.RollBack()
                return {'error': 'Failed to create level: {}'.format(str(ex))}

        elif tool_name == 'place_wall':
            from Autodesk.Revit.DB import (
                Wall, Line, XYZ, Transaction, ElementId,
                FilteredElementCollector, Level, WallType
            )
            METERS_TO_FEET = 3.28084
            start_x = float(arguments.get('start_x', 0.0)) * METERS_TO_FEET
            start_y = float(arguments.get('start_y', 0.0)) * METERS_TO_FEET
            end_x   = float(arguments.get('end_x', 1.0))  * METERS_TO_FEET
            end_y   = float(arguments.get('end_y', 0.0))  * METERS_TO_FEET
            height_m = float(arguments.get('height', 3.0))
            height_ft = height_m * METERS_TO_FEET
            level_name_arg = arguments.get('level_name')
            wall_type_name_arg = arguments.get('wall_type_name')

            # Find level
            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = None
            if level_name_arg:
                for lv in levels:
                    if lv.Name == level_name_arg:
                        target_level = lv
                        break
            if target_level is None and levels:
                target_level = levels[0]
            if target_level is None:
                return {'error': 'No levels found in the document'}

            # Find wall type — exact name, then substring, then first Basic
            # wall (curtain/stacked types can't be created via Wall.Create
            # with an arbitrary line + height).
            from Autodesk.Revit.DB import WallKind
            wall_types = list(FilteredElementCollector(doc).OfClass(WallType).ToElements())
            target_wall_type = None
            if wall_type_name_arg:
                for wt in wall_types:
                    if wt.Name == wall_type_name_arg:
                        target_wall_type = wt
                        break
                if target_wall_type is None:
                    for wt in wall_types:
                        if wall_type_name_arg.lower() in wt.Name.lower():
                            target_wall_type = wt
                            break
                if target_wall_type is None:
                    return {'error': 'Wall type "{}" not found'.format(wall_type_name_arg)}
            if target_wall_type is None:
                for wt in wall_types:
                    try:
                        if wt.Kind == WallKind.Basic:
                            target_wall_type = wt
                            break
                    except Exception:
                        pass
            if target_wall_type is None and wall_types:
                target_wall_type = wall_types[0]
            if target_wall_type is None:
                return {'error': 'No wall types found in the document'}

            # Build geometry
            p1 = XYZ(start_x, start_y, 0.0)
            p2 = XYZ(end_x, end_y, 0.0)
            try:
                wall_line = Line.CreateBound(p1, p2)
            except Exception as ex:
                return {'error': 'Invalid wall line: {}'.format(str(ex))}

            # Compute length in meters for the response
            import math
            dx = float(arguments.get('end_x', 0.0)) - float(arguments.get('start_x', 0.0))
            dy = float(arguments.get('end_y', 0.0)) - float(arguments.get('start_y', 0.0))
            length_m = math.sqrt(dx * dx + dy * dy)

            t = Transaction(doc, "T3Lab AI: Place Wall")
            t.Start()
            try:
                new_wall = Wall.Create(doc, wall_line, target_wall_type.Id, target_level.Id, height_ft, 0.0, False, False)
                t.Commit()
                return {
                    'success': True,
                    'wall_id': eid_value(new_wall.Id),
                    'length_m': round(length_m, 4)
                }
            except Exception as ex:
                t.RollBack()
                return {'error': 'Failed to place wall: {}'.format(str(ex))}

        elif tool_name == 'get_parameter':
            from Autodesk.Revit.DB import ElementId
            element_id_int = int(arguments.get('element_id', 0))
            parameter_name = arguments.get('parameter_name', '')
            eid = make_eid(element_id_int)
            elem = doc.GetElement(eid)
            if elem is None:
                return {'error': 'Element not found: {}'.format(element_id_int)}
            param = elem.LookupParameter(parameter_name)
            if param is None:
                return {'error': 'Parameter "{}" not found on element {}'.format(parameter_name, element_id_int)}
            value = None
            units = ''
            try:
                value = param.AsValueString()
                if value is None:
                    value = param.AsString()
                if value is None:
                    raw = param.AsDouble()
                    value = str(raw)
                    try:
                        if hasattr(param, "GetUnitTypeId"):
                            units = str(param.GetUnitTypeId().TypeId)
                        else:
                            units = str(param.DisplayUnitType)
                    except Exception:
                        try:
                            units = str(param.DisplayUnitType)
                        except Exception:
                            pass
            except Exception as ex:
                return {'error': 'Failed to read parameter: {}'.format(str(ex))}
            return {
                'element_id': element_id_int,
                'parameter': parameter_name,
                'value': value,
                'units': units
            }

        elif tool_name == 'get_elements_by_level':
            from Autodesk.Revit.DB import (
                FilteredElementCollector, Level,
                ElementLevelFilter, BuiltInCategory
            )
            level_name_arg = arguments.get('level_name', '')
            category_arg = arguments.get('category')

            # Find level by name
            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = None
            for lv in levels:
                if lv.Name == level_name_arg:
                    target_level = lv
                    break
            if target_level is None:
                return {'error': 'Level "{}" not found'.format(level_name_arg)}

            # Category name -> BuiltInCategory mapping
            category_map = {
                'Walls':    BuiltInCategory.OST_Walls,
                'Floors':   BuiltInCategory.OST_Floors,
                'Columns':  BuiltInCategory.OST_Columns,
                'Doors':    BuiltInCategory.OST_Doors,
                'Windows':  BuiltInCategory.OST_Windows,
                'Beams':    BuiltInCategory.OST_StructuralFraming,
            }

            level_filter = ElementLevelFilter(target_level.Id)
            collector = FilteredElementCollector(doc).WherePasses(level_filter)

            if category_arg and category_arg in category_map:
                from Autodesk.Revit.DB import ElementCategoryFilter
                cat_filter = ElementCategoryFilter(category_map[category_arg])
                from Autodesk.Revit.DB import LogicalAndFilter
                combined = LogicalAndFilter(level_filter, cat_filter)
                collector = FilteredElementCollector(doc).WherePasses(combined)

            elements_out = []
            for elem in collector:
                try:
                    cat_name = elem.Category.Name if elem.Category else 'Unknown'
                    elem_name = elem.Name if hasattr(elem, 'Name') else ''
                    type_name = ''
                    try:
                        type_elem = doc.GetElement(elem.GetTypeId())
                        if type_elem is not None:
                            type_name = type_elem.Name if hasattr(type_elem, 'Name') else ''
                    except Exception:
                        pass
                    elements_out.append({
                        'id': eid_value(elem.Id),
                        'name': elem_name,
                        'category': cat_name,
                        'type_name': type_name
                    })
                except Exception:
                    pass

            return {
                'level': level_name_arg,
                'count': len(elements_out),
                'elements': elements_out
            }

        # ── get_current_view_info ───────────────────────────────────────────────
        elif tool_name == 'get_current_view_info':
            view = doc.ActiveView
            if view is None:
                return {'error': 'No active view in Revit — open or activate a view first.'}
            result = {
                'name': view.Name,
                'id': eid_value(view.Id),
                'type': str(view.ViewType),
                'scale': view.Scale if hasattr(view, 'Scale') else None,
                'discipline': str(view.Discipline) if hasattr(view, 'Discipline') else None,
                'detail_level': str(view.DetailLevel) if hasattr(view, 'DetailLevel') else None,
                'is_template': view.IsTemplate,
            }
            try:
                cr = view.CropBox
                result['crop_box'] = {
                    'min_x': round(cr.Min.X * 0.3048, 3),
                    'min_y': round(cr.Min.Y * 0.3048, 3),
                    'max_x': round(cr.Max.X * 0.3048, 3),
                    'max_y': round(cr.Max.Y * 0.3048, 3),
                }
            except Exception:
                pass
            return result

        # ── get_current_view_elements ────────────────────────────────────────
        elif tool_name == 'get_current_view_elements':
            cat_arg   = arguments.get('category')
            limit_arg = int(arguments.get('limit', 100))
            view      = doc.ActiveView
            if view is None:
                return {'error': 'No active view in Revit — open or activate a view first.'}

            # Shared category vocabulary (was a private 12-entry map where an
            # UNKNOWN category name silently scanned every element in the
            # view instead of erroring).
            if cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                collector = FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType()
            else:
                collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

            # Exact total BEFORE the cap — counts must come from the DB, not
            # from the model counting a truncated list.
            try:
                total_count = collector.GetElementCount()
            except Exception:
                total_count = None

            elements_out = []
            for elem in collector:
                if len(elements_out) >= limit_arg:
                    break
                try:
                    cat_name = elem.Category.Name if elem.Category else 'Unknown'
                    elements_out.append({
                        'id': eid_value(elem.Id),
                        'name': elem.Name if hasattr(elem, 'Name') else '',
                        'category': cat_name,
                    })
                except Exception:
                    pass
            out = {'view': view.Name, 'count': len(elements_out),
                   'total_count': total_count, 'elements': elements_out}
            if total_count is not None and total_count > len(elements_out):
                out['warning'] = ('List truncated: showing {} of {} elements. '
                                 'Use total_count for statistics; raise limit '
                                 'only if you need the actual ids.'.format(
                                     len(elements_out), total_count))
            return out

        # ── get_available_family_types ───────────────────────────────────────
        elif tool_name == 'get_available_family_types':
            from Autodesk.Revit.DB import FamilySymbol
            cat_arg = arguments.get('category', '')
            collector = FilteredElementCollector(doc).OfClass(FamilySymbol)
            types_out = []
            for sym in collector:
                try:
                    cat_name = sym.Category.Name if sym.Category else ''
                    if cat_arg and cat_name != cat_arg:
                        continue
                    family_name = sym.Family.Name if sym.Family else ''
                    types_out.append({
                        'id': eid_value(sym.Id),
                        'family': family_name,
                        'type': sym.Name,
                        'category': cat_name,
                        'active': sym.IsActive,
                    })
                except Exception:
                    pass
            return {'count': len(types_out), 'types': types_out}

        # ── get_material_quantities ──────────────────────────────────────────
        elif tool_name == 'get_material_quantities':
            cat_arg   = arguments.get('category', 'Walls')
            lvl_arg   = arguments.get('level_name')
            # Route through the shared resolver so this tool speaks the same
            # vocabulary as every other category tool: case-insensitive + the
            # Vietnamese aliases in _BIC_ALIASES (tường/sàn/mái/trần). It used to
            # keep its own case-sensitive 4-entry map, so "walls" / "Tường" gave
            # a false "Unsupported category" in a bilingual office.
            bic, cat_name, err = self._resolve_bic(cat_arg)
            if err:
                return err
            # Only these four expose Area/Volume, so constrain AFTER resolving.
            SUPPORTED = set([
                BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors,
                BuiltInCategory.OST_Roofs, BuiltInCategory.OST_Ceilings,
            ])
            if bic not in SUPPORTED:
                return {'error': ('Material quantities are only available for '
                                  'Walls, Floors, Roofs, Ceilings — "{}" has no '
                                  'area/volume.').format(cat_name)}
            collector = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
            total_area_m2   = 0.0
            total_volume_m3 = 0.0
            items = []
            for elem in collector:
                try:
                    lvl_name = ''
                    try:
                        lv = doc.GetElement(elem.LevelId)
                        lvl_name = lv.Name if lv else ''
                    except Exception:
                        pass
                    if not lvl_name:
                        # Roofs/ceilings expose their level as a parameter
                        try:
                            lp = elem.LookupParameter('Level')
                            if lp:
                                lvl_name = lp.AsValueString() or ''
                        except Exception:
                            pass
                    if lvl_arg and lvl_name != lvl_arg:
                        continue
                    area_ft2 = 0.0
                    vol_ft3  = 0.0
                    try:
                        ap = elem.LookupParameter('Area')
                        if ap:
                            area_ft2 = ap.AsDouble()
                    except Exception:
                        pass
                    try:
                        vp = elem.LookupParameter('Volume')
                        if vp:
                            vol_ft3 = vp.AsDouble()
                    except Exception:
                        pass
                    area_m2 = round(area_ft2 * 0.0929, 3)
                    vol_m3  = round(vol_ft3 * 0.0283, 3)
                    total_area_m2   += area_m2
                    total_volume_m3 += vol_m3
                    items.append({
                        'id': eid_value(elem.Id),
                        'level': lvl_name,
                        'area_m2': area_m2,
                        'volume_m3': vol_m3,
                    })
                except Exception:
                    pass
            return {
                'category': cat_arg,
                'count': len(items),
                'total_area_m2': round(total_area_m2, 3),
                'total_volume_m3': round(total_volume_m3, 3),
                'elements': items,
            }

        # ── ai_element_filter ────────────────────────────────────────────────
        elif tool_name == 'ai_element_filter':
            param_arg = arguments.get('parameter_name')
            val_arg   = (arguments.get('parameter_value') or '').lower()
            try:
                limit_arg = max(0, int(arguments.get('limit', 50)))
            except Exception:
                limit_arg = 50
            try:
                offset_arg = max(0, int(arguments.get('offset', 0)))
            except Exception:
                offset_arg = 0

            # Requested fields, validated: an unknown field name silently
            # dropped would look like "this element has no level" rather than
            # "you asked for a field that does not exist".
            fields = arguments.get('fields')
            if fields:
                bad = [f for f in fields if f not in self._FILTER_FIELDS]
                if bad:
                    return {'error': 'Unknown fields: {}.'.format(
                                ', '.join(u'{}'.format(b) for b in bad)),
                            'supported_fields': list(self._FILTER_FIELDS),
                            'tool': tool_name}
                fields = [f for f in fields]
            else:
                fields = list(self._FILTER_DEFAULT_FIELDS)

            group_by = arguments.get('group_by') or []
            if group_by:
                bad = [g for g in group_by if g not in self._GROUP_BY_KEYS]
                if bad:
                    return {'error': 'Unknown group_by keys: {}.'.format(
                                ', '.join(u'{}'.format(b) for b in bad)),
                            'supported_keys': list(self._GROUP_BY_KEYS),
                            'tool': tool_name}

            elements, meta, err = self._find_elements(doc, arguments)
            if err:
                return err

            # Parameter filter is the one narrowing Revit cannot do for us
            # cheaply across arbitrary parameter names, so it runs here — but
            # only over whatever survived the quick filters above.
            matched, param_values = [], {}
            param_missing = 0
            if param_arg:
                for elem in elements:
                    try:
                        par = elem.LookupParameter(param_arg)
                    except Exception:
                        par = None
                    if not par:
                        param_missing += 1
                        continue
                    try:
                        pval = par.AsValueString() or par.AsString() or ''
                    except Exception:
                        pval = ''
                    if val_arg and val_arg not in pval.lower():
                        continue
                    matched.append(elem)
                    param_values[eid_value(elem.Id)] = pval
            else:
                matched = elements

            total = len(matched)
            cat_names = meta.get('categories') or []
            out = {
                'category': (cat_names[0] if len(cat_names) == 1
                             else (cat_names or None)),
                'filter_param': param_arg,
                'total_count': total,
            }
            for key in ('groups_matched', 'group_count', 'worksets_matched',
                        'levels_matched', 'type_matches'):
                if key in meta:
                    out[key] = meta[key]

            # store_as parks the WHOLE id set server-side. This is the path
            # that makes bulk edits possible at all: no cap, no paging, and
            # the ids never enter the conversation.
            if arguments.get('store_as'):
                handle = u'{}'.format(arguments['store_as']).lstrip('@').strip()
                if not handle:
                    return {'error': 'store_as must be a non-empty name.',
                            'tool': tool_name}
                stored = self._selset_store(
                    doc, handle, [eid_value(e.Id) for e in matched])
                out['saved_set'] = '@' + handle
                out['saved_count'] = stored
                out['hint'] = ('Pass element_ids="@{}" to set_element_workset, '
                               'select_elements, bulk_set_parameter, '
                               'revit_override_color, color_elements, '
                               'edit_elements, operate_element or tag_elements '
                               '— all {} elements, no ids needed.'
                               ).format(handle, stored)

            # ── Aggregated mode: counts, not rows ────────────────────────────
            if group_by:
                buckets = {}
                for elem in matched:
                    key = tuple(self._group_key_value(
                        doc, elem, gkey,
                        param_values.get(eid_value(elem.Id), ''))
                        for gkey in group_by)
                    buckets[key] = buckets.get(key, 0) + 1
                rows = []
                for key in sorted(buckets, key=lambda k: (-buckets[k], k)):
                    row = dict(zip(group_by, key))
                    row['count'] = buckets[key]
                    rows.append(row)
                out['group_by'] = list(group_by)
                out['summary'] = rows[:300]
                out['group_count_rows'] = len(rows)
                if len(rows) > 300:
                    out['note'] = ('Showing the 300 largest of {} groups; '
                                   'total_count still covers all of them.'
                                   ).format(len(rows))
                if not matched:
                    out['summary'] = []
                return out

            # ── Listing mode ────────────────────────────────────────────────
            page = matched[offset_arg:offset_arg + limit_arg]
            if list(fields) == ['id']:
                # Cheapest possible listing: a bare id array, ~1 token an id
                # instead of a dict per element.
                out['ids'] = [eid_value(e.Id) for e in page]
                out['count'] = len(out['ids'])
            else:
                rows = []
                for elem in page:
                    row = self._filter_row(
                        doc, elem, fields,
                        param_values.get(eid_value(elem.Id), ''))
                    # Text-bearing elements (TextNote, ModelText) carry their
                    # content and owner view by default — that is what
                    # spell-check and annotation queries are after.
                    if 'text' not in fields:
                        try:
                            if hasattr(elem, 'Text'):
                                row['text'] = elem.Text
                                ov = doc.GetElement(elem.OwnerViewId)
                                if ov is not None:
                                    row['view'] = elem_name(ov)
                        except Exception:
                            pass
                    rows.append(row)
                out['elements'] = rows
                out['count'] = len(rows)
            out['offset'] = offset_arg
            out['truncated'] = total > offset_arg + out['count']

            # A truncated list silently read as "the whole model" is how the
            # assistant ends up reporting "scanned 4495 notes, 0 errors" after
            # seeing 50 of them — spell out the incomplete coverage, and point
            # at the two ways that do NOT need paging at all.
            if out['truncated']:
                out['warning'] = (
                    'INCOMPLETE LIST: elements {}-{} of {} matches. Do NOT claim '
                    'full coverage from this page. To act on ALL of them, re-call '
                    'with store_as="sel" and pass element_ids="@sel"; to COUNT or '
                    'break them down, re-call with group_by. Only page '
                    '(offset={}) when you genuinely need to read every row.'
                ).format(offset_arg + 1, offset_arg + out['count'], total,
                         offset_arg + out['count'])

            # "0 results" caused by a parameter that simply doesn't exist on
            # this category reads like "no elements" to the model — tell it
            # what actually happened so it can rephrase the query.
            if param_arg and not matched and param_missing:
                out['note'] = ("Parameter '{}' does not exist on any of the {} "
                               "elements scanned. Element names are already "
                               "returned in the 'name' field — query again "
                               "without parameter_name.").format(
                                   param_arg, param_missing)
            return out

        # ── analyze_model_statistics ─────────────────────────────────────────
        elif tool_name == 'analyze_model_statistics':
            from Autodesk.Revit.DB import View, BuiltInCategory as BIC
            stat_cats = [
                ('Walls',    BIC.OST_Walls),
                ('Floors',   BIC.OST_Floors),
                ('Doors',    BIC.OST_Doors),
                ('Windows',  BIC.OST_Windows),
                ('Rooms',    BIC.OST_Rooms),
                ('Columns',  BIC.OST_Columns),
                ('Beams',    BIC.OST_StructuralFraming),
                ('Ceilings', BIC.OST_Ceilings),
                ('Roofs',    BIC.OST_Roofs),
                ('Grids',    BIC.OST_Grids),
                ('Levels',   BIC.OST_Levels),
                ('Sheets',   BIC.OST_Sheets),
            ]
            stats = {}
            for cat_name, bic in stat_cats:
                try:
                    count = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().GetElementCount()
                    stats[cat_name] = count
                except Exception:
                    stats[cat_name] = 0
            total_views = FilteredElementCollector(doc).OfClass(View).GetElementCount()
            return {
                'element_counts': stats,
                'total_views': total_views,
                'project': doc.ProjectInformation.Name,
            }

        # ── create_point_based_element ───────────────────────────────────────
        elif tool_name == 'create_point_based_element':
            from Autodesk.Revit.DB import FamilySymbol, XYZ, Transaction, Wall
            from Autodesk.Revit.DB.Structure import StructuralType
            M2FT = 3.28084
            ftype_name = arguments.get('family_type', '')
            x_ft = float(arguments.get('x', 0)) * M2FT
            y_ft = float(arguments.get('y', 0)) * M2FT
            z_arg = arguments.get('z')
            lvl_name = arguments.get('level_name')
            host_id_arg = arguments.get('host_wall_id')

            # Resolve family symbol: exact type name, "Family:Type", then
            # case-insensitive substring match as a fallback.
            sym = None
            partial = None
            for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
                try:
                    full = '{}:{}'.format(s.Family.Name, s.Name) if s.Family else s.Name
                    if s.Name == ftype_name or full == ftype_name:
                        sym = s
                        break
                    if partial is None and ftype_name.lower() in full.lower():
                        partial = s
                except Exception:
                    pass
            if sym is None:
                sym = partial
            if sym is None:
                return {'error': 'Family type "{}" not found. Use get_available_family_types to list loaded types.'.format(ftype_name)}

            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = levels[0] if levels else None
            if lvl_name:
                for lv in levels:
                    if lv.Name == lvl_name:
                        target_level = lv
                        break
            if target_level is None:
                return {'error': 'No levels found in the document'}

            # Default Z to the target level's elevation so the element lands
            # on the level rather than at absolute 0.
            z_ft = float(z_arg) * M2FT if z_arg is not None else target_level.Elevation
            point = XYZ(x_ft, y_ft, z_ft)

            cat_name = sym.Category.Name if sym.Category else ''
            needs_host = cat_name in ('Doors', 'Windows')
            try:
                placement = str(sym.Family.FamilyPlacementType)
                if 'Hosted' in placement:
                    needs_host = True
            except Exception:
                pass

            host_elem = None
            if host_id_arg:
                host_elem = doc.GetElement(make_eid(int(host_id_arg)))
                if host_elem is None:
                    return {'error': 'Host element not found: {}'.format(host_id_arg)}
            elif needs_host:
                # Auto-pick the nearest wall to the placement point.
                best_d = 1e30
                for w in FilteredElementCollector(doc).OfClass(Wall):
                    try:
                        crv = w.Location.Curve
                        d = crv.Distance(XYZ(x_ft, y_ft, crv.GetEndPoint(0).Z))
                        if d < best_d:
                            best_d = d
                            host_elem = w
                    except Exception:
                        pass
                # Reject hosts farther than ~2 m — the point is nowhere near a wall.
                if host_elem is None or best_d > 2.0 * M2FT:
                    return {'error': '"{}" is a hosted family (doors/windows need a wall). '
                                     'Pass host_wall_id or place the point on/near a wall.'.format(ftype_name)}

            struct_type = StructuralType.NonStructural
            if cat_name == 'Structural Columns':
                struct_type = StructuralType.Column

            t = Transaction(doc, 'T3Lab AI Create Element')
            t.Start()
            try:
                if not sym.IsActive:
                    sym.Activate()
                    doc.Regenerate()
                if host_elem is not None:
                    inst = doc.Create.NewFamilyInstance(point, sym, host_elem, target_level, struct_type)
                else:
                    inst = doc.Create.NewFamilyInstance(point, sym, target_level, struct_type)
                t.Commit()
                result = {'success': True, 'element_id': eid_value(inst.Id),
                          'type': '{}:{}'.format(sym.Family.Name, sym.Name) if sym.Family else sym.Name,
                          'level': target_level.Name}
                if host_elem is not None:
                    result['host_id'] = eid_value(host_elem.Id)
                return result
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── create_line_based_element ────────────────────────────────────────
        elif tool_name == 'create_line_based_element':
            from Autodesk.Revit.DB import FamilySymbol, XYZ, Line, Transaction
            from Autodesk.Revit.DB.Structure import StructuralType
            M2FT = 3.28084
            ftype_name = arguments.get('family_type', '')
            sx = float(arguments.get('start_x', 0)) * M2FT
            sy = float(arguments.get('start_y', 0)) * M2FT
            ex = float(arguments.get('end_x', 0)) * M2FT
            ey = float(arguments.get('end_y', 0)) * M2FT
            sz_arg = arguments.get('start_z')
            ez_arg = arguments.get('end_z')
            lvl_name = arguments.get('level_name')

            sym = None
            partial = None
            for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
                try:
                    full = '{}:{}'.format(s.Family.Name, s.Name) if s.Family else s.Name
                    if s.Name == ftype_name or full == ftype_name:
                        sym = s
                        break
                    if partial is None and ftype_name.lower() in full.lower():
                        partial = s
                except Exception:
                    pass
            if sym is None:
                sym = partial
            if sym is None:
                return {'error': 'Family type "{}" not found. Use get_available_family_types to list loaded types.'.format(ftype_name)}

            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = levels[0] if levels else None
            if lvl_name:
                for lv in levels:
                    if lv.Name == lvl_name:
                        target_level = lv
                        break
            if target_level is None:
                return {'error': 'No levels found in the document'}

            # Default Z to the reference level's elevation so beams land on
            # the level instead of absolute 0.
            sz = float(sz_arg) * M2FT if sz_arg is not None else target_level.Elevation
            ez = float(ez_arg) * M2FT if ez_arg is not None else target_level.Elevation

            p1 = XYZ(sx, sy, sz)
            p2 = XYZ(ex, ey, ez)
            if p1.DistanceTo(p2) < 0.01:
                return {'error': 'Start and end points are (nearly) identical — cannot create a line element.'}
            curve = Line.CreateBound(p1, p2)

            cat_name = sym.Category.Name if sym.Category else ''
            if cat_name == 'Structural Framing':
                struct_type = StructuralType.Beam
            elif cat_name == 'Structural Columns':
                struct_type = StructuralType.Column
            else:
                struct_type = StructuralType.NonStructural

            t = Transaction(doc, 'T3Lab AI Create Line Element')
            t.Start()
            try:
                if not sym.IsActive:
                    sym.Activate()
                    doc.Regenerate()
                inst = doc.Create.NewFamilyInstance(curve, sym, target_level, struct_type)
                t.Commit()
                return {'success': True, 'element_id': eid_value(inst.Id),
                        'type': '{}:{}'.format(sym.Family.Name, sym.Name) if sym.Family else sym.Name,
                        'level': target_level.Name}
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── create_surface_based_element ─────────────────────────────────────
        elif tool_name == 'create_surface_based_element':
            from Autodesk.Revit.DB import (XYZ, Line, CurveArray, CurveLoop,
                                           Transaction, Floor, FloorType)
            from System.Collections.Generic import List as NetList
            elem_type = (arguments.get('element_type') or 'floor').lower()
            boundary  = arguments.get('boundary_points', [])
            lvl_name  = arguments.get('level_name')
            type_name = arguments.get('type_name')
            M2FT = 3.28084

            # Only 'ceiling' and 'roof' get their own branch below; without this
            # guard every other value (including a typo) silently made a FLOOR.
            if elem_type not in ('floor', 'ceiling', 'roof'):
                return {'error': "Unknown element_type '{}'.".format(elem_type),
                        'supported_element_types': ['floor', 'ceiling', 'roof'],
                        'hint': 'Retry with one of supported_element_types.'}

            if len(boundary) < 3:
                return {'error': 'boundary_points must have at least 3 points'}

            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = levels[0] if levels else None
            if lvl_name:
                for lv in levels:
                    if lv.Name == lvl_name:
                        target_level = lv
                        break
            if target_level is None:
                return {'error': 'No levels found in the document'}

            try:
                rev_ver = int(doc.Application.VersionNumber)
            except Exception:
                rev_ver = 0

            # Build the boundary at the level's elevation so the sketch sits
            # on the level (offset 0) instead of at absolute Z=0.
            z_ft = target_level.Elevation
            pts = [XYZ(float(p[0]) * M2FT, float(p[1]) * M2FT, z_ft) for p in boundary]
            segments = []
            for i in range(len(pts)):
                p1 = pts[i]
                p2 = pts[(i + 1) % len(pts)]
                if p1.DistanceTo(p2) > 0.01:
                    segments.append(Line.CreateBound(p1, p2))
            if len(segments) < 3:
                return {'error': 'boundary_points do not form a valid polygon (duplicate/too-close points)'}

            def _pick_type(types):
                picked = types[0] if types else None
                if type_name:
                    for candidate in types:
                        if candidate.Name == type_name:
                            return candidate
                    for candidate in types:
                        if type_name.lower() in candidate.Name.lower():
                            return candidate
                return picked

            t = Transaction(doc, 'T3Lab AI Create Surface Element')
            t.Start()
            try:
                if elem_type == 'ceiling':
                    if rev_ver < 2022:
                        t.RollBack()
                        return {'error': 'Ceiling creation via API requires Revit 2022 or newer (running {}).'.format(rev_ver or 'unknown')}
                    from Autodesk.Revit.DB import Ceiling, CeilingType
                    ceil_types = list(FilteredElementCollector(doc).OfClass(CeilingType).ToElements())
                    ct = _pick_type(ceil_types)
                    if ct is None:
                        t.RollBack()
                        return {'error': 'No ceiling types found in the document'}
                    loop = CurveLoop()
                    for seg in segments:
                        loop.Append(seg)
                    profile = NetList[CurveLoop]()
                    profile.Add(loop)
                    new_elem = Ceiling.Create(doc, profile, ct.Id, target_level.Id)
                    type_used = ct.Name

                elif elem_type == 'roof':
                    from Autodesk.Revit.DB import RoofType, ModelCurveArray, BuiltInCategory as _BIC
                    roof_types = list(FilteredElementCollector(doc)
                                      .OfCategory(_BIC.OST_Roofs)
                                      .OfClass(RoofType).ToElements())
                    rt = _pick_type(roof_types)
                    if rt is None:
                        t.RollBack()
                        return {'error': 'No roof types found in the document'}
                    ca = CurveArray()
                    for seg in segments:
                        ca.Append(seg)
                    ma = ModelCurveArray()
                    new_elem = doc.Create.NewFootPrintRoof(ca, target_level, rt, ma)
                    type_used = rt.Name

                else:  # floor (default)
                    floor_types = list(FilteredElementCollector(doc).OfClass(FloorType).ToElements())
                    ft = _pick_type(floor_types)
                    if ft is None:
                        t.RollBack()
                        return {'error': 'No floor types found in the document'}
                    if rev_ver >= 2022:
                        loop = CurveLoop()
                        for seg in segments:
                            loop.Append(seg)
                        profile = NetList[CurveLoop]()
                        profile.Add(loop)
                        new_elem = Floor.Create(doc, profile, ft.Id, target_level.Id)
                    else:
                        ca = CurveArray()
                        for seg in segments:
                            ca.Append(seg)
                        new_elem = doc.Create.NewFloor(ca, ft, target_level, False)
                    type_used = ft.Name

                t.Commit()
                return {'success': True, 'element_id': eid_value(new_elem.Id),
                        'element_type': elem_type, 'type_used': type_used,
                        'level': target_level.Name}
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── create_grid ──────────────────────────────────────────────────────
        elif tool_name == 'create_grid':
            from Autodesk.Revit.DB import Grid, XYZ, Line, Transaction
            x_spacings = arguments.get('x_spacings', [])
            y_spacings = arguments.get('y_spacings', [])
            ox = float(arguments.get('origin_x', 0)) * 3.28084
            oy = float(arguments.get('origin_y', 0)) * 3.28084
            x_labels = arguments.get('x_labels', [])
            y_labels = arguments.get('y_labels', [])

            def auto_alpha(n):
                labels = []
                for i in range(n):
                    labels.append(chr(ord('A') + i) if i < 26 else 'A{}'.format(i - 25))
                return labels

            if not x_labels:
                x_labels = auto_alpha(len(x_spacings) + 1)
            if not y_labels:
                y_labels = [str(i + 1) for i in range(len(y_spacings) + 1)]

            M2FT = 3.28084
            total_x_ft = sum(x_spacings) * M2FT
            total_y_ft = sum(y_spacings) * M2FT
            # Extend grid lines past the last gridline on each side (min ~3 m)
            margin_ft = max(10.0, 0.15 * max(total_x_ft, total_y_ft))

            def _safe_name(grid, wanted, warnings):
                """Rename a grid, falling back to auto name on duplicates
                instead of failing the whole transaction."""
                try:
                    grid.Name = wanted
                except Exception:
                    warnings.append('Grid name "{}" already in use — kept auto name "{}"'.format(wanted, grid.Name))

            grid_ids = []
            name_warnings = []
            t = Transaction(doc, 'T3Lab AI Create Grid')
            t.Start()
            try:
                # Vertical lines (along Y) at X positions
                x_pos = ox
                for i, spacing in enumerate([0.0] + [s * M2FT for s in x_spacings]):
                    x_pos = ox if i == 0 else x_pos + spacing
                    start = XYZ(x_pos, oy - margin_ft, 0)
                    end   = XYZ(x_pos, oy + total_y_ft + margin_ft, 0)
                    g = Grid.Create(doc, Line.CreateBound(start, end))
                    if i < len(x_labels):
                        _safe_name(g, x_labels[i], name_warnings)
                    grid_ids.append(eid_value(g.Id))

                # Horizontal lines (along X) at Y positions
                y_pos = oy
                for i, spacing in enumerate([0.0] + [s * M2FT for s in y_spacings]):
                    y_pos = oy if i == 0 else y_pos + spacing
                    start = XYZ(ox - margin_ft, y_pos, 0)
                    end   = XYZ(ox + total_x_ft + margin_ft, y_pos, 0)
                    g = Grid.Create(doc, Line.CreateBound(start, end))
                    if i < len(y_labels):
                        _safe_name(g, y_labels[i], name_warnings)
                    grid_ids.append(eid_value(g.Id))

                t.Commit()
                result = {'success': True, 'grid_count': len(grid_ids), 'grid_ids': grid_ids}
                if name_warnings:
                    result['warnings'] = name_warnings
                return result
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── create_room ──────────────────────────────────────────────────────
        elif tool_name == 'create_room':
            from Autodesk.Revit.DB import UV, Transaction
            from Autodesk.Revit.DB.Architecture import Room
            x_m = float(arguments.get('x', 0)) * 3.28084
            y_m = float(arguments.get('y', 0)) * 3.28084
            lvl_name  = arguments.get('level_name')
            room_name = arguments.get('name', 'Room')
            room_num  = arguments.get('number', '')

            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = levels[0] if levels else None
            if lvl_name:
                for lv in levels:
                    if lv.Name == lvl_name:
                        target_level = lv
                        break
            if target_level is None:
                return {'error': 'No level found'}

            t = Transaction(doc, 'T3Lab AI Create Room')
            t.Start()
            try:
                pt = UV(x_m, y_m)
                room = doc.Create.NewRoom(target_level, pt)
                if room_name:
                    room.Name = room_name
                if room_num:
                    room.Number = room_num
                doc.Regenerate()
                area_ft2 = 0.0
                try:
                    area_ft2 = room.Area
                except Exception:
                    pass
                t.Commit()
                result = {'success': True, 'room_id': eid_value(room.Id),
                          'name': room.Name, 'number': room.Number,
                          'area_m2': round(area_ft2 * 0.0929, 2),
                          'enclosed': area_ft2 > 0}
                if area_ft2 <= 0:
                    result['warning'] = ('Room was placed but is not enclosed — no bounding walls '
                                         'around ({}, {}) on level "{}".'.format(
                                             arguments.get('x'), arguments.get('y'), target_level.Name))
                return result
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── create_structural_framing_system ─────────────────────────────────
        elif tool_name == 'create_structural_framing_system':
            from Autodesk.Revit.DB import FamilySymbol, XYZ, Line, Transaction
            from Autodesk.Revit.DB import Structure
            x_bays    = arguments.get('x_bays', [])
            y_bays    = arguments.get('y_bays', [])
            lvl_name  = arguments.get('level_name')
            beam_type = arguments.get('beam_type')
            ox = float(arguments.get('origin_x', 0)) * 3.28084
            oy = float(arguments.get('origin_y', 0)) * 3.28084

            levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
            target_level = levels[0] if levels else None
            if lvl_name:
                for lv in levels:
                    if lv.Name == lvl_name:
                        target_level = lv
                        break

            framing_syms = []
            for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
                cat = s.Category.Name if s.Category else ''
                if cat == 'Structural Framing':
                    framing_syms.append(s)
            sym = None
            if beam_type:
                for s in framing_syms:
                    if s.Name == beam_type or (s.Family and s.Family.Name == beam_type):
                        sym = s
                        break
                if sym is None:
                    for s in framing_syms:
                        full = '{}:{}'.format(s.Family.Name, s.Name) if s.Family else s.Name
                        if beam_type.lower() in full.lower():
                            sym = s
                            break
                if sym is None:
                    return {'error': 'Structural framing type "{}" not found. Available: {}'.format(
                        beam_type, ', '.join(sorted(set(s.Name for s in framing_syms))[:20]) or '(none)')}
            elif framing_syms:
                sym = framing_syms[0]
            if sym is None:
                return {'error': 'No structural framing family type found. Load a beam family first.'}

            if target_level is None:
                return {'error': 'No levels found in the document'}
            # Place beams at the level's elevation, not absolute Z=0
            z_ft = target_level.Elevation

            x_positions = [ox]
            cur = ox
            for sp in x_bays:
                cur += sp * 3.28084
                x_positions.append(cur)
            y_positions = [oy]
            cur = oy
            for sp in y_bays:
                cur += sp * 3.28084
                y_positions.append(cur)

            beam_ids = []
            t = Transaction(doc, 'T3Lab AI Structural Framing System')
            t.Start()
            try:
                if not sym.IsActive:
                    sym.Activate()
                    doc.Regenerate()
                # Beams in X direction
                for y_pos in y_positions:
                    for i in range(len(x_positions) - 1):
                        p1 = XYZ(x_positions[i], y_pos, z_ft)
                        p2 = XYZ(x_positions[i + 1], y_pos, z_ft)
                        crv = Line.CreateBound(p1, p2)
                        inst = doc.Create.NewFamilyInstance(crv, sym, target_level, Structure.StructuralType.Beam)
                        beam_ids.append(eid_value(inst.Id))
                # Beams in Y direction
                for x_pos in x_positions:
                    for j in range(len(y_positions) - 1):
                        p1 = XYZ(x_pos, y_positions[j], z_ft)
                        p2 = XYZ(x_pos, y_positions[j + 1], z_ft)
                        crv = Line.CreateBound(p1, p2)
                        inst = doc.Create.NewFamilyInstance(crv, sym, target_level, Structure.StructuralType.Beam)
                        beam_ids.append(eid_value(inst.Id))
                t.Commit()
                return {'success': True, 'beams_created': len(beam_ids), 'beam_ids': beam_ids}
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}

        # ── delete_element ───────────────────────────────────────────────────
        elif tool_name == 'delete_element':
            from Autodesk.Revit.DB import ElementId, Transaction
            ids = arguments.get('element_ids', [])
            if not ids:
                return {'error': 'No element_ids provided'}
            dry_run = bool(arguments.get('dry_run', False))

            def _describe(eid):
                el = doc.GetElement(eid)
                if el is None:
                    return {'id': eid_value(eid), 'name': '(unknown)', 'category': ''}
                return {
                    'id': eid_value(eid),
                    'name': el.Name if hasattr(el, 'Name') and el.Name else str(el.GetType().Name),
                    'category': el.Category.Name if el.Category else '',
                }

            # ── Preview mode: run the delete inside a transaction to collect the
            # full cascade (doc.Delete returns EVERY affected element id), then
            # roll back. After rollback the elements are restored, so we can
            # resolve their names/categories for a readable preview.
            if dry_run:
                requested = [_describe(make_eid(int(v))) for v in ids]
                affected_raw = set()
                t = Transaction(doc, 'T3Lab AI Delete Preview')
                t.Start()
                try:
                    for eid_val in ids:
                        try:
                            removed = doc.Delete(make_eid(int(eid_val)))
                            if removed:
                                for rid in removed:
                                    affected_raw.add(eid_value(rid))
                        except Exception:
                            pass
                finally:
                    t.RollBack()

                requested_ids = set(int(v) for v in ids)
                cascade_ids = sorted(i for i in affected_raw if i not in requested_ids)
                cascade = [_describe(make_eid(i)) for i in cascade_ids]
                return {
                    'dry_run': True,
                    'requested': requested,
                    'requested_count': len(requested),
                    'cascade': cascade,
                    'cascade_count': len(cascade),
                    'total_affected': len(affected_raw),
                    'note': 'Nothing was deleted. Call again with dry_run=false to apply.',
                }

            t = Transaction(doc, 'T3Lab AI Delete Elements')
            t.Start()
            deleted = []
            failed  = []
            try:
                for eid_val in ids:
                    try:
                        doc.Delete(make_eid(int(eid_val)))
                        deleted.append(int(eid_val))
                    except Exception as ex:
                        failed.append({'id': int(eid_val), 'error': str(ex)})
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            return {'deleted': deleted, 'failed': failed, 'deleted_count': len(deleted)}

        # ── operate_element ──────────────────────────────────────────────────
        elif tool_name == 'operate_element':
            from Autodesk.Revit.DB import ElementId, Transaction
            op      = (arguments.get('operation') or '').lower()
            ids     = arguments.get('element_ids', [])
            cat_arg = arguments.get('category')
            view    = doc.ActiveView

            # ── View-level operations ────────────────────────────────────────
            # Handled BEFORE the element-collection block below: reset_temporary
            # has no element target by definition, and hide_category acts on the
            # Category object rather than on the elements in it — running them
            # through the collector would fail with "no elements visible" on
            # exactly the views where they are most useful (everything hidden).
            if op == 'reset_temporary':
                from Autodesk.Revit.DB import TemporaryViewMode
                t = Transaction(doc, 'T3Lab AI Reset Temporary View Mode')
                t.Start()
                try:
                    view.DisableTemporaryViewMode(
                        TemporaryViewMode.TemporaryHideIsolate)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'view': view.Name,
                        'note': 'Temporary hide/isolate cleared in this view.'}

            if op in ('hide_category', 'unhide_category'):
                from Autodesk.Revit.DB import Category
                if not cat_arg:
                    return {'error': "Operation '{}' requires a `category` "
                                     "argument.".format(op)}
                bic, cat_name, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                cat = Category.GetCategory(doc, bic)
                if cat is None:
                    return {'error': "Category '{}' is not available in this "
                                     "document.".format(cat_name)}
                hide = (op == 'hide_category')
                # Same guard sequence the BatchOut safe-export ladder uses.
                if not view.CanCategoryBeHidden(cat.Id):
                    return {'error': "Category '{}' cannot be hidden in view "
                                     "'{}'.".format(cat_name, view.Name)}
                if view.GetCategoryHidden(cat.Id) == hide:
                    return {'success': True, 'operation': op,
                            'category': cat_name, 'changed': False,
                            'view': view.Name, 'note': 'Already in that state.'}
                t = Transaction(doc, 'T3Lab AI {} Category'.format(
                    'Hide' if hide else 'Unhide'))
                t.Start()
                try:
                    view.SetCategoryHidden(cat.Id, hide)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'category': cat_name,
                        'changed': True, 'view': view.Name}

            if op == 'select_similar':
                from System.Collections.Generic import List
                # Shared with the ribbon's Select Similar so both agree on what
                # "similar" means (see Snippets/_similar.py for why the ribbon
                # module itself can't be imported here).
                from Snippets._similar import is_category_only, family_id_of
                match = (arguments.get('match') or 'type').lower()
                scope = (arguments.get('scope') or 'view').lower()
                if match not in ('category', 'family', 'type'):
                    return {'error': "Unknown match '{}'.".format(match),
                            'supported_match': ['category', 'family', 'type'],
                            'hint': 'Retry with one of supported_match.'}
                if scope not in ('view', 'model'):
                    return {'error': "Unknown scope '{}'.".format(scope),
                            'supported_scope': ['view', 'model'],
                            'hint': 'Retry with one of supported_scope.'}
                if ids:
                    seed_ids = [make_eid(int(i)) for i in ids]
                else:
                    seed_ids = list(uidoc.Selection.GetElementIds())
                seeds = [doc.GetElement(i) for i in seed_ids]
                seeds = [e for e in seeds if e is not None]
                if not seeds:
                    return {'error': 'select_similar needs seed elements — pass '
                                     'element_ids, or select something in Revit '
                                     'first.'}

                def _cat_id(el):
                    try:
                        return eid_value(el.Category.Id) if el.Category else None
                    except Exception:
                        return None

                seed_cats, fam_ids, type_ids, fallback_cats = set(), set(), set(), set()
                for e in seeds:
                    c = _cat_id(e)
                    if c is not None:
                        seed_cats.add(c)
                    if match == 'family':
                        fid = family_id_of(e, doc)
                        if fid is not None:
                            fam_ids.add(eid_value(fid))
                        elif c is not None:
                            # System families (walls, floors) have no Family —
                            # fall back to category, same as the ribbon tool.
                            fallback_cats.add(c)
                    elif match == 'type':
                        if is_category_only(e):
                            if c is not None:
                                fallback_cats.add(c)
                            continue
                        tid = e.GetTypeId()
                        if tid is not None and eid_value(tid) != -1:
                            type_ids.add(eid_value(tid))

                collector = (FilteredElementCollector(doc, view.Id)
                             if scope == 'view' else FilteredElementCollector(doc))
                hits = []
                for e in collector.WhereElementIsNotElementType().ToElements():
                    try:
                        c = _cat_id(e)
                        if match == 'category':
                            if c is not None and c in seed_cats:
                                hits.append(e.Id)
                            continue
                        if c is not None and c in fallback_cats:
                            hits.append(e.Id)
                            continue
                        if match == 'family':
                            fid = family_id_of(e, doc)
                            if fid is not None and eid_value(fid) in fam_ids:
                                hits.append(e.Id)
                        else:
                            tid = e.GetTypeId()
                            if tid is not None and eid_value(tid) in type_ids:
                                hits.append(e.Id)
                    except Exception:
                        continue

                uidoc.Selection.SetElementIds(List[ElementId](hits))
                return {'success': True, 'operation': op, 'match': match,
                        'scope': scope, 'seed_count': len(seeds),
                        'count': len(hits)}

            # Whole-category path: "select/hide all floors" collects every
            # matching element in the active view server-side — no id
            # ferrying, no count cap (same contract as revit_override_color).
            if not ids and cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                elem_ids = list(FilteredElementCollector(doc, view.Id)
                                .OfCategory(bic).WhereElementIsNotElementType()
                                .ToElementIds())
                if not elem_ids:
                    return {'error': 'No {} elements are visible in the '
                                     'active view.'.format(cat_arg)}
            else:
                elem_ids = [make_eid(int(i)) for i in ids]

            if op == 'select':
                from System.Collections.Generic import List
                id_list = List[ElementId](elem_ids)
                uidoc.Selection.SetElementIds(id_list)
                return {'success': True, 'operation': 'select', 'count': len(elem_ids)}

            elif op in ('hide', 'isolate', 'unhide'):
                from System.Collections.Generic import List
                id_col = List[ElementId](elem_ids)
                t = Transaction(doc, 'T3Lab AI {} Elements'.format(op.title()))
                t.Start()
                try:
                    if op == 'hide':
                        view.HideElements(id_col)
                    elif op == 'unhide':
                        view.UnhideElements(id_col)
                    elif op == 'isolate':
                        view.IsolateElementsTemporary(id_col)
                    t.Commit()
                    return {'success': True, 'operation': op, 'count': len(elem_ids)}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}

            elif op in ('pin', 'unpin'):
                # Element.Pinned is a plain property, but not every element
                # accepts it (group members, some view-owned elements) — a
                # single throw must not abort the whole batch, so failures are
                # counted per element instead of rolling the transaction back.
                want = (op == 'pin')
                t = Transaction(doc, 'T3Lab AI {} Elements'.format(op.title()))
                t.Start()
                changed = already = skipped = 0
                try:
                    for eid_obj in elem_ids:
                        el = doc.GetElement(eid_obj)
                        if el is None:
                            skipped += 1
                            continue
                        try:
                            if el.Pinned == want:
                                already += 1
                                continue
                            el.Pinned = want
                            changed += 1
                        except Exception:
                            skipped += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'count': changed,
                        'already_in_state': already, 'skipped': skipped,
                        'total_targeted': len(elem_ids)}

            elif op in ('halftone', 'unhalftone', 'transparency'):
                level = None
                if op == 'transparency':
                    try:
                        level = int(arguments.get('transparency'))
                    except (TypeError, ValueError):
                        return {'error': "Operation 'transparency' requires a "
                                         "`transparency` value from 0 (opaque) "
                                         "to 100 (invisible)."}
                    if level < 0 or level > 100:
                        return {'error': 'transparency must be 0-100, got '
                                         '{}.'.format(level)}
                t = Transaction(doc, 'T3Lab AI {}'.format(op.title()))
                t.Start()
                changed = skipped = 0
                try:
                    for eid_obj in elem_ids:
                        try:
                            # Read-modify-write. Building a fresh
                            # OverrideGraphicSettings here would silently wipe an
                            # existing colour override, so "make the red walls
                            # halftone" has to keep them red.
                            ogs = view.GetElementOverrides(eid_obj)
                            if op == 'transparency':
                                ogs.SetSurfaceTransparency(level)
                            else:
                                ogs.SetHalftone(op == 'halftone')
                            view.SetElementOverrides(eid_obj, ogs)
                            changed += 1
                        except Exception:
                            skipped += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                out = {'success': True, 'operation': op, 'count': changed,
                       'skipped': skipped, 'total_targeted': len(elem_ids)}
                if level is not None:
                    out['transparency'] = level
                return out

            elif op == 'reset_color':
                from Autodesk.Revit.DB import OverrideGraphicSettings, Transaction
                t = Transaction(doc, 'T3Lab AI Reset Color')
                t.Start()
                try:
                    plain = OverrideGraphicSettings()
                    for eid_obj in elem_ids:
                        view.SetElementOverrides(eid_obj, plain)
                    t.Commit()
                    return {'success': True, 'operation': 'reset_color', 'count': len(elem_ids)}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}

            return {'error': 'Unknown operation: {}'.format(op),
                    'supported_operations': ['select', 'hide', 'isolate',
                                             'unhide', 'reset_temporary',
                                             'hide_category', 'unhide_category',
                                             'reset_color', 'pin', 'unpin',
                                             'halftone', 'unhalftone',
                                             'transparency', 'select_similar'],
                    'hint': 'Retry with one of supported_operations.'}

        # ── edit_elements ────────────────────────────────────────────────────
        elif tool_name == 'edit_elements':
            from Autodesk.Revit.DB import (ElementId, Transaction, XYZ, Plane,
                                           ElementTransformUtils, Group,
                                           ElementType)
            from System.Collections.Generic import List
            from Snippets._compat import elem_name
            op      = (arguments.get('operation') or '').lower()
            ids     = arguments.get('element_ids', [])
            cat_arg = arguments.get('category')
            view    = doc.ActiveView

            if op not in ('mirror', 'change_type', 'group', 'ungroup'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['mirror', 'change_type',
                                                 'group', 'ungroup'],
                        'hint': 'Retry with one of supported_operations.'}

            # Targets: explicit ids > whole category in the active view >
            # the live Revit selection.
            if ids:
                elem_ids = [make_eid(int(i)) for i in ids]
            elif cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                elem_ids = list(FilteredElementCollector(doc, view.Id)
                                .OfCategory(bic).WhereElementIsNotElementType()
                                .ToElementIds())
                if not elem_ids:
                    return {'error': 'No {} elements are visible in the active '
                                     'view.'.format(cat_arg)}
            else:
                elem_ids = list(uidoc.Selection.GetElementIds())
            if not elem_ids:
                return {'error': 'No target elements — pass element_ids or '
                                 'category, or select something in Revit first.'}

            if op == 'mirror':
                axis = (arguments.get('axis') or 'x').lower()
                if axis not in ('x', 'y'):
                    return {'error': "Unknown axis '{}'.".format(axis),
                            'supported_axis': ['x', 'y'],
                            'hint': 'Retry with one of supported_axis.'}
                M2FT = 3.28084
                origin = arguments.get('origin') or [0, 0]
                try:
                    ox = float(origin[0]) * M2FT
                    oy = float(origin[1]) * M2FT
                except (TypeError, ValueError, IndexError):
                    ox = oy = 0.0
                # Mirroring across a VERTICAL plane: "x" flips left/right, so
                # the plane's normal points along X.
                normal = XYZ(1, 0, 0) if axis == 'x' else XYZ(0, 1, 0)
                plane = Plane.CreateByNormalAndOrigin(normal, XYZ(ox, oy, 0))
                keep_original = bool(arguments.get('copy', False))
                id_list = List[ElementId](elem_ids)
                t = Transaction(doc, 'T3Lab AI Mirror Elements')
                t.Start()
                new_ids = []
                try:
                    try:
                        res = ElementTransformUtils.MirrorElements(
                            doc, id_list, plane, keep_original)
                        if res:
                            new_ids = [eid_value(i) for i in res]
                    except TypeError:
                        # Older overload without the mirrorCopies flag.
                        ElementTransformUtils.MirrorElements(doc, id_list, plane)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'axis': axis,
                        'kept_original': keep_original,
                        'count': len(elem_ids), 'new_element_ids': new_ids}

            elif op == 'change_type':
                type_name = (arguments.get('type_name') or '').strip()
                fam_name  = (arguments.get('family_name') or '').strip()
                if not type_name:
                    return {'error': 'change_type requires `type_name`.',
                            'hint': 'Call get_available_family_types first to '
                                    'see which type names exist.'}
                target = None
                for et in FilteredElementCollector(doc).OfClass(ElementType):
                    try:
                        # elem_name, not .Name: ElementType hides the getter
                        # from IronPython and raises AttributeError.
                        if elem_name(et) != type_name:
                            continue
                        if fam_name and (getattr(et, 'FamilyName', '') or '') != fam_name:
                            continue
                        target = et
                        break
                    except Exception:
                        continue
                if target is None:
                    return {'error': "No type named '{}'{} in this document.".format(
                                type_name,
                                " in family '{}'".format(fam_name) if fam_name else ''),
                            'hint': 'Call get_available_family_types to list the '
                                    'real type names.'}
                t = Transaction(doc, 'T3Lab AI Change Element Type')
                t.Start()
                changed = skipped = 0
                try:
                    for eid_obj in elem_ids:
                        el = doc.GetElement(eid_obj)
                        if el is None:
                            skipped += 1
                            continue
                        try:
                            if eid_value(el.GetTypeId()) == eid_value(target.Id):
                                skipped += 1
                                continue
                            # The return type differs across Revit versions
                            # (void vs ICollection<ElementId>) — never iterate it.
                            el.ChangeTypeId(target.Id)
                            changed += 1
                        except Exception:
                            skipped += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'type_name': type_name, 'type_id': eid_value(target.Id),
                        'count': changed, 'skipped': skipped,
                        'total_targeted': len(elem_ids)}

            elif op == 'group':
                t = Transaction(doc, 'T3Lab AI Group Elements')
                t.Start()
                try:
                    grp = doc.Create.NewGroup(List[ElementId](elem_ids))
                    gname = arguments.get('group_name')
                    if gname:
                        try:
                            grp.GroupType.Name = gname
                        except Exception:
                            pass   # name clash — the group itself is fine
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'group_id': eid_value(grp.Id),
                        'group_name': elem_name(grp.GroupType),
                        'member_count': len(elem_ids)}

            elif op == 'ungroup':
                t = Transaction(doc, 'T3Lab AI Ungroup Elements')
                t.Start()
                groups = freed = skipped = 0
                try:
                    for eid_obj in elem_ids:
                        el = doc.GetElement(eid_obj)
                        if not isinstance(el, Group):
                            skipped += 1
                            continue
                        try:
                            members = el.UngroupMembers()
                            groups += 1
                            freed += len(list(members)) if members else 0
                        except Exception:
                            skipped += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                if groups == 0:
                    return {'error': 'None of the targeted elements are Groups.',
                            'skipped': skipped,
                            'hint': 'Pass the element_ids of the Group instances '
                                    '(select them in Revit and use '
                                    'revit_get_selected_elements).'}
                return {'success': True, 'operation': op,
                        'groups_ungrouped': groups, 'elements_freed': freed,
                        'skipped': skipped}

        # ── manage_view ──────────────────────────────────────────────────────
        elif tool_name == 'manage_view':
            from Autodesk.Revit.DB import (Transaction, View, ViewDetailLevel,
                                           ViewDiscipline)
            op = (arguments.get('operation') or '').lower()
            if op not in ('get_properties', 'set_scale', 'set_detail_level',
                          'set_discipline', 'set_crop'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['get_properties', 'set_scale',
                                                 'set_detail_level',
                                                 'set_discipline', 'set_crop'],
                        'hint': 'Retry with one of supported_operations.'}

            raw_ids = arguments.get('view_ids') or []
            views = []
            if raw_ids:
                for i in raw_ids:
                    try:
                        v = doc.GetElement(make_eid(int(i)))
                    except Exception:
                        v = None
                    if isinstance(v, View):
                        views.append(v)
                if not views:
                    return {'error': 'view_ids did not resolve to any view.'}
            else:
                views = [doc.ActiveView]

            def _template_of(v):
                try:
                    tid = v.ViewTemplateId
                    if tid is None or eid_value(tid) == -1:
                        return None
                    return doc.GetElement(tid)
                except Exception:
                    return None

            if op == 'get_properties':
                out = []
                for v in views:
                    info = {'id': eid_value(v.Id), 'name': v.Name,
                            'type': str(v.ViewType), 'is_template': v.IsTemplate}
                    for key, attr in (('scale', 'Scale'),
                                      ('detail_level', 'DetailLevel'),
                                      ('discipline', 'Discipline'),
                                      ('crop_active', 'CropBoxActive'),
                                      ('crop_visible', 'CropBoxVisible')):
                        try:
                            val = getattr(v, attr)
                            info[key] = (val if isinstance(val, (bool, int))
                                         else str(val))
                        except Exception:
                            info[key] = None
                    tpl = _template_of(v)
                    info['view_template'] = tpl.Name if tpl is not None else None
                    out.append(info)
                return {'count': len(out), 'views': out}

            # Validate the payload BEFORE opening a transaction.
            detail_map = {'coarse': ViewDetailLevel.Coarse,
                          'medium': ViewDetailLevel.Medium,
                          'fine': ViewDetailLevel.Fine}
            disc_map = {'architectural': ViewDiscipline.Architectural,
                        'structural': ViewDiscipline.Structural,
                        'mechanical': ViewDiscipline.Mechanical,
                        'electrical': ViewDiscipline.Electrical,
                        'plumbing': ViewDiscipline.Plumbing,
                        'coordination': ViewDiscipline.Coordination}
            scale = detail = disc = None
            if op == 'set_scale':
                try:
                    scale = int(arguments.get('scale'))
                except (TypeError, ValueError):
                    return {'error': 'set_scale requires an integer `scale` '
                                     '(the denominator, e.g. 100 for 1:100).'}
                if scale <= 0:
                    return {'error': 'scale must be a positive integer.'}
            elif op == 'set_detail_level':
                key = (arguments.get('detail_level') or '').lower()
                detail = detail_map.get(key)
                if detail is None:
                    return {'error': "Unknown detail_level '{}'.".format(key),
                            'supported_detail_levels': sorted(detail_map),
                            'hint': 'Retry with one of supported_detail_levels.'}
            elif op == 'set_discipline':
                key = (arguments.get('discipline') or '').lower()
                disc = disc_map.get(key)
                if disc is None:
                    return {'error': "Unknown discipline '{}'.".format(key),
                            'supported_disciplines': sorted(disc_map),
                            'hint': 'Retry with one of supported_disciplines.'}
            elif op == 'set_crop':
                if (arguments.get('crop_active') is None
                        and arguments.get('crop_visible') is None):
                    return {'error': 'set_crop needs crop_active and/or '
                                     'crop_visible.'}

            t = Transaction(doc, 'T3Lab AI Manage View')
            t.Start()
            updated, failures = [], []
            try:
                for v in views:
                    try:
                        if op == 'set_scale':
                            v.Scale = scale
                        elif op == 'set_detail_level':
                            v.DetailLevel = detail
                        elif op == 'set_discipline':
                            v.Discipline = disc
                        elif op == 'set_crop':
                            if arguments.get('crop_active') is not None:
                                v.CropBoxActive = bool(arguments['crop_active'])
                            if arguments.get('crop_visible') is not None:
                                v.CropBoxVisible = bool(arguments['crop_visible'])
                        updated.append({'id': eid_value(v.Id), 'name': v.Name})
                    except Exception as ex:
                        # Scale / DetailLevel / Discipline are READ-ONLY while a
                        # view template controls them, and Revit's own message
                        # doesn't say so — name the template instead.
                        tpl = _template_of(v)
                        msg = str(ex)
                        if tpl is not None:
                            msg = ("controlled by view template '{}' — change it "
                                   "there, or detach the template from this view "
                                   "first. (Revit said: {})".format(tpl.Name, ex))
                        failures.append({'id': eid_value(v.Id), 'name': v.Name,
                                         'error': msg})
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            res = {'success': bool(updated), 'operation': op,
                   'updated': updated, 'updated_count': len(updated)}
            if failures:
                res['failed'] = failures
            return res

        # ── manage_view_template ─────────────────────────────────────────────
        elif tool_name == 'manage_view_template':
            from Autodesk.Revit.DB import Transaction, View
            # rename/delete reuse core.view_template (they own their own
            # transaction). list/usage/duplicate are inline: the shared
            # duplicate_templates hardcodes the name to "Copy of X" and all of
            # them return bare counts, which would lose the per-template detail
            # the model needs to report back.
            from core import view_template as vt_mod

            op = (arguments.get('operation') or '').lower()
            if op not in ('list', 'usage', 'rename', 'duplicate', 'delete'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['list', 'usage', 'rename',
                                                 'duplicate', 'delete'],
                        'hint': 'Retry with one of supported_operations.'}

            templates, views_using = {}, {}
            for v in FilteredElementCollector(doc).OfClass(View):
                try:
                    if v.IsTemplate:
                        templates[v.Name] = v
                        continue
                    tid = v.ViewTemplateId
                    if tid is not None and eid_value(tid) != -1:
                        views_using[eid_value(tid)] = views_using.get(
                            eid_value(tid), 0) + 1
                except Exception:
                    continue

            if op in ('list', 'usage'):
                out = []
                for nm, tpl in sorted(templates.items()):
                    entry = {'name': nm, 'id': eid_value(tpl.Id)}
                    if op == 'usage':
                        entry['used_by_views'] = views_using.get(
                            eid_value(tpl.Id), 0)
                    out.append(entry)
                return {'count': len(out), 'templates': out}

            # The remaining operations need one specific template.
            target = None
            tpl_id = arguments.get('template_id')
            tpl_name = (arguments.get('template_name') or '').strip()
            if tpl_id is not None:
                try:
                    cand = doc.GetElement(make_eid(int(tpl_id)))
                    if isinstance(cand, View) and cand.IsTemplate:
                        target = cand
                except Exception:
                    target = None
            elif tpl_name:
                target = templates.get(tpl_name)
                if target is None:
                    for nm, tpl in templates.items():
                        if nm.lower() == tpl_name.lower():
                            target = tpl
                            break
            if target is None:
                return {'error': "View template '{}' not found.".format(
                            tpl_name or tpl_id),
                        'available_templates': sorted(templates)[:20],
                        'hint': 'Call this tool with operation "list" first.'}

            if op == 'rename':
                new_name = (arguments.get('new_name') or '').strip()
                if not new_name:
                    return {'error': 'rename requires `new_name`.'}
                old = target.Name
                try:
                    vt_mod.rename_template(doc, target, new_name)
                except Exception as e:
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'old_name': old,
                        'new_name': new_name, 'id': eid_value(target.Id)}

            if op == 'duplicate':
                new_name = (arguments.get('new_name') or '').strip()
                t = Transaction(doc, 'T3Lab AI Duplicate View Template')
                t.Start()
                try:
                    from Autodesk.Revit.DB import ViewDuplicateOption
                    new_id = target.Duplicate(ViewDuplicateOption.Duplicate)
                    new_tpl = doc.GetElement(new_id)
                    if new_name:
                        try:
                            new_tpl.Name = new_name
                        except Exception:
                            pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'source': target.Name, 'new_name': new_tpl.Name,
                        'new_id': eid_value(new_tpl.Id)}

            # delete
            in_use = views_using.get(eid_value(target.Id), 0)
            if in_use and not bool(arguments.get('force')):
                return {'error': "View template '{}' is still used by {} view(s). "
                                 "Deleting it would silently change every one of "
                                 "them.".format(target.Name, in_use),
                        'used_by_views': in_use,
                        'hint': 'Reassign those views first, or call again with '
                                'force=true if that is really intended.'}
            name = target.Name
            try:
                ok, errs = vt_mod.delete_templates(doc, [target])
            except Exception as e:
                return {'error': str(e)}
            if not ok:
                return {'error': "Could not delete view template '{}'.".format(name)}
            return {'success': True, 'operation': op, 'deleted': name,
                    'was_used_by_views': in_use}

        # ── manage_links ─────────────────────────────────────────────────────
        elif tool_name == 'manage_links':
            from Autodesk.Revit.DB import (Transaction, RevitLinkType,
                                           RevitLinkInstance, CADLinkType,
                                           ImportInstance)
            from Snippets._compat import elem_name
            op = (arguments.get('operation') or '').lower()
            if op not in ('list', 'reload', 'unload', 'delete', 'pin', 'unpin'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['list', 'reload', 'unload',
                                                 'delete', 'pin', 'unpin'],
                        'hint': 'Retry with one of supported_operations.'}
            kind = (arguments.get('link_kind') or 'all').lower()
            if kind not in ('revit', 'cad', 'all'):
                return {'error': "Unknown link_kind '{}'.".format(kind),
                        'supported_link_kinds': ['revit', 'cad', 'all'],
                        'hint': 'Retry with one of supported_link_kinds.'}

            # Instance counts per type, so "list" can say what is placed.
            rvt_instances = {}
            for inst in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
                try:
                    tid = eid_value(inst.GetTypeId())
                    rvt_instances.setdefault(tid, []).append(inst)
                except Exception:
                    continue
            cad_instances = {}
            for inst in FilteredElementCollector(doc).OfClass(ImportInstance):
                try:
                    tid = eid_value(inst.GetTypeId())
                    cad_instances.setdefault(tid, []).append(inst)
                except Exception:
                    continue

            links = []
            if kind in ('revit', 'all'):
                for lt in FilteredElementCollector(doc).OfClass(RevitLinkType):
                    links.append(('revit', lt))
            if kind in ('cad', 'all'):
                for lt in FilteredElementCollector(doc).OfClass(CADLinkType):
                    links.append(('cad', lt))

            def _describe_link(lkind, lt):
                tid = eid_value(lt.Id)
                insts = (rvt_instances if lkind == 'revit' else cad_instances).get(tid, [])
                info = {'kind': lkind, 'name': elem_name(lt), 'id': tid,
                        'instances': len(insts)}
                try:
                    # None for an unloaded link — that is the signal, not an error.
                    info['loaded'] = (lt.GetLinkedFileStatus().ToString()
                                      if hasattr(lt, 'GetLinkedFileStatus')
                                      else None)
                except Exception:
                    info['loaded'] = None
                if lkind == 'cad':
                    try:
                        info['is_link'] = bool(insts and insts[0].IsLinked)
                    except Exception:
                        pass
                try:
                    info['pinned'] = bool(insts[0].Pinned) if insts else None
                except Exception:
                    info['pinned'] = None
                return info

            if op == 'list':
                out = [_describe_link(k, lt) for k, lt in links]
                return {'count': len(out), 'links': out}

            wanted_names = set(n.lower() for n in (arguments.get('link_names') or []))
            wanted_ids = set()
            for i in (arguments.get('link_ids') or []):
                try:
                    wanted_ids.add(int(i))
                except (TypeError, ValueError):
                    continue
            if not wanted_names and not wanted_ids:
                return {'error': "Operation '{}' needs link_names or link_ids — "
                                 "it will not act on every link at once.".format(op),
                        'hint': 'Call operation "list" first and pass the names '
                                'you mean.'}

            targets = [(k, lt) for k, lt in links
                       if elem_name(lt).lower() in wanted_names
                       or eid_value(lt.Id) in wanted_ids]
            if not targets:
                return {'error': 'None of those links exist in this document.',
                        'available_links': [elem_name(lt) for _k, lt in links][:20],
                        'hint': 'Call operation "list" for the exact names.'}

            t = Transaction(doc, 'T3Lab AI Manage Links')
            t.Start()
            done, failed = [], []
            try:
                for lkind, lt in targets:
                    name = elem_name(lt)
                    try:
                        if op == 'unload':
                            lt.Unload(None)      # arg is a save-coordinates callback
                        elif op == 'reload':
                            lt.Reload()
                        elif op == 'delete':
                            doc.Delete(lt.Id)
                        else:   # pin / unpin
                            want = (op == 'pin')
                            insts = (rvt_instances if lkind == 'revit'
                                     else cad_instances).get(eid_value(lt.Id), [])
                            if not insts:
                                failed.append({'name': name,
                                               'error': 'link has no placed instance'})
                                continue
                            for inst in insts:
                                inst.Pinned = want
                        done.append(name)
                    except Exception as ex:
                        failed.append({'name': name, 'error': str(ex)})
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            res = {'success': bool(done), 'operation': op, 'links': done,
                   'count': len(done)}
            if failed:
                res['failed'] = failed
            return res

        # ── manage_revision ──────────────────────────────────────────────────
        elif tool_name == 'manage_revision':
            from Autodesk.Revit.DB import Transaction, Revision, ViewSheet
            from System.Collections.Generic import List
            from Autodesk.Revit.DB import ElementId
            op = (arguments.get('operation') or '').lower()
            if op not in ('list', 'create', 'assign_to_sheets', 'set_issued'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['list', 'create',
                                                 'assign_to_sheets', 'set_issued'],
                        'hint': 'Retry with one of supported_operations.'}

            def _revisions():
                out = []
                for r in FilteredElementCollector(doc).OfClass(Revision):
                    out.append(r)
                try:
                    out.sort(key=lambda r: r.SequenceNumber)
                except Exception:
                    pass
                return out

            def _rev_info(r):
                info = {'id': eid_value(r.Id)}
                for key, attr in (('sequence', 'SequenceNumber'),
                                  ('description', 'Description'),
                                  ('date', 'RevisionDate'),
                                  ('issued', 'Issued'),
                                  ('issued_by', 'IssuedBy'),
                                  ('issued_to', 'IssuedTo')):
                    try:
                        info[key] = getattr(r, attr)
                    except Exception:
                        info[key] = None
                return info

            if op == 'list':
                revs = [_rev_info(r) for r in _revisions()]
                return {'count': len(revs), 'revisions': revs}

            if op == 'create':
                t = Transaction(doc, 'T3Lab AI Create Revision')
                t.Start()
                try:
                    rev = Revision.Create(doc)
                    for attr, key in (('Description', 'description'),
                                      ('RevisionDate', 'date'),
                                      ('IssuedBy', 'issued_by'),
                                      ('IssuedTo', 'issued_to')):
                        val = arguments.get(key)
                        if val:
                            try:
                                setattr(rev, attr, u'{}'.format(val))
                            except Exception:
                                pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'revision': _rev_info(rev)}

            # assign_to_sheets / set_issued both need a target revision.
            target = None
            rid = arguments.get('revision_id')
            revs = _revisions()
            if rid is not None:
                for r in revs:
                    if eid_value(r.Id) == int(rid):
                        target = r
                        break
            elif revs:
                target = revs[-1]      # most recent
            if target is None:
                return {'error': 'No revision found.',
                        'hint': 'Call operation "list", or "create" one first.'}

            if op == 'set_issued':
                want = arguments.get('issued')
                want = True if want is None else bool(want)
                t = Transaction(doc, 'T3Lab AI Set Revision Issued')
                t.Start()
                try:
                    for attr, key in (('IssuedBy', 'issued_by'),
                                      ('IssuedTo', 'issued_to')):
                        val = arguments.get(key)
                        if val:
                            try:
                                setattr(target, attr, u'{}'.format(val))
                            except Exception:
                                pass
                    target.Issued = want
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'revision': _rev_info(target)}

            # assign_to_sheets
            wanted_nums = set(u'{}'.format(n).strip().lower()
                              for n in (arguments.get('sheet_numbers') or []))
            wanted_ids = set()
            for i in (arguments.get('sheet_ids') or []):
                try:
                    wanted_ids.add(int(i))
                except (TypeError, ValueError):
                    continue
            if not wanted_nums and not wanted_ids:
                return {'error': 'assign_to_sheets needs sheet_numbers or sheet_ids.'}
            sheets = []
            for s in FilteredElementCollector(doc).OfClass(ViewSheet):
                try:
                    if (u'{}'.format(s.SheetNumber).strip().lower() in wanted_nums
                            or eid_value(s.Id) in wanted_ids):
                        sheets.append(s)
                except Exception:
                    continue
            if not sheets:
                return {'error': 'No matching sheets found.',
                        'hint': 'Call revit_list_sheets for the exact numbers.'}
            t = Transaction(doc, 'T3Lab AI Assign Revision To Sheets')
            t.Start()
            done, failed = [], []
            try:
                for s in sheets:
                    try:
                        existing = list(s.GetAdditionalRevisionIds())
                        if target.Id not in existing:
                            existing.append(target.Id)
                            s.SetAdditionalRevisionIds(List[ElementId](existing))
                        done.append(s.SheetNumber)
                    except Exception as ex:
                        failed.append({'sheet': s.SheetNumber, 'error': str(ex)})
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            res = {'success': bool(done), 'operation': op,
                   'revision_id': eid_value(target.Id),
                   'sheets': done, 'count': len(done)}
            if failed:
                res['failed'] = failed
            return res

        # ── manage_sheet ─────────────────────────────────────────────────────
        elif tool_name == 'manage_sheet':
            from Autodesk.Revit.DB import Transaction, ViewSheet, ViewSheetSet
            op = (arguments.get('operation') or '').lower()
            if op not in ('duplicate', 'renumber', 'list_sets'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['duplicate', 'renumber',
                                                 'list_sets'],
                        'hint': 'Retry with one of supported_operations.'}

            if op == 'list_sets':
                # Read-only. Creating/editing print sets has to go through
                # PrintManager global state, which is too version-sensitive to
                # promise without testing on every supported Revit.
                sets = []
                for ss in FilteredElementCollector(doc).OfClass(ViewSheetSet):
                    try:
                        sets.append({'name': ss.Name, 'id': eid_value(ss.Id),
                                     'view_count': len(list(ss.Views))})
                    except Exception:
                        continue
                return {'count': len(sets), 'sheet_sets': sets}

            wanted_nums = set(u'{}'.format(n).strip().lower()
                              for n in (arguments.get('sheet_numbers') or []))
            wanted_ids = set()
            for i in (arguments.get('sheet_ids') or []):
                try:
                    wanted_ids.add(int(i))
                except (TypeError, ValueError):
                    continue
            if not wanted_nums and not wanted_ids:
                return {'error': "Operation '{}' needs sheet_numbers or "
                                 "sheet_ids.".format(op)}
            sheets = []
            for s in FilteredElementCollector(doc).OfClass(ViewSheet):
                try:
                    if (u'{}'.format(s.SheetNumber).strip().lower() in wanted_nums
                            or eid_value(s.Id) in wanted_ids):
                        sheets.append(s)
                except Exception:
                    continue
            if not sheets:
                return {'error': 'No matching sheets found.',
                        'hint': 'Call revit_list_sheets for the exact numbers.'}

            if op == 'duplicate':
                from Autodesk.Revit.DB import ViewDuplicateOption
                t = Transaction(doc, 'T3Lab AI Duplicate Sheet')
                t.Start()
                done, failed = [], []
                try:
                    for s in sheets:
                        try:
                            new_id = s.Duplicate(ViewDuplicateOption.Duplicate)
                            new_sheet = doc.GetElement(new_id)
                            done.append({'from': s.SheetNumber,
                                         'new_id': eid_value(new_id),
                                         'new_number': new_sheet.SheetNumber})
                        except Exception as ex:
                            failed.append({'sheet': s.SheetNumber,
                                           'error': str(ex)})
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                res = {'success': bool(done), 'operation': op,
                       'duplicated': done, 'count': len(done)}
                if failed:
                    res['failed'] = failed
                return res

            # renumber
            new_number = (arguments.get('new_number') or '').strip()
            prefix = (arguments.get('prefix') or '').strip()
            if not new_number and not prefix:
                return {'error': 'renumber needs `new_number` (one sheet) or '
                                 '`prefix` (several).'}
            if new_number and len(sheets) != 1:
                return {'error': '`new_number` renames exactly one sheet, but {} '
                                 'matched. Use `prefix` for a batch.'.format(len(sheets))}
            try:
                start_at = int(arguments.get('start_at', 1))
            except (TypeError, ValueError):
                start_at = 1
            t = Transaction(doc, 'T3Lab AI Renumber Sheets')
            t.Start()
            done, failed = [], []
            try:
                for offset, s in enumerate(sheets):
                    old = s.SheetNumber
                    target_num = (new_number if new_number
                                  else '{}{}'.format(prefix, start_at + offset))
                    try:
                        s.SheetNumber = target_num
                        done.append({'from': old, 'to': target_num})
                    except Exception as ex:
                        # Duplicate sheet numbers are the usual cause and Revit
                        # says so obscurely — keep going with the rest.
                        failed.append({'sheet': old, 'wanted': target_num,
                                       'error': str(ex)})
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            res = {'success': bool(done), 'operation': op,
                   'renumbered': done, 'count': len(done)}
            if failed:
                res['failed'] = failed
            return res

        # ── export_model ─────────────────────────────────────────────────────
        elif tool_name == 'export_model':
            fmt = (arguments.get('format') or '').lower()
            if fmt not in ('ifc', 'nwc', 'dwf', 'dgn'):
                return {'error': "Unknown format '{}'.".format(fmt),
                        'supported_formats': ['ifc', 'nwc', 'dwf', 'dgn'],
                        'hint': ('Retry with one of supported_formats. PDF is '
                                 'export_sheets_pdf, DWG is export_dwg, PNG is '
                                 'export_image.')}
            out_dir = arguments.get('output_folder')
            if not out_dir:
                out_dir = (os.path.dirname(doc.PathName) if doc.PathName
                           else os.path.expanduser('~'))
            if not os.path.isdir(out_dir):
                try:
                    os.makedirs(out_dir)
                except Exception as e:
                    return {'error': 'Cannot use output folder {}: {}'.format(
                        out_dir, e)}
            base = (arguments.get('filename') or '').strip()
            if not base:
                base = (os.path.splitext(os.path.basename(doc.PathName))[0]
                        if doc.PathName else 'T3Lab_Export')
            for ch in '\\/:*?"<>|':
                base = base.replace(ch, '_')

            if fmt == 'ifc':
                try:
                    from Autodesk.Revit.DB import (IFCExportOptions, IFCVersion,
                                                   Transaction)
                except ImportError:
                    return {'error': 'This Revit has no IFC exporter — install '
                                     'the Autodesk IFC exporter add-in, then retry.'}
                opts = IFCExportOptions()
                ver = (arguments.get('ifc_version') or 'IFC2x3')
                try:
                    opts.FileVersion = getattr(IFCVersion, ver)
                except Exception:
                    opts.FileVersion = IFCVersion.IFC2x3
                    ver = 'IFC2x3'
                try:
                    opts.WallAndColumnSplitting = True
                except Exception:
                    pass
                # IFC is the ONLY export that must run inside a Transaction.
                t = Transaction(doc, 'T3Lab AI Export IFC')
                t.Start()
                try:
                    doc.Export(out_dir, base, opts)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'format': fmt, 'ifc_version': ver,
                        'folder': out_dir, 'file': base + '.ifc'}

            if fmt == 'nwc':
                try:
                    from Autodesk.Revit.DB import (NavisworksExportOptions,
                                                   NavisworksExportScope)
                except ImportError:
                    return {'error': 'This Revit has no Navisworks exporter — '
                                     'install the Navisworks Exporter add-in, '
                                     'then retry.'}
                opts = NavisworksExportOptions()
                try:
                    opts.ExportScope = NavisworksExportScope.View
                    opts.ViewId = doc.ActiveView.Id
                except Exception:
                    pass
                try:
                    doc.Export(out_dir, base, opts)
                except Exception as e:
                    return {'error': str(e)}
                return {'success': True, 'format': fmt, 'folder': out_dir,
                        'file': base + '.nwc', 'view': doc.ActiveView.Name}

            # dwf / dgn — sheet/view based
            from Autodesk.Revit.DB import ViewSet, ViewSheet
            views = ViewSet()
            wanted_nums = set(u'{}'.format(n).strip().lower()
                              for n in (arguments.get('sheet_numbers') or []))
            picked = []
            if wanted_nums:
                for s in FilteredElementCollector(doc).OfClass(ViewSheet):
                    try:
                        if u'{}'.format(s.SheetNumber).strip().lower() in wanted_nums:
                            views.Insert(s)
                            picked.append(s.SheetNumber)
                    except Exception:
                        continue
                if not picked:
                    return {'error': 'None of those sheet numbers exist.',
                            'hint': 'Call revit_list_sheets for the exact numbers.'}
            else:
                views.Insert(doc.ActiveView)
                picked.append(doc.ActiveView.Name)

            try:
                if fmt == 'dwf':
                    from Autodesk.Revit.DB import DWFExportOptions
                    opts = DWFExportOptions()
                else:
                    from Autodesk.Revit.DB import DGNExportOptions
                    opts = DGNExportOptions()
                doc.Export(out_dir, base, views, opts)
            except Exception as e:
                return {'error': str(e)}
            return {'success': True, 'format': fmt, 'folder': out_dir,
                    'file': base + '.' + fmt, 'exported': picked,
                    'count': len(picked)}

        # ── check_bad_geometry ───────────────────────────────────────────────
        elif tool_name == 'check_bad_geometry':
            import time as _time
            from Autodesk.Revit.DB import View
            # Same probe the badgeometry preflight check runs — one source of
            # truth for the geometry that kills the PDF/DWG exporter.
            from Snippets._geometry_probe import probe_element
            from Snippets._compat import elem_name

            deep = bool(arguments.get('deep_probe'))
            try:
                limit = int(arguments.get('limit', 40))
            except (TypeError, ValueError):
                limit = 40
            limit = max(1, min(limit, 200))

            views = []
            for i in (arguments.get('view_ids') or []):
                try:
                    v = doc.GetElement(make_eid(int(i)))
                except Exception:
                    v = None
                if isinstance(v, View):
                    views.append(v)
            if not views:
                views = [doc.ActiveView]

            collector_cat = None
            cat_arg = arguments.get('category')
            if cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                collector_cat = bic

            # The HTTP worker must not hang: stop cleanly and say so.
            BUDGET_SEC = 25.0
            started = _time.time()
            findings = []
            probed = 0
            timed_out = False
            for v in views:
                if timed_out:
                    break
                try:
                    col = FilteredElementCollector(doc, v.Id)
                    if collector_cat is not None:
                        col = col.OfCategory(collector_cat)
                    elements = list(col.WhereElementIsNotElementType().ToElements())
                except Exception as ex:
                    findings.append({'view': v.Name, 'error': str(ex)})
                    continue
                for el in elements:
                    if _time.time() - started > BUDGET_SEC:
                        timed_out = True
                        break
                    probed += 1
                    try:
                        problems = probe_element(el, v, deep_probe=deep)
                    except Exception as ex:
                        problems = ['probe failed: {}'.format(ex)]
                    if problems:
                        try:
                            cat_name = el.Category.Name if el.Category else None
                        except Exception:
                            cat_name = None
                        findings.append({'id': eid_value(el.Id),
                                         'name': elem_name(el),
                                         'category': cat_name,
                                         'view': v.Name,
                                         'problems': problems[:6]})
                        if len(findings) >= limit:
                            timed_out = False
                            break
                if len(findings) >= limit:
                    break

            res = {'views_scanned': [v.Name for v in views],
                   'elements_probed': probed,
                   'suspect_count': len(findings),
                   'deep_probe': deep,
                   'findings': findings}
            if timed_out:
                res['truncated'] = True
                res['note'] = ('Stopped after {}s to keep the connection alive — '
                               'narrow the scan with `category`, or scan one view '
                               'at a time.'.format(int(BUDGET_SEC)))
            elif not findings:
                res['note'] = ('No degenerate geometry found. If an export still '
                               'crashes, re-run with deep_probe=true — but note '
                               'that probe can itself crash Revit.')
            return res

        # ── manage_material ──────────────────────────────────────────────────
        elif tool_name == 'manage_material':
            from Autodesk.Revit.DB import Material
            op = (arguments.get('operation') or '').lower()
            if op not in ('list', 'get_element_materials'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['list', 'get_element_materials'],
                        'hint': 'Retry with one of supported_operations.'}

            if op == 'list':
                needle = (arguments.get('name_filter') or '').strip().lower()
                mats = []
                for m in FilteredElementCollector(doc).OfClass(Material):
                    try:
                        nm = m.Name
                        if needle and needle not in nm.lower():
                            continue
                        mats.append({'name': nm, 'id': eid_value(m.Id)})
                    except Exception:
                        continue
                mats.sort(key=lambda d: d['name'])
                return {'count': len(mats), 'materials': mats,
                        'hint': 'To assign one, pass its id to bulk_set_parameter '
                                'on the element material parameter.'}

            # get_element_materials
            ids = arguments.get('element_ids') or []
            cat_arg = arguments.get('category')
            if ids:
                targets = [doc.GetElement(make_eid(int(i))) for i in ids]
            elif cat_arg:
                bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                if _cat_err:
                    return _cat_err
                targets = list(FilteredElementCollector(doc, doc.ActiveView.Id)
                               .OfCategory(bic).WhereElementIsNotElementType()
                               .ToElements())
            else:
                targets = [doc.GetElement(i)
                           for i in uidoc.Selection.GetElementIds()]
            targets = [t for t in targets if t is not None]
            if not targets:
                return {'error': 'No elements to inspect — pass element_ids or '
                                 'category, or select something in Revit.'}

            out = []
            for el in targets:
                entry = {'id': eid_value(el.Id), 'materials': []}
                try:
                    # False = non-paint materials (the real assignment).
                    for mid in el.GetMaterialIds(False):
                        mat = doc.GetElement(mid)
                        if mat is not None:
                            entry['materials'].append(
                                {'name': mat.Name, 'id': eid_value(mid)})
                except Exception as ex:
                    entry['error'] = str(ex)
                out.append(entry)
            return {'count': len(out), 'elements': out}

        # ── create_detail_annotation ─────────────────────────────────────────
        elif tool_name == 'create_detail_annotation':
            from Autodesk.Revit.DB import (Transaction, XYZ, Line, CurveLoop,
                                           FilledRegion, FilledRegionType,
                                           ElementTypeGroup, View)
            from System.Collections.Generic import List as NetList
            from Snippets._compat import elem_name
            op = (arguments.get('operation') or '').lower()
            if op not in ('filled_region', 'detail_line'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['filled_region', 'detail_line'],
                        'hint': 'Retry with one of supported_operations.'}

            M2FT = 3.28084
            view = doc.ActiveView
            vid = arguments.get('view_id')
            if vid is not None:
                cand = doc.GetElement(make_eid(int(vid)))
                if not isinstance(cand, View):
                    return {'error': 'view_id does not refer to a view.'}
                view = cand
            try:
                if view.ViewType.ToString() in ('ThreeD', 'Schedule'):
                    return {'error': 'Detail annotation cannot be drawn in a {} '
                                     'view.'.format(view.ViewType)}
            except Exception:
                pass

            if op == 'detail_line':
                start = arguments.get('start')
                end = arguments.get('end')
                try:
                    p1 = XYZ(float(start[0]) * M2FT, float(start[1]) * M2FT, 0)
                    p2 = XYZ(float(end[0]) * M2FT, float(end[1]) * M2FT, 0)
                except (TypeError, ValueError, IndexError):
                    return {'error': 'detail_line needs `start` and `end` as '
                                     '[x, y] pairs in meters.'}
                if p1.DistanceTo(p2) < 1e-6:
                    return {'error': 'start and end are the same point.'}
                t = Transaction(doc, 'T3Lab AI Create Detail Line')
                t.Start()
                try:
                    curve = doc.Create.NewDetailCurve(view, Line.CreateBound(p1, p2))
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'operation': op,
                        'element_id': eid_value(curve.Id), 'view': view.Name}

            # filled_region
            pts = arguments.get('boundary_points') or []
            if len(pts) < 3:
                return {'error': 'filled_region needs at least 3 boundary_points '
                                 '([[x, y], ...] in meters).'}
            try:
                xyz = [XYZ(float(p[0]) * M2FT, float(p[1]) * M2FT, 0) for p in pts]
            except (TypeError, ValueError, IndexError):
                return {'error': 'boundary_points must be [[x, y], ...] numbers '
                                 'in meters.'}

            type_name = (arguments.get('type_name') or '').strip()
            frt_id = None
            if type_name:
                for frt in FilteredElementCollector(doc).OfClass(FilledRegionType):
                    if elem_name(frt) == type_name:
                        frt_id = frt.Id
                        break
                if frt_id is None:
                    return {'error': "No filled region type named '{}'.".format(
                                type_name),
                            'available_types': [
                                elem_name(f) for f in
                                FilteredElementCollector(doc)
                                .OfClass(FilledRegionType)][:20]}
            else:
                try:
                    frt_id = doc.GetDefaultElementTypeId(
                        ElementTypeGroup.FilledRegionType)
                except Exception:
                    frt_id = None
                if frt_id is None or eid_value(frt_id) == -1:
                    first = FilteredElementCollector(doc).OfClass(
                        FilledRegionType).FirstElement()
                    if first is None:
                        return {'error': 'This project has no filled region type.'}
                    frt_id = first.Id

            t = Transaction(doc, 'T3Lab AI Create Filled Region')
            t.Start()
            try:
                loop = CurveLoop()
                for i in range(len(xyz)):
                    a = xyz[i]
                    b = xyz[(i + 1) % len(xyz)]   # closes the loop
                    if a.DistanceTo(b) > 1e-6:
                        loop.Append(Line.CreateBound(a, b))
                loops = NetList[CurveLoop]()
                loops.Add(loop)
                region = FilledRegion.Create(doc, frt_id, view.Id, loops)
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            return {'success': True, 'operation': op,
                    'element_id': eid_value(region.Id), 'view': view.Name,
                    'points': len(xyz)}

        # ── manage_document ──────────────────────────────────────────────────
        elif tool_name == 'manage_document':
            # NOTE: this tool must NOT run inside a Transaction or a
            # TransactionGroup — Save/SaveAs/SynchronizeWithCentral all throw
            # if one is open. Nothing wraps tool calls in a group any more
            # (see __begin_action_group), so this holds as long as it stays
            # that way.
            op = (arguments.get('operation') or '').lower()
            if op not in ('save', 'save_as', 'sync_with_central'):
                return {'error': 'Unknown operation: {}'.format(op),
                        'supported_operations': ['save', 'save_as',
                                                 'sync_with_central'],
                        'hint': 'Retry with one of supported_operations.'}

            if op == 'save':
                if doc.IsFamilyDocument and not doc.PathName:
                    return {'error': 'This document has never been saved, so it '
                                     'has no path — use save_as with a path.'}
                if not doc.PathName:
                    return {'error': 'This document has no path yet — use '
                                     'save_as with a full .rvt path.'}
                try:
                    doc.Save()
                except Exception as e:
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'path': doc.PathName}

            if op == 'save_as':
                from Autodesk.Revit.DB import SaveAsOptions
                path = (arguments.get('path') or '').strip()
                if not path:
                    return {'error': 'save_as requires `path` (a full .rvt path).'}
                if not path.lower().endswith('.rvt'):
                    return {'error': 'save_as `path` must end in .rvt.'}
                folder = os.path.dirname(path)
                if folder and not os.path.isdir(folder):
                    return {'error': 'Folder does not exist: {}'.format(folder)}
                overwrite = bool(arguments.get('overwrite'))
                if os.path.exists(path) and not overwrite:
                    return {'error': 'A file already exists at {}.'.format(path),
                            'hint': 'Pass overwrite=true only if replacing it is '
                                    'really intended.'}
                opts = SaveAsOptions()
                try:
                    opts.OverwriteExistingFile = overwrite
                except Exception:
                    pass
                try:
                    doc.SaveAs(path, opts)
                except Exception as e:
                    return {'error': str(e)}
                return {'success': True, 'operation': op, 'path': path,
                        'overwrote': overwrite}

            # sync_with_central — the most consequential thing this server does.
            if not doc.IsWorkshared:
                return {'error': 'This model is not workshared, so there is no '
                                 'central file to synchronise with. Use "save".'}
            comment = (arguments.get('comment') or '').strip()
            if not comment:
                return {'error': 'sync_with_central requires a non-empty '
                                 '`comment` — it is recorded in the team\'s '
                                 'synchronisation history.'}
            # Opt-in gate: OFF unless the user turned it on in settings.
            allowed = False
            try:
                from config.settings import get_settings
                allowed = get_settings().is_sync_with_central_allowed()
            except Exception:
                allowed = False
            if not allowed:
                return {'error': 'Synchronising with central from the assistant '
                                 'is disabled.',
                        'hint': 'Turn on "allow_sync_with_central" in T3Lab '
                                'settings first, or sync from Revit\'s own '
                                'Collaborate tab.',
                        'setting': 'agents.allow_sync_with_central'}
            try:
                from Autodesk.Revit.DB import (TransactWithCentralOptions,
                                               SynchronizeWithCentralOptions,
                                               RelinquishOptions)
                relinquish = arguments.get('relinquish')
                relinquish = True if relinquish is None else bool(relinquish)
                swc = SynchronizeWithCentralOptions()
                swc.Comment = comment
                try:
                    # Compacting a central file mid-sync is slow and risky.
                    swc.Compact = False
                except Exception:
                    pass
                if relinquish:
                    rel = RelinquishOptions(False)
                    rel.UserWorksets = True
                    rel.StandardWorksets = True
                    rel.ViewWorksets = True
                    rel.FamilyWorksets = True
                    swc.SetRelinquishOptions(rel)
                doc.SynchronizeWithCentral(TransactWithCentralOptions(), swc)
            except Exception as e:
                return {'error': str(e)}
            return {'success': True, 'operation': op, 'comment': comment,
                    'relinquished': relinquish, 'path': doc.PathName}

        # ── color_elements ───────────────────────────────────────────────────
        elif tool_name == 'color_elements':
            from Autodesk.Revit.DB import (Color, OverrideGraphicSettings, Transaction,
                                           FillPatternElement, ElementId)
            cat_arg   = arguments.get('category', 'Rooms')
            param_arg = (arguments.get('parameter_name') or '').strip()
            # Empty parameter_name would dump every element into one 'Unknown'
            # group and paint them all the first palette color (blue) — the
            # classic misfire when the model wanted ONE specific color.
            if not param_arg:
                return {'error': "parameter_name is required — color_elements "
                                 "color-codes elements BY a parameter's values "
                                 "(one distinct color per value, e.g. 'Type Name', "
                                 "'Level'). To apply ONE specific color (e.g. red) "
                                 "use revit_override_color instead."}
            view      = doc.ActiveView

            # Full shared category vocabulary (was a private 5-entry map that
            # silently fell back to Rooms for anything else — "tô màu cửa
            # theo Type" colored ROOMS). Unknown names now error with the
            # supported list, same contract as ai_element_filter.
            bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
            if _cat_err:
                return _cat_err
            collector = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()

            solid_id = self._solid_fill_id(doc)

            # Group elements by param value
            groups = {}
            for elem in collector:
                try:
                    p = elem.LookupParameter(param_arg)
                    val = (p.AsValueString() or p.AsString() or 'Unknown') if p else 'Unknown'
                    groups.setdefault(val, []).append(elem.Id)
                except Exception:
                    pass

            # Parameter found on NO element → coloring would be one arbitrary
            # 'Unknown'-group color, which reads as a bug. Refuse instead.
            if groups and set(groups.keys()) == set(['Unknown']):
                return {'error': "Parameter '{}' was not found on any {} "
                                 "element — nothing was colored. Color-coding "
                                 "needs a real parameter (e.g. 'Type Name', "
                                 "'Level'). To apply ONE specific color to all "
                                 "of them, call revit_override_color "
                                 "instead.".format(param_arg, cat_arg)}

            # Assign distinct colors
            COLORS = [
                (52, 152, 219), (46, 204, 113), (231, 76, 60),
                (155, 89, 182), (241, 196, 15), (26, 188, 156),
                (230, 126, 34), (149, 165, 166), (52, 73, 94), (127, 140, 141),
            ]
            t = Transaction(doc, 'T3Lab AI Color by Parameter')
            t.Start()
            try:
                for idx, (val, eids) in enumerate(groups.items()):
                    r, g, b = COLORS[idx % len(COLORS)]
                    rev_color = Color(r, g, b)
                    ogs = OverrideGraphicSettings()
                    ogs.SetProjectionLineColor(rev_color)
                    if solid_id != ElementId.InvalidElementId:
                        try:
                            ogs.SetSurfaceForegroundPatternId(solid_id)
                            ogs.SetSurfaceForegroundPatternColor(rev_color)
                        except Exception:
                            pass
                    for eid_obj in eids:
                        try:
                            view.SetElementOverrides(eid_obj, ogs)
                        except Exception:
                            pass
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            return {
                'success': True,
                'category': cat_arg,
                'parameter': param_arg,
                'group_count': len(groups),
                'groups': {v: len(ids) for v, ids in groups.items()},
            }

        # ── tag_all_walls ────────────────────────────────────────────────────
        elif tool_name == 'tag_all_walls':
            from Autodesk.Revit.DB import IndependentTag, TagMode, TagOrientation
            from Autodesk.Revit.DB import Wall, Reference
            leader   = bool(arguments.get('leader', False))
            view     = doc.ActiveView
            walls    = FilteredElementCollector(doc, view.Id).OfClass(Wall).ToElements()

            from Autodesk.Revit.DB import UV, XYZ
            t = Transaction(doc, 'T3Lab AI Tag All Walls')
            t.Start()
            tagged = 0
            try:
                for wall in walls:
                    try:
                        loc   = wall.Location
                        mid_pt = loc.Curve.Evaluate(0.5, True)
                        ref   = Reference(wall)
                        uv    = UV(mid_pt.X, mid_pt.Y)
                        tag   = IndependentTag.Create(doc, view.Id, ref, leader, TagMode.TM_ADDBY_CATEGORY, TagOrientation.Horizontal, mid_pt)
                        tagged += 1
                    except Exception:
                        pass
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            return {'success': True, 'tagged_count': tagged}

        # ── tag_all_rooms ────────────────────────────────────────────────────
        elif tool_name == 'tag_all_rooms':
            from Autodesk.Revit.DB import IndependentTag, TagMode, TagOrientation, Reference
            from Autodesk.Revit.DB.Architecture import Room
            view  = doc.ActiveView
            rooms = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()

            t = Transaction(doc, 'T3Lab AI Tag All Rooms')
            t.Start()
            tagged = 0
            try:
                for room in rooms:
                    try:
                        loc = room.Location
                        if loc is None:
                            continue
                        pt  = loc.Point
                        ref = Reference(room)
                        IndependentTag.Create(doc, view.Id, ref, False, TagMode.TM_ADDBY_CATEGORY, TagOrientation.Horizontal, pt)
                        tagged += 1
                    except Exception:
                        pass
                t.Commit()
            except Exception as e:
                t.RollBack()
                return {'error': str(e)}
            return {'success': True, 'tagged_count': tagged}

        # ── export_room_data ─────────────────────────────────────────────────
        elif tool_name == 'export_room_data':
            lvl_filter = arguments.get('level_name')
            rooms_out  = []
            for room in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
                try:
                    lvl_name = room.Level.Name if room.Level else ''
                    if lvl_filter and lvl_name != lvl_filter:
                        continue
                    area_ft2 = 0.0
                    try:
                        ap = room.LookupParameter('Area')
                        if ap:
                            area_ft2 = ap.AsDouble()
                    except Exception:
                        pass
                    dept = ''
                    try:
                        dp = room.LookupParameter('Department')
                        if dp:
                            dept = dp.AsString() or ''
                    except Exception:
                        pass
                    rooms_out.append({
                        'id': eid_value(room.Id),
                        'number': room.Number,
                        'name': room.Name,
                        'level': lvl_name,
                        'area_m2': round(area_ft2 * 0.0929, 2),
                        'department': dept,
                    })
                except Exception:
                    pass
            return {'count': len(rooms_out), 'rooms': rooms_out}

        # ── store_project_data ───────────────────────────────────────────────
        elif tool_name == 'store_project_data':
            import json as _json
            info = doc.ProjectInformation
            data = {
                'name': info.Name,
                'number': info.Number,
                'client': info.ClientName,
                'address': info.Address,
                'status': info.Status,
                'doc_path': doc.PathName,
            }
            out_dir  = os.path.join(os.path.dirname(doc.PathName) if doc.PathName else os.path.expanduser('~'), 'T3Lab_AI_Data')
            try:
                os.makedirs(out_dir)
            except OSError:
                pass
            out_path = os.path.join(out_dir, 'project_data.json')
            with open(out_path, 'w') as f:
                _json.dump(data, f, indent=2)
            return {'success': True, 'file': out_path, 'data': data}

        # ── store_room_data ──────────────────────────────────────────────────
        elif tool_name == 'store_room_data':
            import json as _json
            rooms_out = []
            for room in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
                try:
                    area_ft2 = 0.0
                    try:
                        ap = room.LookupParameter('Area')
                        if ap:
                            area_ft2 = ap.AsDouble()
                    except Exception:
                        pass
                    rooms_out.append({
                        'id': eid_value(room.Id),
                        'number': room.Number,
                        'name': room.Name,
                        'level': room.Level.Name if room.Level else '',
                        'area_m2': round(area_ft2 * 0.0929, 2),
                    })
                except Exception:
                    pass
            out_dir  = os.path.join(os.path.dirname(doc.PathName) if doc.PathName else os.path.expanduser('~'), 'T3Lab_AI_Data')
            try:
                os.makedirs(out_dir)
            except OSError:
                pass
            out_path = os.path.join(out_dir, 'room_data.json')
            with open(out_path, 'w') as f:
                _json.dump({'rooms': rooms_out}, f, indent=2)
            return {'success': True, 'file': out_path, 'room_count': len(rooms_out)}

        # ── query_stored_data ────────────────────────────────────────────────
        elif tool_name == 'query_stored_data':
            import json as _json
            data_type = (arguments.get('data_type') or 'project').lower()
            # Anything that wasn't 'project' used to fall through to the rooms
            # file — a typo silently returned the wrong dataset.
            if data_type not in ('project', 'rooms'):
                return {'error': "Unknown data_type '{}'.".format(data_type),
                        'supported_data_types': ['project', 'rooms'],
                        'hint': 'Retry with one of supported_data_types.'}
            out_dir   = os.path.join(os.path.dirname(doc.PathName) if doc.PathName else os.path.expanduser('~'), 'T3Lab_AI_Data')
            fname     = 'project_data.json' if data_type == 'project' else 'room_data.json'
            fpath     = os.path.join(out_dir, fname)
            if not os.path.isfile(fpath):
                return {'error': 'No stored data found. Run store_project_data or store_room_data first.', 'path': fpath}
            with open(fpath, 'r') as f:
                return _json.load(f)

        # ── send_code_to_revit ───────────────────────────────────────────────
        elif tool_name == 'send_code_to_revit':
            code = arguments.get('code', '')
            if not code:
                return {'error': 'No code provided'}
            # Execute directly in this ExternalEvent context (we are already on
            # the Revit main thread here). Also write result to result.json so
            # file-based clients can read it.
            local_ctx = {
                'doc':    doc,
                'uidoc':  uidoc,
                'app':    doc.Application,
                'result': None,
                'output': [],
            }
            # Redirect stdout for the duration of the exec: under pyRevit the
            # FIRST print() pops the output window as a dialog over Revit —
            # LLM-generated code prints habitually, so without this every
            # code-running chat turn threw a raw-text window in the user's
            # face. Captured text is returned as part of the tool result.
            import sys as _sys
            try:
                from io import StringIO as _StringIO            # CPython 3
            except ImportError:
                from StringIO import StringIO as _StringIO      # IronPython 2.7
            _old_stdout = _sys.stdout
            _buf = _StringIO()
            try:
                _sys.stdout = _buf
                try:
                    exec(code, local_ctx)   # noqa: S102
                finally:
                    _sys.stdout = _old_stdout
                printed      = _buf.getvalue().strip()
                result_val   = local_ctx.get('result')
                output_lines = local_ctx.get('output', [])
                parts = []
                if printed:
                    parts.append(printed)
                if result_val is not None:
                    parts.append(str(result_val))
                elif output_lines:
                    parts.append('\n'.join(str(x) for x in output_lines))
                out_str = '\n'.join(parts) if parts else 'OK'
                if len(out_str) > 8000:
                    out_str = out_str[:8000] + '... [truncated]'
                # Mirror to file-watcher result files for cross-channel clients
                try:
                    from core.file_watcher import RESULT_FILE, RESULT_TXT
                    import json as _json, time as _time
                    _res = {'task_id': 'mcp', 'status': 'success', 'output': out_str,
                            'error': '', 'timestamp': _time.time()}
                    with open(RESULT_FILE, 'w') as _f:
                        _json.dump(_res, _f, indent=2)
                    with open(RESULT_TXT, 'w') as _f:
                        _f.write(out_str)
                except Exception:
                    pass
                return {'success': True, 'result': out_str}
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                err_str = '{}\n{}'.format(e, tb)
                try:
                    from core.file_watcher import RESULT_FILE, RESULT_TXT
                    import json as _json, time as _time
                    _res = {'task_id': 'mcp', 'status': 'error', 'output': '',
                            'error': err_str, 'timestamp': _time.time()}
                    with open(RESULT_FILE, 'w') as _f:
                        _json.dump(_res, _f, indent=2)
                    with open(RESULT_TXT, 'w') as _f:
                        _f.write('ERROR: {}'.format(err_str))
                except Exception:
                    pass
                return {'success': False, 'error': err_str}

        # ── say_hello ────────────────────────────────────────────────────────
        elif tool_name == 'say_hello':
            msg = arguments.get('message', 'Hello from T3Lab AI!')
            try:
                from Autodesk.Revit.UI import TaskDialog
                TaskDialog.Show('T3Lab AI', msg)
                return {'success': True, 'message': msg}
            except Exception as e:
                return {'error': str(e)}

        # ── set_parameter ────────────────────────────────────────────────────
        elif tool_name == 'set_parameter':
            try:
                from Autodesk.Revit.DB import Transaction, StorageType
                eid   = int(arguments.get('element_id', 0))
                pname = arguments.get('parameter_name', '')
                value = arguments.get('value', '')
                elem  = doc.GetElement(make_eid(eid))
                if not elem:
                    return {'error': 'Element not found: {}'.format(eid)}
                param = elem.LookupParameter(pname)
                if not param:
                    return {'error': 'Parameter not found: {}'.format(pname)}
                if param.IsReadOnly:
                    return {'error': 'Parameter is read-only: {}'.format(pname)}
                t = Transaction(doc, 'T3Lab AI Set Parameter')
                t.Start()
                try:
                    st = param.StorageType
                    if st == StorageType.String:
                        param.Set(value)
                    elif st == StorageType.Double:
                        # Try display-unit parsing first ("3000" mm, "3.5 m",
                        # etc. — matches what the user sees in Revit), then
                        # fall back to a raw internal-unit float.
                        parsed = False
                        try:
                            parsed = param.SetValueString(value)
                        except Exception:
                            parsed = False
                        if not parsed:
                            param.Set(float(value))
                    elif st == StorageType.Integer:
                        param.Set(int(float(value)))
                    elif st == StorageType.ElementId:
                        param.Set(make_eid(int(value)))
                    t.Commit()
                    return {'success': True, 'element_id': eid, 'parameter': pname, 'value': value}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── get_all_parameters ───────────────────────────────────────────────
        elif tool_name == 'get_all_parameters':
            try:
                from Autodesk.Revit.DB import StorageType
                eid  = int(arguments.get('element_id', 0))
                elem = doc.GetElement(make_eid(eid))
                if not elem:
                    return {'error': 'Element not found: {}'.format(eid)}
                params = []
                for p in elem.Parameters:
                    try:
                        st = p.StorageType
                        if st == StorageType.String:
                            val = p.AsString() or ''
                        elif st == StorageType.Double:
                            val = p.AsDouble()
                        elif st == StorageType.Integer:
                            val = p.AsInteger()
                        elif st == StorageType.ElementId:
                            val = eid_value(p.AsElementId())
                        else:
                            val = None
                        params.append({
                            'name': p.Definition.Name,
                            'value': val,
                            'storage_type': str(st),
                            'read_only': p.IsReadOnly
                        })
                    except Exception:
                        pass
                params.sort(key=lambda x: x['name'])
                return {'element_id': eid, 'parameters': params, 'count': len(params)}
            except Exception as e:
                return {'error': str(e)}

        # ── move_elements ────────────────────────────────────────────────────
        elif tool_name == 'move_elements':
            try:
                from Autodesk.Revit.DB import (Transaction, ElementTransformUtils,
                                               XYZ)
                import System.Collections.Generic as SCG
                ft = 3.28084
                dx = float(arguments.get('dx', 0)) * ft
                dy = float(arguments.get('dy', 0)) * ft
                dz = float(arguments.get('dz', 0)) * ft
                ids_raw = arguments.get('element_ids', [])
                id_list = SCG.List[ElementId]([make_eid(int(i)) for i in ids_raw])
                t = Transaction(doc, 'T3Lab AI Move Elements')
                t.Start()
                try:
                    ElementTransformUtils.MoveElements(doc, id_list, XYZ(dx, dy, dz))
                    t.Commit()
                    return {'success': True, 'moved': len(ids_raw), 'delta_m': {'dx': arguments.get('dx'), 'dy': arguments.get('dy'), 'dz': arguments.get('dz', 0)}}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── copy_elements ────────────────────────────────────────────────────
        elif tool_name == 'copy_elements':
            try:
                from Autodesk.Revit.DB import (Transaction, ElementTransformUtils, XYZ)
                import System.Collections.Generic as SCG
                ft = 3.28084
                dx = float(arguments.get('dx', 0)) * ft
                dy = float(arguments.get('dy', 0)) * ft
                dz = float(arguments.get('dz', 0)) * ft
                ids_raw = arguments.get('element_ids', [])
                id_list = SCG.List[ElementId]([make_eid(int(i)) for i in ids_raw])
                t = Transaction(doc, 'T3Lab AI Copy Elements')
                t.Start()
                try:
                    new_ids = ElementTransformUtils.CopyElements(doc, id_list, XYZ(dx, dy, dz))
                    t.Commit()
                    return {'success': True, 'copied': len(ids_raw),
                            'new_element_ids': [eid_value(i) for i in new_ids]}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── rotate_element ───────────────────────────────────────────────────
        elif tool_name == 'rotate_element':
            try:
                from Autodesk.Revit.DB import (Transaction, ElementTransformUtils,
                                               XYZ, Line)
                import System.Collections.Generic as SCG
                import math
                ft = 3.28084
                angle_rad = float(arguments.get('angle_degrees', 0)) * math.pi / 180.0
                ox = float(arguments.get('origin_x', 0)) * ft
                oy = float(arguments.get('origin_y', 0)) * ft
                axis = Line.CreateBound(XYZ(ox, oy, 0), XYZ(ox, oy, 1))
                ids_raw = arguments.get('element_ids', [])
                id_list = SCG.List[ElementId]([make_eid(int(i)) for i in ids_raw])
                t = Transaction(doc, 'T3Lab AI Rotate Elements')
                t.Start()
                try:
                    ElementTransformUtils.RotateElements(doc, id_list, axis, angle_rad)
                    t.Commit()
                    return {'success': True, 'rotated': len(ids_raw), 'angle_degrees': arguments.get('angle_degrees')}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── get_element_bounding_box ─────────────────────────────────────────
        elif tool_name == 'get_element_bounding_box':
            try:
                eid  = int(arguments.get('element_id', 0))
                elem = doc.GetElement(make_eid(eid))
                if not elem:
                    return {'error': 'Element not found: {}'.format(eid)}
                bb = elem.get_BoundingBox(None)
                if not bb:
                    return {'error': 'Element has no bounding box'}
                ft_to_m = 0.3048
                return {
                    'element_id': eid,
                    'min': {'x': round(bb.Min.X * ft_to_m, 4), 'y': round(bb.Min.Y * ft_to_m, 4), 'z': round(bb.Min.Z * ft_to_m, 4)},
                    'max': {'x': round(bb.Max.X * ft_to_m, 4), 'y': round(bb.Max.Y * ft_to_m, 4), 'z': round(bb.Max.Z * ft_to_m, 4)}
                }
            except Exception as e:
                return {'error': str(e)}

        # ── create_view ──────────────────────────────────────────────────────
        elif tool_name == 'create_view':
            try:
                from Autodesk.Revit.DB import Transaction
                # Shared with the ManaViews bulk creator, so both know the same
                # nine view types. The old inline version handled only plans and
                # silently produced a 3D view for everything else — asking for a
                # section quietly got you an isometric.
                from core.advanced_view_manager import (create_single_view,
                                                        create_room_3d_views,
                                                        canonical_view_type,
                                                        SUPPORTED_VIEW_TYPES)
                vtype = (arguments.get('view_type') or 'floor_plan').lower()
                name  = arguments.get('name')
                level_name = arguments.get('level_name')
                room_ids = arguments.get('room_ids') or []

                if canonical_view_type(vtype) is None:
                    return {'error': "Unknown view_type '{}'.".format(vtype),
                            'supported_view_types': SUPPORTED_VIEW_TYPES,
                            'hint': 'Retry with one of supported_view_types.'}

                # ── One 3D view per room ─────────────────────────────────
                if room_ids:
                    if canonical_view_type(vtype) != '3D View':
                        return {
                            'error': ("room_ids only works with "
                                      "view_type='3d' (a section box needs a "
                                      "3D view); got '{}'.".format(vtype)),
                            'hint': ("For per-room PLANS use the Create Room "
                                     "Plan tool instead."),
                        }
                    rooms, missing = [], []
                    for raw_id in room_ids:
                        try:
                            el = doc.GetElement(make_eid(int(raw_id)))
                        except Exception:
                            el = None
                        # Duck-typed on purpose: importing
                        # DB.Architecture.Room at module scope would drag the
                        # Architecture assembly into every server import.
                        if el is None or not hasattr(el, 'get_BoundingBox'):
                            missing.append(raw_id)
                        else:
                            rooms.append(el)
                    if not rooms:
                        return {'error': 'None of the given room_ids resolved '
                                         'to an element in this document.',
                                'unresolved_ids': missing}

                    t = Transaction(doc, 'T3Lab AI Create Room 3D Views')
                    t.Start()
                    try:
                        created, skipped = create_room_3d_views(
                            doc, rooms,
                            margin_mm=arguments.get('margin_mm') or 0.0)
                        if not created:
                            t.RollBack()
                            return {'error': 'No view could be created.',
                                    'skipped': skipped,
                                    'unresolved_ids': missing}
                        t.Commit()
                    except Exception as e:
                        t.RollBack()
                        return {'error': str(e)}
                    out = {'success': True,
                           'created_count': len(created),
                           'requested_count': len(room_ids),
                           'views': created}
                    if skipped:
                        out['skipped'] = skipped
                    if missing:
                        out['unresolved_ids'] = missing
                    return out

                t = Transaction(doc, 'T3Lab AI Create View')
                t.Start()
                try:
                    view, err = create_single_view(doc, vtype, name=name,
                                                   level_name=level_name)
                    if err:
                        t.RollBack()
                        return {'error': err, 'view_type': vtype}
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
                return {'success': True, 'view_id': eid_value(view.Id),
                        'view_name': view.Name, 'view_type': vtype}
            except Exception as e:
                return {'error': str(e)}

        # ── set_active_view ──────────────────────────────────────────────────
        elif tool_name == 'set_active_view':
            try:
                from Autodesk.Revit.DB import View
                view_name = arguments.get('view_name')
                view_id   = arguments.get('view_id')
                target_view = None
                if view_id:
                    target_view = doc.GetElement(make_eid(int(view_id)))
                elif view_name:
                    views = FilteredElementCollector(doc).OfClass(View).ToElements()
                    for v in views:
                        if v.Name == view_name:
                            target_view = v
                            break
                if not target_view:
                    return {'error': 'View not found'}
                try:
                    uidoc.ActiveView = target_view
                    return {'success': True, 'view_id': eid_value(target_view.Id), 'view_name': target_view.Name}
                except Exception as e:
                    return {'error': 'Cannot switch view: {}'.format(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── rename_element ───────────────────────────────────────────────────
        elif tool_name == 'rename_element':
            try:
                from Autodesk.Revit.DB import Transaction
                eid      = int(arguments.get('element_id', 0))
                new_name = arguments.get('new_name', '')
                elem     = doc.GetElement(make_eid(eid))
                if not elem:
                    return {'error': 'Element not found: {}'.format(eid)}
                t = Transaction(doc, 'T3Lab AI Rename Element')
                t.Start()
                try:
                    elem.Name = new_name
                    t.Commit()
                    return {'success': True, 'element_id': eid, 'new_name': new_name}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── create_sheet ─────────────────────────────────────────────────────
        elif tool_name == 'create_sheet':
            try:
                from Autodesk.Revit.DB import (Transaction, ViewSheet,
                                               FilteredElementCollector,
                                               FamilySymbol, BuiltInCategory)
                sheet_number = arguments.get('sheet_number', 'A-101')
                sheet_name   = arguments.get('sheet_name', 'New Sheet')
                tb_name      = arguments.get('title_block')

                # Find title block type
                tb_id = ElementId.InvalidElementId
                tb_types = (FilteredElementCollector(doc)
                            .OfCategory(BuiltInCategory.OST_TitleBlocks)
                            .WhereElementIsElementType()
                            .ToElements())
                if tb_types:
                    if tb_name:
                        for tb in tb_types:
                            if tb.Name == tb_name or (hasattr(tb, 'FamilyName') and tb.FamilyName == tb_name):
                                tb_id = tb.Id
                                break
                    if tb_id == ElementId.InvalidElementId:
                        tb_id = tb_types[0].Id

                t = Transaction(doc, 'T3Lab AI Create Sheet')
                t.Start()
                try:
                    sheet = ViewSheet.Create(doc, tb_id)
                    final_number = sheet_number
                    try:
                        sheet.SheetNumber = sheet_number
                    except Exception:
                        # Duplicate sheet number — uniquify instead of failing
                        final_number = None
                        for suffix in range(2, 100):
                            candidate = '{}-{}'.format(sheet_number, suffix)
                            try:
                                sheet.SheetNumber = candidate
                                final_number = candidate
                                break
                            except Exception:
                                continue
                        if final_number is None:
                            t.RollBack()
                            return {'error': 'Sheet number "{}" already exists and no free variant found'.format(sheet_number)}
                    sheet.Name = sheet_name
                    t.Commit()
                    result = {'success': True, 'sheet_id': eid_value(sheet.Id),
                              'sheet_number': final_number, 'sheet_name': sheet_name}
                    if final_number != sheet_number:
                        result['warning'] = 'Sheet number "{}" was taken — used "{}" instead'.format(sheet_number, final_number)
                    return result
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── add_view_to_sheet ────────────────────────────────────────────────
        elif tool_name == 'add_view_to_sheet':
            try:
                from Autodesk.Revit.DB import (Transaction, Viewport, XYZ)
                sheet_id = int(arguments.get('sheet_id', 0))
                view_id  = int(arguments.get('view_id', 0))
                mm_to_ft = 0.00328084
                x = float(arguments.get('x', 297)) * mm_to_ft
                y = float(arguments.get('y', 210)) * mm_to_ft
                sheet = doc.GetElement(make_eid(sheet_id))
                view  = doc.GetElement(make_eid(view_id))
                if not sheet:
                    return {'error': 'Sheet not found: {}'.format(sheet_id)}
                if not view:
                    return {'error': 'View not found: {}'.format(view_id)}
                try:
                    if not Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
                        return {'error': 'View "{}" cannot be placed on sheet "{}" — it is probably '
                                         'already placed on a sheet, or is a view type (schedule/legend) '
                                         'that needs a different placement method.'.format(view.Name, sheet.SheetNumber)}
                except Exception:
                    pass
                t = Transaction(doc, 'T3Lab AI Add View to Sheet')
                t.Start()
                try:
                    vp = Viewport.Create(doc, sheet.Id, view.Id, XYZ(x, y, 0))
                    t.Commit()
                    return {'success': True, 'viewport_id': eid_value(vp.Id),
                            'sheet_id': sheet_id, 'view_id': view_id}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── create_text_note ─────────────────────────────────────────────────
        elif tool_name == 'create_text_note':
            try:
                from Autodesk.Revit.DB import (Transaction, TextNote,
                                               TextNoteOptions, XYZ,
                                               FilteredElementCollector, TextNoteType)
                text      = arguments.get('text', '')
                if not text:
                    return {'error': 'No text provided'}
                ft = 3.28084
                x  = float(arguments.get('x', 0)) * ft
                y  = float(arguments.get('y', 0)) * ft
                type_name = arguments.get('text_type')
                font_size = arguments.get('font_size')

                # Target view: explicit sheet_number > active view. The AI
                # often passes sheet_number — ignoring it silently placed
                # notes on whatever view happened to be active.
                active_view = doc.ActiveView
                sheet_no = arguments.get('sheet_number')
                if sheet_no:
                    from Autodesk.Revit.DB import ViewSheet as _VS
                    for _s in FilteredElementCollector(doc).OfClass(_VS):
                        if _s.SheetNumber == u'{}'.format(sheet_no):
                            active_view = _s
                            break
                if not active_view:
                    return {'error': 'No active view'}

                # Resolve text note type: named type > closest font size >
                # document default > first available.
                tn_types = list(FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
                if not tn_types:
                    return {'error': 'No text note types found in the document'}
                tn_type = None
                if type_name:
                    for tt in tn_types:
                        if tt.Name == type_name:
                            tn_type = tt
                            break
                    if tn_type is None:
                        for tt in tn_types:
                            if type_name.lower() in tt.Name.lower():
                                tn_type = tt
                                break
                if tn_type is None and font_size:
                    # Pick the loaded type whose text height (mm) is closest
                    target_ft = float(font_size) / 304.8
                    best_diff = 1e30
                    for tt in tn_types:
                        try:
                            sp = tt.get_Parameter(
                                __import__('Autodesk.Revit.DB', fromlist=['BuiltInParameter']).BuiltInParameter.TEXT_SIZE)
                            if sp:
                                diff = abs(sp.AsDouble() - target_ft)
                                if diff < best_diff:
                                    best_diff = diff
                                    tn_type = tt
                        except Exception:
                            pass
                if tn_type is None:
                    try:
                        from Autodesk.Revit.DB import ElementTypeGroup
                        default_id = doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)
                        if default_id and default_id != ElementId.InvalidElementId:
                            tn_type = doc.GetElement(default_id)
                    except Exception:
                        pass
                if tn_type is None:
                    tn_type = tn_types[0]

                t = Transaction(doc, 'T3Lab AI Create Text Note')
                try:
                    t.Start()
                    opts = TextNoteOptions(tn_type.Id)
                    note = TextNote.Create(doc, active_view.Id, XYZ(x, y, 0), text, opts)
                    t.Commit()
                    return {'success': True, 'text_note_id': eid_value(note.Id),
                            'text': text, 'type_used': tn_type.Name,
                            'view': active_view.Name}
                except Exception as e:
                    # RollBack itself throws when the transaction never
                    # started or already auto-rolled-back — guard it so the
                    # ORIGINAL error reaches the model instead of "The
                    # transaction has not been started yet".
                    try:
                        if t.HasStarted() and not t.HasEnded():
                            t.RollBack()
                    except Exception:
                        pass
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── get_model_warnings ───────────────────────────────────────────────
        elif tool_name == 'get_model_warnings':
            try:
                limit = int(arguments.get('limit', 50))
                warnings = doc.GetWarnings()
                result = []
                for w in list(warnings)[:limit]:
                    try:
                        failing_ids = [eid_value(i) for i in w.GetFailingElements()]
                        result.append({
                            'description': w.GetDescriptionText(),
                            'failing_element_ids': failing_ids
                        })
                    except Exception:
                        pass
                return {'warnings': result, 'total': len(list(warnings)), 'returned': len(result)}
            except Exception as e:
                return {'error': str(e)}

        # ── get_model_health ─────────────────────────────────────────────────
        elif tool_name == 'get_model_health':
            try:
                from Autodesk.Revit.DB import (FilteredElementCollector,
                                               RevitLinkInstance, Family)
                warning_count = len(list(doc.GetWarnings()))
                elem_count = FilteredElementCollector(doc).WhereElementIsNotElementType().GetElementCount()
                link_count = FilteredElementCollector(doc).OfClass(RevitLinkInstance).GetElementCount()
                family_count = FilteredElementCollector(doc).OfClass(Family).GetElementCount()
                return {
                    'warning_count': warning_count,
                    'element_count': elem_count,
                    'linked_files': link_count,
                    'loaded_families': family_count
                }
            except Exception as e:
                return {'error': str(e)}

        # ── list_worksets ────────────────────────────────────────────────────
        elif tool_name == 'list_worksets':
            try:
                from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
                if not doc.IsWorkshared:
                    return {'workshared': False, 'worksets': []}
                worksets = list(FilteredWorksetCollector(doc)
                                .OfKind(WorksetKind.UserWorkset).ToWorksets())
                needle = (arguments.get('name_contains') or '').strip().lower()
                result = []
                for ws in worksets:
                    if needle and needle not in (ws.Name or '').lower():
                        continue
                    result.append({
                        'id': eid_value(ws.Id),
                        'name': ws.Name,
                        'is_open': ws.IsOpen,
                        'owner': ws.Owner or ''
                    })
                out = {'workshared': True, 'worksets': result,
                       'count': len(result), 'total_count': len(worksets)}
                if needle:
                    out['name_contains'] = arguments.get('name_contains')
                    if not result:
                        out['hint'] = ('No workset name contains that text. '
                                       'Call again without name_contains to see '
                                       'all {} worksets.'.format(len(worksets)))
                return out
            except Exception as e:
                return {'error': str(e)}

        # ── set_element_workset ──────────────────────────────────────────────
        elif tool_name == 'set_element_workset':
            try:
                from Autodesk.Revit.DB import (Transaction, FilteredWorksetCollector,
                                               WorksetKind, WorksetId)
                if not doc.IsWorkshared:
                    return {'error': 'Document is not workshared'}
                ws_name  = arguments.get('workset_name', '')
                ids_raw  = arguments.get('element_ids', [])
                cat_arg  = arguments.get('category')
                # Whole-category path — project-wide (worksets are model-wide,
                # not per-view): "chuyển tất cả tường sang workset X" in one
                # call, no id ferrying, no count cap.
                if not ids_raw and cat_arg:
                    bic, cat_arg, _cat_err = self._resolve_bic(cat_arg)
                    if _cat_err:
                        return _cat_err
                    ids_raw = [eid_value(e.Id) for e in
                               FilteredElementCollector(doc).OfCategory(bic)
                               .WhereElementIsNotElementType()]
                    if not ids_raw:
                        return {'error': 'No {} elements found in the '
                                         'model.'.format(cat_arg)}
                # Find target workset
                worksets = FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset).ToWorksets()
                target_ws = None
                for ws in worksets:
                    if ws.Name == ws_name:
                        target_ws = ws
                        break
                if not target_ws:
                    return {'error': 'Workset not found: {}'.format(ws_name)}
                t = Transaction(doc, 'T3Lab AI Set Workset')
                t.Start()
                try:
                    count = 0
                    for raw_id in ids_raw:
                        elem = doc.GetElement(make_eid(int(raw_id)))
                        if elem:
                            ws_param = elem.get_Parameter(
                                __import__('Autodesk.Revit.DB', fromlist=['BuiltInParameter']).BuiltInParameter.ELEM_PARTITION_PARAM
                            )
                            if ws_param and not ws_param.IsReadOnly:
                                ws_param.Set(eid_value(target_ws.Id))
                                count += 1
                    t.Commit()
                    return {'success': True, 'moved_count': count, 'workset': ws_name}
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── list_levels ──────────────────────────────────────────────────────
        elif tool_name == 'list_levels':
            try:
                from Autodesk.Revit.DB import Level
                levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
                ft_to_m = 0.3048
                result = sorted(
                    [{'id': eid_value(l.Id), 'name': l.Name, 'elevation_m': round(l.Elevation * ft_to_m, 4)} for l in levels],
                    key=lambda x: x['elevation_m']
                )
                return {'levels': result, 'count': len(result)}
            except Exception as e:
                return {'error': str(e)}

        # ── load_family ──────────────────────────────────────────────────────
        elif tool_name == 'load_family':
            try:
                from Autodesk.Revit.DB import Transaction
                import os as _os
                file_path = arguments.get('file_path', '')
                if not _os.path.isfile(file_path):
                    return {'error': 'File not found: {}'.format(file_path)}
                t = Transaction(doc, 'T3Lab AI Load Family')
                t.Start()
                try:
                    success = doc.LoadFamily(file_path)
                    t.Commit()
                    result = {'success': bool(success), 'file_path': file_path}
                    if not success:
                        result['note'] = ('LoadFamily returned False — the family is probably '
                                          'already loaded in this project (Revit does not '
                                          'overwrite without IFamilyLoadOptions).')
                    return result
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e)}
            except Exception as e:
                return {'error': str(e)}

        # ── file_watcher_status ──────────────────────────────────────────────
        elif tool_name == 'file_watcher_status':
            try:
                from core.file_watcher import get_task_watcher
                watcher = get_task_watcher()
                return watcher.get_status()
            except Exception as e:
                return {'error': str(e), 'running': False}

        # ── get_revit_context ────────────────────────────────────────────────
        elif tool_name == 'get_revit_context':
            try:
                ft_to_m = 0.3048
                ctx = {
                    'document': doc.Title,
                    'is_workshared': doc.IsWorkshared,
                    'file_path': doc.PathName or '(unsaved)',
                }
                view = doc.ActiveView
                if view:
                    ctx['active_view'] = {
                        'name':      view.Name,
                        'type':      str(view.ViewType),
                        'id':        eid_value(view.Id),
                    }
                    try:
                        bb = view.get_BoundingBox(view)
                        if bb:
                            ctx['active_view']['view_range'] = {
                                'min': {'x': round(bb.Min.X * ft_to_m, 2),
                                        'y': round(bb.Min.Y * ft_to_m, 2)},
                                'max': {'x': round(bb.Max.X * ft_to_m, 2),
                                        'y': round(bb.Max.Y * ft_to_m, 2)},
                            }
                    except Exception:
                        pass
                try:
                    sel = uidoc.Selection.GetElementIds()
                    if sel and sel.Count > 0:
                        sel_info = []
                        for sid in list(sel)[:10]:
                            el = doc.GetElement(sid)
                            if el:
                                sel_info.append({
                                    'id':       eid_value(el.Id),
                                    'category': el.Category.Name if el.Category else '?',
                                    'name':     el.Name or '',
                                })
                        ctx['selection'] = {'count': sel.Count, 'elements': sel_info}
                    else:
                        ctx['selection'] = {'count': 0, 'elements': []}
                except Exception:
                    ctx['selection'] = {'count': 0, 'elements': []}
                return ctx
            except Exception as e:
                return {'error': str(e)}

        # ── list_open_documents ──────────────────────────────────────────────
        elif tool_name == 'list_open_documents':
            try:
                docs = self.get_open_documents()
                return {
                    'documents': docs,
                    'count': len(docs),
                    'note': ('Tool calls target the ACTIVE document. Use '
                             'switch_active_document to activate another one, or '
                             'open_document / list_recent_documents to open a '
                             'project from disk.'),
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── switch_active_document ───────────────────────────────────────────
        elif tool_name == 'switch_active_document':
            try:
                query = (arguments.get('path_or_title') or '').strip()
                if not query:
                    return {'error': 'path_or_title is required.'}

                uiapp = self._get_uiapp(uiapp)
                open_docs = [d for d in uiapp.Application.Documents if not d.IsLinked]

                def _norm(p):
                    return os.path.normcase(os.path.normpath(p)) if p else ''

                q_lower = query.lower()
                q_path  = _norm(query)

                # Match precedence: exact title → exact file path → file name
                # (with or without .rvt) → unique title substring.
                target = None
                for d in open_docs:
                    if d.Title.lower() == q_lower:
                        target = d
                        break
                if target is None:
                    for d in open_docs:
                        if d.PathName and _norm(d.PathName) == q_path:
                            target = d
                            break
                if target is None:
                    for d in open_docs:
                        base = os.path.basename(d.PathName) if d.PathName else ''
                        if base and (base.lower() == q_lower or
                                     os.path.splitext(base)[0].lower() == q_lower):
                            target = d
                            break
                if target is None:
                    partial = [d for d in open_docs if q_lower in d.Title.lower()]
                    if len(partial) == 1:
                        target = partial[0]
                    elif len(partial) > 1:
                        return {'error': 'Ambiguous document "{}" — several open documents match.'.format(query),
                                'candidates': [d.Title for d in partial]}

                if target is None:
                    # Not open in this Revit instance — if the query is a real
                    # file on disk, open it here instead of failing.
                    if os.path.isfile(query):
                        try:
                            new_uidoc = uiapp.OpenAndActivateDocument(query)
                            new_doc = new_uidoc.Document
                            return {
                                'success': True,
                                'document': new_doc.Title,
                                'path': new_doc.PathName or query,
                                'opened_from_disk': True,
                                'window_activated': True,
                                'note': ('Opened "{}" from disk and activated it; all tool '
                                         'calls now target it.').format(new_doc.Title),
                            }
                        except Exception as e:
                            return {'error': 'Failed to open "{}" from disk: {}'.format(query, str(e)),
                                    'tool': tool_name}
                    return {'error': ('No open document matches "{}" and it is not an existing '
                                      'file path. Pass the title of an open document, or a full '
                                      '.rvt path to open the file from disk.').format(query),
                            'open_documents': [d.Title for d in open_docs]}

                # Already the active document — nothing to switch.
                try:
                    active_doc = uiapp.ActiveUIDocument.Document
                except Exception:
                    active_doc = None
                if active_doc is not None and active_doc.Title == target.Title:
                    return {
                        'success': True,
                        'document': target.Title,
                        'path': target.PathName or '(unsaved)',
                        'window_activated': True,
                        'note': '"{}" is already the active document.'.format(target.Title),
                    }

                # Activate the document's window — that is what makes it the
                # target of every tool call (no hidden redirection: what's on
                # screen is what gets edited). OpenAndActivateDocument takes a
                # file path, so only saved documents can be activated.
                path = target.PathName
                if not path:
                    return {'error': ('"{}" has never been saved, and Revit\'s API can only '
                                      'activate documents by file path. Save it, or click its '
                                      'tab in Revit to activate it manually.').format(target.Title),
                            'open_documents': [d.Title for d in open_docs]}
                uiapp.OpenAndActivateDocument(path)
                return {
                    'success': True,
                    'document': target.Title,
                    'path': path,
                    'window_activated': True,
                    'note': ('"{}" is now the active document — all tool calls target it. '
                             'Call switch_active_document again to retarget.').format(target.Title),
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── open_document ────────────────────────────────────────────────────
        elif tool_name == 'open_document':
            try:
                path = (arguments.get('path') or '').strip()
                if not path:
                    return {'error': 'path is required.'}
                if not os.path.isfile(path):
                    return {'error': ('File not found: "{}". Pass a full path to an existing '
                                      '.rvt / .rfa file — list_recent_documents shows recent '
                                      'project paths.').format(path)}

                uiapp = self._get_uiapp(uiapp)

                def _norm(p):
                    return os.path.normcase(os.path.normpath(p)) if p else ''

                # Already open in this instance? Just activate its window.
                already = None
                for d in uiapp.Application.Documents:
                    if not d.IsLinked and d.PathName and _norm(d.PathName) == _norm(path):
                        already = d
                        break

                new_uidoc = uiapp.OpenAndActivateDocument(path)
                new_doc = new_uidoc.Document
                return {
                    'success': True,
                    'document': new_doc.Title,
                    'path': new_doc.PathName or path,
                    'already_open': already is not None,
                    'note': ('"{}" is now the active document — all tool calls '
                             'target it.').format(new_doc.Title),
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── close_document ───────────────────────────────────────────────────
        elif tool_name == 'close_document':
            try:
                query = (arguments.get('path_or_title') or '').strip()
                save = bool(arguments.get('save', False))
                if not query:
                    return {'error': 'path_or_title is required.'}

                uiapp = self._get_uiapp(uiapp)
                open_docs = [d for d in uiapp.Application.Documents if not d.IsLinked]

                def _norm(p):
                    return os.path.normcase(os.path.normpath(p)) if p else ''

                q_lower = query.lower()
                q_path = _norm(query)

                # Same match precedence as switch_active_document.
                target = None
                for d in open_docs:
                    if d.Title.lower() == q_lower:
                        target = d
                        break
                if target is None:
                    for d in open_docs:
                        if d.PathName and _norm(d.PathName) == q_path:
                            target = d
                            break
                if target is None:
                    for d in open_docs:
                        base = os.path.basename(d.PathName) if d.PathName else ''
                        if base and (base.lower() == q_lower or
                                     os.path.splitext(base)[0].lower() == q_lower):
                            target = d
                            break
                if target is None:
                    partial = [d for d in open_docs if q_lower in d.Title.lower()]
                    if len(partial) == 1:
                        target = partial[0]
                    elif len(partial) > 1:
                        return {'error': 'Ambiguous document "{}" — several open documents match.'.format(query),
                                'candidates': [d.Title for d in partial]}
                if target is None:
                    return {'error': 'No open document matches "{}".'.format(query),
                            'open_documents': [d.Title for d in open_docs]}

                # Revit's API refuses to close the ACTIVE document — activate
                # another open (saved) document first, then close the target.
                try:
                    active_doc = uiapp.ActiveUIDocument.Document
                except Exception:
                    active_doc = None
                if active_doc is not None and active_doc.Title == target.Title:
                    others = [d for d in open_docs
                              if d.Title != target.Title and d.PathName]
                    if not others:
                        return {'error': ('"{}" is the active document and the only switchable '
                                          'document open — Revit\'s API cannot close the active '
                                          'document. Open another project first (open_document), '
                                          'or close it manually in Revit.').format(target.Title),
                                'open_documents': [d.Title for d in open_docs]}
                    uiapp.OpenAndActivateDocument(others[0].PathName)

                title = target.Title
                target.Close(save)
                return {
                    'success': True,
                    'closed': title,
                    'saved': save,
                    'open_documents': [d.Title for d in uiapp.Application.Documents
                                       if not d.IsLinked],
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── list_recent_documents ────────────────────────────────────────────
        elif tool_name == 'list_recent_documents':
            try:
                from pyrevit import HOST_APP
                ini_path = os.path.join(
                    os.environ.get('APPDATA', ''), 'Autodesk', 'Revit',
                    'Autodesk Revit {}'.format(HOST_APP.version), 'Revit.ini')
                if not os.path.isfile(ini_path):
                    return {'error': 'Revit.ini not found: {}'.format(ini_path),
                            'recent_documents': []}

                # Revit.ini is usually UTF-16 LE; older installs use ANSI.
                import codecs
                content = None
                for enc in ('utf-16', 'utf-8', 'latin-1'):
                    try:
                        with codecs.open(ini_path, 'r', encoding=enc) as f:
                            content = f.read()
                        break
                    except Exception:
                        continue
                if content is None:
                    return {'error': 'Could not read Revit.ini', 'recent_documents': []}

                # [Recent File List] section: File1=path ... FileN=path
                recent = []
                in_section = False
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith('['):
                        in_section = line.lower() == '[recent file list]'
                        continue
                    if in_section and line.lower().startswith('file') and '=' in line:
                        path = line.split('=', 1)[1].strip()
                        if path:
                            recent.append({
                                'path': path,
                                'name': os.path.basename(path),
                                'exists': os.path.isfile(path),
                            })

                return {
                    'recent_documents': recent,
                    'count': len(recent),
                    'note': ('Call open_document with one of these paths to open and '
                             'activate the project (exists=false means the file moved '
                             'or is on an unmounted drive).'),
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── show_assistant_pane ──────────────────────────────────────────────
        elif tool_name == 'show_assistant_pane':
            try:
                from System import Guid as SysGuid
                from Autodesk.Revit.UI import DockablePaneId
                action  = (arguments.get('action') or 'show').lower()
                if action not in ('show', 'hide'):
                    return {'error': "Unknown action '{}'.".format(action),
                            'supported_actions': ['show', 'hide'],
                            'hint': 'Retry with one of supported_actions.'}
                message = arguments.get('message', '')
                pane_guid = SysGuid('7F3A9B2E-C4D1-4E8F-A6B5-1234567890AB')
                pane_id   = DockablePaneId(pane_guid)
                uiapp     = self._get_uiapp(uiapp)
                pane      = uiapp.GetDockablePane(pane_id)
                if pane is None:
                    return {'error': 'DockablePane not registered. Restart Revit after installing T3Lab.'}
                if action == 'hide':
                    pane.Hide()
                    return {'success': True, 'action': 'hide'}
                else:
                    pane.Show()
                    result = {'success': True, 'action': 'show'}
                # The pane hosts the full assistant window, which has no
                # message-injection channel. Always report this truthfully:
                # the key used to be ABSENT on the failure path, so the model
                # saw {'success': True} and believed it had delivered text it
                # actually dropped.
                if message:
                    result['message_injected'] = False
                    result['note'] = (
                        'The pane is showing but has no message channel — the '
                        'text was NOT delivered. Say it in your chat reply '
                        'instead.')
                return result
            except Exception as e:
                return {'error': str(e)}

        # ── export_sheets_pdf ────────────────────────────────────────────────
        elif tool_name == 'export_sheets_pdf':
            try:
                from Autodesk.Revit.DB import (FilteredElementCollector, ViewSheet,
                                               PDFExportOptions, ViewSet,
                                               ExportPaperFormat, RasterQualityType,
                                               ExportColorType)
                import os as _os
                sheet_ids_raw  = arguments.get('sheet_ids', [])
                output_folder  = arguments.get('output_folder', '')
                combined       = bool(arguments.get('combined', False))

                if not output_folder:
                    doc_path = doc.PathName
                    output_folder = _os.path.dirname(doc_path) if doc_path else _os.path.expanduser('~')

                if not _os.path.isdir(output_folder):
                    try:
                        _os.makedirs(output_folder)
                    except Exception:
                        return {'error': 'Cannot create output folder: {}'.format(output_folder)}

                # Collect sheets
                all_sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
                if sheet_ids_raw:
                    export_ids = [int(i) for i in sheet_ids_raw]
                    sheets = [s for s in all_sheets if eid_value(s.Id) in export_ids]
                else:
                    sheets = list(all_sheets)

                if not sheets:
                    return {'error': 'No sheets to export'}

                opts = PDFExportOptions()
                opts.Combine = combined

                view_set = ViewSet()
                for s in sheets:
                    view_set.Insert(s)

                doc.Export(output_folder, 'T3Lab_Export', view_set, opts)
                return {'success': True, 'sheet_count': len(sheets), 'output_folder': output_folder, 'combined': combined}
            except Exception as e:
                return {'error': str(e)}

        # ── split_curve ──────────────────────────────────────────────────────
        elif tool_name == 'split_curve':
            from Autodesk.Revit.DB import (ElementId, Transaction, CurveElement,
                                           ModelCurve, DetailCurve)
            try:
                raw_id = arguments.get('element_id')
                if raw_id is None:
                    return {'error': 'element_id is required'}

                el = doc.GetElement(make_eid(int(raw_id)))
                if el is None:
                    return {'error': 'No element with id {}'.format(raw_id)}
                if not isinstance(el, CurveElement):
                    return {'error': ('Element {} is a {}, not a curve element. Only model curves '
                                      'and detail curves (line / arc / spline) can be split.'
                                      ).format(raw_id, el.GetType().Name)}

                is_model  = isinstance(el, ModelCurve)
                is_detail = isinstance(el, DetailCurve)
                if not (is_model or is_detail):
                    return {'error': ('Unsupported curve element type {} — only model curves and '
                                      'detail curves can be split.').format(el.GetType().Name)}

                curve = el.GeometryCurve
                if curve is None:
                    return {'error': 'Element has no geometry curve.'}
                if not curve.IsBound:
                    return {'error': ('Curve is periodic / unbound (e.g. a full circle or ellipse) '
                                      '— it has no endpoints to split at.')}

                # Build the list of normalized cut fractions (0..1 inclusive).
                ratios = arguments.get('split_at_ratios')
                if ratios:
                    interior = sorted(set(round(float(r), 9) for r in ratios if 0.0 < float(r) < 1.0))
                    if not interior:
                        return {'error': 'split_at_ratios must contain at least one value strictly between 0 and 1.'}
                    fracs = [0.0] + interior + [1.0]
                else:
                    n = int(arguments.get('segments', 2))
                    if n < 2:
                        return {'error': 'segments must be >= 2 (or provide split_at_ratios).'}
                    if n > 200:
                        return {'error': 'segments too large (max 200).'}
                    fracs = [float(i) / n for i in range(n + 1)]

                # Map the normalized fractions to RAW curve parameters (Revit maps
                # [0,1] linearly onto [GetEndParameter(0), GetEndParameter(1)]), then
                # slice the ORIGINAL curve with Clone()+MakeBound. This reuses the
                # source geometry sub-range verbatim, so an arc stays an arc and a
                # spline stays a spline — unlike rebuilding endpoints with
                # Line.CreateBound, which flattens every curve into a straight line.
                rp0 = curve.GetEndParameter(0)
                rp1 = curve.GetEndParameter(1)
                sub_curves = []
                for i in range(len(fracs) - 1):
                    a = rp0 + (rp1 - rp0) * fracs[i]
                    b = rp0 + (rp1 - rp0) * fracs[i + 1]
                    lo, hi = (a, b) if a < b else (b, a)
                    seg = curve.Clone()
                    seg.MakeBound(lo, hi)
                    sub_curves.append(seg)

                curve_kind = type(curve).__name__

                # Host context needed to recreate sibling curve elements.
                sketch_plane = el.SketchPlane if is_model else None
                owner_view = None
                if is_detail:
                    try:
                        owner_view = doc.GetElement(el.OwnerViewId)
                    except Exception:
                        owner_view = None
                try:
                    orig_style = el.LineStyle
                except Exception:
                    orig_style = None

                new_ids = []
                t = Transaction(doc, 'T3Lab AI Split Curve')
                t.Start()
                try:
                    # Reshape the original element onto the first segment, then add
                    # one new sibling per remaining segment (same style / host).
                    el.GeometryCurve = sub_curves[0]
                    for seg in sub_curves[1:]:
                        if is_model:
                            new_el = doc.Create.NewModelCurve(seg, sketch_plane)
                        else:
                            if owner_view is None:
                                t.RollBack()
                                return {'error': 'Cannot resolve owner view for the detail curve.'}
                            new_el = doc.Create.NewDetailCurve(owner_view, seg)
                        if orig_style is not None:
                            try:
                                new_el.LineStyle = orig_style
                            except Exception:
                                pass
                        new_ids.append(eid_value(new_el.Id))
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}

                return {
                    'success': True,
                    'curve_type': curve_kind,
                    'geometry_preserved': True,
                    'segment_count': len(sub_curves),
                    'original_element_id': eid_value(el.Id),
                    'new_element_ids': new_ids,
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── split_element ────────────────────────────────────────────────────
        elif tool_name == 'split_element':
            from Autodesk.Revit.DB import (ElementId, Transaction, XYZ,
                                           LocationCurve, ElementTransformUtils)
            try:
                raw_id = arguments.get('element_id')
                if raw_id is None:
                    return {'error': 'element_id is required'}
                el = doc.GetElement(make_eid(int(raw_id)))
                if el is None:
                    return {'error': 'No element with id {}'.format(raw_id)}
                loc = el.Location
                if not isinstance(loc, LocationCurve):
                    return {'error': ('Element {} has no location curve (type {}). Only '
                                      'location-curve elements (wall, beam, pipe, duct, line) '
                                      'can be split; use split_curve for model/detail curves.'
                                      ).format(raw_id, el.GetType().Name)}
                curve = loc.Curve
                if not curve.IsBound:
                    return {'error': 'Location curve is unbound — cannot split.'}
                rp0 = curve.GetEndParameter(0)
                rp1 = curve.GetEndParameter(1)

                # Determine the raw split parameter from an XY point or a ratio.
                if arguments.get('x') is not None and arguments.get('y') is not None:
                    M2FT = 3.28084
                    pt = XYZ(float(arguments['x']) * M2FT, float(arguments['y']) * M2FT, 0)
                    try:
                        proj = curve.Project(pt)
                        split_raw = proj.Parameter
                    except Exception:
                        return {'error': 'Could not project the given point onto the curve.'}
                else:
                    ratio = float(arguments.get('at_ratio', 0.5))
                    if ratio <= 0.0 or ratio >= 1.0:
                        return {'error': 'at_ratio must be strictly between 0 and 1.'}
                    split_raw = rp0 + (rp1 - rp0) * ratio

                lo, hi = (rp0, rp1) if rp0 < rp1 else (rp1, rp0)
                if not (lo < split_raw < hi):
                    return {'error': 'Split position lies outside the curve span.'}

                first = curve.Clone();  first.MakeBound(min(rp0, split_raw), max(rp0, split_raw))
                second = curve.Clone(); second.MakeBound(min(split_raw, rp1), max(split_raw, rp1))

                t = Transaction(doc, 'T3Lab AI Split Element')
                t.Start()
                try:
                    copied = ElementTransformUtils.CopyElement(doc, el.Id, XYZ(0, 0, 0))
                    new_id = list(copied)[0] if copied else None
                    loc.Curve = first
                    if new_id is not None:
                        new_el = doc.GetElement(new_id)
                        new_el.Location.Curve = second
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {
                    'success': True,
                    'original_element_id': eid_value(el.Id),
                    'new_element_id': eid_value(new_id) if new_id is not None else None,
                    'category': el.Category.Name if el.Category else '',
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── join_geometry ────────────────────────────────────────────────────
        elif tool_name == 'join_geometry':
            from Autodesk.Revit.DB import ElementId, Transaction, JoinGeometryUtils
            try:
                a = doc.GetElement(make_eid(int(arguments.get('element_id_a', 0))))
                b = doc.GetElement(make_eid(int(arguments.get('element_id_b', 0))))
                if a is None or b is None:
                    return {'error': 'Both element_id_a and element_id_b must resolve to elements.'}
                unjoin = bool(arguments.get('unjoin', False))
                t = Transaction(doc, 'T3Lab AI Join Geometry')
                t.Start()
                try:
                    joined = JoinGeometryUtils.AreElementsJoined(doc, a, b)
                    if unjoin:
                        if joined:
                            JoinGeometryUtils.UnjoinGeometry(doc, a, b)
                        action = 'unjoined'
                    else:
                        if not joined:
                            JoinGeometryUtils.JoinGeometry(doc, a, b)
                        action = 'joined'
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'action': action,
                        'element_id_a': eid_value(a.Id), 'element_id_b': eid_value(b.Id)}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── bulk_set_parameter ───────────────────────────────────────────────
        elif tool_name == 'bulk_set_parameter':
            from Autodesk.Revit.DB import Transaction, ElementId
            try:
                pname  = arguments.get('parameter_name')
                value  = arguments.get('value')
                if not pname or value is None:
                    return {'error': 'parameter_name and value are required.'}
                value  = str(value)
                fparam = arguments.get('filter_parameter')
                fval   = (arguments.get('filter_value') or '').lower()
                # No cap by default — "set X on ALL walls" must reach every
                # wall (the old default of 500 silently stopped there on big
                # models). An explicit positive limit is still honored.
                limit  = int(arguments.get('limit', 0) or 0)
                ids    = arguments.get('element_ids')

                if ids:
                    elements = [doc.GetElement(make_eid(int(i))) for i in ids]
                    elements = [e for e in elements if e is not None]
                else:
                    cat = arguments.get('category')
                    bic = None
                    if cat:
                        bic, cat, _cat_err = self._resolve_bic(cat)
                        if _cat_err:
                            return _cat_err
                    coll = FilteredElementCollector(doc).WhereElementIsNotElementType()
                    if bic is not None:
                        coll = coll.OfCategory(bic)
                    elements = list(coll)

                modified, skipped, errors = 0, 0, 0
                t = Transaction(doc, 'T3Lab AI Bulk Set Parameter')
                t.Start()
                try:
                    for elem in elements:
                        if limit > 0 and modified >= limit:
                            break
                        try:
                            if fparam:
                                fp = elem.LookupParameter(fparam)
                                pv = ''
                                if fp:
                                    pv = (fp.AsValueString() or fp.AsString() or '')
                                if fval and fval not in pv.lower():
                                    continue
                            p = elem.LookupParameter(pname)
                            ok, _err = self._apply_param_value(p, value)
                            if ok:
                                modified += 1
                            elif p is None:
                                skipped += 1
                            else:
                                errors += 1
                        except Exception:
                            errors += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'parameter': pname, 'value': value,
                        'modified': modified, 'skipped_no_param': skipped, 'errors': errors}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── select_elements ──────────────────────────────────────────────────
        elif tool_name == 'select_elements':
            from Autodesk.Revit.DB import ElementId
            from System.Collections.Generic import List as NetList
            try:
                # No cap by default — "select ALL walls" must select every
                # wall (the old default of 500 silently stopped there). An
                # explicit positive limit is still honored.
                limit = int(arguments.get('limit', 0) or 0)
                ids   = arguments.get('element_ids')

                # An EMPTY list is "select nothing", never "select everything".
                # `if ids:` treated [] the same as an omitted argument, so the
                # branch below collected the whole model — select_elements([])
                # put 37,929 elements into the selection, and any following
                # colour/hide call that falls back to the selection would then
                # have hit every one of them. Omitting the argument entirely
                # still means "use category / whole model".
                if isinstance(ids, (list, tuple)) and len(ids) == 0 \
                        and not arguments.get('category'):
                    uidoc.Selection.SetElementIds(NetList[ElementId]())
                    return {'success': True, 'selected_count': 0,
                            'note': 'Selection cleared (element_ids was empty).'}

                if ids:
                    target = [make_eid(int(i)) for i in ids]
                    if limit > 0:
                        target = target[:limit]
                else:
                    cat = arguments.get('category')
                    bic = None
                    if cat:
                        bic, cat, _cat_err = self._resolve_bic(cat)
                        if _cat_err:
                            return _cat_err
                    coll = FilteredElementCollector(doc).WhereElementIsNotElementType()
                    if bic is not None:
                        coll = coll.OfCategory(bic)
                    pname = arguments.get('parameter_name')
                    pval  = (arguments.get('parameter_value') or '').lower()
                    target = []
                    for elem in coll:
                        if limit > 0 and len(target) >= limit:
                            break
                        if pname:
                            p = elem.LookupParameter(pname)
                            if not p:
                                continue
                            v = (p.AsValueString() or p.AsString() or '')
                            if pval and pval not in v.lower():
                                continue
                        target.append(elem.Id)

                if bool(arguments.get('add_to_selection', False)):
                    current = list(uidoc.Selection.GetElementIds())
                    seen = set(eid_value(i) for i in current)
                    for i in target:
                        if eid_value(i) not in seen:
                            current.append(i)
                    target = current
                net = NetList[ElementId]()
                for i in target:
                    net.Add(i)
                uidoc.Selection.SetElementIds(net)
                shown = None
                if bool(arguments.get('show', False)) and net.Count:
                    shown = self._show_elements_smart(uidoc, doc, target)
                out = {'success': True, 'selected_count': net.Count}
                if shown:
                    out.update(shown)
                return out
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── tag_elements ─────────────────────────────────────────────────────
        elif tool_name == 'tag_elements':
            from Autodesk.Revit.DB import (Transaction, IndependentTag, TagMode,
                                           TagOrientation, Reference, LocationCurve,
                                           LocationPoint)
            try:
                cat = arguments.get('category')
                bic, cat, _cat_err = self._resolve_bic(cat)
                if _cat_err:
                    return _cat_err
                leader = bool(arguments.get('leader', False))
                view = doc.ActiveView
                elems = FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType().ToElements()
                t = Transaction(doc, 'T3Lab AI Tag Elements')
                t.Start()
                tagged, failed = 0, 0
                try:
                    for elem in elems:
                        try:
                            loc = elem.Location
                            if isinstance(loc, LocationPoint):
                                pt = loc.Point
                            elif isinstance(loc, LocationCurve):
                                pt = loc.Curve.Evaluate(0.5, True)
                            else:
                                failed += 1
                                continue
                            IndependentTag.Create(doc, view.Id, Reference(elem), leader,
                                                  TagMode.TM_ADDBY_CATEGORY,
                                                  TagOrientation.Horizontal, pt)
                            tagged += 1
                        except Exception:
                            failed += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'category': cat, 'tagged_count': tagged, 'failed': failed}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── create_dimension ─────────────────────────────────────────────────
        elif tool_name == 'create_dimension':
            from Autodesk.Revit.DB import (Transaction, ElementId, Grid, Reference,
                                           ReferenceArray, Line, XYZ)
            try:
                ids = arguments.get('element_ids', [])
                if len(ids) < 2:
                    return {'error': 'element_ids must contain at least 2 grids/line elements.'}
                M2FT = 3.28084
                offset = float(arguments.get('offset', 1.0)) * M2FT
                view = doc.ActiveView

                grids = []
                for i in ids:
                    e = doc.GetElement(make_eid(int(i)))
                    if isinstance(e, Grid):
                        grids.append(e)
                if len(grids) < 2:
                    return {'error': 'Need at least 2 grids. Only grids are supported by create_dimension.'}

                refs = ReferenceArray()
                pts = []
                for g in grids:
                    refs.Append(Reference(g))
                    pts.append(g.Curve.GetEndPoint(0))

                c0 = grids[0].Curve
                gdir = (c0.GetEndPoint(1) - c0.GetEndPoint(0)).Normalize()
                perp = XYZ(-gdir.Y, gdir.X, 0)
                origin = pts[0] + gdir.Multiply(offset)
                vals = [(p - origin).DotProduct(perp) for p in pts]
                lo, hi = min(vals), max(vals)
                if abs(hi - lo) < 1e-6:
                    return {'error': 'Selected grids are coincident — nothing to dimension.'}
                start = origin + perp.Multiply(lo)
                end   = origin + perp.Multiply(hi)
                dim_line = Line.CreateBound(start, end)

                t = Transaction(doc, 'T3Lab AI Create Dimension')
                t.Start()
                try:
                    dim = doc.Create.NewDimension(view, dim_line, refs)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'dimension_id': eid_value(dim.Id), 'grid_count': len(grids)}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── get_schedule_data ────────────────────────────────────────────────
        elif tool_name == 'get_schedule_data':
            from Autodesk.Revit.DB import ViewSchedule, ElementId, SectionType
            try:
                sched = None
                sid = arguments.get('schedule_id')
                sname = arguments.get('schedule_name')
                # Frequent model mistake: calling this tool with `category`
                # (confusing it with create_schedule / get_material_quantities).
                # This tool only READS an EXISTING ViewSchedule by name/id and
                # has no `category` arg. Silently falling through to all_sched[0]
                # returns an unrelated schedule that poisons the next turn (the
                # model then emits garbage → "Could not read data" fallback).
                # Turn the dead-end into a self-correcting redirect instead.
                if sid is None and not sname and arguments.get('category'):
                    _cat = u'{}'.format(arguments.get('category'))
                    return {
                        'error': "get_schedule_data reads an EXISTING schedule "
                                 "by schedule_name/schedule_id and has no "
                                 "'category' argument.",
                        'hint': u"For a quantity takeoff of '{c}', call "
                                u"get_material_quantities(category='{c}') for "
                                u"areas/volumes, or analyze_model_statistics for "
                                u"counts. To read '{c}' as a table, first "
                                u"create_schedule(category='{c}'), then call "
                                u"get_schedule_data(schedule_name=...).".format(
                                    c=_cat)
                    }
                if sid is not None:
                    cand = doc.GetElement(make_eid(int(sid)))
                    if isinstance(cand, ViewSchedule):
                        sched = cand
                if sched is None:
                    all_sched = [s for s in FilteredElementCollector(doc).OfClass(ViewSchedule)
                                 if not s.IsTemplate]
                    if sname:
                        for s in all_sched:
                            if s.Name == sname:
                                sched = s; break
                        if sched is None:
                            for s in all_sched:
                                if sname.lower() in s.Name.lower():
                                    sched = s; break
                    elif all_sched:
                        sched = all_sched[0]
                if sched is None:
                    # Dead-end errors teach the model nothing — hand it the
                    # real schedule names so the next call can succeed.
                    try:
                        _names = sorted(s.Name for s in all_sched)[:40]
                    except Exception:
                        _names = []
                    return {'error': 'No matching schedule found.',
                            'available_schedules': _names,
                            'hint': 'Call get_schedule_data again with one of '
                                    'available_schedules (exact name).'}

                defn = sched.Definition
                headers = []
                for i in range(defn.GetFieldCount()):
                    try:
                        headers.append(defn.GetField(i).GetName())
                    except Exception:
                        headers.append('Field{}'.format(i))

                limit = int(arguments.get('limit', 200))
                body = sched.GetTableData().GetSectionData(SectionType.Body)
                n_rows = body.NumberOfRows
                n_cols = body.NumberOfColumns

                # Statistics MUST be computed here over the FULL body — the
                # agent loop truncates big results before the model sees
                # them, and an LLM "counting" a truncated row list produces
                # confident nonsense (observed: 108 windows reported from a
                # cut-off dump). row_count / column_totals below are exact
                # regardless of how many raw rows survive truncation.
                import re as _re_num

                def _cell_num(s):
                    # Whole cell must be "number [unit]" ("108", "12.5 m2",
                    # "95%") — a bare prefix match would happily pull 50 out
                    # of a text cell like '50" x 60"' and poison the totals.
                    try:
                        s2 = (s or u'').strip().replace(u',', u'')
                        m = _re_num.match(
                            u'^(-?\\d+(?:\\.\\d+)?)\\s*'
                            u'[A-Za-z°²³%]{0,4}[23]?$', s2)
                        return float(m.group(1)) if m else None
                    except Exception:
                        return None

                rows = []
                data_rows = 0
                totals_rows = []
                sums = [0.0] * n_cols
                sum_hits = [0] * n_cols
                for r in range(n_rows):
                    cells = []
                    for c in range(n_cols):
                        try:
                            cells.append(sched.GetCellText(SectionType.Body, r, c))
                        except Exception:
                            cells.append('')
                    if len(rows) < limit:
                        rows.append(cells)
                    stripped = [(c or u'').strip() for c in cells]
                    if not any(stripped):
                        continue                      # blank separator row
                    if stripped == [h.strip() for h in headers]:
                        continue                      # repeated header row
                    first = next((c for c in stripped if c), u'')
                    if u'total' in first.lower():
                        totals_rows.append(cells)     # Revit grand/group total
                        continue
                    data_rows += 1
                    for c in range(n_cols):
                        v = _cell_num(stripped[c])
                        if v is not None:
                            sums[c] += v
                            sum_hits[c] += 1
                column_totals = {}
                for c in range(n_cols):
                    if sum_hits[c]:
                        column_totals[headers[c] if c < len(headers)
                                      else 'Field{}'.format(c)] = round(sums[c], 2)

                return {'schedule': sched.Name, 'id': eid_value(sched.Id),
                        'headers': headers,
                        'row_count': data_rows,
                        'rows_returned': len(rows),
                        'rows_truncated': n_rows > len(rows),
                        'column_totals': column_totals,
                        'schedule_totals_rows': totals_rows,
                        'note': ('row_count and column_totals are computed '
                                 'over the FULL schedule - use THESE for any '
                                 'statistic; never count or sum the (possibly '
                                 'truncated) rows yourself.'),
                        'rows': rows}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── create_schedule ──────────────────────────────────────────────────
        elif tool_name == 'create_schedule':
            from Autodesk.Revit.DB import (Transaction, ViewSchedule, ElementId,
                                           SchedulableField)
            try:
                cat = arguments.get('category')
                bic = self._bic_map().get(cat)
                if bic is None:
                    return {'error': 'Unknown category "{}".'.format(cat)}
                fields = arguments.get('fields') or []
                name = arguments.get('name')

                t = Transaction(doc, 'T3Lab AI Create Schedule')
                t.Start()
                try:
                    sched = ViewSchedule.CreateSchedule(doc, ElementId(bic))
                    defn = sched.Definition
                    available = {}
                    for sf in defn.GetSchedulableFields():
                        try:
                            available[sf.GetName(doc)] = sf
                        except Exception:
                            pass
                    added = []
                    if fields:
                        for fname in fields:
                            sf = available.get(fname)
                            if sf is None:
                                for k, v in available.items():
                                    if fname.lower() in k.lower():
                                        sf = v; break
                            if sf is not None:
                                try:
                                    defn.AddField(sf)
                                    added.append(fname)
                                except Exception:
                                    pass
                    else:
                        # No fields specified — add a handful of common ones.
                        for k in ['Family and Type', 'Type', 'Level', 'Count', 'Comments', 'Mark']:
                            sf = available.get(k)
                            if sf is not None:
                                try:
                                    defn.AddField(sf); added.append(k)
                                except Exception:
                                    pass
                    if name:
                        try:
                            sched.Name = name
                        except Exception:
                            pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'schedule_id': eid_value(sched.Id),
                        'name': sched.Name, 'fields_added': added}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── duplicate_view ───────────────────────────────────────────────────
        elif tool_name == 'duplicate_view':
            from Autodesk.Revit.DB import Transaction, ElementId, View, ViewDuplicateOption
            try:
                view = doc.GetElement(make_eid(int(arguments.get('view_id', 0))))
                if not isinstance(view, View):
                    return {'error': 'view_id does not refer to a view.'}
                mode = (arguments.get('mode') or 'plain').lower()
                if mode not in ('plain', 'with_detailing', 'dependent'):
                    return {'error': "Unknown mode '{}'.".format(mode),
                            'supported_modes': ['plain', 'with_detailing',
                                                'dependent'],
                            'hint': 'Retry with one of supported_modes.'}
                opt = ViewDuplicateOption.Duplicate
                if mode == 'with_detailing':
                    opt = ViewDuplicateOption.WithDetailing
                elif mode == 'dependent':
                    opt = ViewDuplicateOption.AsDependent
                if not view.CanViewBeDuplicated(opt):
                    return {'error': 'This view cannot be duplicated with mode "{}".'.format(mode)}
                name = arguments.get('name')
                t = Transaction(doc, 'T3Lab AI Duplicate View')
                t.Start()
                try:
                    new_id = view.Duplicate(opt)
                    if name:
                        try:
                            doc.GetElement(new_id).Name = name
                        except Exception:
                            pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                new_v = doc.GetElement(new_id)
                return {'success': True, 'new_view_id': eid_value(new_id), 'name': new_v.Name}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── apply_view_template ──────────────────────────────────────────────
        elif tool_name == 'apply_view_template':
            from Autodesk.Revit.DB import Transaction, ElementId, View
            try:
                tpl_name = arguments.get('template_name', '')
                view_ids = arguments.get('view_ids', [])
                if not view_ids:
                    return {'error': 'view_ids is required.'}
                templates = [v for v in FilteredElementCollector(doc).OfClass(View) if v.IsTemplate]
                tpl = None
                for v in templates:
                    if v.Name == tpl_name:
                        tpl = v; break
                if tpl is None:
                    for v in templates:
                        if tpl_name.lower() in v.Name.lower():
                            tpl = v; break
                if tpl is None:
                    return {'error': 'View template "{}" not found.'.format(tpl_name)}
                t = Transaction(doc, 'T3Lab AI Apply View Template')
                t.Start()
                applied, failed = 0, 0
                try:
                    for vid in view_ids:
                        try:
                            v = doc.GetElement(make_eid(int(vid)))
                            v.ViewTemplateId = tpl.Id
                            applied += 1
                        except Exception:
                            failed += 1
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'template': tpl.Name, 'applied': applied, 'failed': failed}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── create_view_filter ───────────────────────────────────────────────
        elif tool_name == 'create_view_filter':
            from Autodesk.Revit.DB import (Transaction, ElementId, View,
                                           ParameterFilterElement, ElementParameterFilter,
                                           ParameterFilterRuleFactory, OverrideGraphicSettings,
                                           Color)
            from System.Collections.Generic import List as NetList
            try:
                name = arguments.get('name')
                cats = arguments.get('categories', [])
                cat_ids = NetList[ElementId]()
                for c in cats:
                    bic = self._bic_map().get(c)
                    if bic is not None:
                        cat_ids.Add(ElementId(bic))
                if cat_ids.Count == 0:
                    return {'error': 'No valid categories resolved from {}.'.format(cats)}

                view = doc.GetElement(make_eid(int(arguments['view_id']))) if arguments.get('view_id') else doc.ActiveView

                # Optional single "contains" rule on a parameter.
                elem_filter = None
                pname = arguments.get('parameter_name')
                pval  = arguments.get('parameter_value')
                if pname and pval is not None:
                    # Resolve the parameter id from a sample element in one of the categories.
                    pid = None
                    for c in cats:
                        bic = self._bic_map().get(c)
                        if bic is None:
                            continue
                        for e in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
                            p = e.LookupParameter(pname)
                            if p:
                                pid = p.Id; break
                        if pid:
                            break
                    if pid is not None:
                        rule = None
                        try:
                            rule = ParameterFilterRuleFactory.CreateContainsRule(pid, str(pval))
                        except Exception:
                            try:
                                rule = ParameterFilterRuleFactory.CreateContainsRule(pid, str(pval), False)
                            except Exception:
                                rule = None
                        if rule is not None:
                            elem_filter = ElementParameterFilter(rule)

                t = Transaction(doc, 'T3Lab AI Create View Filter')
                t.Start()
                try:
                    if elem_filter is not None:
                        pfe = ParameterFilterElement.Create(doc, name, cat_ids, elem_filter)
                    else:
                        pfe = ParameterFilterElement.Create(doc, name, cat_ids)
                    view.AddFilter(pfe.Id)
                    if bool(arguments.get('hide', False)):
                        view.SetFilterVisibility(pfe.Id, False)
                    color = arguments.get('color')
                    if color:
                        rgb = self._parse_color(color)
                        if rgb:
                            ogs = OverrideGraphicSettings()
                            col = Color(rgb[0], rgb[1], rgb[2])
                            ogs.SetProjectionLineColor(col)
                            ogs.SetSurfaceForegroundPatternColor(col)
                            view.SetFilterOverrides(pfe.Id, ogs)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'filter_id': eid_value(pfe.Id), 'name': name,
                        'view': view.Name}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── place_views_on_sheets ────────────────────────────────────────────
        elif tool_name == 'place_views_on_sheets':
            from Autodesk.Revit.DB import (Transaction, ElementId, ViewSheet, Viewport,
                                           XYZ, FamilySymbol, BuiltInCategory)
            try:
                view_ids = arguments.get('view_ids', [])
                if not view_ids:
                    return {'error': 'view_ids is required.'}
                tb_name = arguments.get('title_block')
                existing_sheet_id = arguments.get('sheet_id')

                tb = None
                tbs = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).OfClass(FamilySymbol))
                if tb_name:
                    for s in tbs:
                        full = '{}:{}'.format(s.Family.Name, s.Name) if s.Family else s.Name
                        if s.Name == tb_name or full == tb_name or tb_name.lower() in full.lower():
                            tb = s; break
                if tb is None and tbs:
                    tb = tbs[0]

                results = []
                t = Transaction(doc, 'T3Lab AI Place Views On Sheets')
                t.Start()
                try:
                    if existing_sheet_id:
                        sheet = doc.GetElement(make_eid(int(existing_sheet_id)))
                        col, row = 0, 0
                        for vid in view_ids:
                            v_eid = make_eid(int(vid))
                            if Viewport.CanAddViewToSheet(doc, sheet.Id, v_eid):
                                center = XYZ(0.5 + col * 0.9, 0.9 - row * 0.7, 0)
                                vp = Viewport.Create(doc, sheet.Id, v_eid, center)
                                results.append({'sheet_id': eid_value(sheet.Id), 'viewport_id': eid_value(vp.Id)})
                                col += 1
                                if col > 1:
                                    col = 0; row += 1
                    else:
                        if tb is None:
                            t.RollBack()
                            return {'error': 'No title block available to create sheets.'}
                        if not tb.IsActive:
                            tb.Activate(); doc.Regenerate()
                        for vid in view_ids:
                            v_eid = make_eid(int(vid))
                            sheet = ViewSheet.Create(doc, tb.Id)
                            if Viewport.CanAddViewToSheet(doc, sheet.Id, v_eid):
                                vp = Viewport.Create(doc, sheet.Id, v_eid, XYZ(1.0, 0.7, 0))
                                results.append({'sheet_id': eid_value(sheet.Id),
                                                'sheet_number': sheet.SheetNumber,
                                                'viewport_id': eid_value(vp.Id)})
                            else:
                                results.append({'sheet_id': eid_value(sheet.Id), 'error': 'view could not be placed'})
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'placed': len(results), 'results': results}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── export_dwg ───────────────────────────────────────────────────────
        elif tool_name == 'export_dwg':
            from Autodesk.Revit.DB import DWGExportOptions, ElementId
            from System.Collections.Generic import List as NetList
            import os as _os
            try:
                ids = arguments.get('sheet_ids') or arguments.get('view_ids') or []
                if not ids:
                    return {'error': 'Provide sheet_ids or view_ids to export.'}
                folder = arguments.get('output_folder', '')
                if not folder:
                    dp = doc.PathName
                    folder = _os.path.dirname(dp) if dp else _os.path.expanduser('~')
                if not _os.path.isdir(folder):
                    _os.makedirs(folder)
                id_list = NetList[ElementId]()
                for i in ids:
                    id_list.Add(make_eid(int(i)))
                opts = DWGExportOptions()
                ok = doc.Export(folder, 'T3Lab_Export', id_list, opts)
                return {'success': bool(ok), 'count': id_list.Count, 'output_folder': folder}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── export_image ─────────────────────────────────────────────────────
        elif tool_name == 'export_image':
            from Autodesk.Revit.DB import (ImageExportOptions, ExportRange, ImageFileType,
                                           ImageResolution, ElementId)
            from System.Collections.Generic import List as NetList
            import os as _os
            try:
                view = doc.GetElement(make_eid(int(arguments['view_id']))) if arguments.get('view_id') else doc.ActiveView
                folder = arguments.get('output_folder', '')
                if not folder:
                    dp = doc.PathName
                    folder = _os.path.dirname(dp) if dp else _os.path.expanduser('~')
                if not _os.path.isdir(folder):
                    _os.makedirs(folder)
                safe = ''.join(ch for ch in view.Name if ch.isalnum() or ch in ' _-').strip() or 'view'
                base = _os.path.join(folder, 'T3Lab_' + safe)
                opts = ImageExportOptions()
                opts.FilePath = base
                opts.ExportRange = ExportRange.SetOfViews
                view_ids = NetList[ElementId]()
                view_ids.Add(view.Id)
                opts.SetViewsAndSheets(view_ids)
                opts.HLRandWFViewsFileType = ImageFileType.PNG
                opts.ShadowViewsFileType = ImageFileType.PNG
                opts.ImageResolution = ImageResolution.DPI_150
                try:
                    opts.PixelSize = int(arguments.get('width', 1600))
                except Exception:
                    pass
                # Revit appends " - <ViewType> - <ViewName>" to the base name,
                # so the exact output path isn't known up-front. Snapshot the
                # matching files before, export, and report what changed —
                # callers (assistant vision capture) need the real file path.
                prefix = 'T3Lab_' + safe
                before = {}
                try:
                    for fn in _os.listdir(folder):
                        if fn.startswith(prefix):
                            p = _os.path.join(folder, fn)
                            before[fn] = _os.path.getmtime(p)
                except Exception:
                    pass
                doc.ExportImage(opts)
                files = []
                try:
                    for fn in _os.listdir(folder):
                        if not fn.startswith(prefix):
                            continue
                        if not fn.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff')):
                            continue
                        p = _os.path.join(folder, fn)
                        if fn not in before or _os.path.getmtime(p) != before[fn]:
                            files.append(p)
                except Exception:
                    pass
                return {'success': True, 'view': view.Name,
                        'output_folder': folder, 'files': files}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── create_project_parameter ─────────────────────────────────────────
        elif tool_name == 'create_project_parameter':
            from Autodesk.Revit.DB import (Transaction, BuiltInParameterGroup,
                                           ExternalDefinitionCreationOptions)
            import os as _os
            try:
                name = arguments.get('name')
                cats = arguments.get('categories', [])
                if not name or not cats:
                    return {'error': 'name and categories are required.'}
                group_name = arguments.get('group', 'Data')
                instance = bool(arguments.get('instance', True))
                type_name = (arguments.get('type') or 'Text')

                app = doc.Application
                # Ensure a shared-parameter file exists.
                sp_path = app.SharedParametersFilename
                if not sp_path or not _os.path.isfile(sp_path):
                    data_dir = _os.path.join(_os.path.expanduser('~'), 'T3Lab_AI_Data')
                    if not _os.path.isdir(data_dir):
                        _os.makedirs(data_dir)
                    sp_path = _os.path.join(data_dir, 'T3Lab_SharedParameters.txt')
                    if not _os.path.isfile(sp_path):
                        open(sp_path, 'w').close()
                    app.SharedParametersFilename = sp_path
                def_file = app.OpenSharedParameterFile()
                if def_file is None:
                    return {'error': 'Could not open shared parameter file.'}

                grp = def_file.Groups.get_Item('T3Lab') or def_file.Groups.Create('T3Lab')

                # Resolve the data type across Revit versions (SpecTypeId vs ParameterType).
                spec = None
                try:
                    from Autodesk.Revit.DB import SpecTypeId
                    spec_map = {
                        'text': SpecTypeId.String.Text, 'integer': SpecTypeId.Int.Integer,
                        'number': SpecTypeId.Number, 'length': SpecTypeId.Length,
                        'area': SpecTypeId.Area, 'yesno': SpecTypeId.Boolean.YesNo,
                    }
                    spec = spec_map.get(type_name.lower(), SpecTypeId.String.Text)
                    ext_opts = ExternalDefinitionCreationOptions(name, spec)
                except Exception:
                    from Autodesk.Revit.DB import ParameterType
                    pt_map = {
                        'text': ParameterType.Text, 'integer': ParameterType.Integer,
                        'number': ParameterType.Number, 'length': ParameterType.Length,
                        'area': ParameterType.Area, 'yesno': ParameterType.YesNo,
                    }
                    ext_opts = ExternalDefinitionCreationOptions(name, pt_map.get(type_name.lower(), ParameterType.Text))

                ext_def = None
                for d in grp.Definitions:
                    if d.Name == name:
                        ext_def = d; break
                if ext_def is None:
                    ext_def = grp.Definitions.Create(ext_opts)

                cat_set = app.Create.NewCategorySet()
                for c in cats:
                    bic = self._bic_map().get(c)
                    if bic is None:
                        continue
                    try:
                        cat_set.Insert(doc.Settings.Categories.get_Item(bic))
                    except Exception:
                        pass
                if cat_set.IsEmpty:
                    return {'error': 'No valid categories resolved.'}

                binding = (app.Create.NewInstanceBinding(cat_set) if instance
                           else app.Create.NewTypeBinding(cat_set))

                t = Transaction(doc, 'T3Lab AI Create Project Parameter')
                t.Start()
                try:
                    group_param = None
                    try:
                        from Autodesk.Revit.DB import GroupTypeId
                        group_param = GroupTypeId.Data
                    except Exception:
                        group_param = BuiltInParameterGroup.PG_DATA

                    try:
                        ok = doc.ParameterBindings.Insert(ext_def, binding, group_param)
                        if not ok:
                            ok = doc.ParameterBindings.ReInsert(ext_def, binding, group_param)
                    except Exception:
                        ok = doc.ParameterBindings.Insert(ext_def, binding, BuiltInParameterGroup.PG_DATA)
                        if not ok:
                            ok = doc.ParameterBindings.ReInsert(ext_def, binding, BuiltInParameterGroup.PG_DATA)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'parameter': name, 'binding': 'instance' if instance else 'type',
                        'categories': cats}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── room_to_floor ────────────────────────────────────────────────────
        elif tool_name == 'room_to_floor':
            from Autodesk.Revit.DB import (Transaction, ElementId, Floor, FloorType,
                                           SpatialElementBoundaryOptions, CurveLoop)
            from System.Collections.Generic import List as NetList
            try:
                ids = arguments.get('room_ids') or ([arguments['room_id']] if arguments.get('room_id') else [])
                if not ids:
                    return {'error': 'Provide room_id or room_ids.'}
                ftype_name = arguments.get('floor_type')
                ftypes = list(FilteredElementCollector(doc).OfClass(FloorType))
                ftype = None
                if ftype_name:
                    for f in ftypes:
                        if f.Name == ftype_name or ftype_name.lower() in f.Name.lower():
                            ftype = f; break
                if ftype is None and ftypes:
                    ftype = ftypes[0]
                if ftype is None:
                    return {'error': 'No floor types available.'}

                bopts = SpatialElementBoundaryOptions()
                created = []
                t = Transaction(doc, 'T3Lab AI Room To Floor')
                t.Start()
                try:
                    for rid in ids:
                        room = doc.GetElement(make_eid(int(rid)))
                        if room is None:
                            continue
                        loops = room.GetBoundarySegments(bopts)
                        if not loops or loops.Count == 0:
                            continue
                        loop = CurveLoop()
                        for seg in loops[0]:
                            loop.Append(seg.GetCurve())
                        level_id = room.LevelId
                        try:
                            profile = NetList[CurveLoop]()
                            profile.Add(loop)
                            fl = Floor.Create(doc, profile, ftype.Id, level_id)
                            created.append(eid_value(fl.Id))
                        except Exception:
                            # Older Revit fallback (NewFloor) — best effort.
                            try:
                                from Autodesk.Revit.DB import CurveArray
                                ca = CurveArray()
                                for seg in loops[0]:
                                    ca.Append(seg.GetCurve())
                                fl = doc.Create.NewFloor(ca, False)
                                created.append(eid_value(fl.Id))
                            except Exception:
                                pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'floors_created': len(created), 'floor_ids': created}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── purge_unused ─────────────────────────────────────────────────────
        elif tool_name == 'purge_unused':
            from Autodesk.Revit.DB import (Transaction, FamilySymbol, View,
                                           ElementId)
            try:
                dry_run = bool(arguments.get('dry_run', True))
                used_type_ids = set()
                from Autodesk.Revit.DB import FamilyInstance
                for fi in FilteredElementCollector(doc).OfClass(FamilyInstance):
                    try:
                        used_type_ids.add(eid_value(fi.GetTypeId()))
                    except Exception:
                        pass
                unused_syms = [s for s in FilteredElementCollector(doc).OfClass(FamilySymbol)
                               if eid_value(s.Id) not in used_type_ids]

                views = list(FilteredElementCollector(doc).OfClass(View))
                used_tpl = set()
                for v in views:
                    try:
                        if not v.IsTemplate and eid_value(v.ViewTemplateId) != -1:
                            used_tpl.add(eid_value(v.ViewTemplateId))
                    except Exception:
                        pass
                unused_tpl = [v for v in views if v.IsTemplate and eid_value(v.Id) not in used_tpl]

                report = {
                    'unused_family_types': len(unused_syms),
                    'unused_family_type_names': [s.Name for s in unused_syms[:30]],
                    'unused_view_templates': len(unused_tpl),
                    'unused_view_template_names': [v.Name for v in unused_tpl[:30]],
                }
                if dry_run:
                    report['dry_run'] = True
                    report['note'] = 'Set dry_run=false to delete these items.'
                    return report

                deleted = 0
                t = Transaction(doc, 'T3Lab AI Purge Unused')
                t.Start()
                try:
                    for s in unused_syms:
                        try:
                            doc.Delete(s.Id); deleted += 1
                        except Exception:
                            pass
                    for v in unused_tpl:
                        try:
                            doc.Delete(v.Id); deleted += 1
                        except Exception:
                            pass
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                report['dry_run'] = False
                report['deleted'] = deleted
                return report
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── audit_model ──────────────────────────────────────────────────────
        elif tool_name == 'audit_model':
            from Autodesk.Revit.DB import (View, FamilyInstance, FamilySymbol,
                                           ImportInstance, Group, BuiltInCategory as _BIC)
            try:
                # Warnings grouped by description.
                warn_groups = {}
                try:
                    for w in doc.GetWarnings():
                        d = w.GetDescriptionText()
                        warn_groups[d] = warn_groups.get(d, 0) + 1
                except Exception:
                    pass
                top_warnings = sorted(warn_groups.items(), key=lambda kv: kv[1], reverse=True)[:15]

                imported_cad = FilteredElementCollector(doc).OfClass(ImportInstance).GetElementCount()
                model_groups = FilteredElementCollector(doc).OfCategory(_BIC.OST_IOSModelGroups).WhereElementIsNotElementType().GetElementCount()
                in_place = 0
                for fi in FilteredElementCollector(doc).OfClass(FamilyInstance):
                    try:
                        if fi.Symbol and fi.Symbol.Family and fi.Symbol.Family.IsInPlace:
                            in_place += 1
                    except Exception:
                        pass

                used_type_ids = set()
                for fi in FilteredElementCollector(doc).OfClass(FamilyInstance):
                    try:
                        used_type_ids.add(eid_value(fi.GetTypeId()))
                    except Exception:
                        pass
                unused_syms = sum(1 for s in FilteredElementCollector(doc).OfClass(FamilySymbol)
                                  if eid_value(s.Id) not in used_type_ids)

                views = list(FilteredElementCollector(doc).OfClass(View))
                default_named = sum(1 for v in views if not v.IsTemplate and (
                    v.Name.startswith('Copy of') or 'Copy 1' in v.Name))

                return {
                    'warnings_total': sum(warn_groups.values()),
                    'warnings_by_type': [{'description': d, 'count': c} for d, c in top_warnings],
                    'imported_cad_instances': imported_cad,
                    'model_groups': model_groups,
                    'in_place_families': in_place,
                    'unused_family_types': unused_syms,
                    'views_with_default_names': default_named,
                    'total_views': len(views),
                }
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        # ── create_workset ───────────────────────────────────────────────────
        elif tool_name == 'create_workset':
            from Autodesk.Revit.DB import Transaction, Workset, WorksetTable, FilteredWorksetCollector, WorksetKind
            try:
                if not doc.IsWorkshared:
                    return {'error': 'Document is not workshared — cannot create worksets.'}
                name = arguments.get('name')
                if not name:
                    return {'error': 'name is required.'}
                existing = [w.Name for w in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)]
                if name in existing:
                    return {'error': 'Workset "{}" already exists.'.format(name)}
                t = Transaction(doc, 'T3Lab AI Create Workset')
                t.Start()
                try:
                    ws = Workset.Create(doc, name)
                    t.Commit()
                except Exception as e:
                    t.RollBack()
                    return {'error': str(e), 'tool': tool_name}
                return {'success': True, 'workset': name, 'workset_id': eid_value(ws.Id)}
            except Exception as e:
                return {'error': str(e), 'tool': tool_name}

        return {'error': 'Tool not implemented'}

    def start_server(self):
        """Start the MCP server. Returns True when the server is up —
        including when it was already running (the desired post-condition
        holds; callers like the startup auto-start and MCPControl treat the
        return as "is the server on", not "did a new thread spawn")."""
        if self._is_running:
            return True

        # Prefilter: is something already LISTENING on this port? connect()
        # sees both normal and wildcard (0.0.0.0, e.g. pyRevit Routes)
        # listeners. Deliberately NO bind-test here: IronPython releases a
        # closed socket lazily (on .NET GC), so a probe bind poisons the very
        # port it just declared free and the real bind right after fails —
        # that was the "10048 on every port" (and, with SO_REUSEADDR, the
        # WSAEACCES 10013) failure. The real bind below is the only bind.
        def has_listener(port):
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.25)
            try:
                s.connect(('127.0.0.1', port))
                return True
            except Exception:
                return False
            finally:
                s.close()

        self._start_error = None

        # Bind synchronously, walking the port range on ANY bind failure —
        # a zombie socket left by a killed Revit fails the bind with 10048
        # (or WSAEACCES 10013) even though nothing answers on the port; the
        # walk just moves on. The real bind is the only free-vs-taken test.
        def bind_first_free():
            bind_error = None
            port = 48884
            while port <= 48894:
                if not has_listener(port):
                    try:
                        # 127.0.0.1 explicitly — binding 'localhost' leaves
                        # the address family to the resolver (IPv4 vs ::1);
                        # clients resolving the other family pay a ~2s
                        # fallback per request.
                        return (_ThreadedHTTPServer(('127.0.0.1', port),
                                                    MCPRequestHandler),
                                port, None)
                    except Exception as e:
                        bind_error = e
                port += 1
            return None, None, bind_error

        http_server, port, bind_error = bind_first_free()
        if http_server is None:
            # IronPython frees closed sockets lazily (.NET GC) — a port
            # released a moment ago (e.g. by stop_server during a toggle)
            # can still look bound. Collect and retry once before giving up.
            try:
                import System
                System.GC.Collect()
                System.GC.WaitForPendingFinalizers()
            except Exception:
                pass
            http_server, port, bind_error = bind_first_free()
        if http_server is None:
            self._start_error = bind_error
            msg = "No usable port in range 48884-48894"
            if bind_error is not None:
                msg += " (last bind error: {})".format(bind_error)
            raise Exception(msg)

        self._port = port
        http_server.mcp_server = self
        self._http_server = http_server

        # Initialize External Event for thread safety. NOTE: this only
        # succeeds when start_server() is itself called on Revit's main
        # thread (e.g. from an MCP Control dialog button). When the assistant
        # auto-starts the server from a background probe thread,
        # ExternalEvent.Create fails here and must instead be created up-front
        # via ensure_external_event() on the pushbutton UI thread.
        if HAS_REVIT_UI and not self._external_event:
            self.ensure_external_event()

        # NOTE: this server does NOT use pyRevit Routes. Earlier builds
        # activated the Routes server here and wrote its port into the
        # pyRevit config — that made Routes squat 0.0.0.0 ports inside the
        # T3Lab 48884-48894 range and fail activation in every additional
        # Revit instance ("Routes servers failed activation" at startup).

        def run_server():
            try:
                self._is_running = True
                http_server.serve_forever()
            except Exception as e:
                self._start_error = e
            finally:
                self._is_running = False

        self._server_thread = threading.Thread(target=run_server)
        self._server_thread.daemon = True
        self._server_thread.start()

        # The socket is already bound and listening — this poll only waits
        # for the thread to flip the flag (a serve_forever failure surfaces
        # through _start_error instead of a silent False).
        import time
        deadline = time.time() + 5.0
        while time.time() < deadline and not self._is_running:
            if self._start_error is not None or not self._server_thread.is_alive():
                break
            time.sleep(0.05)
        return self._is_running

    def stop_server(self):
        """Stop the MCP server"""
        if not self._is_running:
            return False

        self._is_running = False

        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
            # IronPython frees closed sockets on .NET GC, not on close() —
            # collect now so an immediate restart can rebind this port
            # instead of walking to the next one.
            try:
                import System
                System.GC.Collect()
                System.GC.WaitForPendingFinalizers()
            except Exception:
                pass

        if self._server_thread:
            self._server_thread.join(timeout=5)
            self._server_thread = None

        return True

    def get_server_stats(self):
        """Get server statistics"""
        return {
            'running': self._is_running,
            'port': self._port,
            'total_clients': self._total_clients,
            'commands_processed': self._commands_processed,
            'current_clients': len(self._clients),
            'tools_count': len(self._tools),
            'external_event_ready': self._external_event is not None,
        }

    def register_tool(self, name, description, input_schema, handler):
        """Register a custom tool"""
        self._tools[name] = {
            'name': name,
            'description': description,
            'inputSchema': input_schema
        }


def get_t3labai_server():
    """Get the singleton T3LabAI server instance"""
    return T3LabAIServer()
