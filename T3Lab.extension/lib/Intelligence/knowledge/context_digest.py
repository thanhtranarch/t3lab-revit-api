# -*- coding: utf-8 -*-
"""
ContextDigest — build a human-readable CONTEXT.md for a linked folder.

When the user links an external folder as project knowledge, the RAG index
alone is invisible: you cannot open it, review it, or hand it to a colleague.
This module walks the linked folder, reads every indexable document FROM END
TO END, and writes a single digest markdown INTO that folder (default
`<folder>/context/CONTEXT.md`) — so the folder carries its own plain-text
summary of what it contains, right next to the source files.

Full-document coverage: a PDF is cut into page-aligned windows and EVERY
window goes through the LLM extraction pass, then the per-window results are
merged back into one entry for the file. Reading only the first window would
miss exactly what a BEP/IEP appendix is wanted for — the naming tables and
workflows sit deep in the document, not on the cover page.

Nothing is dropped silently: files that could not be read (no text layer,
oversized, unsupported) are listed in their own section of the digest with
the reason, so a scanned PDF that needs OCR is visible instead of absent.

The digest is also picked up by the normal KnowledgeStore scan, so a query
that matches the summary can lead the model to the right source document.

Feedback-loop guard: the output subfolder is NEVER walked, so a rescan can
never summarise a previous digest into a new one.

Pure Python + guarded imports — importable under CPython 3 for tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import io
import json
import os
import re
import time

TEXT_EXTS = ('.txt', '.md')
PDF_EXT = '.pdf'
INDEXABLE_EXTS = TEXT_EXTS + (PDF_EXT,)

DEFAULT_OUT_SUBDIR = 'context'
DEFAULT_OUT_NAME = 'CONTEXT.md'
INDEX_NAME = 'context_index.json'   # machine-readable sidecar next to CONTEXT.md

# Assistant-written summaries that may sit inside a linked folder. Summarising
# them again feeds the model its own prose back as project evidence — the same
# self-citation guard KnowledgeStore.GENERATED_DOCS applies to the RAG index.
GENERATED_DOCS = frozenset(['PROJECT_CONTEXT.md', DEFAULT_OUT_NAME])

# Bump whenever _EXTRACT_SYSTEM / _MERGE_SYSTEM / _SUMMARY_SYSTEM change, so
# stored per-document summaries are treated as stale and regenerated. Without
# this, an incremental rescan would keep serving answers produced by the old
# prompt for every file that has not been edited since.
PROMPT_VERSION = 3

_EXCERPT_CHARS = 400        # lead excerpt kept per document (no-LLM fallback)
_MAX_FILES = 300            # hard cap so a huge share can't hang the UI thread
_MAX_BYTES = 20 * 1024 * 1024   # skip files bigger than this (rag_processor cap)
# One LLM call per window, so this constant IS the cost dial. 14k chars (~3.5k
# tokens) needed 109 calls for the 1.15M chars of the FJX appendix folder; 28k
# halves that, and every provider in llm_router handles the context (Claude and
# GPT are 128k+, and Ollama defaults to 8k tokens ≈ 32k chars). Raise further
# only with a provider that is known to keep recall over a longer input.
_LLM_INPUT_CHARS = 28000    # text budget of ONE LLM extraction call (a window)
_MAX_DOC_WINDOWS = 24       # windows per document ≈ 670k chars ≈ 300+ pages
_MAX_PDF_PAGES = 1500       # page ceiling handed to the PDF extractor
# Output budgets. These were the real cause of "information is missing": a
# 47-row discipline-code table is ~700 tokens on its own, so max_tokens=700 cut
# the Document Type table off mid-cell ("| AA | Authority" and nothing more) in
# the FJX digest. Standards documents ARE tables; the budget has to fit them.
_EXTRACT_MAX_TOKENS = 4000
_MERGE_MAX_TOKENS = 4000
_SUMMARY_MAX_TOKENS = 3000

# Input budget of the per-document merge. At 12000 chars, joining the 23 parts
# of the 176-page BIM Manual (~46k chars of already-extracted rules) discarded
# three quarters of them BEFORE the merge ran. When the parts still do not fit,
# the merge is skipped entirely rather than allowed to lose content.
#
# Ceiling comes from OllamaProvider.NUM_CTX_MAX (32768 tokens): its _num_ctx_for
# estimates prompt tokens as chars/3, so prompt+output must stay under
# ~32768 tokens or Ollama silently truncates the input. 60000 chars ≈ 20k
# tokens + 4k output leaves comfortable headroom.
_MERGE_INPUT_CHARS = 60000
_SUMMARY_INPUT_CHARS = 60000


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _clean(text, limit=_EXCERPT_CHARS):
    """Collapse whitespace and trim to `limit` chars on a word boundary."""
    words = (text or '').split()
    if not words:
        return ''
    out = ' '.join(words)
    if len(out) <= limit:
        return out
    cut = out[:limit]
    if ' ' in cut:
        cut = cut[:cut.rfind(' ')]
    return cut + '…'


def _doc_pages(path):
    """Every readable page of a document, as ([(page_no, text), ...], reason).

    Page numbers are 0 for plain text (and for a PDF the extractor could only
    byte-scan). The reason explains an empty result — it is reported verbatim
    in the digest, because "no text" alone cannot tell a scan needing OCR from
    an owner-password-protected file that decrypts fine.
    """
    try:
        from Intelligence.knowledge import pdf_cache
        return pdf_cache.get_pages(path)
    except Exception as ex:
        return [], u'reader error ({0})'.format(ex)


def _split_long(text, size):
    """Cut one oversized page into <=size pieces on a line boundary.

    A .md/.txt file is a SINGLE page, and a byte-scanned PDF collapses to one
    pseudo-page too — without this, a 200k-char file would be one window and
    everything past the provider's input limit would silently vanish, which is
    exactly the truncation this module exists to remove.
    """
    if len(text) <= size:
        return [text]
    out = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = text.rfind('\n', int(size * 0.6), size)
        if cut <= 0:
            cut = text.rfind(' ', int(size * 0.6), size)
        if cut <= 0:
            cut = size
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out


def _windows(pages, size=_LLM_INPUT_CHARS, max_windows=_MAX_DOC_WINDOWS):
    """Cut pages into LLM-sized windows, never splitting a page needlessly.

    Returns (windows, pages_used, truncated) where each window is
    {"text", "first", "last"}. Pages are packed whole; only a page that is
    itself bigger than `size` gets split (see _split_long).
    """
    out, buf, total, first, last, used = [], [], 0, None, None, 0
    flat = []
    for page_no, text in (pages or []):
        text = (text or '').strip()
        if not text:
            continue
        used += 1
        for piece in _split_long(text, size):
            flat.append((page_no, piece))

    for page_no, text in flat:
        if buf and total + len(text) > size:
            out.append({'text': '\n'.join(buf), 'first': first, 'last': last})
            buf, total, first = [], 0, None
        if first is None:
            first = page_no
        last = page_no
        buf.append(text)
        total += len(text)
    if buf:
        out.append({'text': '\n'.join(buf), 'first': first, 'last': last})
    if len(out) > max_windows:
        return out[:max_windows], used, True
    return out, used, False


# ─── LLM extraction (RAG text → the rules that actually matter) ───────────────
# A raw lead excerpt of a BEP appendix is a cover page — useless. What the
# assistant needs is the NAMING CODES, FILE FORMATS and WORKFLOWS buried in the
# middle of the document, so EVERY window of the file is passed through the
# extraction brief (map) and the partial results are merged into one entry for
# the file (reduce).

_NO_INFO = u'NO_USEFUL_INFO'

_EXTRACT_SYSTEM = (
    u"You are a BIM specialist reading project standard documents "
    u"(BEP/IEP/guideline).\n"
    u"Task: EXTRACT information that is ACTUALLY PRESENT in the text, so the "
    u"project's own standards can be applied correctly.\n"
    u"Prioritise, where the document contains them:\n"
    u"- NAMING rules (file, model, sheet, view, workset, family): the code "
    u"structure, the meaning of each field, and concrete EXAMPLES.\n"
    u"- CODE TABLES: discipline codes, company/originator codes, document type "
    u"codes, level/zone codes, revision/suitability. Keep them as "
    u"`Code — Meaning` tables.\n"
    u"- FILE FORMATS & deliverables: extensions, software versions, sheet "
    u"sizes, folder structure.\n"
    u"- WORKFLOWS: the steps, who does what, milestones, approval conditions.\n"
    u"- THRESHOLDS/PARAMETERS: LOD, clash tolerances, coordination frequency.\n"
    u"RULES:\n"
    u"- Use ONLY what is in the text. Never infer, never add outside "
    u"knowledge.\n"
    u"- Reproduce codes, symbols and numbers exactly as written.\n"
    u"- REPRODUCE CODE TABLES IN FULL. Never abbreviate a table, never write "
    u"'...' or 'and so on', never stop partway. If a table has 47 rows, give "
    u"all 47 rows. Completeness of tables matters more than brevity.\n"
    u"- NEVER GUESS what a code stands for. Give a meaning only where the "
    u"document states it. If a code appears with no explanation, list the code "
    u"with an empty meaning rather than inventing an expansion.\n"
    u"- OMIT, ENTIRELY, any category the text says nothing about. Do NOT emit "
    u"placeholder lines such as 'Not specified', 'Not mentioned', 'Not "
    u"applicable' or 'None' — a missing heading already means 'absent', and "
    u"such filler crowds out real content.\n"
    u"- The file name and folder path are NOT content. Never present them as a "
    u"naming rule, a code or a folder-structure rule.\n"
    u"- Do not invent workflows, roles or milestones. A drawing or a matrix "
    u"with no prose describes no workflow.\n"
    u"- Write in ENGLISH even when the source document is in another "
    u"language, but keep codes, identifiers and proper names verbatim.\n"
    u"- If the whole text holds nothing useful, reply with exactly one line: "
    + _NO_INFO + u"\n"
    u"- Markdown bullets and tables. No preamble, no closing remarks. Length "
    u"is not capped — be complete, not chatty."
)

_MERGE_SYSTEM = (
    u"Below are SEPARATE extracts from THE SAME document, in page order.\n"
    u"MERGE them into a single extract for that document:\n"
    u"- Group under: **Naming & codes**, **Code tables**, **File formats & "
    u"deliverables**, **Folder structure**, **Workflows**, **Thresholds & "
    u"parameters**. Drop any group with no data.\n"
    u"- Remove duplicates, but do NOT drop any code, number or example that "
    u"appeared. Merging must LOSE NOTHING: every table row present in the "
    u"input must be present in your answer. Do not summarise a table — carry "
    u"it over in full.\n"
    u"- Reproduce codes, symbols and numbers exactly. Keep page notes.\n"
    u"- Never infer or add anything beyond the given extracts, and never guess "
    u"the meaning of a code that arrived without one.\n"
    u"- Drop placeholder lines ('Not specified', 'Not mentioned', 'None') "
    u"instead of carrying them over.\n"
    u"- Write in ENGLISH; keep codes and proper names verbatim.\n"
    u"- Markdown bullets/tables. Length is not capped — completeness first. "
    u"No preamble."
)

_SUMMARY_SYSTEM = (
    u"You are a BIM Manager. Below are extracts from several standard "
    u"documents belonging to ONE project.\n"
    u"Consolidate them into a quick-reference for the whole project, grouped "
    u"under: **Naming & codes**, **File formats & deliverables**, **Folder "
    u"structure**, **Workflows**, **Thresholds & parameters**.\n"
    u"RULES: use only the given information; cite the source file name in "
    u"parentheses after each rule; where documents conflict, state both and "
    u"prefer the one with the newer version/date. Never invent and never guess "
    u"what a code stands for. Carry code tables over in full rather than "
    u"summarising them. Omit placeholder lines ('Not specified', 'None') "
    u"entirely. Write in ENGLISH, keeping codes verbatim. Markdown; length is "
    u"not capped — completeness first."
)


def _default_chat_fn():
    """LLMRouter-backed chat seam, or None when no provider is usable."""
    try:
        from Intelligence.llm_router import LLMRouter
        router = LLMRouter()
    except Exception:
        return None

    def _chat(system_prompt, user_content, max_tokens=_EXTRACT_MAX_TOKENS):
        try:
            return router.chat([], system_prompt, user_content,
                               max_tokens=max_tokens)
        except Exception:
            return None
    return _chat


# Leading markdown decoration and an optional "Label:" prefix, so the negative
# phrase can be tested on its own.
_RE_DECOR = re.compile(r'^\s*(?:[-*+•]\s*|\d+[.)]\s*|#{1,6}\s*)*')
_RE_LABEL = re.compile(r'^\*{0,2}_{0,2}[^:|\n]{0,70}_{0,2}\*{0,2}\s*:\s*')
_NEG_BODY = (
    r'(?:'
    r'not\s+(?:specified|mentioned|present|applicable|explicitly|provided|'
    r'stated|available|given|defined|included)'
    r'|none\s*(?:specified|mentioned|provided|given)?'
    r'|no\s+(?:specific|specified|information|workflow|workflows|steps|'
    r'examples|details|mention|data)'
    r'|nothing\s+(?:specified|mentioned|present)'
    r'|n/?a'
    r')'
)
# `\s*` after the decoration matters: models write `**Sheet sizes:** not
# specified`, where the colon sits INSIDE the bold markers, so stripping the
# label leaves `** not specified`.
_RE_NEGATIVE = re.compile(r'^\*{0,2}_{0,2}\s*' + _NEG_BODY + r'\b', re.I)
# Also catch the phrase trailing a short line ("... naming not mentioned").
# Anchored at the end and length-guarded so a real rule is never swallowed.
_RE_NEGATIVE_TAIL = re.compile(
    _NEG_BODY + r'\s*[.)\]]*\s*$', re.I)

# A filler line is short by nature; the length guard stops the pattern from
# eating a real rule that merely happens to open with "No specific ...".
_FILLER_MAX_CHARS = 90


def strip_filler(text):
    """Drop 'X: Not specified' padding the model adds despite instructions.

    231 such lines were counted in one FJX digest — pure noise that also eats
    the output budget the code tables needed. Removed deterministically here so
    the digest stays readable even when a local model ignores the prompt.
    Table rows (starting with '|') are never touched, so a legitimate
    'Not specified' cell inside a code table survives.
    """
    if not text:
        return text
    kept = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('|'):
            kept.append(line)
            continue
        body = _RE_LABEL.sub('', _RE_DECOR.sub('', line)).strip()
        if body and len(body) <= _FILLER_MAX_CHARS \
                and _RE_NEGATIVE.match(body):
            continue
        # trailing form: the whole (short) line ends in the negative phrase and
        # carries nothing else of substance
        whole = _RE_DECOR.sub('', line).strip()
        if whole and len(whole) <= _FILLER_MAX_CHARS \
                and _RE_NEGATIVE_TAIL.search(whole) \
                and not re.search(r'\d', whole):
            continue
        kept.append(line)
    out = u'\n'.join(kept)
    # a heading whose whole body was filler leaves a dangling title
    out = re.sub(r'\n{3,}', u'\n\n', out)
    return out.strip()


def _no_info(text):
    flat = (text or '').upper().replace(' ', '_')
    # the Vietnamese sentinel is still honoured so digests/prompts written
    # before the switch to English output keep behaving
    return _NO_INFO in flat or 'KHONG_CO_THONG_TIN' in flat


def _llm_extract(chat_fn, filename, text, part_label=u''):
    """Ask the LLM for the rules inside one chunk of a document. '' when none."""
    if not chat_fn or not text or not text.strip():
        return ''
    # The path is given only so page notes can be attributed; the system prompt
    # forbids treating it as content, because the model used to report the file
    # name itself as a "naming rule" and the folder path as a "folder structure
    # rule". Labels are English now, matching the requested output language.
    user = (u"SOURCE (reference only, not content): {0}{1}\n\n"
            u"DOCUMENT TEXT:\n{2}").format(filename, part_label, text)
    try:
        out = chat_fn(_EXTRACT_SYSTEM, user, _EXTRACT_MAX_TOKENS)
    except Exception:
        return ''
    out = (out or '').strip()
    if not out or _no_info(out):
        return ''
    out = strip_filler(out)
    return out if out else ''


def _pages_label(count):
    return u"{0} page{1}".format(count, u"" if count == 1 else u"s")


def _part_label(win):
    """`(trang 12–19)` for a real PDF window, '' when pages are unknown."""
    first, last = win.get('first'), win.get('last')
    if not first:
        return u''
    if last and last != first:
        return u" (trang {}–{})".format(first, last)
    return u" (trang {})".format(first)


def extract_document(chat_fn, rel, pages, progress_cb=None,
                     max_windows=_MAX_DOC_WINDOWS,
                     window_chars=_LLM_INPUT_CHARS):
    """Map-reduce the WHOLE document through the extraction brief.

    Every window is extracted (map); when more than one window yielded
    something, the partials are merged into a single entry (reduce). The
    merge is best-effort — if the LLM call fails the partials are kept
    verbatim under their page headings, so a failed reduce never costs
    content.

    Returns {"summary", "pages", "windows", "windows_used", "truncated"}.
    """
    wins, n_pages, truncated = _windows(pages, size=window_chars,
                                        max_windows=max_windows)
    info = {'summary': u'', 'pages': n_pages, 'windows': len(wins),
            'windows_used': 0, 'truncated': truncated,
            'merge_skipped': False}
    if not wins:
        return info

    parts = []
    for i, win in enumerate(wins, start=1):
        if progress_cb:
            try:
                progress_cb(u"{} [{}/{}]".format(
                    os.path.basename(rel), i, len(wins)))
            except Exception:
                pass
        got = _llm_extract(chat_fn, rel, win['text'], _part_label(win))
        if got:
            parts.append((_part_label(win).strip()
                          or u"part {0}".format(i), got))
    info['windows_used'] = len(parts)
    if not parts:
        return info
    if len(parts) == 1:
        info['summary'] = parts[0][1]
        return info

    joined = u"\n\n".join(u"#### {0}\n{1}".format(lbl, txt)
                          for lbl, txt in parts)
    # The merge is a READABILITY step, never a compression step. When the parts
    # do not fit its input budget, merging would silently drop whatever was
    # truncated away — the 23 parts of the BIM Manual lost three quarters of
    # their content that way. Keep the parts verbatim instead: less tidy, but
    # nothing goes missing, which is the whole point of the digest.
    if len(joined) > _MERGE_INPUT_CHARS:
        info['summary'] = joined
        info['merge_skipped'] = True
        return info

    merged = u''
    if chat_fn:
        if progress_cb:
            try:
                progress_cb(u"{0} · merging {1} parts".format(
                    os.path.basename(rel), len(parts)))
            except Exception:
                pass
        try:
            merged = (chat_fn(_MERGE_SYSTEM,
                              u"SOURCE (reference only): {0}\n\n{1}".format(
                                  rel, joined),
                              _MERGE_MAX_TOKENS) or u'').strip()
        except Exception:
            merged = u''
        if _no_info(merged):
            merged = u''
        merged = strip_filler(merged)
        # A merge that came back dramatically shorter than its input has
        # summarised the tables away rather than carrying them over. Prefer the
        # verbatim parts in that case.
        if merged and len(merged) < len(joined) * 0.45:
            info['summary'] = joined
            info['merge_skipped'] = True
            return info
    info['summary'] = merged or joined
    return info


def iter_documents(folder, out_dir=None):
    """Yield indexable file paths under `folder`, skipping the output dir."""
    skip = _norm(out_dir) if out_dir else None
    for dirpath, dirnames, filenames in os.walk(folder):
        if skip and _norm(dirpath).startswith(skip):
            dirnames[:] = []          # never descend into the digest folder
            continue
        # keep hidden/system folders out of the digest
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fname in sorted(filenames):
            if fname in GENERATED_DOCS:
                continue    # our own project summary — never re-summarise it
            if os.path.splitext(fname)[1].lower() in INDEXABLE_EXTS:
                yield os.path.join(dirpath, fname)


def _prev_entries(out_dir):
    """Per-document results from the previous run, keyed by relative path."""
    try:
        with io.open(os.path.join(out_dir, INDEX_NAME), 'r',
                     encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}, u''
    if int(data.get('prompt_version') or 0) != PROMPT_VERSION:
        return {}, u''      # prompts changed → every summary is stale
    entries = data.get('entries')
    if not isinstance(entries, dict):
        return {}, u''
    return entries, data.get('overview') or u''


def build_context_file(folder, out_subdir=DEFAULT_OUT_SUBDIR,
                       out_name=DEFAULT_OUT_NAME, max_files=_MAX_FILES,
                       progress_cb=None, use_llm=True, chat_fn=None,
                       force=False):
    """Scan `folder` and write the digest markdown inside it.

    use_llm: run each document through the LLM to pull out the naming codes,
    file formats and workflows (RAG text in → rules out). Every window of the
    file is read, not just the opening pages. Falls back to a plain lead
    excerpt per file when no provider is reachable, so the digest is always
    produced.

    INCREMENTAL by default: a document whose (mtime, size) is unchanged since
    the last run reuses its stored summary — no share read, no parse, no LLM
    call. On the FJX appendix folder a full pass is 121 LLM calls (10-60 min
    depending on provider); an unchanged re-Rescan is now seconds. Pass
    force=True to redo everything, and bump PROMPT_VERSION whenever the
    extraction prompts change so stored summaries are discarded.

    Returns {"path", "files", "skipped", "folder", "llm", "pages", "parts",
    "reused", "unreadable"} or None when the folder is unusable / nothing
    could be written.
    """
    if not folder or not os.path.isdir(folder):
        return None
    out_dir = os.path.join(folder, out_subdir)
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except Exception:
        return None

    if use_llm and chat_fn is None:
        chat_fn = _default_chat_fn()
    llm_on = bool(use_llm and chat_fn)

    prev, prev_overview = ({}, u'') if force else _prev_entries(out_dir)

    rows, dropped, fresh = [], [], {}
    n, n_llm, n_pages, n_parts, n_reused = 0, 0, 0, 0, 0
    changed = False          # any document re-read this run?
    for path in iter_documents(folder, out_dir):
        rel = os.path.relpath(path, folder)
        if n >= max_files:
            dropped.append((rel, u"over the {0}-document limit".format(
                max_files)))
            continue
        try:
            size = os.path.getsize(path)
        except Exception:
            dropped.append((rel, u"cannot be opened"))
            continue
        if size > _MAX_BYTES:
            dropped.append((rel, u"too large ({0:.1f} MB > {1} MB)".format(
                size / 1048576.0, _MAX_BYTES // 1048576)))
            continue

        # ── unchanged since last run? reuse and skip all the expensive work ──
        from Intelligence.knowledge import pdf_cache
        fp = pdf_cache.fingerprint(path)
        old = prev.get(rel)
        if old and fp and pdf_cache._same(old.get('fp'), fp):
            if old.get('unreadable'):
                dropped.append((rel, old.get('reason')
                                or u'no extractable text'))
                fresh[rel] = old
                continue
            if old.get('summary'):
                fresh[rel] = old
                rows.append({'rel': rel, 'summary': old['summary'],
                             'note': old.get('note') or u''})
                n_pages += int(old.get('pages') or 0)
                n_parts += int(old.get('parts') or 0)
                if old.get('llm'):
                    n_llm += 1
                n_reused += 1
                n += 1
                continue
        if progress_cb:
            try:
                progress_cb(os.path.basename(path))
            except Exception:
                pass

        pages, why = _doc_pages(path)
        if not pages:
            dropped.append((rel, why or u"no extractable text"))
            fresh[rel] = {'fp': fp, 'unreadable': True,
                          'reason': why or u"no extractable text"}
            continue

        summary, note, by_llm, parts = u'', u'', False, 0
        if llm_on:
            got = extract_document(chat_fn, rel, pages, progress_cb=progress_cb)
            summary = got['summary']
            if summary:
                by_llm = True
                parts = got['windows_used']
                n_llm += 1
                n_parts += parts
                note = u"{0} · {1} part(s) read{2}{3}".format(
                    _pages_label(got['pages']), got['windows_used'],
                    u" · kept per-part (merge would have lost content)"
                    if got.get('merge_skipped') else u"",
                    u" · TRUNCATED at the first {0} parts".format(
                        _MAX_DOC_WINDOWS) if got['truncated'] else u"")
        if not summary:
            # no provider, or the LLM found nothing usable — keep the file in
            # the digest with a raw lead excerpt rather than dropping it
            summary = (_clean(u'\n'.join(t for _p, t in pages[:3]))
                       or u"_(no content could be extracted)_")
            note = u"{0} · raw excerpt".format(_pages_label(len(pages)))
        # counted on BOTH paths, so the sidecar reports the real page total
        # even when no LLM provider was reachable
        n_pages += len(pages)
        rows.append({'rel': rel, 'summary': summary, 'note': note})
        fresh[rel] = {'fp': fp, 'summary': summary, 'note': note,
                      'pages': len(pages), 'parts': parts, 'llm': by_llm}
        changed = True
        n += 1

    if not changed:
        # A file added/removed changes the count; a file that FLIPPED
        # readable<->unreadable at constant count does not, yet it changes what
        # the cross-document overview should say (a doc left or entered the
        # readable corpus). Compare the readable set, not just the total, so a
        # flip still forces a rebuild instead of serving a stale summary.
        prev_unreadable  = set(r for r, v in prev.items() if v.get('unreadable'))
        fresh_unreadable = set(r for r, v in fresh.items() if v.get('unreadable'))
        if len(fresh) != len(prev) or prev_unreadable != fresh_unreadable:
            changed = True

    # consolidated cross-document view — the actual "project standard" answer.
    # Reused verbatim when nothing was re-read: its only inputs are the
    # per-document summaries, which are then byte-identical.
    overview = ''
    if llm_on and rows and not changed and prev_overview:
        overview = prev_overview
    elif llm_on and rows:
        if progress_cb:
            try:
                progress_cb('consolidating…')
            except Exception:
                pass
        # Fair share per document instead of joined[:budget]: a flat cut fed
        # the first few documents in full and the rest not at all, so the
        # consolidated summary silently ignored every file past the cutoff.
        share = max(1200, _SUMMARY_INPUT_CHARS // max(1, len(rows)))
        joined = '\n\n'.join(
            u"### {0}\n{1}".format(r['rel'], r['summary'][:share])
            for r in rows)
        try:
            overview = (chat_fn(_SUMMARY_SYSTEM,
                                joined[:_SUMMARY_INPUT_CHARS],
                                _SUMMARY_MAX_TOKENS) or '').strip()
        except Exception:
            overview = ''
        if _no_info(overview):
            overview = ''
        overview = strip_filler(overview)

    lines = [
        '# Context — {0}'.format(
            os.path.basename(folder.rstrip('\\/')) or folder),
        '',
        '> Generated automatically by T3Lab Assistant — it extracts the rules '
        '(naming, codes, file formats, workflows) from the documents in this '
        'folder so the assistant can look them up quickly. **Do not edit by '
        'hand** (a rescan overwrites this file). Always check the source '
        'document before applying anything here.',
        '',
        '- Source folder: `{0}`'.format(folder),
        '- Documents processed: **{0}**{1}{2}'.format(
            len(rows),
            ' ({0} skipped)'.format(len(dropped)) if dropped else '',
            ' · {0} unchanged, reused'.format(n_reused) if n_reused else ''),
        '- Extraction: **{0}**'.format(
            u'LLM read FULL TEXT ({0}/{1} documents · {2} pages · {3} '
            u'passes)'.format(n_llm, len(rows), n_pages, n_parts)
            if llm_on else
            u'raw excerpts, no LLM provider available (· {0} pages · read '
            u'but not summarised)'.format(n_pages)),
        '- Updated: {0}'.format(time.strftime('%Y-%m-%d %H:%M:%S')),
        '',
    ]
    if overview:
        lines.append('## ⭐ Project standards summary (consolidated)')
        lines.append('')
        lines.append(overview)
        lines.append('')
    if rows:
        lines.append('## Document index')
        lines.append('')
        for r in rows:
            lines.append('- `{0}`{1}'.format(
                r['rel'], u"  — {0}".format(r['note']) if r['note'] else u''))
        lines.append('')
        lines.append('## Detail per document')
        lines.append('')
        for r in rows:
            lines.append('### {0}'.format(r['rel']))
            if r['note']:
                lines.append('')
                lines.append('*{0}*'.format(r['note']))
            lines.append('')
            lines.append(r['summary'])
            lines.append('')
    else:
        lines.append('_No readable .pdf/.txt/.md document found in this '
                     'folder._')
        lines.append('')
    if dropped:
        # visible, not silent, and with the REAL cause: "needs a password" and
        # "image-only, needs OCR" call for completely different remedies
        lines.append('## Documents that could NOT be read')
        lines.append('')
        for rel, reason in dropped:
            lines.append('- `{0}` — {1}'.format(rel, reason))
        lines.append('')

    out_path = os.path.join(out_dir, out_name)
    try:
        with io.open(out_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines))
    except Exception:
        return None

    # machine-readable sidecar so the UI can report exact counts without
    # re-walking a (possibly remote) folder on the UI thread
    try:
        meta = {
            'files': len(rows),
            'skipped': len(dropped),
            'llm': n_llm,
            'pages': n_pages,
            'parts': n_parts,
            'reused': n_reused,
            'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'docs': [r['rel'] for r in rows],
            'unreadable': [{'file': rel, 'reason': reason}
                           for rel, reason in dropped],
            # what makes the NEXT rescan cheap: per-document fingerprints and
            # their summaries, plus the prompt version they were produced with
            'prompt_version': PROMPT_VERSION,
            'overview': overview,
            'entries': fresh,
        }
        # ensure_ascii=True + decode: json.dumps returns bytes-str on py2,
        # and io.open(encoding=...) demands unicode (same as knowledge_store)
        payload = json.dumps(meta, ensure_ascii=True, indent=1)
        if isinstance(payload, bytes):
            payload = payload.decode('ascii')
        with io.open(os.path.join(out_dir, INDEX_NAME), 'w',
                     encoding='utf-8', newline='\n') as f:
            f.write(payload)
    except Exception:
        pass
    try:
        from Intelligence.knowledge import pdf_cache
        pdf_cache.prune()
    except Exception:
        pass
    return {'path': out_path, 'files': len(rows), 'skipped': len(dropped),
            'folder': folder, 'llm': n_llm, 'pages': n_pages,
            'parts': n_parts, 'reused': n_reused, 'unreadable': list(dropped)}


def read_context_stats(folder, out_subdir=DEFAULT_OUT_SUBDIR,
                       out_name=DEFAULT_OUT_NAME):
    """Read back an EXISTING digest without re-walking the folder.

    Cheap enough for the UI thread even when `folder` is a network share:
    one small sidecar read (or one markdown read as fallback for digests
    written before the sidecar existed).

    Returns {"files", "skipped", "llm", "pages", "updated", "path", "exists"};
    exists=False when the folder has never been scanned. `pages` is 0 for
    digests written before full-document reading existed.
    """
    out_dir = os.path.join(folder or '', out_subdir)
    md_path = os.path.join(out_dir, out_name)
    stats = {'files': 0, 'skipped': 0, 'llm': 0, 'pages': 0, 'updated': '',
             'path': md_path, 'exists': False}

    idx = os.path.join(out_dir, INDEX_NAME)
    if os.path.isfile(idx):
        try:
            with io.open(idx, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stats.update({
                'files': int(data.get('files') or 0),
                'skipped': int(data.get('skipped') or 0),
                'llm': int(data.get('llm') or 0),
                'pages': int(data.get('pages') or 0),
                'updated': data.get('updated') or '',
                'exists': True,
            })
            return stats
        except Exception:
            pass

    # fallback: parse the numbers back out of CONTEXT.md
    if os.path.isfile(md_path):
        try:
            with io.open(md_path, 'r', encoding='utf-8',
                         errors='replace') as f:
                head = f.read(4000)
            stats['exists'] = True
            # Digests exist on disk in three vintages: Vietnamese headers,
            # then "LLM đọc TOÀN VĂN", now English. Match all of them so an
            # older CONTEXT.md still reports its counts instead of showing 0.
            m = re.search(r'(?:Documents processed'
                          r'|Số tài liệu đã (?:xử lý|tóm tắt))'
                          r'[^*\d]*\*\*(\d+)\*\*', head)
            if m:
                stats['files'] = int(m.group(1))
            m = re.search(r'\((\d+) skipped\)|\(bỏ qua (\d+)\)', head)
            if m:
                stats['skipped'] = int(m.group(1) or m.group(2))
            m = re.search(r'(?:LLM read FULL TEXT'
                          r'|LLM đọc [Tt][Oo][Àà][Nn] [Vv][Ăă][Nn])'
                          r' \((\d+)/', head)
            if m:
                stats['llm'] = int(m.group(1))
            m = re.search(r'· (\d+) (?:pages|trang) ·', head)
            if m:
                stats['pages'] = int(m.group(1))
            m = re.search(r'(?:Updated|Cập nhật):\s*([0-9\-: ]+)', head)
            if m:
                stats['updated'] = m.group(1).strip()
        except Exception:
            pass
    return stats


