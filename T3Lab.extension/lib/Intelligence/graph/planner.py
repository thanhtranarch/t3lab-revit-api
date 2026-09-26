# -*- coding: utf-8 -*-
"""
PLAN operation — decompose a user goal into a TaskGraph.

This is the module that decides a turn is more than one thing. Everything
downstream (parallel execution, reduction, verification) only matters if the
decomposition is trustworthy, so the bar here is deliberately high: the planner
would rather return a single node — today's exact behaviour — than split a
sentence it is not sure about.

Splitting rules, strictest first:

  1. STRONG sequencers and clause punctuation ("rồi", "sau đó", "then", ";",
     numbered lists) — these join clauses and essentially never join nouns.
     `semantics.split_sequenced` owns the table.
  2. WEAK sequencers ("và", "and") — split ONLY when both halves independently
     classify to a specialist via the dispatcher's KEYWORD stage. "tường và
     cột" fails that test (neither half is a command) and stays one goal;
     "thống kê tường và xuất pdf" passes it.
  3. Everything else stays whole.

A fragment that carries no verb of its own is glued back onto its predecessor:
"thống kê tường rồi cửa" is two objects for one verb, not two requests.

Ordering rules:

  - READ-only sub-goals are independent: they fan out and run together.
  - A WRITER sub-goal depends on every sub-goal the user stated before it. The
    user said "làm A rồi làm B" and meant that order; a write that jumps its
    queue can act on state a previous step was about to change.

Pure Python, CPython-3 testable.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

from Intelligence.graph import topology
from Intelligence.graph.primitives import AGENT, FALLBACK, REDUCER, Node
from Intelligence.graph.router import GraphRouter

try:
    from Intelligence.language import analyzer as _analyzer
    from Intelligence.language import semantics as _semantics
    HAS_LANGUAGE = True
except Exception:                                   # pragma: no cover
    HAS_LANGUAGE = False
    _analyzer = None
    _semantics = None

# Above this many sub-goals the request is a document, not a chat message.
# Planning 9 agent turns off one line is a cost the user did not agree to.
MAX_NODES = 6

# A fragment shorter than this cannot be a request on its own.
_MIN_GOAL_CHARS = 4


class Plan(object):
    """A TaskGraph plus everything the orchestrator needs to run it."""

    def __init__(self, graph, utterance=None, sub_utterances=None,
                 decisions=None, lang='vi'):
        self.graph          = graph
        self.utterance      = utterance
        self.sub_utterances = dict(sub_utterances or {})   # node_id -> Utterance
        self.decisions      = dict(decisions or {})        # node_id -> route
        self.lang           = lang

    @property
    def is_multi(self):
        """True when the plan is worth running as a graph at all."""
        return len(self.agent_nodes()) > 1

    def agent_nodes(self):
        """The agent nodes the USER asked for — dormant fallback copies are
        not steps of the plan, they are insurance on one of them."""
        return [n for n in self.graph.nodes()
                if n.kind == AGENT and not self.graph.is_fallback_target(n.id)]


    def has_writer(self):
        return any(n.writer for n in self.agent_nodes())

    def describe(self):
        return self.graph.describe()


class Planner(object):
    """Turns a user message into a Plan."""

    def __init__(self, router=None, dispatcher=None, skills_engine=None):
        self.router = router or GraphRouter(dispatcher=dispatcher,
                                            skills_engine=skills_engine,
                                            allow_llm=False)

    # ── public ───────────────────────────────────────────────────────────

    def plan(self, text, lang=None, utterance=None):
        """Build a Plan for `text`. Never raises; returns a 1-node plan on
        anything it cannot decompose confidently."""
        raw = (text or u'').strip()
        utt = utterance
        if utt is None and HAS_LANGUAGE:
            try:
                utt = _analyzer.analyze(raw)
            except Exception:
                utt = None
        reply_lang = lang or (utt.reply_language() if utt is not None else 'vi')

        goals = self._split(raw, utt)
        if len(goals) > MAX_NODES:
            # Keep the first MAX_NODES and fold the rest into the last node,
            # so nothing the user asked for is silently dropped.
            head = goals[:MAX_NODES - 1]
            tail = u'; '.join(goals[MAX_NODES - 1:])
            goals = head + [tail]

        if len(goals) <= 1:
            return self._single(raw, utt, reply_lang)
        return self._multi(raw, goals, utt, reply_lang)

    # ── splitting ────────────────────────────────────────────────────────

    def _split(self, raw, utt):
        """Sub-goals in the order the user stated them."""
        if not raw:
            return []
        if not HAS_LANGUAGE:
            return [raw]

        try:
            parts = (list(utt.segments) if utt is not None
                     else _semantics.split_sequenced(raw))
        except Exception:
            parts = [raw]
        parts = [p.strip() for p in parts if p and p.strip()]
        if not parts:
            return [raw]

        # Weak split ("và"/"and") applies INSIDE each strong segment, and only
        # where both halves stand on their own as commands.
        expanded = []
        for part in parts:
            expanded.extend(self._maybe_weak_split(part))

        merged = self._merge_verbless(expanded)
        return [g for g in merged if len(g) >= _MIN_GOAL_CHARS] or [raw]

    def _maybe_weak_split(self, part):
        try:
            halves = _semantics.weak_split(part)
        except Exception:
            return [part]
        if len(halves) < 2:
            return [part]
        # Every half must independently look like a request. The keyword stage
        # is the right judge: it is the same test the assistant already uses to
        # decide a message is a command, and it costs nothing.
        if all(self._is_standalone_request(h) for h in halves):
            return halves
        return [part]

    def _is_standalone_request(self, text):
        """True when `text` on its own reads as a request, not a noun phrase."""
        text = (text or u'').strip()
        if len(text) < _MIN_GOAL_CHARS:
            return False
        dispatcher = getattr(self.router, 'dispatcher', None)
        if dispatcher is None:
            return False
        try:
            decision = dispatcher.classify(text, allow_llm=False)
        except Exception:
            return False
        # 'keyword' source means a real verb/frame matched — a bare noun
        # phrase ("cột") falls through to the default route and fails here,
        # which is exactly what keeps "tường và cột" a single goal.
        return bool(decision and decision.get('source') == 'keyword')

    def _merge_verbless(self, parts):
        """Glue a fragment with no request of its own onto its predecessor."""
        if len(parts) < 2:
            return parts
        out = [parts[0]]
        for part in parts[1:]:
            if self._is_standalone_request(part):
                out.append(part)
            else:
                # "thống kê tường rồi cửa" — one verb, two objects.
                out[-1] = u'{} {}'.format(out[-1], part).strip()
        return out

    # ── graph construction ───────────────────────────────────────────────

    def _analyze(self, text):
        if not HAS_LANGUAGE:
            return None
        try:
            return _analyzer.analyze(text)
        except Exception:
            return None

    def _single(self, raw, utt, lang):
        """One goal — a one-node graph, so the caller can treat every turn the
        same way even when nothing was decomposed."""
        decision = self.router.route(raw, utterance=utt)
        node = Node('n1', raw, kind=AGENT,
                    specialist=decision.get('specialist'),
                    skill=decision.get('skill'),
                    writer=decision.get('writer', False))
        graph = topology.linear([node], goal=raw)
        return Plan(graph, utterance=utt,
                    sub_utterances={'n1': utt},
                    decisions={'n1': decision}, lang=lang)

    def _multi(self, raw, goals, utt, lang):
        """Several goals — a diamond: plan → branches → reduce."""
        nodes, sub_utts, decisions = [], {}, {}
        for i, goal in enumerate(goals):
            nid = 'n{}'.format(i + 1)
            sub_utt = self._analyze(goal)
            decision = self.router.route(goal, utterance=sub_utt)
            node = Node(nid, goal, kind=AGENT,
                        specialist=decision.get('specialist'),
                        skill=decision.get('skill'),
                        writer=decision.get('writer', False))
            nodes.append(node)
            sub_utts[nid] = sub_utt
            decisions[nid] = decision

        sink = Node('reduce', raw, kind=REDUCER)

        graph = topology.TaskGraph(goal=raw, pattern='diamond')
        for node in nodes:
            graph.add_node(node)
        graph.add_node(sink)

        # Dependencies. A read node is independent — nothing precedes it, so
        # the whole read set is one parallel layer. A writer node inherits a
        # dependency on every node the user named before it, which preserves
        # the stated order without serialising the reads that surround it.
        for i, node in enumerate(nodes):
            if node.writer:
                for earlier in nodes[:i]:
                    graph.add_edge(earlier.id, node.id)
            graph.add_edge(node.id, sink.id)

        # Fault tolerance: a failed read node re-runs on its fallback route.
        # Writers get no fallback — re-running a model edit under a different
        # prompt is how an edit gets applied twice.
        for node in nodes:
            if node.writer:
                continue
            for alt in self.router.alternatives(node.specialist):
                alt_id = '{}_alt'.format(node.id)
                if alt_id in graph:
                    continue
                graph.add_node(Node(alt_id, node.goal, kind=AGENT,
                                    specialist=alt, optional=True,
                                    meta={'fallback_for': node.id}))
                graph.add_edge(node.id, alt_id, kind=FALLBACK,
                               label=u'retry as {}'.format(alt))
                # The fallback also feeds the reducer, but the edge is not a
                # dependency: `layers()` drops edges out of a fallback-only
                # node, so the reducer never waits on a path that may never run.
                graph.add_edge(alt_id, sink.id)

        return Plan(graph, utterance=utt, sub_utterances=sub_utts,
                    decisions=decisions, lang=lang)
