# -*- coding: utf-8 -*-
"""
office_text — plain text out of .docx and .xlsx for the knowledge index.

Projects advertised "PDF/DOCX/MD" as knowledge files, but the index only ever
read .txt/.md/.pdf: a BEP in Word or a drawing register in Excel was copied
into the project and then silently never searched. Both formats are a zip of
XML parts, so the standard library is enough — no Office, no COM, no
third-party package (CPython 3 inside Revit, and the same code runs headless
in the tests).

    .docx  word/document.xml — paragraphs in order, "Heading N" styles become
           markdown headings (the chunker cuts sections on them), tables become
           "cell | cell" rows.
    .xlsx  xl/sharedStrings.xml + every worksheet in workbook order, each under
           a "## Sheet: <name>" heading, one "value | value" line per row.

Both return ([(0, text)], reason) — the same shape pdf_cache gives a text file
(page 0 = no page numbers). Limits mirror the PDF path: the file size cap of
rag_processor.MAX_PDF_BYTES, and a text ceiling equal to the PDF extractor's
1500-page ceiling at ~2000 characters a page. XML parts are streamed with
iterparse and parsing stops at the ceiling, and a part whose UNCOMPRESSED size
is absurd is refused, so a zip bomb cannot pin a worker thread.

Pure Python — importable under CPython 3 for tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import os
import re
import zipfile
import xml.etree.ElementTree as ET

DOCX_EXT = '.docx'
XLSX_EXT = '.xlsx'
OFFICE_EXTS = (DOCX_EXT, XLSX_EXT)

try:
    from Intelligence.rag_processor import MAX_PDF_BYTES as MAX_OFFICE_BYTES
except Exception:                                   # pragma: no cover
    MAX_OFFICE_BYTES = 20 * 1024 * 1024
# PDF pages are capped at 1500 (pdf_cache → extract_pdf_pages_ex max_pages);
# 1500 dense pages ≈ 3M characters.
MAX_OFFICE_CHARS = 3000000
# One XML part, uncompressed. A 20 MB workbook inflates ~10x; anything far past
# that is not a document anyone wrote by hand.
MAX_PART_BYTES = 400 * 1024 * 1024

_CELL_SEP = u' | '
_HEADING_RE = re.compile(r'^heading\s*([1-6])$', re.I)
_BadZip = getattr(zipfile, 'BadZipFile', None) or zipfile.BadZipfile


def _local(tag):
    """'{ns}name' → 'name' (transitional and strict OOXML namespaces alike)."""
    if not isinstance(tag, (str, type(u''))):
        return u''
    return tag.rsplit('}', 1)[-1]


def _attr(elem, name):
    """Attribute by LOCAL name, whatever namespace prefix it carries."""
    for k, v in elem.attrib.items():
        if _local(k) == name:
            return v
    return None


class _Budget(object):
    """Running character count shared by one extraction."""

    def __init__(self, limit):
        self.limit = limit
        self.used = 0
        self.full = False

    def take(self, text):
        if self.full:
            return u''
        room = self.limit - self.used
        if len(text) >= room:
            text = text[:max(room, 0)]
            self.full = True
        self.used += len(text)
        return text


def _open_part(zf, name):
    """Open one zip member for streaming, or raise ValueError with a reason."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise ValueError(u'missing part {}'.format(name))
    if info.file_size > MAX_PART_BYTES:
        raise ValueError(u'{} is {:.0f} MB uncompressed — too large'.format(
            name, info.file_size / 1048576.0))
    return zf.open(info)


def _check_file(path, ext):
    """(zipfile, '') or (None, reason) — size cap + 'is this really OOXML'."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, u'cannot be opened'
    if size == 0:
        return None, u'empty file (0 bytes)'
    if size > MAX_OFFICE_BYTES:
        return None, u'larger than the {0} MB limit'.format(
            MAX_OFFICE_BYTES // (1024 * 1024))
    try:
        zf = zipfile.ZipFile(path)
    except (_BadZip, IOError, OSError):
        # A password-protected Office file is an OLE container, not a zip.
        return None, u'not a readable {} (password-protected or damaged)'.format(
            ext.lstrip('.').upper())
    return zf, u''


# ─── DOCX ─────────────────────────────────────────────────────────────────────

def _docx_lines(stream, budget):
    """Yield text lines of word/document.xml in reading order.

    Paragraphs nest (a text box is a w:p inside a run of another w:p), so
    paragraphs and table cells are stacks. mc:Fallback holds the legacy VML
    copy of a drawing that mc:Choice already carries — skipped, or every
    text box would be read twice.
    """
    paras = []           # stack of [runs, style] — innermost paragraph last
    rows = []            # stack: cell texts of each open table row
    cells = []           # stack: paragraph texts of each open table cell
    skip = 0             # depth inside mc:Fallback
    for event, elem in ET.iterparse(stream, events=('start', 'end')):
        name = _local(elem.tag)
        if event == 'start':
            if name == 'Fallback':
                skip += 1
            elif skip:
                pass
            elif name == 'p':
                paras.append([[], None])
            elif name == 'tr':
                rows.append([])
            elif name == 'tc':
                cells.append([])
            continue
        # ── end events ──
        if name == 'Fallback':
            skip -= 1
            elem.clear()
            continue
        if skip:
            continue
        if name == 't':
            if elem.text and paras:
                paras[-1][0].append(elem.text)
        elif name == 'tab':
            if paras:
                paras[-1][0].append(u'\t')
        elif name in ('br', 'cr'):
            if paras:
                paras[-1][0].append(u'\n')
        elif name == 'pStyle':
            if paras:
                paras[-1][1] = _attr(elem, 'val')
        elif name == 'p':
            runs, style = paras.pop() if paras else ([], None)
            text = u''.join(runs).strip()
            if text:
                if cells:
                    cells[-1].append(u' '.join(text.split()))
                else:
                    style = u'{}'.format(style or u'')
                    m = _HEADING_RE.match(style)
                    if m:
                        text = u'#' * int(m.group(1)) + u' ' + text
                    elif style.lower() == u'title':
                        text = u'# ' + text
                    yield text
            elem.clear()
        elif name == 'tc':
            texts = cells.pop() if cells else []
            if rows:
                rows[-1].append(u' '.join(texts))
            elem.clear()
        elif name == 'tr':
            row = rows.pop() if rows else []
            line = _CELL_SEP.join(c for c in row if c)
            if line:
                if cells:
                    cells[-1].append(line)           # table nested in a cell
                else:
                    yield line
            elem.clear()
        if budget.full:
            return


def extract_docx(path):
    """([(0, text)], reason) for a .docx file."""
    zf, why = _check_file(path, DOCX_EXT)
    if zf is None:
        return [], why
    budget = _Budget(MAX_OFFICE_CHARS)
    out = []
    try:
        with zf:
            with _open_part(zf, 'word/document.xml') as stream:
                for line in _docx_lines(stream, budget):
                    line = budget.take(line)
                    if line:
                        out.append(line)
                    if budget.full:
                        break
    except ValueError as ex:
        return [], u'not a Word document ({})'.format(ex)
    except ET.ParseError as ex:
        return [], u'damaged document XML ({})'.format(ex)
    except Exception as ex:
        return [], u'reader error ({})'.format(ex)
    text = u'\n'.join(out).strip()
    if not text:
        return [], u'no text (the document is empty or only has images)'
    return [(0, text)], u''


# ─── XLSX ─────────────────────────────────────────────────────────────────────

def _shared_strings(zf):
    """Shared string table (list), [] when the workbook has none."""
    try:
        stream = _open_part(zf, 'xl/sharedStrings.xml')
    except ValueError:
        return []
    out = []
    buf = []
    in_phonetic = 0
    with stream:
        for event, elem in ET.iterparse(stream, events=('start', 'end')):
            name = _local(elem.tag)
            if event == 'start':
                if name == 'si':
                    buf = []
                elif name == 'rPh':
                    in_phonetic += 1      # furigana runs are not cell text
                continue
            if name == 't' and not in_phonetic and elem.text:
                buf.append(elem.text)
            elif name == 'rPh':
                in_phonetic -= 1
            elif name == 'si':
                out.append(u''.join(buf))
                elem.clear()
    return out


def _sheet_targets(zf):
    """[(sheet name, part name)] in workbook order."""
    rels = {}
    try:
        with _open_part(zf, 'xl/_rels/workbook.xml.rels') as stream:
            for elem in ET.parse(stream).getroot():
                if _local(elem.tag) == 'Relationship':
                    target = (elem.get('Target') or u'').replace(u'\\', u'/')
                    if target.startswith(u'/'):
                        target = target.lstrip(u'/')
                    elif not target.startswith(u'xl/'):
                        target = u'xl/' + target
                    rels[elem.get('Id')] = target
    except (ValueError, ET.ParseError):
        rels = {}
    sheets = []
    try:
        with _open_part(zf, 'xl/workbook.xml') as stream:
            for elem in ET.parse(stream).getroot().iter():
                if _local(elem.tag) != 'sheet':
                    continue
                target = rels.get(_attr(elem, 'id'))
                if target:
                    sheets.append((elem.get('name') or u'Sheet', target))
    except (ValueError, ET.ParseError):
        sheets = []
    names = set(zf.namelist())
    sheets = [(n, t) for n, t in sheets if t in names]
    if sheets:
        return sheets
    # No usable workbook map — fall back to the worksheet parts by number.
    parts = [n for n in names
             if re.match(r'^xl/worksheets/sheet\d+\.xml$', n)]
    parts.sort(key=lambda n: int(re.findall(r'\d+', n)[-1]))
    return [(u'Sheet{}'.format(re.findall(r'\d+', p)[-1]), p) for p in parts]


def _fmt_number(text):
    """'12.0' → '12' so whole numbers read like they do in Excel."""
    if text and re.match(r'^-?\d+\.0+$', text):
        return text.split(u'.', 1)[0]
    return text


def _sheet_rows(stream, strings, budget):
    """Yield one 'a | b | c' line per non-empty row of a worksheet."""
    row = []
    cell_type = None
    value = None
    inline = []
    in_is = 0
    for event, elem in ET.iterparse(stream, events=('start', 'end')):
        name = _local(elem.tag)
        if event == 'start':
            if name == 'row':
                row = []
            elif name == 'c':
                cell_type = elem.get('t')
                value = None
                inline = []
            elif name == 'is':
                in_is += 1
            continue
        if name == 'v':
            value = elem.text
        elif name == 't' and in_is:
            if elem.text:
                inline.append(elem.text)
        elif name == 'is':
            in_is -= 1
        elif name == 'c':
            text = u''
            if cell_type == 's':
                try:
                    text = strings[int(value)]
                except (TypeError, ValueError, IndexError):
                    text = u''
            elif cell_type == 'inlineStr':
                text = u''.join(inline)
            elif cell_type == 'b':
                text = u'TRUE' if (value or u'').strip() == u'1' else u'FALSE'
            elif value is not None:
                text = _fmt_number(u'{}'.format(value))
            text = u' '.join(text.split())
            if text:
                row.append(text)
            elem.clear()
        elif name == 'row':
            if row:
                yield _CELL_SEP.join(row)
            elem.clear()
            if budget.full:
                return
        elif name == 'sheetData':
            elem.clear()


def extract_xlsx(path):
    """([(0, text)], reason) for a .xlsx file — every sheet, in order."""
    zf, why = _check_file(path, XLSX_EXT)
    if zf is None:
        return [], why
    budget = _Budget(MAX_OFFICE_CHARS)
    out = []
    try:
        with zf:
            strings = _shared_strings(zf)
            for sheet_name, part in _sheet_targets(zf):
                if budget.full:
                    break
                lines = []
                with _open_part(zf, part) as stream:
                    for line in _sheet_rows(stream, strings, budget):
                        line = budget.take(line)
                        if line:
                            lines.append(line)
                        if budget.full:
                            break
                if lines:
                    head = budget.take(u'## Sheet: {}'.format(sheet_name))
                    out.append(head + u'\n' + u'\n'.join(lines))
    except ValueError as ex:
        return [], u'not an Excel workbook ({})'.format(ex)
    except ET.ParseError as ex:
        return [], u'damaged workbook XML ({})'.format(ex)
    except Exception as ex:
        return [], u'reader error ({})'.format(ex)
    text = u'\n\n'.join(out).strip()
    if not text:
        return [], u'no cell values (the workbook is empty)'
    return [(0, text)], u''


def extract_pages(path):
    """Dispatch on extension: ([(0, text)], reason)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == DOCX_EXT:
        return extract_docx(path)
    if ext == XLSX_EXT:
        return extract_xlsx(path)
    return [], u'unsupported file type'
