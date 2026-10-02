# -*- coding: utf-8 -*-
"""
Telemetry — per-turn latency and token accounting for the T3Lab Assistant.

Answers the questions that were previously unanswerable: how long does a turn
take to first token, how many Revit round-trips did the tools cost, and is the
Anthropic prompt cache actually being hit?

Nothing here is on the critical path: `TurnTimer` is a plain value holder and
`record()` appends one JSONL line, best-effort, never raising. The caller
decides which thread does the write (script.py already has a pool-thread
logging seam — see _log_activity).

Layout on disk:
    %APPDATA%/T3LabAI/telemetry/<YYYY-MM-DD>.jsonl

Read it back with `dev/bench_assistant.py`.

Pure Python + guarded imports — importable under CPython 3 for tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Telemetry"

import io
import json
import os
import time


# Keep the on-disk history bounded — one file per day, older ones pruned.
_RETENTION_DAYS = 30

# A turn that never streamed a token has no TTFT; use None rather than 0 so the
# aggregator can tell "instant" from "never happened".
_UNSET = None


def _appdata_dir():
    """%APPDATA%/T3LabAI (or ~/T3LabAI off Windows). Mirrors config.settings."""
    base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI')


def telemetry_dir():
    """Directory holding the JSONL files. Created on demand."""
    d = os.path.join(_appdata_dir(), 'telemetry')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def _log_path(when=None):
    stamp = time.strftime('%Y-%m-%d', time.localtime(when or time.time()))
    return os.path.join(telemetry_dir(), '{}.jsonl'.format(stamp))


def _ms(delta):
    """Seconds (float) -> whole milliseconds, or None when unmeasured."""
    if delta is _UNSET:
        return None
    return int(round(delta * 1000.0))


class TurnTimer(object):
    """One assistant turn's timings and counters.

    Usage:
        t = TurnTimer(provider='claude', model='...')
        t.mark_first_token()          # first streamed delta
        t.add_usage({...})            # per LLM call, accumulates
        t.tool_roundtrip(n_calls=3)   # one Revit round-trip carrying n calls
        t.finish(status='done')
        telemetry.record(t)
    """

    def __init__(self, provider=None, model=None, specialist=None,
                 skills=None, local=False, now=None):
        self.provider    = provider or u''
        self.model       = model or u''
        self.specialist  = specialist or u''
        self.skills      = list(skills or [])
        self.local       = bool(local)
        self.t_send      = now if now is not None else time.time()
        self.t_first     = _UNSET
        self.t_done      = _UNSET
        self.status      = u''
        self.iterations  = 0
        self.tool_runs   = 0
        # Distinct Revit round-trips. With P2 batching this is < tool_runs
        # whenever the model asked for several reads in one assistant turn;
        # equal to tool_runs on the sequential path.
        self.roundtrips  = 0
        self.input_tokens          = 0
        self.output_tokens         = 0
        self.cache_read_tokens     = 0
        self.cache_creation_tokens = 0

    # ── marks ────────────────────────────────────────────────────────────────

    def mark_first_token(self, now=None):
        """First streamed delta reached the UI. Idempotent — only the first
        call counts, so a per-delta caller can invoke it unconditionally."""
        if self.t_first is _UNSET:
            self.t_first = now if now is not None else time.time()

    def finish(self, status=u'', now=None):
        self.status = status or u''
        self.t_done = now if now is not None else time.time()
        return self

    def tool_roundtrip(self, n_calls=1):
        """One crossing to Revit's main thread carrying `n_calls` tool calls."""
        self.roundtrips += 1
        self.tool_runs  += max(0, int(n_calls))

    def add_usage(self, usage):
        """Accumulate one LLM call's token usage. Accepts the Anthropic shape
        and ignores anything it doesn't recognise, so providers that report
        nothing simply leave the counters at zero."""
        if not isinstance(usage, dict):
            return
        for key, attr in (
                ('input_tokens',                'input_tokens'),
                ('output_tokens',               'output_tokens'),
                ('cache_read_input_tokens',     'cache_read_tokens'),
                ('cache_creation_input_tokens', 'cache_creation_tokens')):
            try:
                val = int(usage.get(key) or 0)
            except Exception:
                continue
            if val:
                setattr(self, attr, getattr(self, attr) + val)

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def ttft_ms(self):
        """Time to first token, in ms. None when nothing ever streamed."""
        if self.t_first is _UNSET:
            return None
        return _ms(self.t_first - self.t_send)

    @property
    def total_ms(self):
        if self.t_done is _UNSET:
            return None
        return _ms(self.t_done - self.t_send)

    @property
    def cached_input_tokens(self):
        """Input tokens served from cache + fresh, i.e. the whole prefix."""
        return self.cache_read_tokens + self.input_tokens

    @property
    def cache_hit_ratio(self):
        """Share of prompt input tokens that came from cache (0.0-1.0).

        None when the turn reported no input tokens at all — a provider that
        does not report usage must not read as a 0% hit rate.
        """
        total = self.cached_input_tokens
        if total <= 0:
            return None
        return float(self.cache_read_tokens) / float(total)

    def to_dict(self):
        return {
            'ts':          int(self.t_send),
            'provider':    self.provider,
            'model':       self.model,
            'specialist':  self.specialist,
            'skills':      self.skills,
            'local':       self.local,
            'status':      self.status,
            'ttft_ms':     self.ttft_ms,
            'total_ms':    self.total_ms,
            'iterations':  self.iterations,
            'tool_runs':   self.tool_runs,
            'roundtrips':  self.roundtrips,
            'in_tok':      self.input_tokens,
            'out_tok':     self.output_tokens,
            'cache_read':  self.cache_read_tokens,
            'cache_write': self.cache_creation_tokens,
        }


# ─── sink ─────────────────────────────────────────────────────────────────────

def record(turn, path=None):
    """Append one turn to today's JSONL. Best-effort; never raises.

    Same write discipline as every other JSON writer in the codebase:
    serialize to ASCII first, then write through io.open(encoding='utf-8') —
    json.dump onto a byte-mode file blows up mid-write under IronPython 2.7 as
    soon as the payload carries Vietnamese, truncating the file.
    """
    try:
        data = turn.to_dict() if hasattr(turn, 'to_dict') else dict(turn)
        line = json.dumps(data, ensure_ascii=True, sort_keys=True)
        if isinstance(line, bytes):
            line = line.decode('ascii')
        if path is None:
            prune_once()
        target = path or _log_path()
        with io.open(target, 'a', encoding='utf-8') as f:
            f.write(line + u'\n')
        return True
    except Exception:
        return False


def prune(days=_RETENTION_DAYS, now=None):
    """Delete telemetry files older than `days`. Best-effort; returns count.

    Covers both sinks that share this directory: the per-turn files here and
    the graph traces graph/observability.py writes into telemetry/graph/ —
    one retention policy for both, as observability.py promises.
    """
    removed = 0
    try:
        cutoff = (now or time.time()) - days * 86400.0
        d = telemetry_dir()
        for folder in (d, os.path.join(d, 'graph')):
            try:
                names = os.listdir(folder)
            except Exception:
                continue
            for name in names:
                if not name.endswith('.jsonl'):
                    continue
                full = os.path.join(folder, name)
                try:
                    if os.path.getmtime(full) < cutoff:
                        os.remove(full)
                        removed += 1
                except Exception:
                    continue
    except Exception:
        pass
    return removed


_PRUNED = []


def prune_once():
    """prune() the first time any sink writes in this session. Never raises.

    Nothing called prune() before, so the "one file per day, older ones
    pruned" promise above was never kept and the folder only grew.
    """
    if _PRUNED:
        return 0
    _PRUNED.append(True)
    try:
        return prune()
    except Exception:
        return 0


def read_turns(path):
    """Parse one JSONL file into a list of dicts. Skips malformed lines."""
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
