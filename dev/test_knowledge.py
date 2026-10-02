# -*- coding: utf-8 -*-
"""
CPython 3 test harness for the T3Lab Assistant knowledge stack.

Run:  python3 dev/test_knowledge.py
Exit code 0 = all pass. No external test framework — plain asserts,
mirroring dev/audit_tools.py conventions.

Covers modules that must be importable OUTSIDE Revit (guarded clr imports):
    lib/Intelligence/knowledge/*        (vi_text, chunker, bm25, embeddings fusion)
    lib/Intelligence/agents/dispatcher  (keyword stage)
    lib/Intelligence/skills_engine      (frontmatter parsing)
    lib/Intelligence/comments/*         (pdf annots, sheet matcher)
"""
from __future__ import unicode_literals

import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

# Sandbox %APPDATA% BEFORE any config/settings import, so settings.json,
# projects/ and skills/ land in a throwaway dir instead of the real one.
import tempfile
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='t3lab_test_')

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


# ─── vi_text ──────────────────────────────────────────────────────────────────

def test_vi_text():
    print('[vi_text]')
    from Intelligence.knowledge import vi_text

    check('fold basic', vi_text.fold_diacritics('tường') == 'tuong')
    check('fold dj', vi_text.fold_diacritics('đường Đông') == 'duong Dong')
    check('fold passthrough', vi_text.fold_diacritics('Wall-101') == 'Wall-101')
    check('fold empty', vi_text.fold_diacritics('') == '')

    toks = vi_text.tokenize('Có bao nhiêu bức tường trong dự án?')
    check('tokenize vi', 'tuong' in toks and 'bao' in toks and 'nhieu' in toks, repr(toks))
    check('tokenize stopword drop', 'trong' not in toks, repr(toks))
    toks2 = vi_text.tokenize('How many walls are in the project?')
    check('tokenize en', 'walls' in toks2 and 'many' in toks2, repr(toks2))
    check('tokenize stopword en', 'the' not in toks2 and 'in' not in toks2, repr(toks2))

    s = vi_text.word_match_score(['mat', 'bang', 'tang', '01'], ['mat', 'bang', 'tang', 'mai'])
    check('word_match_score partial', 0.7 < s <= 1.0, s)
    check('word_match_score empty', vi_text.word_match_score([], ['a']) == 0.0)

    # bigram tokens (BM25 index/query use these) — unigrams preserved, adjacent
    # bigrams appended; the default (unigram-only) path is unchanged.
    bg = vi_text.tokenize('chiều cao lan can', bigrams=True)
    check('bigram keeps unigrams', 'lan' in bg and 'cao' in bg, repr(bg))
    check('bigram adjacency', 'lan_can' in bg and 'chieu_cao' in bg, repr(bg))
    check('bigram bridges stopword-collision', 'lan_can' in bg, repr(bg))
    check('default has no bigram',
          '_' not in ''.join(vi_text.tokenize('chiều cao lan can')), repr(bg))


# ─── chunker ──────────────────────────────────────────────────────────────────

def test_chunker():
    print('[chunker]')
    from Intelligence.knowledge import chunker

    words_p1 = ' '.join('w{}'.format(i) for i in range(1000))
    words_p2 = ' '.join('v{}'.format(i) for i in range(100))
    chunks = chunker.chunk_pages([(1, words_p1), (2, words_p2)],
                                 target_words=300, overlap_words=50)

    check('chunks produced', len(chunks) >= 4, len(chunks))
    check('no chunk spans pages',
          all(c['page'] in (1, 2) for c in chunks))
    check('seq unique', len(set(c['seq'] for c in chunks)) == len(chunks))
    p1 = [c for c in chunks if c['page'] == 1]
    first_words = p1[0]['text'].split()
    second_words = p1[1]['text'].split()
    check('overlap present', first_words[-50:] == second_words[:50])
    all_p1_words = set()
    for c in p1:
        all_p1_words.update(c['text'].split())
    check('no words lost page1', len(all_p1_words) == 1000, len(all_p1_words))

    tiny = chunker.chunk_text('one two three')
    check('tiny text single chunk', len(tiny) == 1 and tiny[0]['page'] == 0)
    check('empty text no chunk', chunker.chunk_text('') == [])


# ─── bm25 ─────────────────────────────────────────────────────────────────────

CORPUS = [
    ('d_aaa', [
        {'page': 1, 'seq': 0,
         'text': 'Chiều cao lan can ban công tối thiểu 1100 mm theo tiêu chuẩn an toàn.'},
        {'page': 2, 'seq': 1,
         'text': 'Cửa thoát hiểm phải mở theo chiều thoát nạn, chiều rộng tối thiểu 800 mm.'},
    ]),
    ('d_bbb', [
        {'page': 1, 'seq': 0,
         'text': 'Fire rated walls shall achieve a two hour rating at stair cores.'},
        {'page': 1, 'seq': 1,
         'text': 'Handrail height for stairs is 900 mm measured from nosing.'},
    ]),
]


def test_bm25():
    print('[bm25]')
    from Intelligence.knowledge.bm25_index import BM25Index, make_chunk_key

    idx = BM25Index()
    for doc_id, chunks in CORPUS:
        idx.add_document(doc_id, chunks)

    check('size', idx.size == 4, idx.size)

    hits = idx.search('chiều cao lan can bao nhiêu?', top_k=3)
    check('vi query hits', len(hits) >= 1)
    check('vi query top is lan can chunk',
          hits[0][0] == make_chunk_key('d_aaa', 1, 0), hits and hits[0][0])

    hits2 = idx.search('fire rating of walls', top_k=3)
    check('en query top is fire chunk',
          hits2 and hits2[0][0] == make_chunk_key('d_bbb', 1, 0),
          hits2 and hits2[0][0])

    # diacritic-free query must still match diacritic content
    hits3 = idx.search('chieu cao lan can', top_k=3)
    check('folded query matches',
          hits3 and hits3[0][0] == make_chunk_key('d_aaa', 1, 0))

    # persistence round-trip
    idx2 = BM25Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    hits4 = idx2.search('chiều cao lan can', top_k=1)
    check('round-trip search identical', hits4 and hits4[0][0] == hits[0][0])

    # allowed_docs filter restricts scoring to given documents
    hits5 = idx.search('height mm', top_k=5, allowed_docs=set(['d_bbb']))
    check('allowed_docs filter', hits5 and all(
        k.startswith('d_bbb#') for k, _ in hits5), hits5)

    idx.remove_document('d_aaa')
    check('remove_document size', idx.size == 2, idx.size)
    check('removed doc unfindable', not any(
        k.startswith('d_aaa#') for k, _ in idx.search('lan can', top_k=5)))

    # bigram precision: the chunk with the exact PHRASE must beat the one that
    # only has the words scattered — the win a unigram-only index can't make.
    pidx = BM25Index()
    pidx.add_document('d_phrase', [
        {'page': 1, 'seq': 0, 'text': 'Yêu cầu tường chịu lực dày 220 mm.'},
        {'page': 1, 'seq': 1,
         'text': 'Bức tường ngăn không chịu tải, lực gió tính riêng.'},
    ])
    ph = pidx.search('tường chịu lực', top_k=2)
    check('bigram phrase wins',
          ph and ph[0][0] == make_chunk_key('d_phrase', 1, 0), ph)


# ─── embeddings ───────────────────────────────────────────────────────────────

def test_embeddings():
    print('[embeddings]')
    from Intelligence.knowledge.embeddings import (
        OllamaEmbedder, cosine, rrf_fuse)

    check('cosine identical', abs(cosine([1.0, 2.0], [1.0, 2.0]) - 1.0) < 1e-9)
    check('cosine orthogonal', abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9)
    check('cosine mismatched len', cosine([1.0], [1.0, 2.0]) == 0.0)
    check('cosine zero vec', cosine([0.0, 0.0], [1.0, 1.0]) == 0.0)

    fused = rrf_fuse([['a', 'b', 'c'], ['b', 'a', 'd']])
    check('rrf both-listed first', fused[0] in ('a', 'b') and set(fused[:2]) == set(['a', 'b']), fused)
    check('rrf keeps all keys', set(fused) == set(['a', 'b', 'c', 'd']))
    fused_w = rrf_fuse([['a'], ['b']], weights=[2.0, 1.0])
    check('rrf weights', fused_w[0] == 'a', fused_w)

    # fake transport: /api/tags lists the model; /api/embed batches
    calls = {}

    def fake_get(url, timeout_ms=0):
        calls['get'] = url
        return json.dumps({'models': [{'name': 'nomic-embed-text:latest'}]})

    def fake_post(url, payload, headers=None, timeout_ms=0):
        calls['post'] = url
        if url.endswith('/api/embed'):
            return json.dumps({'embeddings': [[0.123456, 0.2]] * len(payload['input'])})
        return None

    emb = OllamaEmbedder(http_post_fn=fake_post, http_get_fn=fake_get,
                         host='http://x:11434')
    check('embedder available', emb.is_available() is True)
    vecs = emb.embed(['one', 'two'])
    check('batch embed', vecs is not None and len(vecs) == 2)
    check('vector rounding', vecs and vecs[0][0] == 0.1235, vecs and vecs[0][0])

    # fallback path: /api/embed missing → per-text /api/embeddings
    def fake_post2(url, payload, headers=None, timeout_ms=0):
        if url.endswith('/api/embed'):
            return None
        return json.dumps({'embedding': [0.5, 0.5]})

    emb2 = OllamaEmbedder(http_post_fn=fake_post2, http_get_fn=fake_get,
                          host='http://x:11434')
    vecs2 = emb2.embed(['one'])
    check('fallback per-text embed', vecs2 == [[0.5, 0.5]], vecs2)

    # unreachable host → unavailable, embed returns None
    def dead_get(url, timeout_ms=0):
        return None

    emb3 = OllamaEmbedder(http_post_fn=fake_post, http_get_fn=dead_get,
                          host='http://x:11434')
    check('unavailable when no host', emb3.is_available() is False)
    check('embed None when unavailable', emb3.embed(['x']) is None)


# ─── knowledge_store ──────────────────────────────────────────────────────────

def test_knowledge_store():
    print('[knowledge_store]')
    import tempfile, shutil, time as _time
    from Intelligence.knowledge.knowledge_store import KnowledgeStore

    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, 'docs')
    os.makedirs(src)
    try:
        with open(os.path.join(src, 'standard.md'), 'wb') as f:
            f.write('Chiều cao lan can ban công tối thiểu 1100 mm.\n'
                    'Cửa thoát hiểm rộng tối thiểu 800 mm.'.encode('utf-8'))
        with open(os.path.join(src, 'notes.txt'), 'wb') as f:
            f.write(b'Handrail height for stairs is 900 mm from nosing.')
        # An unsupported type is skipped by scan(). This used to be
        # skip.docx — .docx/.xlsx ARE indexed now (office_text), see
        # test_office_text below.
        with open(os.path.join(src, 'skip.dwg'), 'wb') as f:
            f.write(b'not indexable')

        store = KnowledgeStore(os.path.join(tmp, 'idx'), [src], 'test')
        r1 = store.scan()
        check('scan added 2', r1['added'] == 2, r1)

        r2 = store.scan()
        check('rescan unchanged', r2['unchanged'] == 2 and r2['added'] == 0, r2)

        hits = store.search('chiều cao lan can', top_k=3)
        check('store search hit', len(hits) >= 1)
        check('citation fields', hits and hits[0]['file'] == 'standard.md'
              and hits[0]['page'] == 0 and 'lan can' in hits[0]['text'])

        st = store.stats()
        check('stats', st['files'] == 2 and st['chunks'] == 2, st)

        # attachment indexing + reload from disk (fresh store instance)
        att = os.path.join(tmp, 'attach.txt')
        with open(att, 'wb') as f:
            f.write(b'Concrete cover for beams shall be 25 mm minimum.')
        entry = store.index_file(att)
        check('attachment indexed', entry and entry['chunks'] == 1, entry)

        store2 = KnowledgeStore(os.path.join(tmp, 'idx'), [src], 'test')
        hits2 = store2.search('concrete cover beams', top_k=2)
        check('persisted index reload', hits2 and hits2[0]['file'] == 'attach.txt')

        # file change → rescan reindexes; file removal → entry dropped
        _time.sleep(0.02)
        with open(os.path.join(src, 'standard.md'), 'ab') as f:
            f.write(b'\nExtra line about ramp slope 1:12 maximum.')
        os.utime(os.path.join(src, 'standard.md'), None)
        r3 = store2.scan()
        check('changed file reindexed', r3['updated'] == 1, r3)
        os.remove(os.path.join(src, 'notes.txt'))
        r4 = store2.scan()
        check('deleted file removed', r4['removed'] == 1, r4)

        # hybrid channel: fake embedder → embed_pending → fused search
        class FakeEmbedder(object):
            MODEL = 'fake'

            def is_available(self, recheck=False):
                return True

            def embed(self, texts):
                out = []
                for t in texts:
                    lowered = t.lower()
                    out.append([1.0, 0.0] if ('lan can' in lowered
                                              or 'chieu cao' in lowered)
                               else [0.0, 1.0])
                return out

        n_emb = store2.embed_pending(FakeEmbedder())
        check('embed_pending vectorized', n_emb >= 2, n_emb)
        st2 = store2.stats(include_vectors=True)
        check('stats vectors', st2['vectors'] == n_emb, st2)
        hits3 = store2.search('chiều cao lan can', top_k=2,
                              embedder=FakeEmbedder())
        check('hybrid search works', hits3 and hits3[0]['file'] == 'standard.md',
              hits3 and hits3[0]['file'])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── pdf_crypt + py2 byte-semantics guard ─────────────────────────────────────

def test_pdf_crypt():
    print('[pdf_crypt]')
    import hashlib, struct
    from Intelligence.knowledge import pdf_crypt as pc

    # RC4 against the RFC 6229 / classic test vector
    ct = pc.rc4(b'Key', b'Plaintext')
    check('rc4 known vector',
          ct == bytes(bytearray.fromhex('bbf316e8d940af0ad3')), repr(ct))
    check('rc4 involutive', pc.rc4(b'Key', ct) == b'Plaintext')
    check('rc4 empty key passthrough', pc.rc4(b'', b'abc') == b'abc')

    # PDF literal-string escapes: \r must become 0x0D, not 'r'. Getting this
    # wrong corrupts /O and /U, so the empty-password check rejects a valid key.
    body = br'/O (a\rb\bc\)d\\e\101f)'
    got = pc._pdf_string(body, b'O')
    check('pdf string escapes',
          got == b'a\rb\x08c)d\\e' + b'A' + b'f', repr(got))
    check('pdf string hex form',
          pc._pdf_string(br'/U <4142 43>', b'U') == b'ABC',
          repr(pc._pdf_string(br'/U <4142 43>', b'U')))

    # /CF sub-dictionary must not shadow the top-level /Length (bits vs bytes)
    enc = (br'<< /CF << /StdCF << /CFM /V2 /Length 16 /Type /CryptFilter >> >>'
           br' /Filter /Standard /Length 128 /P -3904 /R 4 /V 4 >>')
    top, cf = pc._split_cf(enc)
    check('split_cf removes nested dict', b'/CryptFilter' not in top, top)
    check('split_cf keeps top Length',
          pc._int(pc._RE_LEN, top, 0) == 128, pc._int(pc._RE_LEN, top, 0))
    check('split_cf returns cf block', b'/CFM' in cf, cf)

    # unencrypted file → no decryptor at all
    check('build None when unencrypted',
          pc.build(b'%PDF-1.4\ntrailer<</Root 1 0 R>>', lambda n: b'') is None)

    # end-to-end: derive the key for a synthetic R4/RC4 doc and round-trip it
    pad = bytes(pc._PAD)
    o_val = bytes(bytearray(range(32)))
    doc_id = b'0123456789abcdef'
    h = hashlib.md5()
    h.update(pad)
    h.update(o_val)
    h.update(struct.pack(b'<i', -3904))
    h.update(doc_id)
    key = h.digest()
    for _ in range(50):
        key = hashlib.md5(key[:16]).digest()
    key = key[:16]
    val = hashlib.md5(pad + doc_id).digest()
    val = pc.rc4(key, val)
    for i in range(1, 20):
        val = pc.rc4(bytes(bytearray((b ^ i) & 0xFF for b in bytearray(key))),
                     val)
    u_val = val + pad[:16]

    def _hexlit(b):
        return b'<' + bytearray(b).hex().encode('ascii') + b'>' \
            if hasattr(bytearray(b), 'hex') else b''

    enc_dict = (b'<< /Filter /Standard /V 4 /R 4 /Length 128 /P -3904 '
                b'/EncryptMetadata true /CF << /StdCF << /CFM /V2 '
                b'/Length 16 >> >> /O ' + _hexlit(o_val) +
                b' /U ' + _hexlit(u_val) + b' >>')
    raw = (b'%PDF-1.7\n/Encrypt 3 0 R\n/ID [<'
           + bytearray(doc_id).hex().encode('ascii') + b'> <00>]\n')
    dec = pc.build(raw, lambda n: enc_dict)
    check('build accepts empty user password',
          dec is not None and dec.usable, dec and dec.reason)
    check('derived key matches',
          dec is not None and bytes(dec.key) == key,
          dec and bytes(dec.key) != key)
    if dec and dec.usable:
        blob = b'stream payload that was encrypted' * 3
        enc_blob = dec.decrypt(blob, 7, 0)      # RC4 is symmetric
        check('per-object round-trip', dec.decrypt(enc_blob, 7, 0) == blob)
        check('wrong object number differs', dec.decrypt(enc_blob, 8, 0) != blob)

    # a wrong password must be reported, never silently mis-decrypted
    bad = pc.build(raw, lambda n: enc_dict, password=b'nope')
    check('wrong password reported',
          bad is not None and not bad.usable and 'password' in bad.reason,
          bad and bad.reason)


def test_py2_byte_semantics():
    """Guard the exact construct that made every PDF unreadable in Revit.

    `int in b'...'` is legal on py3 and a TypeError on IronPython 2.7, so a
    CPython-only test suite cannot catch it by running the code. Assert on the
    source instead — the byte-scanning modules must compare against int sets.
    """
    print('[py2_byte_semantics]')
    import io as _io
    lib = os.path.join(LIB, 'Intelligence', 'knowledge')
    pat = re.compile(r'\[[^\]]*\]\s*(?:not\s+)?in\s+b[\'"]')
    for name in ('pdf_text.py', 'pdf_crypt.py', 'pdf_cache.py'):
        path = os.path.join(lib, name)
        with _io.open(path, 'r', encoding='utf-8') as f:
            src = f.read()
        hits = []
        for i, line in enumerate(src.splitlines(), start=1):
            code = line.split('#', 1)[0]
            if pat.search(code):
                hits.append('%s:%d %s' % (name, i, code.strip()))
        check('%s has no "int in bytes" test' % name, not hits,
              ' | '.join(hits))

    # and the semantics the fix relies on
    from Intelligence.knowledge.pdf_text import _DELIMS
    buf = bytearray(b'/F1 Tf')
    check('_DELIMS holds ints', all(isinstance(d, int) for d in _DELIMS))
    check('_DELIMS matches a space', buf[3] in _DELIMS, buf[3])
    check('_DELIMS rejects a letter', buf[1] not in _DELIMS, buf[1])


# ─── pdf_cache (one extraction per file, shared by all consumers) ─────────────

def test_pdf_cache():
    print('[pdf_cache]')
    import tempfile, shutil, time as _time
    from Intelligence.knowledge import pdf_cache as pcache

    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, 'doc.md')
        with open(p, 'wb') as f:
            f.write(b'Naming code WH-ARC-01 applies to all sheets.')

        pcache.clear()
        pages1, why1 = pcache.get_pages(p)
        check('cache first read', pages1 and why1 == '', (pages1, why1))

        # a second read must NOT touch the file: make it unreadable-by-content
        # and confirm the cached text still comes back
        with open(p, 'wb') as f:
            f.write(b'')                      # now empty on disk...
        os.utime(p, (1000000, 1000000))       # ...but fingerprint changes too
        pages_new, why_new = pcache.get_pages(p)
        check('changed file re-extracted',
              not pages_new and 'empty' in why_new, (pages_new, why_new))

        # restore content, then prove the in-memory hit is used
        with open(p, 'wb') as f:
            f.write(b'Restored text with code WH-STR-02.')
        pages2, _w = pcache.get_pages(p)
        calls = {'n': 0}
        real_extract = pcache._extract

        def counting_extract(path):
            calls['n'] += 1
            return real_extract(path)

        pcache._extract = counting_extract
        try:
            pages3, _w3 = pcache.get_pages(p)
            check('unchanged file not re-extracted', calls['n'] == 0, calls['n'])
            check('cached pages identical', pages3 == pages2)

            # disk cache survives a cleared memory cache (the second consumer
            # in a fresh process — this is what knowledge_store.scan() hits)
            pcache._mem.clear()
            del pcache._mem_order[:]
            pages4, _w4 = pcache.get_pages(p)
            check('disk cache serves fresh process', calls['n'] == 0, calls['n'])
            check('disk cached pages identical', pages4 == pages2)

            # use_cache=False always re-extracts
            pcache.get_pages(p, use_cache=False)
            check('use_cache=False bypasses', calls['n'] == 1, calls['n'])
        finally:
            pcache._extract = real_extract

        # fingerprint must react to size as well as mtime
        fp_before = pcache.fingerprint(p)
        with open(p, 'ab') as f:
            f.write(b' extra')
        os.utime(p, (1000000, 1000000))
        fp_after = pcache.fingerprint(p)
        check('fingerprint tracks size', not pcache._same(fp_before, fp_after),
              (fp_before, fp_after))
        check('fingerprint missing file', pcache.fingerprint(
            os.path.join(tmp, 'nope.pdf')) is None)

        check('prune returns count', isinstance(pcache.prune(), int))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── context_digest (full-document rescan) ────────────────────────────────────

def test_context_digest():
    print('[context_digest]')
    import tempfile, shutil
    from Intelligence.knowledge import context_digest as cd

    # ── windowing: every page lands in a window, none is dropped ──────────
    pages = [(i, 'page {} '.format(i) + 'x' * 900) for i in range(1, 21)]
    wins, used, trunc = cd._windows(pages, size=3000)
    check('windows cover all pages', used == 20, used)
    check('windows not truncated', trunc is False)
    joined = ' '.join(w['text'] for w in wins)
    check('every page present in windows',
          all('page {} '.format(i) in joined for i in range(1, 21)))
    check('window page range', wins[0]['first'] == 1 and wins[-1]['last'] == 20,
          (wins[0]['first'], wins[-1]['last']))
    check('window respects size', all(len(w['text']) <= 3000 + 950
                                      for w in wins))

    # a single oversized page is split, not swallowed whole
    big = [(1, 'A' * 2000 + '\n' + 'B' * 2000 + '\n' + 'C' * 2000)]
    bwins, _u, _t = cd._windows(big, size=2500)
    check('oversized page split', len(bwins) >= 2, len(bwins))
    check('oversized page keeps tail',
          'C' * 1000 in ' '.join(w['text'] for w in bwins))

    # max_windows is a reported cap, never a silent one
    twins, _u2, ttrunc = cd._windows(pages, size=1000, max_windows=2)
    check('truncation flagged', ttrunc is True and len(twins) == 2, len(twins))

    # ── map-reduce: EVERY window is sent, partials merged ─────────────────
    seen = []

    def fake_chat(system, user, max_tokens=700):
        seen.append(user)
        if system == cd._MERGE_SYSTEM:
            # proportionate to its input, or the anti-compression guard
            # (rightly) rejects it and keeps the parts verbatim instead
            return 'MERGED ' + ('z' * int(len(user) * 0.8))
        # each window reports the marker it contains
        for i in range(1, 21):
            if 'MARK{}'.format(i) in user:
                return '- rule from MARK{}'.format(i)
        return 'KHONG_CO_THONG_TIN'

    mpages = [(i, 'MARK{} '.format(i) + 'y' * 900) for i in range(1, 11)]
    got = cd.extract_document(fake_chat, 'bep.pdf', mpages, window_chars=3000)
    n_win = len(cd._windows(mpages, size=3000)[0])
    check('all windows extracted', got['windows_used'] == n_win,
          (got['windows_used'], n_win))
    check('pages counted', got['pages'] == 10, got['pages'])
    check('merge ran on multi-part', got['summary'].startswith('MERGED')
          or got['windows'] == 1, got['summary'][:40])

    # single-window doc skips the merge call
    seen[:] = []
    one = cd.extract_document(fake_chat, 'one.pdf', [(1, 'MARK3 short')])
    check('single window no merge', one['summary'] == '- rule from MARK3'
          and len(seen) == 1, (one['summary'], len(seen)))

    # a failed merge keeps the partials instead of losing them
    def merge_dies(system, user, max_tokens=700):
        if system == cd._MERGE_SYSTEM:
            raise RuntimeError('provider down')
        return '- rule A' if 'MARK1' in user else '- rule B'

    kept = cd.extract_document(merge_dies, 'x.pdf',
                               [(1, 'MARK1 ' + 'z' * 2000),
                                (2, 'MARK2 ' + 'z' * 2000)],
                               window_chars=2500)
    check('failed merge keeps partials',
          'rule A' in kept['summary'] and 'rule B' in kept['summary'],
          kept['summary'][:60])

    # ── filler stripping (231 such lines polluted one real digest) ────────
    noisy = '\n'.join([
        '**Naming & codes**',
        '* `FJX_C_WH_ARC_SD_PJW_L1_0201_0` (real example)',
        '* **Software versions**: Not explicitly mentioned.',
        '* Sheet sizes: Not specified',
        '* Clash tolerances: Not mentioned.',
        'Not present in the text.',
        '#### Company/Originator Codes',
        'N/A',
        '* No specific examples provided in the text.',
        '**Code tables**',
        '| Code | Meaning |',
        '| --- | --- |',
        '| ARC | Architectural |',
        '| AA | Not specified |',
    ])
    clean = cd.strip_filler(noisy)
    check('filler: keeps real content',
          'FJX_C_WH_ARC_SD_PJW_L1_0201_0' in clean)
    check('filler: drops "Not explicitly mentioned"',
          'Software versions' not in clean, clean)
    check('filler: drops "Not specified"', 'Sheet sizes' not in clean, clean)
    check('filler: drops "Not mentioned"',
          'Clash tolerances' not in clean, clean)
    check('filler: drops bare "Not present in the text."',
          'Not present in the text' not in clean, clean)
    check('filler: drops N/A', not any(
        l.strip() == 'N/A' for l in clean.splitlines()), clean)
    check('filler: never touches table rows',
          '| AA | Not specified |' in clean and '| ARC | Architectural |'
          in clean, clean)
    check('filler: keeps headings', '**Code tables**' in clean)
    check('filler: empty input safe', cd.strip_filler('') == '')

    # ── the merge must never compress content away ────────────────────────
    big_parts = [(i, 'MARK{0} '.format(i) + 'q' * 20000) for i in range(1, 8)]

    def compressing_chat(system, user, max_tokens=700):
        if system == cd._MERGE_SYSTEM:
            return 'tiny summary that threw the tables away'
        return '- rule with a long table ' + 'r' * 3000

    res_big = cd.extract_document(compressing_chat, 'huge.pdf', big_parts,
                                  window_chars=20000)
    check('merge skipped when input too large',
          res_big['merge_skipped'] is True, res_big['merge_skipped'])
    check('all parts kept verbatim when merge skipped',
          all('rule with a long table' in res_big['summary']
              for _ in (0,)) and res_big['summary'].count('####') >= 2,
          res_big['summary'][:80])

    def shrinking_chat(system, user, max_tokens=700):
        if system == cd._MERGE_SYSTEM:
            return 'lost most of it'
        return '- detailed rule ' + 's' * 400

    res_shrink = cd.extract_document(shrinking_chat, 'shrink.pdf',
                                     [(1, 'a' * 900), (2, 'b' * 900)],
                                     window_chars=1000)
    check('over-compressing merge rejected',
          res_shrink['merge_skipped'] is True
          and 'lost most of it' not in res_shrink['summary'],
          res_shrink['summary'][:60])

    # a faithful merge IS accepted
    def faithful_chat(system, user, max_tokens=700):
        if system == cd._MERGE_SYSTEM:
            return 'MERGED ' + 'm' * 900
        return '- rule ' + 'n' * 400

    res_ok = cd.extract_document(faithful_chat, 'ok.pdf',
                                 [(1, 'a' * 900), (2, 'b' * 900)],
                                 window_chars=1000)
    check('faithful merge accepted',
          res_ok['summary'].startswith('MERGED')
          and res_ok['merge_skipped'] is False, res_ok['summary'][:40])

    # ── end-to-end digest over a folder ───────────────────────────────────
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, 'iep.md'), 'wb') as f:
            f.write(('MARK1 naming code WH-ARC-01\n' + 'w' * 30000
                     + '\nMARK2 deep rule LOD 350').encode('utf-8'))
        with open(os.path.join(tmp, 'empty.txt'), 'wb') as f:
            f.write(b'   ')

        res = cd.build_context_file(tmp, chat_fn=fake_chat)
        check('digest written', res and os.path.isfile(res['path']), res)
        check('digest counts doc', res and res['files'] == 1, res)
        check('digest reports unreadable', res and res['skipped'] == 1, res)
        check('digest counts pages', res and res['pages'] >= 1, res)

        with io.open(res['path'], 'r', encoding='utf-8') as f:
            md = f.read()
        check('unreadable section listed',
              'could NOT be read' in md and 'empty.txt' in md)
        check('unreadable reason is specific',
              'empty file' in md, md[md.find('could NOT be read'):][:120])
        check('digest markdown is english',
              'Documents processed' in md and 'Detail per document' in md)
        check('deep content reached the LLM',
              any('MARK2' in u for u in seen))

        stats = cd.read_context_stats(tmp)
        check('sidecar round-trip', stats['exists'] and stats['files'] == 1
              and stats['skipped'] == 1, stats)
        check('sidecar pages', stats['pages'] == res['pages'], stats)

        # rerun must not summarise its own CONTEXT.md back into itself
        docs = [os.path.basename(p) for p in cd.iter_documents(tmp)]
        check('generated docs excluded',
              'CONTEXT.md' not in docs and 'PROJECT_CONTEXT.md' not in docs,
              docs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_context_digest_incremental():
    """A rescan with nothing changed must cost zero LLM calls."""
    print('[context_digest: incremental]')
    import tempfile, shutil
    from Intelligence.knowledge import context_digest as cd
    from Intelligence.knowledge import pdf_cache as pcache

    calls = {'n': 0}

    def chat(system, user, max_tokens=700):
        calls['n'] += 1
        if system == cd._SUMMARY_SYSTEM:
            return 'OVERVIEW TEXT'
        return '- rule from ' + ('A' if 'alpha' in user else 'B')

    tmp = tempfile.mkdtemp()
    try:
        pcache.clear()
        a = os.path.join(tmp, 'a.md')
        b = os.path.join(tmp, 'b.md')
        with open(a, 'wb') as f:
            f.write(b'alpha naming code WH-ARC-01')
        with open(b, 'wb') as f:
            f.write(b'beta folder structure rule')

        r1 = cd.build_context_file(tmp, chat_fn=chat)
        first = calls['n']
        check('first pass calls the LLM', first >= 3, first)
        check('first pass reuses nothing', r1['reused'] == 0, r1['reused'])

        calls['n'] = 0
        r2 = cd.build_context_file(tmp, chat_fn=chat)
        check('unchanged rescan makes no LLM call', calls['n'] == 0, calls['n'])
        check('unchanged rescan reuses all', r2['reused'] == 2, r2['reused'])
        check('counts unchanged', (r2['files'], r2['pages'], r2['llm'])
              == (r1['files'], r1['pages'], r1['llm']),
              (r1['files'], r1['pages'], r1['llm'],
               r2['files'], r2['pages'], r2['llm']))
        with io.open(r2['path'], 'r', encoding='utf-8') as f:
            md2 = f.read()
        check('overview reused verbatim', 'OVERVIEW TEXT' in md2)
        check('reuse reported in digest', 'unchanged, reused' in md2)

        # editing ONE file must re-extract only that file
        with open(a, 'wb') as f:
            f.write(b'alpha naming code WH-ARC-01 REVISED with LOD 350')
        calls['n'] = 0
        r3 = cd.build_context_file(tmp, chat_fn=chat)
        check('edited file reprocessed', calls['n'] >= 1, calls['n'])
        check('other file still reused', r3['reused'] == 1, r3['reused'])
        check('both files still present', r3['files'] == 2, r3['files'])

        # a new file is picked up
        with open(os.path.join(tmp, 'c.md'), 'wb') as f:
            f.write(b'gamma clash tolerance 25 mm')
        r4 = cd.build_context_file(tmp, chat_fn=chat)
        check('new file indexed', r4['files'] == 3, r4['files'])
        check('existing files reused', r4['reused'] == 2, r4['reused'])

        # force=True redoes everything
        calls['n'] = 0
        r5 = cd.build_context_file(tmp, chat_fn=chat, force=True)
        check('force reuses nothing', r5['reused'] == 0, r5['reused'])
        check('force calls the LLM again', calls['n'] >= 3, calls['n'])

        # a PROMPT_VERSION bump must invalidate stored summaries
        old_v = cd.PROMPT_VERSION
        try:
            cd.PROMPT_VERSION = old_v + 1
            calls['n'] = 0
            r6 = cd.build_context_file(tmp, chat_fn=chat)
            check('prompt bump invalidates cache', r6['reused'] == 0,
                  r6['reused'])
            check('prompt bump re-runs the LLM', calls['n'] >= 3, calls['n'])
        finally:
            cd.PROMPT_VERSION = old_v

        # an unreadable file stays reported across an incremental rescan
        with open(os.path.join(tmp, 'blank.txt'), 'wb') as f:
            f.write(b'   ')
        r7 = cd.build_context_file(tmp, chat_fn=chat)
        r8 = cd.build_context_file(tmp, chat_fn=chat)
        check('unreadable persists when reused',
              r7['skipped'] == 1 and r8['skipped'] == 1,
              (r7['skipped'], r8['skipped']))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_context_digest_readability_flip():
    """A doc that flips readable->unreadable at constant count must rebuild the
    cross-document overview. The unreadable re-read branch never sets `changed`
    and the file count is unchanged, so before the readable-set gate the stale
    overview (still built from the doc's old summary) was served verbatim."""
    print('[context_digest: readability flip rebuilds overview]')
    import tempfile, shutil
    from Intelligence.knowledge import context_digest as cd
    from Intelligence.knowledge import pdf_cache as pcache

    calls = {'overview': 0}

    def chat(system, user, max_tokens=700):
        if system == cd._SUMMARY_SYSTEM:
            calls['overview'] += 1
            return 'OVERVIEW TEXT'
        return '- rule from ' + ('A' if 'alpha' in user else 'B')

    tmp = tempfile.mkdtemp()
    try:
        pcache.clear()
        a = os.path.join(tmp, 'a.md')
        b = os.path.join(tmp, 'b.md')
        with open(a, 'wb') as f:
            f.write(b'alpha naming code WH-ARC-01')
        with open(b, 'wb') as f:
            f.write(b'beta folder structure rule')

        r1 = cd.build_context_file(tmp, chat_fn=chat)
        check('both readable on first pass', r1['skipped'] == 0, r1['skipped'])

        # A becomes unreadable (whitespace only). Its fp changes so it IS
        # re-read, but the count stays 2 and the unreadable branch sets no
        # `changed` flag — the exact case the count-only gate missed.
        calls['overview'] = 0
        with open(a, 'wb') as f:
            f.write(b'   ')
        r2 = cd.build_context_file(tmp, chat_fn=chat)
        check('A now unreadable', r2['skipped'] == 1, r2['skipped'])
        check('overview REBUILT after the flip (not served stale)',
              calls['overview'] == 1, calls['overview'])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── dispatcher (keyword stage) ───────────────────────────────────────────────

def test_dispatcher():
    print('[dispatcher]')
    from Intelligence.agents.dispatcher import AgentDispatcher

    d = AgentDispatcher()

    def label(text, **kw):
        return d.classify(text, allow_llm=False, **kw)['specialist']

    check('vi count → revit_data',
          label('Có bao nhiêu bức tường trong dự án?') == 'revit_data')
    check('en count → revit_data',
          label('how many walls are there') == 'revit_data')
    check('liet ke → revit_data',
          label('liệt kê các sheet') == 'revit_data')
    check('rename → revit_action',
          label('đổi tên sheet A-101 thành A-102') == 'revit_action')
    check('delete → revit_action',
          label('xóa các text note trong view này') == 'revit_action')
    check('export → export',
          label('xuất pdf toàn bộ sheet') == 'export')
    check('standard question → knowledge',
          label('tiêu chuẩn chiều cao lan can là bao nhiêu?') == 'knowledge')
    check('doc question → knowledge',
          label('trong tài liệu có nói về cấp chống cháy không') == 'knowledge')
    check('action beats knowledge',
          label('sửa chiều cao lan can theo tiêu chuẩn') == 'revit_action')
    check('to do (color) → revit_action',
          label('tô đỏ tường') == 'revit_action')
    check('boi xanh → revit_action',
          label('bôi xanh các cột tầng 2') == 'revit_action')
    check('en to-do not action', label('tell me what to do') == 'general')
    check('cmt → comment',
          label('hoàn thiện các cmt trong bản vẽ') == 'comment')
    check('bluebeam → comment',
          label('xử lý markup bluebeam') == 'comment')
    check('annotated pdf attach → comment',
          d.classify('xem giúp file này', attached_pdf_annotated=True,
                     allow_llm=False)['specialist'] == 'comment')
    check('greeting → general', label('chào bạn, khỏe không?') == 'general')
    check('ban ve not draw-verb',
          label('bản vẽ này thuộc model nào') == 'general')
    check('empty → general', label('') == 'general')

    # LLM stage with a fake provider
    class FakeProvider(object):
        def chat(self, messages, system, user, max_tokens=60, **kw):
            return '{"label": "revit_data", "skill": null}'

    r = d.classify('mấy cái đó nằm đâu', provider=FakeProvider(),
                   allow_llm=True)
    check('llm stage used for ambiguous',
          r['specialist'] == 'revit_data' and r['source'] == 'llm', r)

    class BadProvider(object):
        def chat(self, *a, **kw):
            return 'not json at all'

    r2 = d.classify('mấy cái đó nằm đâu', provider=BadProvider(),
                    allow_llm=True)
    check('bad llm → general default',
          r2['specialist'] == 'general' and r2['source'] == 'default', r2)


# ─── specialists ──────────────────────────────────────────────────────────────

def test_specialists():
    print('[specialists]')
    from Intelligence.agents.specialists import (
        get_spec, SPECIALISTS, READ_TOOLS, ACTION_TOOLS)

    check('registry names', set(SPECIALISTS.keys()) ==
          set(['general', 'revit_data', 'revit_action', 'knowledge',
               'comment', 'multi_doc', 'modeling', 'qa_check', 'export']))
    check('unknown → general', get_spec('nope').name == 'general')

    data = get_spec('revit_data')
    check('data read-only', not data.allows_writes and not data.use_launcher)
    check('data budget', data.max_iterations == 6)
    check('data subset all providers',
          data.tools_for(local=False) == READ_TOOLS and
          data.tools_for(local=True) == READ_TOOLS)
    check('data has no write tools',
          'delete_element' not in READ_TOOLS and 'set_parameter' not in READ_TOOLS)

    act = get_spec('revit_action')
    check('action local subset', act.tools_for(local=True) == ACTION_TOOLS)
    check('action cloud full', act.tools_for(local=False) is None)
    check('action has writes',
          'set_parameter' in ACTION_TOOLS and 'export_dwg' in ACTION_TOOLS)

    gen = get_spec('general')
    check('general default tools', gen.tools_for(local=True) is None)

    # prompt building must not need Revit — uses agent_loop's builder
    from Intelligence.agents.specialists import build_specialist_prompt
    p = build_specialist_prompt(data,
                                project_instructions='PROJ-RULE',
                                skills_block='## Active skill: X',
                                local=True)
    check('prompt has role', 'DATA specialist' in p)
    check('prompt has few-shot (local)', 'Examples' in p)
    check('prompt has project rules', 'PROJ-RULE' in p)
    check('prompt has skills block', 'Active skill' in p)
    p2 = build_specialist_prompt(data, local=False)
    check('no few-shot on cloud', 'Examples' not in p2)

    # The specialist prompt must stay STATIC: live Revit state used to be
    # interpolated in here, which changed the system block on every turn and
    # cost the prompt cache every hit. Passing a context is now inert.
    p3 = build_specialist_prompt(data, 'CTX-HERE',
                                 project_instructions='PROJ-RULE',
                                 skills_block='## Active skill: X',
                                 local=True)
    check('live context never lands in the system prompt',
          'CTX-HERE' not in p3)
    check('same static prompt whatever the context', p3 == p)


# ─── skills engine ────────────────────────────────────────────────────────────

def test_skills():
    print('[skills]')
    from Intelligence.skills_engine import (
        parse_frontmatter, SkillsEngine, build_skills_block)

    meta, body = parse_frontmatter(
        '---\n'
        'name: my-skill\n'
        'description: Mot skill thu nghiem\n'
        'triggers: ten sheet, doi ten sheet\n'
        'agents: [revit_action, general]\n'
        'tools: rename_element\n'
        '---\n'
        '# Body here\nRule 1.')
    check('fm name', meta.get('name') == 'my-skill')
    check('fm triggers list', meta.get('triggers') == ['ten sheet', 'doi ten sheet'])
    check('fm bracket list', meta.get('agents') == ['revit_action', 'general'])
    check('fm body', body.startswith('# Body here'))

    m2, b2 = parse_frontmatter('no frontmatter at all')
    check('no fence passthrough', m2 == {} and b2 == 'no frontmatter at all')

    # engine over the real built-in skills dir
    engine = SkillsEngine()
    n = engine.scan()
    check('builtin skills found', n >= 3, n)
    ids = [s['id'] for s in engine.get_catalog()]
    check('starter skills present',
          'sheet-naming-standard' in ids and 'qa-checklist' in ids
          and 'comment-resolution-playbook' in ids, ids)

    hits = engine.match('đổi tên sheet A-101 giúp mình')
    check('trigger match (folded)', 'sheet-naming-standard' in hits, hits)
    hits2 = engine.match('xử lý markup bluebeam')
    check('comment skill match', 'comment-resolution-playbook' in hits2, hits2)
    check('no match', engine.match('chào bạn') == [])

    # ── ranking ───────────────────────────────────────────────────────────
    # match() used to iterate `sorted(self._skills)` and the caller took
    # element 0, so which playbook reached the model was decided by the
    # alphabet. A sheet-naming question matches both `iso19650-naming` and
    # `sheet-naming-standard`; the old code always answered with the former
    # purely because "i" < "s".
    q = 'đặt tên sheet theo chuẩn ISO 19650'
    ranked = engine.match(q)
    check('both naming skills match', set(['iso19650-naming',
                                           'sheet-naming-standard'])
          <= set(ranked), ranked)
    check('the more specific skill wins, not the alphabetical one',
          ranked[0] == 'sheet-naming-standard', ranked)
    check('ranking is not alphabetical', ranked != sorted(ranked), ranked)

    scored = engine.match_scored(q)
    check('scores descend',
          all(scored[i][1] >= scored[i + 1][1] for i in range(len(scored) - 1)),
          scored)
    check('match() and match_scored() agree',
          [s for s, _ in scored] == ranked)
    check('no match scores nothing', engine.match_scored('chào bạn') == [])
    check('empty text scores nothing', engine.match_scored('') == [])

    # A longer trigger phrase is stronger evidence than a bare word.
    long_hit = engine.match_scored('hoàn thiện cmt trên bản vẽ')
    bare_hit = engine.match_scored('cmt')
    check('phrase beats bare word',
          long_hit and bare_hit
          and long_hit[0][0] == bare_hit[0][0]
          and long_hit[0][1] > bare_hit[0][1], (long_hit, bare_hit))

    body = engine.get_body('sheet-naming-standard')
    check('body loaded', 'Sheet Number' in body or 'sheet' in body.lower())

    check('filter by agent', engine.filter_for_specialist(
        ['sheet-naming-standard'], 'revit_action') == ['sheet-naming-standard'])
    check('filter blocks wrong agent', engine.filter_for_specialist(
        ['qa-checklist'], 'comment') == [])
    check('skill tools', 'rename_element' in
          engine.tools_for('sheet-naming-standard'))

    block = build_skills_block(['qa-checklist'])
    check('skills block built', 'Active skill' in block and 'QA' in block, block[:60])
    check('empty block', build_skills_block([]) == '')

    # ── /slash specialist resolution (regression: a bare "/skill-id" was
    #    classified from its own boilerplate — the word "MODIFY" matched the
    #    action-verb table — so a reference playbook got the revit_action
    #    role, write tools and the "tô đỏ tường" few-shot, and the local
    #    model replayed the previous turn's colour override) ───────────────
    check('specialist keeps declared choice',
          engine.specialist_for('warning-triage', 'revit_data') == 'revit_data')
    check('specialist rejects undeclared choice',
          engine.specialist_for('iso19650-naming', 'revit_action') == 'general',
          engine.specialist_for('iso19650-naming', 'revit_action'))
    check('specialist falls back to first agent',
          engine.specialist_for('qa-checklist', None) == 'revit_data')
    check('specialist unknown skill', engine.specialist_for('nope', 'general') is None)
    check('agents_for', 'knowledge' in engine.agents_for('iso19650-naming'))

    check('reference skill (no tools)', engine.is_reference_skill('iso19650-naming'))
    check('reference skill bep', engine.is_reference_skill('bep-guideline'))
    check('tool skill is not reference',
          not engine.is_reference_skill('warning-triage'))
    check('unknown skill is not reference', not engine.is_reference_skill('nope'))


# ─── knowledge_agent ──────────────────────────────────────────────────────────

def test_query_builder():
    """Retrieval ran on the raw message, so a follow-up that carries none of
    the words that found the document ("còn cầu thang thì sao?") searched the
    whole corpus for two syllables and came back with noise."""
    print('[query_builder]')
    from Intelligence.knowledge.query_builder import (build_retrieval_query,
                                                      is_followup,
                                                      salient_terms)

    hist = [
        {'role': 'user',
         'content': 'chiều cao lan can ban công theo tiêu chuẩn là bao nhiêu?'},
        {'role': 'assistant',
         'content': 'Theo TCVN, lan can ban công tối thiểu 1.1 m [1]'},
    ]

    check('vietnamese follow-up marker', is_followup('còn cầu thang thì sao?'))
    check('english follow-up marker', is_followup('what about stairs?'))
    check('bare anaphora', is_followup('nó là gì'))
    check('a self-contained question is not a follow-up',
          not is_followup('kiểm tra toàn bộ sheet trong dự án'))
    check('a long message carries its own subject',
          not is_followup('chiều cao lan can cho nhà công nghiệp có khác '
                          'không, và áp dụng cho tầng nào'))
    check('empty is not a follow-up', not is_followup(''))

    q = build_retrieval_query('còn cầu thang thì sao?', hist)
    check('the user words come first and survive intact',
          q.startswith('còn cầu thang thì sao?'), q)
    check('the previous subject is carried in',
          'lan' in q and 'chieu' in q, q)
    check('question scaffolding is not carried',
          ' bao ' not in ' ' + q and ' nhieu ' not in ' ' + q, q)

    check('a self-contained question is left alone',
          build_retrieval_query('kiểm tra toàn bộ sheet trong dự án', hist)
          == 'kiểm tra toàn bộ sheet trong dự án')
    check('no history changes nothing',
          build_retrieval_query('còn cầu thang thì sao?', None)
          == 'còn cầu thang thì sao?')
    check('empty stays empty', build_retrieval_query('', hist) == '')

    # Block-list content (a vision turn) must not crash the miner.
    blocks = [{'role': 'user',
               'content': [{'type': 'text', 'text': 'tiêu chuẩn lan can'},
                           {'type': 'image', 'source': {}}]}]
    check('block-list history is mined, not crashed',
          'lan' in build_retrieval_query('còn cầu thang thì sao?', blocks))

    terms = salient_terms('chiều cao lan can ban công theo tiêu chuẩn')
    check('terms keep appearance order', terms[0] == 'chieu', terms)
    check('terms are de-duplicated', len(terms) == len(set(terms)), terms)
    check('exclusions honoured',
          'chieu' not in salient_terms('chiều cao lan can',
                                       exclude={'chieu'}))


def test_rerank_diversity():
    """search() truncated to top_k straight off the ranking, so three chunks
    of one page could take the entire budget the model ever sees."""
    print('[rerank]')
    from Intelligence.knowledge.rerank import diversify

    hits = [
        {'k': 'a#1', 'd': 'a', 't': 'lan can ban cong cao 1.1 m'},
        {'k': 'a#2', 'd': 'a', 't': 'lan can ban cong toi thieu 1.1 met'},
        {'k': 'a#3', 'd': 'a', 't': 'cau thang lan can 0.9 m'},
        {'k': 'b#1', 'd': 'b', 't': 'cua di rong 0.8 m theo tieu chuan'},
        {'k': 'c#1', 'd': 'c', 't': 'cau thang bo chieu rong toi thieu'},
    ]
    doc = lambda h: h['d']          # noqa: E731
    txt = lambda h: h['t']          # noqa: E731

    top3 = [h['k'] for h in diversify(hits, 3, doc, txt)]
    check('one document cannot own the whole budget',
          len([k for k in top3 if k.startswith('a#')]) <= 2, top3)
    check('a second document gets in', 'b#1' in top3, top3)
    check('ranking order is otherwise preserved', top3[0] == 'a#1', top3)

    check('the cap is a preference, not a starvation rule',
          len(diversify([{'k': 'a#%d' % i, 'd': 'a', 't': 'text %d' % i}
                         for i in range(5)], 4, doc, txt)) == 4)
    check('backfill keeps everything reachable',
          len(diversify(hits, 9, doc, txt)) == 5)

    check('no extractors = plain truncation',
          [h['k'] for h in diversify(hits, 3)] == ['a#1', 'a#2', 'a#3'])
    check('empty in, empty out', diversify([], 3) == [])
    check('top_k 0 returns nothing', diversify(hits, 0) == [])
    check('a single candidate is returned as-is',
          len(diversify(hits[:1], 3, doc, txt)) == 1)

    # A near-duplicate of an already-selected chunk adds nothing.
    dup = [{'k': 'x#1', 'd': 'x', 't': 'chieu cao lan can toi thieu mot met'},
           {'k': 'y#1', 'd': 'y', 't': 'chieu cao lan can toi thieu mot met'},
           {'k': 'z#1', 'd': 'z', 't': 'chieu rong cua di hai met'}]
    picked = [h['k'] for h in diversify(dup, 2, doc, txt)]
    check('a near-duplicate loses to new information',
          picked == ['x#1', 'z#1'], picked)


def test_knowledge_agent():
    print('[knowledge_agent]')
    import tempfile, shutil
    from Intelligence.knowledge.knowledge_store import KnowledgeStore
    from Intelligence.knowledge.knowledge_agent import (
        KnowledgeAgent, format_citation_line)

    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, 'docs')
    os.makedirs(src)
    try:
        with open(os.path.join(src, 'fire.md'), 'wb') as f:
            f.write('Cửa thoát hiểm phải có chiều rộng tối thiểu 800 mm '
                    'và mở theo chiều thoát nạn.'.encode('utf-8'))
        store = KnowledgeStore(os.path.join(tmp, 'idx'), [src], 'test')
        store.scan()

        agent = KnowledgeAgent(store=store, fallback_store=None)
        seen = {}

        def chat_fn(system_prompt, query):
            seen['system'] = system_prompt
            seen['query'] = query
            return 'Chiều rộng tối thiểu là 800 mm [1].'

        res = agent.answer('cửa thoát hiểm rộng bao nhiêu?', [], chat_fn,
                           viet=True)
        check('agent done', res['status'] == 'done', res)
        check('citation built', res['citations'] and
              res['citations'][0]['file'] == 'fire.md', res.get('citations'))
        check('excerpt in query', '800 mm' in seen['query'])
        check('grounding rule in system (vi)', 'trích đoạn' in seen['system'])

        # The prompt used to be Vietnamese-only with an "always answer in
        # English" line bolted on; both languages are now spelled out and the
        # caller picks one (2026-07-28).
        # Same query (so retrieval still hits the Vietnamese doc), English out.
        agent.answer('cửa thoát hiểm rộng bao nhiêu?', [], chat_fn, viet=False)
        check('grounding rule in system (en)',
              'excerpt' in seen['system'].lower(), seen['system'][:120])
        check('english prompt has no Vietnamese rules',
              'NGUYÊN TẮC' not in seen['system'])

        line = format_citation_line(res['citations'])
        check('citation line', 'fire.md' in line and 'Nguồn' in line, line)

        # reference block for the tool agent (grounding without the knowledge
        # specialist): empty when nothing retrieved, populated + capped otherwise.
        from Intelligence.knowledge.knowledge_agent import build_reference_block
        check('ref block empty on no hits', build_reference_block([]) == '')
        ref_hits = agent.retrieve('cửa thoát hiểm rộng bao nhiêu?', top_k=3)
        block = build_reference_block(ref_hits, excerpt_chars=500, max_items=2)
        check('ref block has source + guidance',
              'fire.md' in block and 'reference material' in block.lower()
              and '800 mm' in block, block[:80])
        check('ref block caps items',
              block.count('] fire.md') <= 2, block)

        res2 = agent.answer('chủ đề hoàn toàn khác biệt xyz', [], chat_fn)
        check('no hits status', res2['status'] == 'no_hits', res2)

        def mute_fn(system_prompt, query):
            return None
        res3 = agent.answer('cửa thoát hiểm', [], mute_fn)
        check('llm_failed status', res3['status'] == 'llm_failed', res3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── pdf_annots / sheet_matcher / comment_agent ───────────────────────────────

def _make_pdf(objects):
    parts = [b'%PDF-1.4\n']
    for num, body in objects:
        parts.append('{} 0 obj\n'.format(num).encode('ascii'))
        parts.append(body if isinstance(body, bytes) else body.encode('latin-1'))
        parts.append(b'\nendobj\n')
    parts.append(b'trailer\n<< >>\n%%EOF\n')
    return b''.join(parts)


def _utf16be_hex(text):
    return 'FEFF' + ''.join('%04X' % ord(c) for c in text)


def test_pdf_annots():
    print('[pdf_annots]')
    import tempfile, shutil, zlib
    from Intelligence.comments import pdf_annots

    tmp = tempfile.mkdtemp()
    try:
        # fixture 1: literal + escaped parens + UTF-16BE hex + a Link (skip)
        pdf1 = os.path.join(tmp, 'A-101_MatBang.pdf')
        objs = [
            (1, '<< /Type /Catalog /Pages 2 0 R >>'),
            (2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
            (3, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                '/Annots [4 0 R 5 0 R 6 0 R] >>'),
            (4, '<< /Type /Annot /Subtype /Text /Rect [100 700 120 720] '
                '/Contents (Move dimension \\(outside\\) gridline A) '
                '/T (Nguyen B) /Subj (Dim) /M (D:20260718) >>'),
            (5, '<< /Type /Annot /Subtype /FreeText /Rect [10 10 60 40] '
                '/Contents <{}> /T <{}> >>'.format(
                    _utf16be_hex('Sửa kích thước dầm'),
                    _utf16be_hex('Linh'))),
            (6, '<< /Type /Annot /Subtype /Link /Rect [0 0 1 1] >>'),
        ]
        with open(pdf1, 'wb') as f:
            f.write(_make_pdf(objs))

        check('has_annotations true', pdf_annots.has_annotations(pdf1))
        recs, partial = pdf_annots.extract_annotations(pdf1)
        check('two markup annots (Link skipped)', len(recs) == 2, len(recs))
        check('not partial', partial is False)
        r1 = [r for r in recs if r['subtype'] == 'Text'][0]
        check('literal content + escapes',
              r1['content'] == 'Move dimension (outside) gridline A',
              r1['content'])
        check('author + subject', r1['author'] == 'Nguyen B'
              and r1['subject'] == 'Dim')
        check('rect parsed', r1['rect'] == [100.0, 700.0, 120.0, 720.0])
        check('page number', r1['page'] == 1)
        r2 = [r for r in recs if r['subtype'] == 'FreeText'][0]
        check('utf16be content', r2['content'] == 'Sửa kích thước dầm',
              repr(r2['content']))
        check('utf16be author', r2['author'] == 'Linh', repr(r2['author']))

        # fixture 2: annotation packed inside a Flate ObjStm
        inner = ('<< /Type /Annot /Subtype /FreeText /Rect [10 10 50 30] '
                 '/Contents (Trong ObjStm) >>')
        header = '7 0 '
        stream = zlib.compress((header + inner).encode('latin-1'))
        body8 = (b'<< /Type /ObjStm /N 1 /First '
                 + str(len(header)).encode('ascii')
                 + b' /Filter /FlateDecode /Length '
                 + str(len(stream)).encode('ascii')
                 + b' >>\nstream\n' + stream + b'\nendstream')
        pdf2 = os.path.join(tmp, 'objstm.pdf')
        objs2 = [
            (1, '<< /Type /Catalog /Pages 2 0 R >>'),
            (2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
            (3, '<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>'),
            (8, body8),
        ]
        with open(pdf2, 'wb') as f:
            f.write(_make_pdf(objs2))
        recs2, partial2 = pdf_annots.extract_annotations(pdf2)
        check('objstm annot found', len(recs2) == 1 and
              recs2[0]['content'] == 'Trong ObjStm', recs2)
        check('objstm not partial', partial2 is False)

        # fixture 3: no annotations
        pdf3 = os.path.join(tmp, 'plain.pdf')
        with open(pdf3, 'wb') as f:
            f.write(_make_pdf([
                (1, '<< /Type /Catalog /Pages 2 0 R >>'),
                (2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
                (3, '<< /Type /Page /Parent 2 0 R >>')]))
        check('has_annotations false', not pdf_annots.has_annotations(pdf3))
        recs3, _p3 = pdf_annots.extract_annotations(pdf3)
        check('no annots empty', recs3 == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sheet_matcher():
    print('[sheet_matcher]')
    from Intelligence.comments import sheet_matcher

    sheets = [
        {'name': 'MAT BANG TANG 1', 'number': 'A-101', 'id': 111},
        {'name': 'MAT DUNG TRUC A', 'number': 'A-201', 'id': 222},
    ]

    cands = sheet_matcher.extract_sheet_candidates(
        [(1, 'DU AN X ... SHEET NO: A-201 ... khung ten')],
        'A-101_MatBang.pdf')
    check('filename candidate first', cands and cands[0].startswith('A-101'),
          cands)
    check('keyword candidate found',
          any(c.replace('-', '') == 'A201' for c in cands), cands)

    m = sheet_matcher.match_sheets(['A-101'], sheets)
    check('exact match', m and m['id'] == 111 and m['score'] == 1.0, m)
    m2 = sheet_matcher.match_sheets(['A101'], sheets)
    check('separator-insensitive', m2 and m2['id'] == 111, m2)
    m3 = sheet_matcher.match_sheets(['Z-999'], sheets, 'khongkhop.pdf')
    check('no match none', m3 is None, m3)
    m4 = sheet_matcher.match_sheets([], sheets, 'Mat Bang Tang 1.pdf')
    check('fuzzy name match', m4 and m4['id'] == 111 and m4['score'] >= 0.55,
          m4)


def test_comment_agent():
    print('[comment_agent]')
    import tempfile, shutil
    from Intelligence.comments.comment_agent import CommentAgent

    tmp = tempfile.mkdtemp()
    try:
        pdf1 = os.path.join(tmp, 'A-101_MatBang.pdf')
        with open(pdf1, 'wb') as f:
            f.write(_make_pdf([
                (1, '<< /Type /Catalog /Pages 2 0 R >>'),
                (2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
                (3, '<< /Type /Page /Parent 2 0 R /Annots [4 0 R] >>'),
                (4, '<< /Type /Annot /Subtype /Text /Rect [1 2 3 4] '
                    '/Contents (Doi dim ra ngoai grid) /T (QA) >>')]))

        calls = []

        def fake_execute(name, args):
            calls.append(name)
            if name == 'revit_list_sheets':
                return {'count': 1, 'sheets': [
                    {'name': 'MAT BANG TANG 1', 'number': 'A-101', 'id': 111}]}
            if name == 'list_open_documents':
                return {'documents': [{'title': 'ModelA'}]}
            if name == 'revit_get_project_info':
                return {'name': 'Landmark'}
            return {}

        class FakeProvider(object):
            def chat(self, messages, system, user, max_tokens=0, **kw):
                assert 'a1' in user
                return ('{"items": [{"id": "a1", "action_type": '
                        '"fix_dimension", "description": "Doi dim ra ngoai",'
                        ' "instruction": "Move the dimension on sheet A-101 '
                        'outside gridline"}]}')

        agent = CommentAgent()
        report = agent.analyze(pdf1, fake_execute, FakeProvider(), None)
        check('sheet matched', report['sheet_match']
              and report['sheet_match']['number'] == 'A-101', report['sheet_match'])
        check('model open', report['model_open'] is True)
        check('mcp tools called', 'revit_list_sheets' in calls
              and 'list_open_documents' in calls)
        item = report['items'][0]
        check('proposal merged',
              item['proposal']['action_type'] == 'fix_dimension',
              item['proposal'])
        check('item sheet ref', item['matched_sheet']['id'] == 111)

        run_instr = agent.build_run_instruction(item, report)
        check('run instruction', 'A-101' in run_instr
              and 'Doi dim ra ngoai grid' in run_instr, run_instr[:80])
        note_instr = agent.build_note_instruction(item, report)
        check('note instruction', 'create_text_note' in note_instr
              and 'CMT-1' in note_instr, note_instr[:80])

        md = agent.report_to_markdown(report)
        check('markdown table', '| a1 |' in md and 'A-101' in md)

        # LLM mute → proposals stay manual, pipeline never crashes
        class MuteProvider(object):
            def chat(self, *a, **kw):
                return None
        report2 = agent.analyze(pdf1, fake_execute, MuteProvider(), None)
        check('mute → manual default',
              report2['items'][0]['proposal']['action_type'] == 'manual')

        # PDF without annotations → clean error
        pdf3 = os.path.join(tmp, 'plain.pdf')
        with open(pdf3, 'wb') as f:
            f.write(_make_pdf([
                (1, '<< /Type /Catalog /Pages 2 0 R >>'),
                (2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
                (3, '<< /Type /Page /Parent 2 0 R >>')]))
        report3 = agent.analyze(pdf3, fake_execute, FakeProvider(), None)
        check('no annotations error', report3['error'] == 'no_annotations')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── project_store ────────────────────────────────────────────────────────────

def test_project_store():
    print('[project_store]')
    from config.project_store import ProjectStore
    from Intelligence.knowledge.knowledge_store import get_active_store

    ps = ProjectStore()
    meta = ps.create_project('Landmark Tower')
    check('created', meta['id'].startswith('p_'), meta)
    check('listed', any(p['id'] == meta['id'] for p in ps.list_projects()))

    ps.set_active_project(meta['id'])
    check('active id', ps.get_active_project_id() == meta['id'])

    ps.update_project(meta['id'], {'instructions': 'Follow standard ABC.'})
    check('prompt addendum', ps.get_active_prompt_addendum() ==
          'Follow standard ABC.')

    hp = ps.history_path(meta['id'], 'docA')
    check('history path scoped', meta['id'] in hp and hp.endswith('docA.json'))

    store = ps.knowledge_store_for(meta['id'])
    check('store scoped', store is not None and meta['id'] in store.index_dir)

    files_dir = os.path.join(ps.project_dir(meta['id']), 'files')
    with open(os.path.join(files_dir, 'rule.md'), 'wb') as f:
        f.write('Chiều cao lan can 1200 mm áp dụng riêng project này.'
                .encode('utf-8'))
    store.scan()

    astore = get_active_store()
    check('get_active_store → project store', astore is store)
    hits = astore.search('chiều cao lan can')
    check('project-scoped search', hits and hits[0]['file'] == 'rule.md',
          hits and hits[0]['file'])

    ps.delete_project(meta['id'])
    check('delete clears active', ps.get_active_project_id() is None)
    check('active store falls back to global',
          get_active_store() is not store)


# ─── office_text: .docx / .xlsx ───────────────────────────────────────────────

_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_S_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_PR_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _zip_bytes(parts):
    """A zip built in memory: {member name: xml text} -> bytes."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text.encode('utf-8'))
    return buf.getvalue()


def _docx_fixture():
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>File Naming</w:t></w:r></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">Chiều cao </w:t></w:r>'
        '<w:r><w:t>lan can 1200</w:t></w:r><w:r><w:tab/><w:t>mm</w:t></w:r>'
        '<w:del><w:r><w:delText>DELETED</w:delText></w:r></w:del></w:p>'
        '<w:tbl>'
        '<w:tr><w:tc><w:p><w:r><w:t>Code</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>Discipline</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>AR</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>Architecture</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>incl. interiors</w:t></w:r></w:p></w:tc></w:tr>'
        '</w:tbl>'
        '<w:p><w:r><w:t xml:space="preserve">Before </w:t></w:r><w:r>'
        '<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>'
        '<w:txbxContent><w:p><w:r><w:t>Boxed note</w:t></w:r></w:p>'
        '</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict>'
        '<w:txbxContent><w:p><w:r><w:t>Boxed note</w:t></w:r></w:p>'
        '</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent>'
        '</w:r><w:r><w:t>after</w:t></w:r></w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="{}" xmlns:mc="http://schemas.openxmlformats.'
           'org/markup-compatibility/2006"><w:body>{}</w:body></w:document>'
           ).format(_W_NS, body)
    return _zip_bytes({'[Content_Types].xml': '<Types/>',
                       'word/document.xml': doc})


def _xlsx_fixture(with_rels=True):
    parts = {
        'xl/workbook.xml': (
            '<workbook xmlns="{}" xmlns:r="{}"><sheets>'
            '<sheet name="Rooms" sheetId="1" r:id="rId2"/>'
            '<sheet name="Doors" sheetId="2" r:id="rId1"/>'
            '<sheet name="Empty" sheetId="3" r:id="rId3"/>'
            '</sheets></workbook>').format(_S_NS, _R_NS),
        'xl/sharedStrings.xml': (
            '<sst xmlns="{}"><si><t>Name</t></si><si><t>Area</t></si>'
            '<si><r><t>Lob</t></r><r><t>by</t></r><rPh><t>PHONETIC</t></rPh></si>'
            '<si><t>Phòng họp</t></si></sst>').format(_S_NS),
        'xl/worksheets/sheet1.xml': (
            '<worksheet xmlns="{}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c t="s"><v>2</v></c><c><v>42.0</v></c><c t="b"><v>1</v></c></row>'
            '<row r="3"><c t="s"><v>3</v></c><c><v>18.5</v></c>'
            '<c><f>SUM(B2:B3)</f><v>60.5</v></c></row>'
            '<row r="4"/>'
            '</sheetData></worksheet>').format(_S_NS),
        'xl/worksheets/sheet2.xml': (
            '<worksheet xmlns="{}"><sheetData><row>'
            '<c t="inlineStr"><is><t>D-01</t></is></c><c><v>0.9</v></c>'
            '</row></sheetData></worksheet>').format(_S_NS),
        'xl/worksheets/sheet3.xml': (
            '<worksheet xmlns="{}"><sheetData/></worksheet>').format(_S_NS),
    }
    if with_rels:
        parts['xl/_rels/workbook.xml.rels'] = (
            '<Relationships xmlns="{}">'
            '<Relationship Id="rId1" Type="x" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId2" Type="x" Target="/xl/worksheets/sheet1.xml"/>'
            '<Relationship Id="rId3" Type="x" Target="worksheets/sheet3.xml"/>'
            '</Relationships>').format(_PR_NS)
    return _zip_bytes(parts)


def test_office_text():
    print('[office_text: docx / xlsx]')
    import tempfile
    from Intelligence.knowledge import office_text as O
    from Intelligence.knowledge import knowledge_store as KS
    from Intelligence.knowledge import context_digest as CD
    from Intelligence.knowledge import pdf_cache as PC

    tmp = tempfile.mkdtemp(prefix='t3lab_office_')

    def put(name, data):
        path = os.path.join(tmp, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    # ── docx ──
    docx = put('BEP.docx', _docx_fixture())
    pages, why = O.extract_docx(docx)
    text = pages[0][1] if pages else ''
    check('docx: one text page 0, no reason', len(pages) == 1
          and pages[0][0] == 0 and why == '', (pages, why))
    check('docx: Heading 1 becomes a markdown heading',
          text.startswith('# File Naming'), text[:40])
    check('docx: runs joined, tab kept, Vietnamese intact',
          'Chiều cao lan can 1200\tmm' in text, text)
    check('docx: deleted (tracked) text excluded', 'DELETED' not in text)
    check('docx: table rows as cell | cell',
          'Code | Discipline' in text
          and 'AR | Architecture incl. interiors' in text, text)
    check('docx: text box read once (mc:Fallback skipped)',
          text.count('Boxed note') == 1, text)
    check('docx: paragraph around the text box keeps both halves',
          'Before after' in text, text)

    # ── xlsx ──
    xlsx = put('Register.xlsx', _xlsx_fixture())
    pages, why = O.extract_xlsx(xlsx)
    text = pages[0][1] if pages else ''
    check('xlsx: one text page, no reason', len(pages) == 1 and why == '',
          (pages, why))
    check('xlsx: sheets in WORKBOOK order with Sheet headers',
          text.index('## Sheet: Rooms') < text.index('## Sheet: Doors'), text)
    check('xlsx: shared + rich strings, phonetic runs dropped',
          'Name | Area' in text and 'Lobby | 42 | TRUE' in text
          and 'PHONETIC' not in text, text)
    check('xlsx: Vietnamese shared string + numbers + cached formula value',
          'Phòng họp | 18.5 | 60.5' in text, text)
    check('xlsx: inline strings', 'D-01 | 0.9' in text, text)
    check('xlsx: empty sheet has no header', 'Sheet: Empty' not in text, text)

    no_rels = put('NoRels.xlsx', _xlsx_fixture(with_rels=False))
    pages, why = O.extract_xlsx(no_rels)
    check('xlsx without workbook rels falls back to sheetN parts',
          pages and '## Sheet: Sheet1' in pages[0][1]
          and 'D-01' in pages[0][1], (pages, why))

    # ── failures say why ──
    ole = put('Locked.docx', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 encrypted')
    pages, why = O.extract_pages(ole)
    check('password-protected / not-a-zip explained',
          pages == [] and 'password' in why, why)
    empty = put('Empty.xlsx', b'')
    check('0-byte file explained', O.extract_pages(empty)[1].startswith('empty'))
    nodoc = put('NoBody.docx', _zip_bytes({'word/other.xml': '<x/>'}))
    check('docx without word/document.xml explained',
          'not a Word document' in O.extract_pages(nodoc)[1])
    check('other extensions unsupported',
          O.extract_pages(os.path.join(tmp, 'a.dwg'))[1] == 'unsupported file type')

    old_cap = O.MAX_OFFICE_BYTES
    O.MAX_OFFICE_BYTES = 10
    try:
        check('size cap (same as the PDF cap) enforced',
              'limit' in O.extract_pages(docx)[1])
    finally:
        O.MAX_OFFICE_BYTES = old_cap
    check('size cap mirrors rag_processor.MAX_PDF_BYTES',
          O.MAX_OFFICE_BYTES == 20 * 1024 * 1024, O.MAX_OFFICE_BYTES)
    old_chars = O.MAX_OFFICE_CHARS
    O.MAX_OFFICE_CHARS = 30
    try:
        pages, _ = O.extract_pages(xlsx)
        check('character ceiling stops extraction',
              pages and len(pages[0][1]) <= 30 + 2, pages)
    finally:
        O.MAX_OFFICE_CHARS = old_chars

    # ── registered everywhere the knowledge stack lists extensions ──
    for mod, label in ((KS, 'knowledge_store'), (CD, 'context_digest')):
        check('{} indexes .docx and .xlsx'.format(label),
              '.docx' in mod.INDEXABLE_EXTS and '.xlsx' in mod.INDEXABLE_EXTS)
    pages, why = PC.get_pages(docx, use_cache=False)
    check('pdf_cache routes .docx to office_text',
          pages and 'File Naming' in pages[0][1], (pages, why))
    pages, why = PC.get_pages(xlsx)
    check('pdf_cache caches .xlsx', pages and 'D-01' in pages[0][1], why)
    listed = [os.path.basename(p) for p in CD.iter_documents(tmp)]
    check('context digest walks Word/Excel files',
          'BEP.docx' in listed and 'Register.xlsx' in listed, listed)

    # ── end to end: scan + search with citations ──
    src = os.path.join(tmp, 'src')
    os.makedirs(src)
    with open(docx, 'rb') as f:
        put(os.path.join('src', 'BEP.docx'), f.read())
    with open(xlsx, 'rb') as f:
        put(os.path.join('src', 'Register.xlsx'), f.read())
    store = KS.KnowledgeStore(os.path.join(tmp, 'idx'), [src], 'office')
    r = store.scan()
    check('scan indexes both office files', r['added'] == 2, r)
    hits = store.search('discipline architecture interiors', top_k=3)
    check('docx passage retrievable', hits and hits[0]['file'] == 'BEP.docx',
          hits and hits[0]['file'])
    hits = store.search('Lobby area', top_k=3)
    check('xlsx passage retrievable', hits and hits[0]['file'] == 'Register.xlsx',
          hits and hits[0]['file'])
    kinds = sorted(e.get('kind') for e in store._manifest['files'].values())
    check('manifest kind per format', kinds == ['docx', 'xlsx'], kinds)


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    test_vi_text()
    test_chunker()
    test_bm25()
    test_embeddings()
    test_knowledge_store()
    test_pdf_crypt()
    test_py2_byte_semantics()
    test_pdf_cache()
    test_context_digest()
    test_context_digest_incremental()
    test_context_digest_readability_flip()
    test_dispatcher()
    test_specialists()
    test_skills()
    test_query_builder()
    test_rerank_diversity()
    test_knowledge_agent()
    test_project_store()
    test_office_text()
    test_pdf_annots()
    test_sheet_matcher()
    test_comment_agent()

    print('')
    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All knowledge-stack tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
