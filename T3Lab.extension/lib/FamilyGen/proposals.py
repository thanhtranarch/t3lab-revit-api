# -*- coding: utf-8 -*-
"""Store for family schemas an external AI proposes through MCP.

``famigen_propose_family`` saves the schema here and shows it in the FamiGen
review pane; ``famigen_create_family`` can then build it by ``proposal_id``
without the client resending the whole schema. Records are small JSON files in
``%APPDATA%\\T3LabAI\\famigen_proposals`` so they survive an engine reload and
are shared by every Python engine in the Revit process. Pure Python - no Revit.

The module also remembers the FamiGen window that MCP opened (``active
window``). It lives here rather than in ``GUI.FamiGenDialog`` because the
FamiGen pushbutton reloads that module on every click, which would forget an
open window and make the next proposal open a second one.
"""
import io
import json
import os
import re
import time
import uuid

MAX_RECORDS = 50
_ID_RE = re.compile(r'^FG-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$')


def default_folder():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI', 'famigen_proposals')


def is_valid_id(proposal_id):
    return isinstance(proposal_id, str) and bool(_ID_RE.match(proposal_id))


class ProposalStore(object):
    def __init__(self, folder=None, clock=None):
        self.folder = folder or default_folder()
        self._clock = clock or time.time

    def _path(self, proposal_id):
        return os.path.join(self.folder, proposal_id + '.json')

    def new_id(self):
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self._clock()))
        return 'FG-{}-{}'.format(stamp, uuid.uuid4().hex[:6])

    def save(self, schema, source='mcp', note=''):
        """Persist `schema`; returns the new proposal id."""
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder)
        proposal_id = self.new_id()
        record = {'id': proposal_id, 'created': self._clock(), 'source': source,
                  'note': note or '', 'schema': schema}
        with io.open(self._path(proposal_id), 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2))
        self.prune()
        return proposal_id

    def load(self, proposal_id):
        """The stored record, or None for an unknown or malformed id."""
        if not is_valid_id(proposal_id):
            return None
        path = self._path(proposal_id)
        if not os.path.isfile(path):
            return None
        try:
            with io.open(path, 'r', encoding='utf-8') as handle:
                return json.loads(handle.read())
        except (IOError, OSError, ValueError):
            return None

    def ids(self):
        if not os.path.isdir(self.folder):
            return []
        names = [n[:-5] for n in os.listdir(self.folder) if n.endswith('.json')]
        return sorted(n for n in names if is_valid_id(n))

    def prune(self):
        ids = self.ids()
        for old in ids[:-MAX_RECORDS] if len(ids) > MAX_RECORDS else []:
            try:
                os.remove(self._path(old))
            except OSError:
                pass


_ACTIVE = {'window': None}


def set_active_window(window):
    _ACTIVE['window'] = window


def get_active_window():
    return _ACTIVE['window']


def clear_active_window(window=None):
    if window is None or _ACTIVE['window'] is window:
        _ACTIVE['window'] = None
