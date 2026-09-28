# -*- coding: utf-8 -*-
"""
CPython 3 test for the MCP server's GET /health and the bridge probe that
depends on it.

Run:  python3 dev/test_mcp_health.py
Exit 0 = all pass. Plain asserts, mirroring dev/test_server_categories.py.

The dead-code sweep af2a9ab (2026-09-26) deleted MCPRequestHandler.do_GET —
BaseHTTPRequestHandler calls it by 'do_' + verb, so nothing referenced it by
name. GET /health then answered 501, bridge._port_alive() declared every Revit
window started since dead, and Claude could only reach the one window still
running the older build ("MCP only connects to one project"). This drives the
real handler on a real socket, then the bridge probe against it and against
the listeners it must keep telling apart.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

from http.server import BaseHTTPRequestHandler, HTTPServer

import core.bridge as B
import core.server as S

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


def _serve(server_cls, handler_cls):
    httpd = server_cls(('127.0.0.1', 0), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _get(port, path):
    """(status, parsed-json-or-None) for GET path."""
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:{}{}'.format(port, path), timeout=3) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, None


class _LegacyHandler(BaseHTTPRequestHandler):
    """A server from the af2a9ab window: POST only, GET -> 501."""

    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class _ForeignHandler(_LegacyHandler):
    """Same 501, but not a Python BaseHTTP listener (e.g. pyRevit Routes)."""

    def version_string(self):
        return 'Microsoft-HTTPAPI/2.0'


def test_server_health():
    print('[server: GET /health]')
    httpd = _serve(S._ThreadedHTTPServer, S.MCPRequestHandler)
    try:
        port = httpd.server_port
        check('handler defines do_GET', callable(getattr(S.MCPRequestHandler, 'do_GET', None)))
        status, body = _get(port, '/health')
        check('/health -> 200', status == 200, status)
        body = body or {}
        check('/health status ok', body.get('status') == 'ok', body)
        check('/health reports this pid', body.get('pid') == os.getpid(), body)
        check('/health reports its port', body.get('port') == port, body)
        status, _ = _get(port, '/no-such-endpoint')
        check('unknown GET path -> 404', status == 404, status)
        check('bridge sees the server alive', B._port_alive(port, timeout=3))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_bridge_probe_classification():
    print('[bridge: _port_alive]')
    legacy = _serve(HTTPServer, _LegacyHandler)
    foreign = _serve(HTTPServer, _ForeignHandler)
    try:
        check('legacy BaseHTTP 501 counts as alive',
              B._port_alive(legacy.server_port, timeout=3))
        check('foreign 501 is not a T3Lab server',
              not B._port_alive(foreign.server_port, timeout=3))
    finally:
        for httpd in (legacy, foreign):
            httpd.shutdown()
            httpd.server_close()
    # Closed ports time out rather than refuse on some locked-down machines;
    # either way the probe must say dead.
    check('closed port is dead', not B._port_alive(legacy.server_port, timeout=0.5))


if __name__ == '__main__':
    test_server_health()
    test_bridge_probe_classification()
    if FAILURES:
        print('\n{} FAILED: {}'.format(len(FAILURES), ', '.join(FAILURES)))
        sys.exit(1)
    print('\nall passed')
