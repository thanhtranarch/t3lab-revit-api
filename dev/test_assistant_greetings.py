# -*- coding: utf-8 -*-
"""
CPython 3 tests for the Assistant's welcome-greeting pool.

Run:  python3 dev/test_assistant_greetings.py
Exit 0 = all pass. Plain asserts, mirroring dev/test_assistant_ui.py.

The empty chat opens on one headline ("Good evening, Thanh"). It used to be a
single fixed phrase per time of day; it now comes from a pool per period in
T3Lab.extension/lib/GUI/AssistantGreetings.py, picked at random and never the
same twice in a row. These tests pin the pool's shape (enough options per
period, short enough for a narrow dock, English by default) and the no-repeat
rule, and check the chat window actually uses the module.
"""
from __future__ import unicode_literals

import io
import os
import random
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from GUI import AssistantGreetings as G  # noqa: E402  (pure Python, no clr)

DIALOG = os.path.join(LIB, 'GUI', 'T3LabAssistantDialog.py')
MODULE = os.path.join(LIB, 'GUI', 'AssistantGreetings.py')

FAILURES = []


def check(label, ok, detail=None):
    line = '  {}  {}'.format('ok  ' if ok else 'FAIL', label)
    if not ok and detail is not None:
        line += '   {!r}'.format(detail)
    print(line)
    if not ok:
        FAILURES.append(label)


def _read(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return f.read()


#: Minimum options a period's OWN pool must offer (neutral ones come on top).
MIN_OWN = {'en': 5, 'vi': 3}
MIN_NEUTRAL = {'en': 5, 'vi': 3}


def _own(lang):
    return G._VI if lang == 'vi' else G._EN


def _neutral(lang):
    return G._VI_NEUTRAL if lang == 'vi' else G._EN_NEUTRAL


def _all_templates():
    for lang in ('en', 'vi'):
        for period, pool in _own(lang).items():
            for t in pool:
                yield lang, period, t
        for t in _neutral(lang):
            yield lang, 'neutral', t


# ─── periods ──────────────────────────────────────────────────────────────────

def test_every_hour_has_a_period():
    print('[periods]')
    got = {h: G.period_for_hour(h) for h in range(24)}
    check('every hour maps to a known period',
          all(p in G.PERIODS for p in got.values()), got)
    check('every period is reachable',
          set(got.values()) == set(G.PERIODS),
          sorted(set(G.PERIODS) - set(got.values())))
    expected = {0: 'late', 4: 'late', 5: 'early', 6: 'early', 7: 'morning',
                10: 'morning', 11: 'midday', 12: 'midday', 13: 'afternoon',
                17: 'afternoon', 18: 'evening', 21: 'evening', 22: 'late',
                23: 'late'}
    wrong = {h: got[h] for h, p in expected.items() if got[h] != p}
    check('bucket boundaries are unchanged', not wrong, wrong)
    check('a bad hour never raises',
          G.period_for_hour('x') in G.PERIODS
          and G.period_for_hour(None) in G.PERIODS)


def test_every_period_has_several_options():
    print('[pool size]')
    for lang in ('en', 'vi'):
        own = _own(lang)
        check('{}: a pool for every period'.format(lang),
              set(own) == set(G.PERIODS), sorted(set(G.PERIODS) ^ set(own)))
        thin = {p: len(own.get(p, ())) for p in G.PERIODS
                if len(own.get(p, ())) < MIN_OWN[lang]}
        check('{}: every period has >= {} own phrases'.format(
            lang, MIN_OWN[lang]), not thin, thin)
        check('{}: >= {} neutral phrases'.format(lang, MIN_NEUTRAL[lang]),
              len(_neutral(lang)) >= MIN_NEUTRAL[lang], len(_neutral(lang)))
        for p in G.PERIODS:
            pool = G.templates(p, viet=(lang == 'vi'))
            if len(pool) != len(set(pool)):
                check('{}/{}: no duplicate in the combined pool'.format(lang, p),
                      False, sorted({t for t in pool if pool.count(t) > 1}))


# ─── template shape ───────────────────────────────────────────────────────────

def test_templates_are_well_formed():
    print('[template shape]')
    no_slot = [t for _, _, t in _all_templates() if t.count('{name}') != 1]
    check('every template has exactly one {name}', not no_slot, no_slot)
    too_long = [(t, len(t.replace('{name}', '')))
                for _, _, t in _all_templates()
                if len(t.replace('{name}', '')) > G.MAX_LEN]
    check('<= {} characters before the name'.format(G.MAX_LEN),
          not too_long, too_long)
    check('MAX_LEN keeps the headline short', G.MAX_LEN <= 32, G.MAX_LEN)
    stray = [t for _, _, t in _all_templates()
             if re.search(r'\{(?!name\})', t) or t != t.strip()
             or '  ' in t]
    check('no stray braces, padding or double spaces', not stray, stray)


def test_english_pool_is_english():
    """All user-facing UI text is English; the Vietnamese pool is only used
    when the user set the reply language to Vietnamese."""
    print('[language]')
    non_ascii = [t for lang, _, t in _all_templates()
                 if lang == 'en' and any(ord(c) > 127 for c in t)]
    check('English templates are plain ASCII', not non_ascii, non_ascii)
    check('English is the default',
          G.templates('morning') == G.templates('morning', viet=False))


def test_fill():
    print('[fill]')
    check('the name goes into the slot',
          G.fill("Good morning, {name}", "Thanh") == "Good morning, Thanh")
    check('surrounding blanks in the name are dropped',
          G.fill("Hi {name}, how can I help?", "  Thanh ")
          == "Hi Thanh, how can I help?")
    bad = []
    for _, _, t in _all_templates():
        out = G.fill(t, "")
        if ('{' in out or ', ?' in out or ' ?' in out or out.endswith(',')
                or '  ' in out or ' .' in out or ',.' in out or not out):
            bad.append((t, out))
    check('no name: the slot disappears cleanly', not bad, bad)
    check('no name: question keeps its mark',
          G.fill("Ready to model, {name}?", None) == "Ready to model?")


def test_base_greeting_is_unchanged():
    """The onboarding card ("Good morning!") keeps the old single phrases."""
    print('[base phrase]')
    legacy_en = {0: "Working late", 5: "Early start", 8: "Good morning",
                 12: "Good afternoon", 15: "Good afternoon",
                 19: "Good evening", 23: "Working late"}
    legacy_vi = {0: "Khuya rồi", 5: "Dậy sớm nhỉ", 8: "Chào buổi sáng",
                 12: "Chào buổi trưa", 15: "Chào buổi chiều",
                 19: "Chào buổi tối"}
    wrong = {h: G.base_greeting(h) for h, v in legacy_en.items()
             if G.base_greeting(h) != v}
    check('English base phrases match the old _time_greeting', not wrong, wrong)
    wrong = {h: G.base_greeting(h, viet=True) for h, v in legacy_vi.items()
             if G.base_greeting(h, viet=True) != v}
    check('Vietnamese base phrases match the old _time_greeting', not wrong,
          wrong)


# ─── picking ──────────────────────────────────────────────────────────────────

def test_never_the_same_twice_in_a_row():
    print('[no repeat]')
    for lang in ('en', 'vi'):
        viet = lang == 'vi'
        picker = G.GreetingPicker(random.Random(20261002))
        repeats = []
        prev = None
        # Walk the clock several times so period changes are covered too: a
        # neutral phrase lives in every pool and must not repeat across the
        # boundary either.
        for i in range(24 * 60):
            tpl = picker.pick(hour=(i // 10) % 24, viet=viet)
            if tpl == prev:
                repeats.append((i, tpl))
            prev = tpl
        check('{}: no immediate repeat over {} picks'.format(lang, 24 * 60),
              not repeats, repeats[:3])

        # Every pick comes from the pool of the hour it was asked for.
        picker = G.GreetingPicker(random.Random(7))
        stray = []
        for h in range(24):
            for _ in range(20):
                tpl = picker.pick(hour=h, viet=viet)
                if tpl not in G.templates(G.period_for_hour(h), viet):
                    stray.append((h, tpl))
        check('{}: every pick fits its hour'.format(lang), not stray, stray[:3])

        # The pool is really used, not just its first two entries.
        for p_hour in (2, 6, 9, 12, 15, 20):
            picker = G.GreetingPicker(random.Random(p_hour))
            seen = {picker.pick(hour=p_hour, viet=viet) for _ in range(400)}
            pool = set(G.templates(G.period_for_hour(p_hour), viet))
            check('{}: every phrase of the {} pool shows up'.format(
                lang, G.period_for_hour(p_hour)), seen == pool,
                sorted(pool - seen))


def test_session_picker_never_repeats():
    print('[session picker]')
    prev, repeats = None, 0
    for _ in range(300):
        tpl = G.pick_template(hour=9)
        repeats += tpl == prev
        prev = tpl
    check('pick_template never returns the previous template', repeats == 0,
          repeats)
    out = G.greeting("Thanh", hour=20)
    check('greeting() returns a filled evening/neutral phrase',
          'Thanh' in out and '{name}' not in out, out)


def test_module_is_pure_python():
    print('[module]')
    src = _read(MODULE)
    imports = re.findall(r'^\s*(?:import|from)\s+(\S+)', src, re.MULTILINE)
    heavy = [m for m in imports
             if m.split('.')[0] in ('clr', 'System', 'pyrevit', 'Autodesk')]
    check('no clr / System / pyrevit / Revit import', not heavy, heavy)


# ─── the chat window uses it ──────────────────────────────────────────────────

def _method(src, name):
    m = re.search(r'\n    def {}\(.*?(?=\n    def |\nclass |\Z)'.format(name),
                  src, re.S)
    return m.group() if m else ''


def test_dialog_uses_the_pool():
    print('[wiring]')
    src = _read(DIALOG)
    check('the dialog imports the module',
          'from GUI import AssistantGreetings as _greetings' in src)
    render = _method(src, '_render_greeting')
    check('the headline is picked from the pool',
          '_greetings.pick_template(' in render and '_greetings.fill(' in render)
    check('the old fixed "{phrase}, {name}" format is gone',
          'u"{}, {}".format(' not in render)
    check('a new phrase only on a fresh conversation / period change',
          'fresh' in render and '_greeting_key' in render)
    check('_update_welcome_greeting passes fresh through',
          'fresh=fresh' in _method(src, '_update_welcome_greeting'))
    for name in ('new_chat_clicked', 'reset_chat_clicked'):
        check('{} draws a new phrase'.format(name),
              '_update_welcome_greeting(fresh=True)' in _method(src, name))
    # _time_greeting is module-level (no 4-space indent), so slice it by hand.
    tg = src.split('\ndef _time_greeting(', 1)
    tg = tg[1].split('\ndef ', 1)[0] if len(tg) == 2 else ''
    check('the onboarding phrase delegates to base_greeting',
          '_greetings.base_greeting(' in tg)


def main():
    print('')
    for fn in (test_every_hour_has_a_period,
               test_every_period_has_several_options,
               test_templates_are_well_formed,
               test_english_pool_is_english,
               test_fill,
               test_base_greeting_is_unchanged,
               test_never_the_same_twice_in_a_row,
               test_session_picker_never_repeats,
               test_module_is_pure_python,
               test_dialog_uses_the_pool):
        fn()
        print('')

    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant greeting tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
