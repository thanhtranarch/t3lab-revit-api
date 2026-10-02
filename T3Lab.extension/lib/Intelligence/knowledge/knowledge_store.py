# -*- coding: utf-8 -*-
"""
KnowledgeStore — persistent RAG index manager.

Owns one index directory (global: %APPDATA%/T3LabAI/rag_index/, or a
project-scoped one) and its source directories. Incremental scan by
(mtime, size), BM25 always available, optional semantic channel when an
embedder is supplied (see embeddings.py).

Layout on disk:
    <index_dir>/manifest.json          file registry
    <index_dir>/bm25.json              inverted index
    <index_dir>/chunks/<doc_id>.json   chunk texts (lazy-loaded per doc)
    <index_dir>/vectors/<doc_id>.json  embedding vectors (optional)

Thread-safe via an internal lock (precedent: LLMRouter._status_lock).
Pure Python + guarded imports — importable under CPython 3 for tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import hashlib
import io
import json
import os
import threading
import time

from Intelligence.knowledge import chunker
from Intelligence.knowledge.bm25_index import BM25Index, doc_id_of

TEXT_EXTS = ('.txt', '.md')
PDF_EXT = '.pdf'
# Word + Excel (office_text): the Projects UI has always advertised DOCX/XLSX
# as knowledge files, but nothing read them until these were registered.
from Intelligence.knowledge.office_text import OFFICE_EXTS
INDEXABLE_EXTS = TEXT_EXTS + (PDF_EXT,) + OFFICE_EXTS

# Files the assistant itself writes into an indexed folder. Never index them:
# they are generated FROM the corpus, so re-ingesting them makes the model cite
# its own summary as project evidence.
GENERATED_DOCS = frozenset(['PROJECT_CONTEXT.md'])

_CHUNK_CACHE_CAP = 8          # per-store: how many docs' chunk files stay in memory
_SCAN_SLEEP_SEC = 0.01        # breather between files so Revit stays responsive

# Bump whenever the BM25 tokenization changes (so on-disk indexes rebuild).
#   1 = unigram tokens
#   2 = unigram + adjacent bigram tokens (vi_text.tokenize bigrams=True)
INDEX_SCHEMA_VERSION = 2


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _doc_id_for(path):
    digest = hashlib.md5(_norm(path).encode('utf-8')).hexdigest()
    return 'd_' + digest[:10]


def _write_json(path, data):
    payload = json.dumps(data, ensure_ascii=True)
    if isinstance(payload, bytes):
        payload = payload.decode('ascii')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(payload)


def _read_json(path, default=None):
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


class KnowledgeStore(object):

    def __init__(self, index_dir, source_dirs=None, scope_label='global'):
        self.index_dir = index_dir
        self.source_dirs = list(source_dirs or [])
        self.scope_label = scope_label
        self._lock = threading.Lock()
        self._manifest = None       # {"version":1, "files": {norm_path: entry}}
        self._bm25 = None
        self._chunk_cache = {}      # doc_id -> {chunk_key: {"text","page"}}
        self._vector_cache = {}     # doc_id -> {chunk_key: [float]}

    # ── loading / saving ──────────────────────────────────────────────────

    def _dirs(self):
        for name in ('', 'chunks', 'vectors'):
            d = os.path.join(self.index_dir, name) if name else self.index_dir
            if not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except Exception:
                    pass

    def _ensure_loaded(self):
        """Callers hold self._lock."""
        if self._manifest is not None:
            return
        self._dirs()
        self._manifest = _read_json(
            os.path.join(self.index_dir, 'manifest.json'),
            {"version": 1, "files": {}})
        if 'files' not in self._manifest:
            self._manifest['files'] = {}
        self._bm25 = BM25Index.from_dict(
            _read_json(os.path.join(self.index_dir, 'bm25.json'), {}))
        # Tokenizer/schema migration: an index built by an older tokenizer
        # would only ever match its old unigram postings. Drop it so the next
        # scan() rebuilds every document with the current tokenization — one
        # background pass, then bigram-aware retrieval for all queries.
        if self._manifest.get('schema') != INDEX_SCHEMA_VERSION:
            self._manifest['files'] = {}
            self._manifest['schema'] = INDEX_SCHEMA_VERSION
            self._bm25 = BM25Index()

    def _save(self):
        """Callers hold self._lock."""
        self._manifest['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self._manifest['schema'] = INDEX_SCHEMA_VERSION
        try:
            _write_json(os.path.join(self.index_dir, 'manifest.json'), self._manifest)
            _write_json(os.path.join(self.index_dir, 'bm25.json'), self._bm25.to_dict())
        except Exception:
            pass

    # ── extraction / indexing ─────────────────────────────────────────────

    def _extract_chunks(self, path):
        """Return (chunks, meta) for a file, or (None, reason) if unusable."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in INDEXABLE_EXTS:
            return None, 'unsupported'
        # Shared with context_digest via pdf_cache: a Rescan builds CONTEXT.md
        # and then indexes the same folder, and reading + inflating 46 MB off
        # an SMB share twice cost ~3 minutes of the wall clock for nothing.
        from Intelligence.knowledge import pdf_cache
        pages, reason = pdf_cache.get_pages(path)
        if not pages:
            return None, (reason or 'no_text')
        if ext == PDF_EXT:
            partial = (len(pages) == 1 and pages[0][0] == 0)
            return chunker.chunk_pages(pages), {
                'kind': 'pdf', 'pages': len(pages),
                'status': 'partial' if partial else 'ok'}
        return chunker.chunk_text(pages[0][1]), {
            'kind': ext.lstrip('.'), 'pages': 1, 'status': 'ok'}

    def _index_one_locked(self, path, scope):
        """Extract + index a single file. Callers hold self._lock.
        Returns the manifest entry (also stored), or None."""
        npath = _norm(path)
        doc_id = _doc_id_for(path)
        try:
            stat = os.stat(path)
        except Exception:
            return None
        chunks, meta = self._extract_chunks(path)
        entry = {
            'doc_id': doc_id,
            'mtime': stat.st_mtime,
            'size': stat.st_size,
            'scope': scope,
            'indexed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        if chunks is None:
            entry.update({'kind': os.path.splitext(path)[1].lstrip('.'),
                          'pages': 0, 'chunks': 0, 'status': 'failed:' + meta})
            self._bm25.remove_document(doc_id)   # drop stale chunks if any
            self._manifest['files'][npath] = entry
            return entry
        entry.update(meta)
        entry['chunks'] = len(chunks)
        self._bm25.add_document(doc_id, chunks)
        # chunk texts on disk, keyed for get_chunk()
        chunk_map = {}
        for c in chunks:
            key = '{}#p{}#c{}'.format(doc_id, c['page'], c['seq'])
            chunk_map[key] = {'text': c['text'], 'page': c['page']}
        try:
            _write_json(os.path.join(self.index_dir, 'chunks', doc_id + '.json'),
                        {'file': npath, 'chunks': chunk_map})
        except Exception:
            entry['status'] = 'failed:write'
        self._chunk_cache.pop(doc_id, None)
        self._vector_cache.pop(doc_id, None)
        # a re-indexed file invalidates its old vectors
        try:
            vpath = os.path.join(self.index_dir, 'vectors', doc_id + '.json')
            if os.path.isfile(vpath):
                os.remove(vpath)
        except Exception:
            pass
        self._manifest['files'][npath] = entry
        return entry

    def _remove_locked(self, npath):
        entry = self._manifest['files'].pop(npath, None)
        if not entry:
            return
        doc_id = entry.get('doc_id')
        if doc_id:
            self._bm25.remove_document(doc_id)
            self._chunk_cache.pop(doc_id, None)
            self._vector_cache.pop(doc_id, None)
            for sub in ('chunks', 'vectors'):
                try:
                    p = os.path.join(self.index_dir, sub, doc_id + '.json')
                    if os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass

    # ── public API ────────────────────────────────────────────────────────

    def scan(self, progress_cb=None):
        """Incrementally (re)index every file under source_dirs.

        Returns {"added": n, "updated": n, "removed": n, "unchanged": n}.
        """
        result = {'added': 0, 'updated': 0, 'removed': 0, 'unchanged': 0}
        seen = set()
        with self._lock:
            self._ensure_loaded()
            for root_dir in list(self.source_dirs):
                if not root_dir or not os.path.isdir(root_dir):
                    continue
                for dirpath, _dirnames, filenames in os.walk(root_dir):
                    for fname in filenames:
                        if os.path.splitext(fname)[1].lower() not in INDEXABLE_EXTS:
                            continue
                        if fname in GENERATED_DOCS:
                            # Assistant-written summaries live in the same
                            # files/ dir that is a RAG source. Indexing them
                            # feeds the model its own prose back as if it were
                            # project evidence — a self-citation loop.
                            # context_digest guards its output dir the same way.
                            continue
                        path = os.path.join(dirpath, fname)
                        npath = _norm(path)
                        seen.add(npath)
                        old = self._manifest['files'].get(npath)
                        try:
                            stat = os.stat(path)
                        except Exception:
                            continue
                        if old and old.get('mtime') == stat.st_mtime \
                                and old.get('size') == stat.st_size:
                            result['unchanged'] += 1
                            continue
                        if progress_cb:
                            try:
                                progress_cb(fname)
                            except Exception:
                                pass
                        self._index_one_locked(path, 'knowledge')
                        result['updated' if old else 'added'] += 1
                        time.sleep(_SCAN_SLEEP_SEC)
            # drop entries whose file vanished, and knowledge-scope entries
            # no longer under any source dir (dir removed from settings)
            for npath in list(self._manifest['files'].keys()):
                entry = self._manifest['files'][npath]
                if not os.path.isfile(npath) or \
                        (entry.get('scope') == 'knowledge' and npath not in seen):
                    self._remove_locked(npath)
                    result['removed'] += 1
            self._save()
        return result

    def index_file(self, path, scope='attachment'):
        """Index one file on the fly (chat attachment). Skips work when the
        stored (mtime, size) still matches. Returns the manifest entry."""
        with self._lock:
            self._ensure_loaded()
            npath = _norm(path)
            old = self._manifest['files'].get(npath)
            try:
                stat = os.stat(path)
            except Exception:
                return None
            if old and old.get('mtime') == stat.st_mtime \
                    and old.get('size') == stat.st_size:
                return old
            entry = self._index_one_locked(path, scope)
            self._save()
            return entry

    def search(self, query, top_k=6, embedder=None, doc_ids=None):
        """Hybrid retrieval. BM25 always runs; when an embedder is supplied
        and vectors exist, the BM25 top-50 candidates are re-ranked
        semantically and fused via RRF.

        doc_ids: optional set of doc_ids to restrict retrieval to
        (attached-file queries).

        Returns list of {"key","score","text","file","page"} best-first.
        """
        with self._lock:
            self._ensure_loaded()
            candidates = self._bm25.search(query, top_k=50, allowed_docs=doc_ids)
        if not candidates:
            return []

        ranked_keys = [k for k, _ in candidates]
        if embedder is not None:
            try:
                ranked_keys = self._semantic_fuse(query, candidates, embedder)
            except Exception:
                ranked_keys = [k for k, _ in candidates]

        # Diversify BEFORE truncating. Ranking is per-chunk and independent,
        # so the strongest few are routinely consecutive chunks of one page —
        # with top_k as low as 2 or 3 that spends the whole budget on a single
        # passage and the model never sees the rest. Materialize a few extra
        # candidates so there is something to choose between.
        pool = []
        score_map = dict(candidates)
        for key in ranked_keys[:max(top_k * 4, top_k + 6)]:
            info = self.get_chunk(key)
            if not info:
                continue
            pool.append({
                'key':   key,
                'score': score_map.get(key, 0.0),
                'text':  info['text'],
                'file':  info['file'],
                'page':  info['page'],
            })

        try:
            from Intelligence.knowledge.rerank import diversify
            return diversify(pool, top_k,
                             doc_of=lambda h: doc_id_of(h['key']),
                             text_of=lambda h: h.get('text'))
        except Exception:
            return pool[:top_k]

    def _semantic_fuse(self, query, bm25_ranked, embedder):
        """Cosine re-rank of BM25 candidates + reciprocal-rank fusion.
        Degrades to BM25 order on any failure (no vectors, Ollama down)."""
        from Intelligence.knowledge.embeddings import cosine, rrf_fuse
        qvec_list = embedder.embed([query])
        if not qvec_list or not qvec_list[0]:
            return [k for k, _ in bm25_ranked]
        qvec = qvec_list[0]

        sims = []
        for key, _score in bm25_ranked:
            vec = self._get_vector(key)
            if vec:
                sims.append((key, cosine(qvec, vec)))
        if not sims:
            return [k for k, _ in bm25_ranked]
        sims.sort(key=lambda kv: -kv[1])
        return rrf_fuse([[k for k, _ in bm25_ranked],
                         [k for k, _ in sims]])

    def _get_vector(self, chunk_key):
        doc_id = doc_id_of(chunk_key)
        with self._lock:
            if doc_id not in self._vector_cache:
                data = _read_json(
                    os.path.join(self.index_dir, 'vectors', doc_id + '.json'), {})
                self._vector_cache[doc_id] = (data or {}).get('vectors', {})
                while len(self._vector_cache) > _CHUNK_CACHE_CAP:
                    self._vector_cache.pop(next(iter(self._vector_cache)))
            return self._vector_cache[doc_id].get(chunk_key)

    def get_chunk(self, chunk_key):
        """Return {"text","file","page"} for a chunk key, or None."""
        doc_id = doc_id_of(chunk_key)
        with self._lock:
            self._ensure_loaded()
            if doc_id not in self._chunk_cache:
                data = _read_json(
                    os.path.join(self.index_dir, 'chunks', doc_id + '.json'), {})
                self._chunk_cache[doc_id] = data or {}
                while len(self._chunk_cache) > _CHUNK_CACHE_CAP:
                    self._chunk_cache.pop(next(iter(self._chunk_cache)))
            data = self._chunk_cache.get(doc_id) or {}
        info = (data.get('chunks') or {}).get(chunk_key)
        if not info:
            return None
        fname = data.get('file', '')
        return {
            'text': info.get('text', ''),
            'file': os.path.basename(fname),
            'path': fname,
            'page': info.get('page', 0),
        }

    def stats(self, include_vectors=False):
        """{"files": n, "chunks": n, "vectors": n, "updated": ts}.

        include_vectors=True reads every vectors/<doc>.json to count
        entries — skip it for UI labels (called on the UI thread).
        """
        with self._lock:
            self._ensure_loaded()
            files = self._manifest['files']
            n_vec = 0
            if include_vectors:
                vdir = os.path.join(self.index_dir, 'vectors')
                try:
                    for fname in os.listdir(vdir):
                        data = _read_json(os.path.join(vdir, fname), {})
                        n_vec += len((data or {}).get('vectors', {}))
                except Exception:
                    pass
            return {
                'files':   len(files),
                'chunks':  self._bm25.size,
                'vectors': n_vec,
                'updated': self._manifest.get('updated', ''),
            }

    def embed_pending(self, embedder, progress_cb=None, budget_sec=120):
        """Vectorize chunks that have no embedding yet, within a time budget.
        Returns the number of chunks embedded. Safe no-op when the embedder
        is unavailable."""
        try:
            if embedder is None or not embedder.is_available():
                return 0
        except Exception:
            return 0
        started = time.time()
        embedded = 0
        with self._lock:
            self._ensure_loaded()
            doc_ids = sorted(set(
                e.get('doc_id') for e in self._manifest['files'].values()
                if e.get('doc_id') and e.get('chunks')))
        for doc_id in doc_ids:
            if time.time() - started > budget_sec:
                break
            vpath = os.path.join(self.index_dir, 'vectors', doc_id + '.json')
            cdata = _read_json(
                os.path.join(self.index_dir, 'chunks', doc_id + '.json'), {})
            chunk_map = (cdata or {}).get('chunks', {})
            if not chunk_map:
                continue
            vdata = _read_json(vpath, {}) or {}
            vectors = vdata.get('vectors', {})
            missing = [k for k in chunk_map if k not in vectors]
            if not missing:
                continue
            if progress_cb:
                try:
                    progress_cb(os.path.basename(cdata.get('file', doc_id)))
                except Exception:
                    pass
            # small batches keep each HTTP call quick on CPU-only machines
            for i in range(0, len(missing), 16):
                if time.time() - started > budget_sec:
                    break
                batch = missing[i:i + 16]
                vecs = embedder.embed([chunk_map[k]['text'] for k in batch])
                if not vecs:
                    break
                for key, vec in zip(batch, vecs):
                    if vec:
                        vectors[key] = vec
                        embedded += 1
            try:
                _write_json(vpath, {'model': getattr(embedder, 'MODEL', ''),
                                    'dim': len(next(iter(vectors.values()))) if vectors else 0,
                                    'vectors': vectors})
            except Exception:
                pass
            with self._lock:
                self._vector_cache.pop(doc_id, None)
        return embedded


# ─── Store singletons ─────────────────────────────────────────────────────────

_stores = {}
_stores_lock = threading.Lock()


def _appdata_dir():
    base = os.environ.get('APPDATA', '') or os.path.expanduser('~')
    return os.path.join(base, 'T3LabAI')


def default_knowledge_dir():
    """%APPDATA%/T3LabAI/knowledge/ — created on first use."""
    d = os.path.join(_appdata_dir(), 'knowledge')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def get_global_store():
    """Singleton global store; source dirs refresh from settings each call."""
    with _stores_lock:
        store = _stores.get('global')
        if store is None:
            store = KnowledgeStore(
                os.path.join(_appdata_dir(), 'rag_index'),
                [default_knowledge_dir()],
                scope_label='global')
            _stores['global'] = store
    dirs = [default_knowledge_dir()]
    try:
        from config.settings import get_settings
        for d in get_settings().get_knowledge_dirs():
            if d and d not in dirs:
                dirs.append(d)
    except Exception:
        pass
    store.source_dirs = dirs
    return store


def get_active_store():
    """Project-aware store: the active project's store when one is set
    (see config.project_store), else the global store."""
    try:
        from config.settings import get_settings
        pid = get_settings().get_active_project()
        if pid:
            from config.project_store import ProjectStore
            store = ProjectStore().knowledge_store_for(pid)
            if store is not None:
                return store
    except Exception:
        pass
    return get_global_store()
