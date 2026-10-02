# -*- coding: utf-8 -*-
"""
OBSERVABILITY & FEEDBACK — spans, traces, and the JSONL they land in.

`telemetry.py` already answers "how did that TURN go?" (latency, tokens, cache
hits). It cannot answer the questions a graph raises, because it has no idea
the turn had structure:

    which shape did the planner choose, and was it right?
    which node was slow — or was it the reducer all along?
    how often does a fallback path actually rescue a node?
    which specialist is failing verification, and on what kind of question?

A GraphTrace records exactly that: one span per node with its status, route,
duration and verdict, plus the plan that produced them. It writes next to the
existing telemetry so both live under one directory and one retention policy.

Nothing here is on the critical path — every method is best-effort and returns
rather than raising, matching `telemetry.record`'s discipline. A broken trace
must never cost the user their answer.

Pure Python, CPython-3 testable.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import io
import json
import os
import time

try:
    from Intelligence import telemetry as _telemetry
except Exception:                                   # pragma: no cover
    _telemetry = None


def graph_dir():
    """%APPDATA%/T3LabAI/telemetry/graph — created on demand."""
    if _telemetry is not None:
        base = _telemetry.telemetry_dir()
    else:
        base = os.path.join(
            os.environ.get('APPDATA', '') or os.path.expanduser('~'),
            'T3LabAI', 'telemetry')
    path = os.path.join(base, 'graph')
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def _log_path(when=None):
    stamp = time.strftime('%Y-%m-%d', time.localtime(when or time.time()))
    return os.path.join(graph_dir(), '{}.jsonl'.format(stamp))


class Span(object):
    """One node's slice of a trace."""

    __slots__ = ('node_id', 'kind', 'specialist', 'status', 'writer',
                 'started', 'finished', 'attempts', 'chars', 'error',
                 'verdict', 'recovered_by')

    def __init__(self, node_id, kind='agent', specialist=None, writer=False):
        self.node_id      = node_id
        self.kind         = kind
        self.specialist   = specialist
        self.writer       = bool(writer)
        self.status       = 'pending'
        self.started      = time.time()
        self.finished     = None
        self.attempts     = 0
        self.chars        = 0
        self.error        = None
        self.verdict      = None
        self.recovered_by = None

    def close(self, result=None):
        self.finished = time.time()
        if result is not None:
            self.status   = result.status
            self.attempts = result.attempts
            self.chars    = len(result.output or u'')
            self.error    = (result.error or u'')[:200] or None
            self.verdict  = result.data.get('verdict')
            self.recovered_by = result.data.get('recovered_by')
        return self

    @property
    def ms(self):
        if self.finished is None:
            return None
        return int(round((self.finished - self.started) * 1000.0))

    def to_dict(self):
        return {'node': self.node_id, 'kind': self.kind,
                'specialist': self.specialist, 'writer': self.writer,
                'status': self.status, 'ms': self.ms,
                'attempts': self.attempts, 'chars': self.chars,
                'error': self.error, 'verdict': self.verdict,
                'recovered_by': self.recovered_by}


class GraphTrace(object):
    """Everything one graph run did, ready to serialise.

    Doubles as the executor's `on_event` sink: pass `trace.on_event` straight
    into GraphExecutor and the spans build themselves.
    """

    def __init__(self, goal=u'', pattern=u'', lang=u'', analysis=None):
        self.goal     = goal or u''
        self.pattern  = pattern or u''
        self.lang     = lang or u''
        self.analysis = analysis            # Utterance.to_dict(), or None
        self.started  = time.time()
        self.finished = None
        self.spans    = []                  # ordered
        self.events   = []                  # (name, payload) for debugging
        self.adaptations = []               # what the orchestrator changed
        self._by_node = {}

    # ── executor sink ────────────────────────────────────────────────────

    def on_event(self, name, payload):
        """Wire straight into GraphExecutor(on_event=...). Never raises."""
        try:
            self.events.append((name, payload))
            if name == 'node_start':
                span = Span(payload.get('node'), kind=payload.get('kind'),
                            specialist=payload.get('specialist'),
                            writer=payload.get('writer', False))
                self.spans.append(span)
                self._by_node[span.node_id] = span
            elif name == 'node_skipped':
                span = Span(payload.get('node'))
                span.status = 'skipped'
                span.finished = time.time()
                self.spans.append(span)
            elif name == 'node_recovered':
                span = self._by_node.get(payload.get('node'))
                if span is not None:
                    span.recovered_by = payload.get('alt')
        except Exception:
            pass

    def close_node(self, result):
        span = self._by_node.get(result.node_id)
        if span is not None:
            span.close(result)

    def close(self, state=None):
        self.finished = time.time()
        if state is not None:
            for result in state.results().values():
                self.close_node(result)
        return self

    def note_adaptation(self, node_id, what, detail=u''):
        """Record a decision the orchestrator changed mid-run."""
        self.adaptations.append({'node': node_id, 'change': what,
                                 'detail': u'{}'.format(detail)[:200]})

    # ── output ───────────────────────────────────────────────────────────

    @property
    def total_ms(self):
        if self.finished is None:
            return None
        return int(round((self.finished - self.started) * 1000.0))

    def to_dict(self):
        return {'ts': int(self.started), 'goal': self.goal[:400],
                'pattern': self.pattern, 'lang': self.lang,
                'total_ms': self.total_ms,
                'nodes': len([s for s in self.spans if s.kind == 'agent']),
                'spans': [s.to_dict() for s in self.spans],
                'adaptations': self.adaptations,
                'analysis': self.analysis}

    def summary(self):
        """One-line human summary — goes into the debug log."""
        tally = {}
        for span in self.spans:
            tally[span.status] = tally.get(span.status, 0) + 1
        bits = u', '.join(u'{}={}'.format(k, v)
                          for k, v in sorted(tally.items()))
        return u'graph[{}] {} nodes in {} ms ({})'.format(
            self.pattern, len(self.spans), self.total_ms, bits or u'-')


def record(trace, path=None):
    """Append one trace to today's JSONL. Best-effort; never raises.

    Same write discipline as `telemetry.record`: serialise to ASCII first, then
    write through io.open(encoding='utf-8'). json.dump onto a byte-mode file
    truncates mid-write under IronPython 2.7 the moment the payload carries
    Vietnamese — and every goal here is a user's own sentence.
    """
    try:
        data = trace.to_dict() if hasattr(trace, 'to_dict') else dict(trace)
        line = json.dumps(data, ensure_ascii=True, sort_keys=True)
        if isinstance(line, bytes):
            line = line.decode('ascii')
        if path is None and _telemetry is not None:
            # Traces carry the user's own sentence (goal): same 30-day
            # retention as telemetry, applied once per session.
            _telemetry.prune_once()
        with io.open(path or _log_path(), 'a', encoding='utf-8') as f:
            f.write(line + u'\n')
        return True
    except Exception:
        return False


def read_traces(path):
    """Parse one JSONL file into dicts. Skips malformed lines."""
    out = []
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out
