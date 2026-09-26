# -*- coding: utf-8 -*-
"""
`analyze(text)` → one `Utterance` every consumer shares.

Before this module each consumer re-derived the same facts from the raw string
with its own private rules: `dispatcher.py` folded diacritics and matched
keywords, `nlu_engine.py` had its own `_norm`/`_expand`/`_is_viet`, script.py
had `_is_viet_text`, and the knowledge stack tokenised a third way. A word
taught to one of them taught the others nothing, and they disagreed about the
same sentence often enough to be visible in the chat (a Vietnamese message
answered in English by an English-detected reply-language).

One analysis now feeds all of them:

    utt = analyze(u"đừng xóa text note trong view này")
    utt.lang            'vi'
    utt.modality        'command'
    utt.negation        {'negated': True, 'prohibitive': True, ...}
    utt.scope           'view'
    utt.risk['level']   'destructive'
    utt.categories      ['TextNotes']
    utt.is_safe_to_act  False        ← prohibitive negation
    utt.to_prompt_hint()             ← compact block injected into the LLM prompt

`to_prompt_hint()` is the part the model sees, and it is where "better context
analysis" stops being an internal detail: the model is told, in words, that
this was a prohibition scoped to the current view over TextNotes, instead of
being left to re-derive it from a sentence in a language it handles less well
than English.

Pure Python, CPython-3 testable.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

from Intelligence.knowledge import vi_text
from Intelligence.language import entities, lang_detect, normalizer, semantics

# Reply-language pinning: below this confidence a detection is a guess, and the
# caller's previous language is the better answer. Diacritics always clear it.
MIN_LANG_CONFIDENCE = 0.35


class Utterance(object):
    """One analysed user message. Plain attributes — no lazy evaluation.

    Cheap enough to build per turn: everything here is table lookups and
    regexes over a chat message, measured in tens of microseconds, against a
    turn that costs an LLM round-trip.
    """

    def __init__(self, raw):
        self.raw = raw or u''
        # ── text forms ────────────────────────────────────────────────────
        # normalized: teencode/telex/typos repaired — ROUTING SIGNALS ONLY.
        # The model, the transcript and the saved history always get `raw`.
        self.normalized = u''
        self.folded = u''
        self.tokens = []
        # ── language ──────────────────────────────────────────────────────
        self.lang = 'unknown'
        self.lang_confidence = 0.0
        self.mixed = False
        self.has_diacritics = False
        # ── semantics ─────────────────────────────────────────────────────
        self.modality = 'statement'
        self.negation = {'negated': False, 'prohibitive': False,
                         'markers': []}
        self.scope = 'unspecified'
        self.risk = {'level': 'none', 'rank': 0, 'markers': []}
        # ── entities ──────────────────────────────────────────────────────
        self.categories = []
        self.colors = []
        self.levels = []
        self.sheets = {'numbers': [], 'prefixes': []}
        self.measures = []
        self.quantity = {'all': False, 'count': None, 'comparator': None}
        # ── structure ─────────────────────────────────────────────────────
        self.segments = []      # strong-sequencer split ("rồi", "then", ";")
        self.weak_segments = [] # "và" / "and" split — planner-gated

    # ── derived properties ───────────────────────────────────────────────

    @property
    def is_question(self):
        return self.modality == 'question'

    @property
    def is_command(self):
        return self.modality == 'command'


    @property
    def is_safe_to_act(self):
        """False when the sentence forbids the action its own verbs name.

        A prohibitive negation ("đừng xóa", "no need to delete") is the one
        case where matching a write verb must NOT license a write: the user
        said the opposite of what the keyword table read.
        """
        return not self.negation.get('prohibitive', False)

    @property
    def needs_confirmation(self):
        """True for operations Ctrl+Z cannot take back, or that publish work."""
        return self.risk.get('rank', 0) >= 1

    @property
    def is_multi_goal(self):
        return len(self.segments) > 1

    def reply_language(self, fallback='vi'):
        """The language to ANSWER in.

        `fallback` is what the caller was already using — an unknown or
        low-confidence read must never flip the session's language, which is
        what made a bare "BatchOut" switch a Vietnamese chat to English.
        """
        if self.lang == 'unknown' or self.lang_confidence < MIN_LANG_CONFIDENCE:
            return fallback
        return self.lang

    # ── prompt hint ──────────────────────────────────────────────────────

    def to_prompt_hint(self, lang=None):
        """Compact analysis block for the LLM system prompt, or '' when the
        sentence is plain enough that the model needs no help.

        Deliberately short — it rides in front of every specialist turn, and
        a paragraph of metadata would cost more prompt budget than it saves.
        Only facts the model would plausibly get WRONG are included.
        """
        out = []
        if self.negation.get('prohibitive'):
            out.append(u"- The user is telling you NOT to do the action named "
                       u"in this message. Do not perform it; confirm what you "
                       u"will skip.")
        if self.scope != 'unspecified':
            out.append(u"- Scope: {}.".format(_SCOPE_TEXT.get(
                self.scope, self.scope)))
        elif self.is_command:
            out.append(u"- Scope: NOT stated. Ask before touching anything "
                       u"wider than the current view.")
        # A question ABOUT a destructive operation ("xóa được không?") is not a
        # destructive request — warning about it there would answer a question
        # nobody asked with a confirmation prompt.
        if self.needs_confirmation and not self.is_question:
            out.append(u"- Risk: {} operation. State exactly what will change "
                       u"and get an explicit yes before executing."
                       .format(self.risk.get('level')))
        if self.categories:
            out.append(u"- Revit categories named: {}.".format(
                u", ".join(self.categories)))
        if self.colors:
            out.append(u"- Colour requested: {}.".format(
                u", ".join(self.colors)))
        if self.levels:
            out.append(u"- Levels named: {}.".format(
                u", ".join(l['label'] for l in self.levels)))
        if self.sheets.get('numbers') or self.sheets.get('prefixes'):
            bits = list(self.sheets.get('numbers') or [])
            bits += [u"prefix {}".format(p)
                     for p in (self.sheets.get('prefixes') or [])]
            out.append(u"- Sheets named: {}.".format(u", ".join(bits)))
        if self.measures:
            out.append(u"- Measurements: {}.".format(u", ".join(
                u"{}{} ({} mm)".format(m['value'], m['unit'], m['mm'])
                for m in self.measures)))
        if self.quantity.get('all'):
            out.append(u"- The user said EVERY matching element, not a sample.")
        if self.is_multi_goal:
            out.append(u"- This message contains {} separate requests: {}."
                       .format(len(self.segments),
                               u" | ".join(self.segments)))
        if self.mixed:
            out.append(u"- Mixed Vietnamese/English input; the English words "
                       u"are BIM jargon, not a language switch.")

        answer_lang = lang or self.reply_language()
        if answer_lang == 'vi':
            out.append(u"- Answer in Vietnamese.")
        elif answer_lang == 'en':
            out.append(u"- Answer in English.")

        if not out:
            return u""
        return u"## Message analysis\n" + u"\n".join(out)

    def to_dict(self):
        """Plain-dict view for logging and the graph trace."""
        return {
            'raw': self.raw,
            'normalized': self.normalized,
            'lang': self.lang,
            'lang_confidence': self.lang_confidence,
            'mixed': self.mixed,
            'modality': self.modality,
            'negated': self.negation.get('negated'),
            'prohibitive': self.negation.get('prohibitive'),
            'scope': self.scope,
            'risk': self.risk.get('level'),
            'categories': list(self.categories),
            'colors': list(self.colors),
            'levels': [l['label'] for l in self.levels],
            'sheets': dict(self.sheets),
            'segments': list(self.segments),
        }

    def __repr__(self):
        return ('<Utterance {} {} scope={} risk={} cats={}>'
                .format(self.lang, self.modality, self.scope,
                        self.risk.get('level'), self.categories))


_SCOPE_TEXT = {
    'selection': u'only the elements currently selected',
    'view':      u'only the active view',
    'sheet':     u'the named sheets',
    'level':     u'the named level(s)',
    'project':   u'the ENTIRE project — the user said so explicitly',
}


def analyze(text, normalize=True):
    """Build an `Utterance` from a raw user message.

    `normalize=False` skips teencode/typo repair — used when the caller has
    already normalised, or is analysing machine-generated text (a skill's
    boilerplate) where repair is pointless.
    """
    utt = Utterance(text)
    raw = utt.raw
    if not raw.strip():
        return utt

    utt.normalized = normalizer.normalize(raw) if normalize else raw
    work = utt.normalized
    utt.folded = normalizer.fold(work)
    utt.tokens = vi_text.tokenize(work)

    verdict = lang_detect.detect(raw)
    utt.lang = verdict['lang']
    utt.lang_confidence = verdict['confidence']
    utt.mixed = verdict['mixed']
    utt.has_diacritics = verdict['diacritics']

    # Levels come first: `detect_scope` needs to know whether a level was
    # named before it can fall back to a 'level' scope.
    utt.levels = entities.extract_levels(work)

    # Semantics run on the text that still carries diacritics. The negation and
    # imperative tables need them (see the semantics module docstring); the
    # normaliser only ever ADDS diacritics, via the teencode table, so the
    # normalised form is the better input whenever the raw one has none.
    sem_text = raw if utt.has_diacritics else work
    sem = semantics.analyze_sentence(sem_text, has_level=bool(utt.levels))
    utt.negation = sem['negation']
    utt.modality = sem['modality']
    utt.scope = sem['scope']
    utt.risk = sem['risk']
    utt.segments = sem['segments'] or [raw.strip()]
    utt.weak_segments = semantics.weak_split(raw)

    # The scope marker is consumed by the scope, not by the object list.
    ent_text = entities.mask_phrase(work, sem.get('scope_phrase') or u'')
    utt.categories = entities.extract_categories(ent_text)
    utt.colors = entities.extract_colors(ent_text)
    utt.sheets = entities.extract_sheets(ent_text)
    utt.measures = entities.extract_measures(ent_text)
    utt.quantity = entities.extract_quantity(ent_text)

    return utt


# ─── Cross-turn ellipsis ─────────────────────────────────────────────────────

# Frames that carry no verb of their own and inherit the previous turn's.
_ELLIPSIS_FRAMES_VI = (
    'con', 'the con', 'con cai', 'vay con', 'the thi', 'tuong tu',
    'lam tuong tu', 'nhu tren', 'nhu vay', 'y het', 'giong vay',
)
_ELLIPSIS_FRAMES_EN = (
    'what about', 'how about', 'and the', 'same for', 'same with',
    'do the same', 'likewise', 'and for',
)

# Above this many words the message is a request in its own right, whatever
# frame it opens with. Mirrors routing.MAX_ANSWER_WORDS, which draws the same
# line for continuation replies.
_MAX_ELLIPSIS_WORDS = 8


def resolve_ellipsis(previous, current):
    """Fill a verb-less follow-up from the previous turn.

    "thống kê tường" → "còn cửa thì sao?" means "thống kê cửa". The follow-up
    names a new object but no action, so routing it on its own words lands
    nowhere and the model loses the thread.

    Args:
        previous: the `Utterance` that steered the previous turn, or None.
        current:  the `Utterance` just analysed.

    Returns a NEW Utterance for the reconstructed request, or `current`
    unchanged when this is not an ellipsis.
    """
    if previous is None or current is None:
        return current
    if not current.raw.strip():
        return current
    # A message with its own action verb or its own question frame is a
    # complete request — never rewrite it.
    if current.is_command or current.risk.get('rank', 0) > 0:
        return current
    if len(current.folded.split()) > _MAX_ELLIPSIS_WORDS:
        return current

    padded = u' ' + current.folded + u' '
    frames = _ELLIPSIS_FRAMES_VI + _ELLIPSIS_FRAMES_EN
    if not any((u' ' + f + u' ') in padded for f in frames):
        return current
    # It must introduce something new to substitute in, otherwise there is
    # nothing to carry over and the previous turn already answered it.
    if not (current.categories or current.levels
            or current.sheets.get('numbers')
            or current.sheets.get('prefixes')):
        return current

    rebuilt = _substitute(previous, current)
    if not rebuilt or rebuilt == previous.raw:
        return current

    out = analyze(rebuilt)
    out.raw = current.raw          # the transcript still shows what was typed
    return out


def _substitute(previous, current):
    """Previous request with its object swapped for the new one.

    Word-level substitution rather than template filling: the previous raw
    sentence already carries the verb, the scope and the phrasing, so
    replacing only the category words preserves everything else the user said.
    """
    base = previous.raw
    if not base.strip():
        return None

    old_cats = previous.categories
    new_cats = current.categories
    if old_cats and new_cats:
        # Rebuild from the previous sentence with its category words dropped,
        # then append the new object — safer than a positional splice, which
        # would need alignment between folded and raw offsets per alias.
        stem = _strip_category_words(base, old_cats)
        tail = current.raw.strip().rstrip(u'?？.!')
        return u"{} {}".format(stem.strip(), tail).strip()

    # No category swap available (a level or sheet changed instead) — the
    # previous sentence plus the new qualifier is still the right request.
    return u"{} {}".format(base.strip(),
                           current.raw.strip().rstrip(u'?？.!')).strip()


def _strip_category_words(text, cats):
    """Remove the alias words that produced `cats` from `text`."""
    aliases = []
    for phrase, cat in entities._CATEGORY_BY_PHRASE.items():
        if cat in cats:
            aliases.append(phrase)
    aliases.sort(key=lambda p: (-len(p.split()), -len(p)))

    folded = vi_text.fold_diacritics(text).lower()
    keep = [True] * len(text)
    for alias in aliases:
        needle = u' ' + alias + u' '
        hay = u' ' + folded + u' '
        idx = hay.find(needle)
        while idx >= 0:
            # `hay` is offset by the leading space added above.
            start = idx
            for i in range(start, min(start + len(alias), len(text))):
                keep[i] = False
            hay = hay[:idx + 1] + u' ' * len(alias) + hay[idx + 1 + len(alias):]
            idx = hay.find(needle)
    out = u''.join(c for c, k in zip(text, keep) if k)
    return u' '.join(out.split())
