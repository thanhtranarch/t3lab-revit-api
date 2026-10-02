# -*- coding: utf-8 -*-
"""
Welcome-greeting phrases for the T3Lab Assistant.

The empty chat opens on one headline ("Good evening, Thanh"). It used to be
one fixed phrase per time of day, so the pane said exactly the same thing
every time it was opened in the same hour. This module holds a small pool per
period and picks from it at random, never repeating the previous pick.

Rules the pools follow (pinned by dev/test_assistant_greetings.py):
  * every template carries exactly one ``{name}``;
  * at most MAX_LEN characters without the name, so the headline fits a
    narrow dock on one or two lines;
  * the English pool is the default; the Vietnamese pool is only used when
    the user set the reply language to Vietnamese (``_ui_viet()``);
  * the same template is never picked twice in a row.

Pure Python, no clr / Revit imports, so the headless tests can import it.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import random
import re

#: Longest a template may be once ``{name}`` is removed.
MAX_LEN = 32

#: Order matters only for documentation; period_for_hour() is the authority.
PERIODS = ('late', 'early', 'morning', 'midday', 'afternoon', 'evening')

# ─── English ──────────────────────────────────────────────────────────────────
_EN = {
    # 22:00 – 04:59
    'late': (
        "Working late, {name}?",
        "Burning the midnight oil, {name}?",
        "Still at it, {name}?",
        "Night shift, {name}?",
        "Late-night modeling, {name}?",
        "Quiet hours, {name}. Let's build.",
    ),
    # 05:00 – 06:59
    'early': (
        "Early start, {name}",
        "Up before the sun, {name}?",
        "Fresh start, {name}",
        "Early bird, {name}. Let's go.",
        "Good morning, {name}",
    ),
    # 07:00 – 10:59
    'morning': (
        "Good morning, {name}",
        "Morning, {name}. Ready to model?",
        "Morning, {name}. What's first?",
        "Let's get modeling, {name}",
        "Fresh day, fresh model, {name}",
    ),
    # 11:00 – 12:59 — "Good afternoon" before noon reads wrong in English.
    'midday': (
        "Hello, {name}",
        "Midday check-in, {name}",
        "Lunch break soon, {name}?",
        "Halfway through the day, {name}",
        "Good day, {name}",
    ),
    # 13:00 – 17:59
    'afternoon': (
        "Good afternoon, {name}",
        "Afternoon, {name}. What's next?",
        "Back at it, {name}?",
        "Keep the momentum, {name}",
        "Afternoon, {name}. Let's model.",
    ),
    # 18:00 – 21:59
    'evening': (
        "Good evening, {name}",
        "Evening, {name}. One more task?",
        "Wrapping up the day, {name}?",
        "Evening shift, {name}?",
        "Still building, {name}?",
    ),
}

#: Fit any hour; mixed into every period's pool.
_EN_NEUTRAL = (
    "Ready to model, {name}?",
    "What are we building today, {name}?",
    "Hi {name}, how can I help?",
    "Welcome back, {name}",
    "What's on the drawing board, {name}?",
    "Ready when you are, {name}",
)

# ─── Vietnamese (reply language = vi) ─────────────────────────────────────────
_VI = {
    'late': (
        "Khuya rồi, {name}",
        "Thức khuya thế, {name}?",
        "Vẫn đang làm à, {name}?",
    ),
    'early': (
        "Dậy sớm nhỉ, {name}",
        "Bắt đầu sớm thế, {name}?",
        "Chào buổi sáng sớm, {name}",
    ),
    'morning': (
        "Chào buổi sáng, {name}",
        "Buổi sáng tốt lành, {name}",
        "Sáng nay dựng gì, {name}?",
    ),
    'midday': (
        "Chào buổi trưa, {name}",
        "Trưa rồi, {name}",
        "Nghỉ trưa chưa, {name}?",
    ),
    'afternoon': (
        "Chào buổi chiều, {name}",
        "Buổi chiều vui vẻ, {name}",
        "Chiều nay làm gì tiếp, {name}?",
    ),
    'evening': (
        "Chào buổi tối, {name}",
        "Buổi tối vui vẻ, {name}",
        "Tối rồi vẫn làm à, {name}?",
    ),
}

_VI_NEUTRAL = (
    "Sẵn sàng dựng model chưa, {name}?",
    "Hôm nay mình dựng gì, {name}?",
    "Chào {name}, cần mình giúp gì?",
)

# The phrase without a name, as the onboarding card shows it ("Good morning!").
# Unchanged from the old single-phrase _time_greeting() on purpose.
_BASE = {
    'en': {'late': "Working late", 'early': "Early start",
           'morning': "Good morning", 'midday': "Good afternoon",
           'afternoon': "Good afternoon", 'evening': "Good evening"},
    'vi': {'late': "Khuya rồi", 'early': "Dậy sớm nhỉ",
           'morning': "Chào buổi sáng", 'midday': "Chào buổi trưa",
           'afternoon': "Chào buổi chiều", 'evening': "Chào buổi tối"},
}

_NAME_SLOT = re.compile(r",?\s*\{name\}")


def _current_hour():
    import datetime
    return datetime.datetime.now().hour


def period_for_hour(hour=None):
    """Map an hour (0-23) to a period key. Never raises."""
    try:
        if hour is None:
            hour = _current_hour()
        hour = int(hour) % 24
    except Exception:
        return 'afternoon'
    if hour < 5 or hour >= 22:
        return 'late'
    if hour < 7:
        return 'early'
    if hour < 11:
        return 'morning'
    if hour < 13:
        return 'midday'
    if hour < 18:
        return 'afternoon'
    return 'evening'


def templates(period, viet=False):
    """Every template that fits ``period``: its own pool + the neutral pool."""
    own = (_VI if viet else _EN).get(period) or ()
    return list(own) + list(_VI_NEUTRAL if viet else _EN_NEUTRAL)


def fill(template, name):
    """Put ``name`` into ``template``; with no name, drop the slot cleanly.

    "Ready to model, {name}?" -> "Ready to model?"   (not "Ready to model, ?")
    """
    name = (name or "").strip()
    if name:
        return template.replace("{name}", name)
    return _NAME_SLOT.sub("", template).strip()


def base_greeting(hour=None, viet=False):
    """Plain time-of-day phrase with no name ("Good evening"). Never raises."""
    return _BASE['vi' if viet else 'en'][period_for_hour(hour)]


class GreetingPicker(object):
    """Random template per call, never the same one twice in a row."""

    def __init__(self, rng=None):
        self._rng = rng or random.Random()
        self.last = None

    def pick(self, hour=None, viet=False):
        pool = templates(period_for_hour(hour), viet)
        choices = [t for t in pool if t != self.last] or pool
        tpl = self._rng.choice(choices)
        self.last = tpl
        return tpl


# One picker for the Revit session: the CPython engine is persistent, so a
# reopened pane / floating window keeps the no-repeat memory too.
_PICKER = GreetingPicker()


def pick_template(hour=None, viet=False):
    """Next template from the session picker. Never raises."""
    try:
        return _PICKER.pick(hour, viet)
    except Exception:
        return "Xin chào, {name}" if viet else "Hello, {name}"


def greeting(name, hour=None, viet=False):
    """Convenience: a filled, freshly picked greeting."""
    return fill(pick_template(hour, viet), name)
