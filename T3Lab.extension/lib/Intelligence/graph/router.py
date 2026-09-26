# -*- coding: utf-8 -*-
"""
ROUTER primitive — decides where a piece of work goes.

Thin by design: `AgentDispatcher` already does the classification well and is
covered by dev/test_assistant_routing.py, so this wraps it rather than
competing with it. What it adds is the two things a graph needs and a flat
dispatcher does not:

    alternatives()   a ranked fallback route, so a failed node has somewhere
                     to go instead of taking the whole turn down with it
                     (the "redundant paths" property)
    writer detection which routes modify the model — the executor must know
                     before it schedules anything, because two writers may
                     never run at once

The language layer is threaded through: the dispatcher is asked to classify the
NORMALISED text (so "ktra warning ko" reaches the QA keywords at all), and a
prohibitive negation demotes a write route to a read route, because "đừng xóa
tường" must never be scheduled as a writer node.

Pure Python, CPython-3 testable.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

# Specialists that can modify the model. Kept here rather than imported from
# specialists.py because the executor needs it before any spec is resolved, and
# a graph must be schedulable without the specialist registry being importable
# (it is, in Revit; it is not, in a bare CPython test of the topology).
WRITER_SPECIALISTS = frozenset(['revit_action', 'modeling', 'export'])

# Where a specialist falls back to when its node fails. A read specialist falls
# back to 'general' (full catalog, no restrictions); a WRITE specialist falls
# back to nothing — retrying a failed model edit under a different prompt is
# how you get the edit applied twice.
_FALLBACK_ROUTE = {
    'revit_data': 'general',
    'knowledge':  'revit_data',
    'qa_check':   'revit_data',
    'multi_doc':  'revit_data',
    'comment':    'general',
    'general':    None,
    'revit_action': None,
    'modeling':     None,
    'export':       None,
}


class GraphRouter(object):
    """Routes a goal onto a specialist, with fallbacks and writer flags."""

    def __init__(self, dispatcher=None, skills_engine=None, provider=None,
                 allow_llm=False):
        """
        dispatcher:    an AgentDispatcher (or anything with .classify()).
                       None disables routing — every goal becomes 'general'.
        allow_llm:     whether the dispatcher may spend a classification call.
                       Defaults False here: the planner routes every sub-goal,
                       and N classification round-trips in front of a turn is
                       exactly the latency the dispatcher's own timeout exists
                       to avoid.
        """
        self.dispatcher    = dispatcher
        self.skills_engine = skills_engine
        self.provider      = provider
        self.allow_llm     = bool(allow_llm)

    def route(self, goal, utterance=None):
        """Returns {'specialist', 'skill', 'source', 'confidence', 'writer'}.

        `utterance` is the analysed form of `goal` (Intelligence.language). When
        supplied, its normalised text is what gets classified and its negation
        analysis can demote the route.
        """
        text = goal or u''
        if utterance is not None and utterance.normalized:
            text = utterance.normalized

        decision = {'specialist': 'general', 'skill': None,
                    'source': 'default', 'confidence': 0.3}
        if self.dispatcher is not None:
            try:
                decision = dict(self.dispatcher.classify(
                    text,
                    provider=(self.provider if self.allow_llm else None),
                    skills_engine=self.skills_engine,
                    allow_llm=self.allow_llm) or decision)
            except Exception:
                pass

        specialist = decision.get('specialist') or 'general'

        # A prohibition is not a write instruction. The keyword stage matched
        # the verb inside "đừng xóa text note" and returned revit_action; the
        # user asked for the opposite of that.
        if utterance is not None and not utterance.is_safe_to_act:
            if specialist in WRITER_SPECIALISTS:
                specialist = 'revit_data'
                decision['source'] = 'negation-demoted'
                decision['confidence'] = min(
                    decision.get('confidence', 0.5), 0.5)

        decision['specialist'] = specialist
        decision['writer'] = specialist in WRITER_SPECIALISTS
        return decision

    def alternatives(self, specialist):
        """Fallback specialists for a failed node, best first (possibly [])."""
        alt = _FALLBACK_ROUTE.get(specialist, 'general')
        return [alt] if alt else []

