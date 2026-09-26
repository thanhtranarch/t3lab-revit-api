# -*- coding: utf-8 -*-
"""
Bilingual Revit entity extraction — Vietnamese and English words → Revit names.

The assistant's tools all speak Revit's own vocabulary ("Walls", "Doors",
"Level 2", "A-101"). The user speaks Vietnamese, English, or both in the same
sentence. Everything that translates between the two lives here, in ONE table,
so a word taught to the router is also known to the planner, the verifier and
the prompt hint.

What it recognises:

    categories  tường / wall / vách → "Walls"          (Revit category names)
    colors      đỏ / red → "red"                       (canonical color word)
    levels      tầng 3 / level 3 / L3 / tầng hầm       (ordinal or basement)
    sheets      A-101 / A101 / bản vẽ A                (sheet numbers/prefixes)
    quantities  tất cả / all / 5 cái / hơn 10          (counts + comparators)
    measures    3m / 3000mm / 2.5 m / cao 3m           (value + unit in mm)

Nothing here talks to Revit — it is a pure lexicon plus regexes, so it is
CPython-3 testable and cannot fail inside a transaction.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import re

from Intelligence.knowledge import vi_text

# ─── Categories ───────────────────────────────────────────────────────────────
# Alias → Revit category name. Longest match wins, so "cửa sổ" (window) never
# resolves as "cửa" (door) — the single most damaging mistranslation here.
#
# An alias written WITH Vietnamese diacritics is matched against the raw text;
# one written in plain ASCII is matched against the diacritic-folded text. That
# split is not cosmetic — it is what stops single-syllable aliases from eating
# ordinary Vietnamese words that happen to fold the same way:
#
#     tủ (cabinet)  vs  từ (from)     "dựng model từ ảnh" → Casework
#     đèn (light)   vs  đến (to)      "đến tầng 3"        → LightingFixtures
#     cửa (door)    vs  của (of)      "chiều cao của tường" → Doors
#     thẻ (tag)     vs  thế/the       "thế nào"           → Tags
#
# Every one of those was a live false positive. Multi-syllable aliases have no
# such collisions and stay ASCII, so diacritic-less typing still matches them.
_CATEGORY_TABLE = (
    # (canonical Revit category, (aliases...))
    ('Windows',              ('cua so', 'cuaso', 'window', 'windows',
                              'o cua', 'cua kinh', u'cửa sổ', u'ô cửa')),
    ('Doors',                ('cua di', 'cua ra vao', 'door', 'doors',
                              'cua chinh', 'cua phu', u'cửa')),
    ('Walls',                ('tuong', 'buc tuong', 'vach ngan',
                              'wall', 'walls', 'tuong chiu luc',
                              'tuong bao', 'tuong ngan', u'tường', u'vách')),
    ('Floors',               ('san nha', 'ban san', 'floor', 'floors',
                              'slab', 'slabs', 'san be tong', u'sàn')),
    ('Roofs',                ('mai nha', 'roof', 'roofs', 'mai doc', u'mái')),
    ('Ceilings',             ('tran nha', 'ceiling', 'ceilings',
                              'tran gia', u'trần')),
    ('Columns',              ('column', 'columns', 'cot be tong',
                              'cot thep', u'cột')),
    ('StructuralColumns',    ('cot ket cau', 'structural column',
                              'structural columns')),
    ('StructuralFraming',    ('beam', 'beams', 'framing',
                              'dam ngang', 'dam doc', u'dầm', u'xà')),
    ('StructuralFoundation', ('dai mong', 'foundation', 'foundations',
                              'footing', 'footings', u'móng')),
    ('Stairs',               ('cau thang', 'thang bo', 'stair', 'stairs',
                              'staircase')),
    ('Railings',             ('lan can', 'tay vin', 'railing', 'railings',
                              'handrail', 'guardrail')),
    ('Ramps',                ('duong doc', 'ramp', 'ramps', u'dốc')),
    ('Rooms',                ('can phong', 'room', 'rooms', u'phòng')),
    ('Areas',                ('dien tich', 'area', 'areas')),
    ('Furniture',            ('noi that', 'do noi that', 'furniture',
                              'ban ghe')),
    ('Casework',             ('tu bep', 'tu quan ao', 'casework', 'cabinet',
                              'cabinets', u'tủ')),
    ('PlumbingFixtures',     ('thiet bi ve sinh', 'thiet bi nuoc',
                              'plumbing fixture', 'plumbing fixtures',
                              'sanitary')),
    ('LightingFixtures',     ('thiet bi chieu sang', 'lighting',
                              'lighting fixture', 'lighting fixtures',
                              'light', 'lights', u'đèn')),
    ('MechanicalEquipment',  ('thiet bi co khi', 'thiet bi may',
                              'mechanical equipment', 'hvac')),
    ('ElectricalFixtures',   ('thiet bi dien', 'o cam', 'electrical fixture',
                              'electrical fixtures')),
    ('Ducts',                ('ong gio', 'duong ong gio', 'duct', 'ducts')),
    ('Pipes',                ('ong nuoc', 'duong ong', 'pipe',
                              'pipes', 'piping', u'ống')),
    ('CableTrays',           ('mang cap', 'thang cap', 'cable tray',
                              'cable trays')),
    ('Grids',                ('luoi truc', 'grid', 'grids',
                              'gridline', 'gridlines', u'trục', u'lưới')),
    ('Levels',               ('cao do', 'code cao do', 'level', 'levels',
                              'muc cao do')),
    ('Sheets',               ('ban ve', 'to ban ve', 'sheet', 'sheets',
                              'khung ten')),
    ('Views',                ('khung nhin', 'goc nhin', 'view', 'views',
                              'mat bang', 'mat dung', 'mat cat')),
    ('Dimensions',           ('kich thuoc', 'duong kich thuoc', 'dimension',
                              'dimensions')),
    ('TextNotes',            ('ghi chu', 'chu thich', 'text note',
                              'text notes', 'textnote')),
    ('Tags',                 ('tag', 'tags', 'ky hieu', u'thẻ', u'nhãn')),
    ('GenericModel',         ('mo hinh chung', 'generic model',
                              'generic models')),
    ('CurtainWallPanels',    ('vach kinh', 'mat dung kinh', 'curtain panel',
                              'curtain wall', 'curtain panels')),
    ('Parking',              ('bai do xe', 'cho do xe', 'parking')),
    ('Site',                 ('dia hinh', 'san nen', 'site', 'topography',
                              'topo')),
    ('Materials',            ('vat lieu', 'material', 'materials')),
)


def _split_alias_tables(table):
    """(raw_map, raw_phrases, folded_map, folded_phrases) from an alias table.

    An alias containing a Vietnamese-only letter goes to the raw matcher; the
    rest go to the folded matcher. Both lists come out sorted longest-first so
    the more specific alias always consumes its span first.
    """
    raw_map, folded_map = {}, {}
    for canonical, aliases in table:
        for alias in aliases:
            target = (raw_map if vi_text.fold_diacritics(alias) != alias
                      else folded_map)
            # First writer wins: the table is ordered so the more specific
            # category (Windows) is declared before the ambiguous one (Doors).
            target.setdefault(alias, canonical)

    def _order(keys):
        return sorted(keys, key=lambda p: (-len(p.split()), -len(p)))

    return (raw_map, _order(raw_map.keys()),
            folded_map, _order(folded_map.keys()))


(_CATEGORY_RAW_MAP, _CATEGORY_RAW_PHRASES,
 _CATEGORY_FOLDED_MAP, _CATEGORY_FOLDED_PHRASES) = \
    _split_alias_tables(_CATEGORY_TABLE)

# Kept for callers that want the whole alias→category mapping in one dict
# (analyzer._strip_category_words walks it to remove an object from a sentence).
_CATEGORY_BY_PHRASE = {}
_CATEGORY_BY_PHRASE.update(_CATEGORY_FOLDED_MAP)
_CATEGORY_BY_PHRASE.update(_CATEGORY_RAW_MAP)

# ─── Colors ───────────────────────────────────────────────────────────────────
# Same diacritic rule as the categories, and for the same reason — every
# single-syllable Vietnamese colour folds onto a common word:
#     đỏ→"do" (English do / đo=measure)   tím→"tim" (tìm=search)
#     hồng→"hong" (hỏng=broken)           đen→"den" (đến=to)
#     trắng→"trang" (trang=page)          vàng→"vang" (vắng=absent)
# The "màu X" forms carry the noun and are safe folded, so diacritic-less
# input still resolves as long as the user writes "mau do" rather than "do".
_COLOR_TABLE = (
    ('red',    ('mau do', 'red', u'đỏ')),
    # A bare "xanh" is genuinely ambiguous (green or blue). Green is the
    # reading Vietnamese speakers default to; anyone who means blue says
    # "xanh dương"/"xanh lam", and longest-match gives them blue.
    ('green',  ('xanh la', 'xanh luc', 'la cay', 'green', 'xanh')),
    ('blue',   ('xanh duong', 'xanh lam', 'xanh bien', 'blue')),
    ('yellow', ('mau vang', 'yellow', u'vàng')),
    ('orange', ('mau cam', 'orange', u'cam')),
    ('purple', ('mau tim', 'purple', 'violet', u'tím')),
    ('pink',   ('mau hong', 'pink', u'hồng')),
    ('black',  ('mau den', 'black', u'đen')),
    ('white',  ('mau trang', 'white', u'trắng')),
    ('grey',   ('mau xam', 'grey', 'gray', u'xám')),
    ('brown',  ('mau nau', 'brown', u'nâu')),
    ('cyan',   ('xanh ngoc', 'cyan')),
)

(_COLOR_RAW_MAP, _COLOR_RAW_PHRASES,
 _COLOR_FOLDED_MAP, _COLOR_FOLDED_PHRASES) = _split_alias_tables(_COLOR_TABLE)

# ─── Regexes ──────────────────────────────────────────────────────────────────

# "tầng 3", "tang 3", "level 3", "lầu 2", "L3", "tầng trệt", "tầng hầm 1"
_LEVEL_NAMED_RE = re.compile(
    r'\b(?:tang|lau|level|storey|story|floor)\s*(\d{1,3})\b')
_LEVEL_SHORT_RE = re.compile(r'\b[lL](\d{1,2})\b')
_GROUND_RE = re.compile(r'\b(?:tang\s+tret|tret|ground\s+floor|ground)\b')
_BASEMENT_RE = re.compile(
    r'\b(?:tang\s+ham|ham|basement|b)\s*(\d{1,2})?\b')

# "A-101", "A101", "TK-05", "E204b"
_SHEET_RE = re.compile(r'\b([A-Za-z]{1,3})[-\s]?(\d{2,4})([A-Za-z])?\b')
# "sheet A", "bản vẽ G", "các sheet TK".
#
# Matched CASE-SENSITIVELY on the raw text, requiring an uppercase prefix.
# Lowercase would sweep in the Vietnamese word that follows "sheet" in
# perfectly ordinary sentences — "export cái sheet này ra pdf" reported a sheet
# prefix of "NAY". Sheet numbering prefixes are uppercase in every drawing set
# this extension has ever exported, so the requirement costs nothing.
#
# The lookahead rejects the prefix of a full sheet number ("sheet A-101"),
# which `_SHEET_RE` already reports in full.
_SHEET_PREFIX_RE = re.compile(
    r'\b(?:[Ss]heets?|[Bb]ản vẽ|[Bb]an ve)\s+([A-Z]{1,3})\b(?![-\s]*\d)')

# Measures. Everything is normalised to millimetres so callers compare one unit.
_UNIT_TO_MM = {'mm': 1.0, 'cm': 10.0, 'dm': 100.0, 'm': 1000.0,
               'km': 1000000.0, 'in': 25.4, 'ft': 304.8, "'": 304.8,
               '"': 25.4}
_MEASURE_RE = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*(mm|cm|dm|km|m|in|ft|\'|")(?![a-z0-9])',
    re.IGNORECASE)

# Counts and comparators
_ALL_RE = re.compile(
    r'\b(?:tat ca|toan bo|tat ca cac|moi|all|every|entire|whole|'
    r'ca model|ca project|toan project)\b')
_COUNT_RE = re.compile(
    r'\b(\d{1,6})\s*(?:cai|con|chiec|to|pcs?|items?|elements?|nos?)\b')
_COMPARATOR_RE = re.compile(
    r'\b(lon hon|nho hon|tren|duoi|it nhat|nhieu nhat|bang|'
    r'greater than|less than|more than|fewer than|at least|at most|'
    r'above|below|over|under|equal to|equals?)\s*(\d+(?:[.,]\d+)?)')

_WS_RE = re.compile(r'\s+')


def _padded(folded):
    return u' ' + _WS_RE.sub(u' ', folded).strip() + u' '


_WORDCHAR_RE = re.compile(r'[a-z0-9]')


def _prepare(text):
    """Two index-aligned, space-padded views of `text`: with and without
    diacritics, every separator flattened to a single space character.

    Alignment is what makes the dual matcher work: a span consumed in one view
    can be blanked at the same offsets in the other, so "cửa sổ" matched by the
    raw table also stops "cua"/"so" from matching in the folded table.
    """
    raw_l = (text or u'').lower()
    folded_l = vi_text.fold_diacritics(raw_l)
    if len(folded_l) != len(raw_l):
        # Should not happen for Vietnamese (folding is 1:1), but a stray
        # locale-sensitive lowercase would desynchronise the offsets. Falling
        # back to folded-only matching is wrong-but-safe; misaligned blanking
        # would corrupt words.
        raw_l = folded_l

    raw_chars, fold_chars = [], []
    for rc, fc in zip(raw_l, folded_l):
        if _WORDCHAR_RE.match(fc):
            raw_chars.append(rc)
            fold_chars.append(fc)
        else:
            raw_chars.append(u' ')
            fold_chars.append(u' ')
    return [u' ' + u''.join(raw_chars) + u' ',
            u' ' + u''.join(fold_chars) + u' ']


def _consume(views, which, phrases, mapping, found):
    """Longest-first scan of one view; blanks each hit in BOTH views."""
    for phrase in phrases:
        needle = u' ' + phrase + u' '
        while True:
            idx = views[which].find(needle)
            if idx < 0:
                break
            value = mapping[phrase]
            if value not in found:
                found.append(value)
            start = idx + 1
            blank = u' ' * len(phrase)
            for k in (0, 1):
                views[k] = (views[k][:start] + blank
                            + views[k][start + len(phrase):])


def _extract_aliases(text, raw_phrases, raw_map, folded_phrases, folded_map):
    """Diacritic-aware aliases first, then the ASCII ones."""
    views = _prepare(text)
    found = []
    _consume(views, 0, raw_phrases, raw_map, found)
    _consume(views, 1, folded_phrases, folded_map, found)
    return found


def mask_phrase(text, folded_phrase):
    """Blank one folded phrase out of `text`, preserving length and offsets.

    Used to remove a scope marker ("trong view này") before category
    extraction, so its "view" does not also register as the Views category.
    """
    if not text or not folded_phrase:
        return text
    views = _prepare(text)
    idx = views[1].find(u' ' + folded_phrase + u' ')
    if idx < 0:
        return text
    # `views` carries one leading pad space, so view index idx (the pad before
    # the phrase) is text index idx - 1 + 1 = idx.
    chars = list(text)
    for i in range(idx, min(idx + len(folded_phrase), len(chars))):
        chars[i] = u' '
    return u''.join(chars)


def extract_categories(text):
    """Revit category names mentioned in the text, in order of appearance.

    Known limitation: a bare diacritic-less "cua" resolves to nothing, because
    it is indistinguishable from "của" (of). Write "cua di"/"cua so" or type
    the diacritics. Guessing here would tag every possessive as a Door.
    """
    return _extract_aliases(text, _CATEGORY_RAW_PHRASES, _CATEGORY_RAW_MAP,
                            _CATEGORY_FOLDED_PHRASES, _CATEGORY_FOLDED_MAP)


def extract_colors(text):
    """Canonical color words mentioned in the text."""
    return _extract_aliases(text, _COLOR_RAW_PHRASES, _COLOR_RAW_MAP,
                            _COLOR_FOLDED_PHRASES, _COLOR_FOLDED_MAP)


def extract_levels(text):
    """Level references: [{'kind','value','label'}].

    kind is 'level' (numbered storey), 'ground', or 'basement'.
    """
    folded = vi_text.fold_diacritics(text or u'').lower()
    padded = _padded(folded)
    out = []
    seen = set()

    def _add(kind, value, label):
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            out.append({'kind': kind, 'value': value, 'label': label})

    for m in _BASEMENT_RE.finditer(padded):
        # A bare "b" is only a basement next to a digit ("b1"); on its own it
        # is far more likely a stray letter or a sheet prefix.
        if m.group(0).strip() == 'b' and not m.group(1):
            continue
        num = int(m.group(1)) if m.group(1) else 1
        _add('basement', num, u'B{}'.format(num))
    if _GROUND_RE.search(padded):
        _add('ground', 0, u'Ground')
    for m in _LEVEL_NAMED_RE.finditer(padded):
        num = int(m.group(1))
        _add('level', num, u'Level {}'.format(num))
    for m in _LEVEL_SHORT_RE.finditer(text or u''):
        num = int(m.group(1))
        _add('level', num, u'Level {}'.format(num))
    return out


def extract_sheets(text):
    """Sheet references: {'numbers': [...], 'prefixes': [...]}.

    Matched on the RAW text, not the folded one — sheet numbers are case- and
    punctuation-sensitive identifiers, and folding would merge "A-101" with
    any Vietnamese word that happens to fold the same way.
    """
    raw = text or u''
    numbers = []
    for m in _SHEET_RE.finditer(raw):
        prefix, digits, suffix = m.group(1), m.group(2), m.group(3) or u''
        # A pure measurement ("3000mm") is caught by the unit suffix; a year
        # or a plain number has no letter prefix and never reaches here.
        token = m.group(0)
        if token.lower().endswith(('mm', 'cm', 'm2', 'm3')):
            continue
        numbers.append(u'{}-{}{}'.format(prefix.upper(), digits,
                                         suffix.upper()))
    prefixes = [m.group(1) for m in _SHEET_PREFIX_RE.finditer(raw)]
    return {'numbers': numbers, 'prefixes': prefixes}


def extract_measures(text):
    """Measurements as [{'value','unit','mm'}], normalised to millimetres."""
    out = []
    for m in _MEASURE_RE.finditer(text or u''):
        try:
            value = float(m.group(1).replace(u',', u'.'))
        except Exception:
            continue
        unit = m.group(2).lower()
        factor = _UNIT_TO_MM.get(unit)
        if factor is None:
            continue
        out.append({'value': value, 'unit': unit,
                    'mm': round(value * factor, 3)})
    return out


def extract_quantity(text):
    """Quantity intent: {'all', 'count', 'comparator'}.

    all         True when the user asked for everything ("tất cả", "all")
    count       explicit item count ("5 cái") or None
    comparator  ('>', 10) style tuple from "lớn hơn 10" / "at least 10"
    """
    folded = vi_text.fold_diacritics(text or u'').lower()
    padded = _padded(folded)

    count = None
    m = _COUNT_RE.search(padded)
    if m:
        try:
            count = int(m.group(1))
        except Exception:
            count = None

    comparator = None
    cm = _COMPARATOR_RE.search(padded)
    if cm:
        word = cm.group(1)
        op = '>'
        if word in ('nho hon', 'duoi', 'less than', 'fewer than', 'below',
                    'under'):
            op = '<'
        elif word in ('it nhat', 'at least'):
            op = '>='
        elif word in ('nhieu nhat', 'at most'):
            op = '<='
        elif word in ('bang', 'equal to', 'equal', 'equals'):
            op = '=='
        try:
            comparator = (op, float(cm.group(2).replace(u',', u'.')))
        except Exception:
            comparator = None

    return {'all': bool(_ALL_RE.search(padded)),
            'count': count,
            'comparator': comparator}


