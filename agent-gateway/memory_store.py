"""memory_store.py — Persistent Vector Memory for Intelli Agent Gateway

Stores and semantically searches three kinds of memories:
  page   — auto-captured when the browser navigates to a page
  chat   — conversation summaries pinned after session compaction
  manual — facts/notes pinned explicitly by the user or agent

Backend
-------
ChromaDB persistent client with its built-in ONNX all-MiniLM-L6-v2 embedding
model.  The model (~22 MB) is downloaded to the data path on first use and
cached.  All embeddings are computed locally – no extra API calls.

Graceful fallback: if ChromaDB is unavailable a pure-Python keyword (BM25-
inspired) store is used instead, so the gateway never fails to start.

Data path
---------
$INTELLI_MEMORY_DIR or  <this file's dir>/workspace/memory/   (created auto).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA_DIR = os.path.join(_HERE, 'workspace', 'memory')
_DATA_DIR = os.environ.get('INTELLI_MEMORY_DIR', _DEFAULT_DATA_DIR)

_COLLECTION_NAME = 'intelli_memory'
_MAX_CONTENT_LEN = 2000   # chars stored per memory
_TOP_K           = 5      # default search results
_INJECT_K        = 3      # memories injected into system prompt
_DECAY_FACTOR    = 0.3    # age penalty weight (0 = no decay, 1 = strong decay)
_DECAY_HORIZON   = 90     # days after which decay reaches max
_MMR_LAMBDA      = 0.6    # trade-off: 1=pure relevance, 0=pure diversity

SOURCES = ('page', 'chat', 'manual')


# ---------------------------------------------------------------------------
# ChromaDB backend
# ---------------------------------------------------------------------------

class _ChromaBackend:
    def __init__(self, data_dir: str):
        import chromadb  # type: ignore
        os.makedirs(data_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=data_dir)
        # Use the default embedding function (ONNX MiniLM, no GPU required)
        self._col = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={'hnsw:space': 'cosine'},
        )
        logger.info('memory_store: ChromaDB backend at %s (%d entries)',
                    data_dir, self._col.count())

    # --- write ops ---

    def add(self, doc_id: str, text: str, metadata: dict) -> None:
        self._col.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def delete(self, doc_id: str) -> bool:
        try:
            self._col.delete(ids=[doc_id])
            return True
        except Exception:
            return False

    # --- read ops ---

    def search(self, query: str, n: int) -> List[Dict[str, Any]]:
        if self._col.count() == 0:
            return []
        results = self._col.query(
            query_texts=[query],
            n_results=min(n, self._col.count()),
            include=['documents', 'metadatas', 'distances'],
        )
        out = []
        for doc, meta, dist in zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0],
        ):
            out.append({
                'id':       meta.get('doc_id', ''),
                'text':     doc,
                'metadata': meta,
                'score':    round(1.0 - dist, 4),   # cosine similarity
            })
        return out

    def list_recent(self, n: int) -> List[Dict[str, Any]]:
        total = self._col.count()
        if total == 0:
            return []
        res = self._col.get(
            limit=min(n, total),
            include=['documents', 'metadatas'],
        )
        out = []
        for doc, meta in zip(res['documents'], res['metadatas']):
            out.append({'id': meta.get('doc_id', ''), 'text': doc, 'metadata': meta})
        # Sort by timestamp desc
        out.sort(key=lambda x: x['metadata'].get('timestamp_unix', 0), reverse=True)
        return out[:n]

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        res = self._col.get(ids=[doc_id], include=['documents', 'metadatas'])
        if not res['ids']:
            return None
        return {'id': doc_id, 'text': res['documents'][0], 'metadata': res['metadatas'][0]}

    def count(self) -> int:
        return self._col.count()


# ---------------------------------------------------------------------------
# Keyword fallback backend (BM25-inspired, no deps)
# ---------------------------------------------------------------------------

class _KeywordBackend:
    """Dead-simple in-memory keyword store used when ChromaDB is unavailable."""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}   # id -> {text, metadata}
        logger.warning('memory_store: ChromaDB unavailable — using keyword fallback')

    def add(self, doc_id: str, text: str, metadata: dict) -> None:
        self._store[doc_id] = {'text': text, 'metadata': metadata}

    def delete(self, doc_id: str) -> bool:
        return bool(self._store.pop(doc_id, None))

    def search(self, query: str, n: int) -> List[Dict[str, Any]]:
        query_words = set(re.findall(r'\w+', query.lower()))
        scored = []
        for doc_id, entry in self._store.items():
            words = re.findall(r'\w+', entry['text'].lower())
            word_set = set(words)
            tf = sum(words.count(w) for w in query_words) / max(len(words), 1)
            overlap = len(query_words & word_set) / max(len(query_words), 1)
            score = 0.5 * tf + 0.5 * overlap
            if score > 0:
                scored.append({'id': doc_id, 'text': entry['text'],
                                'metadata': entry['metadata'], 'score': round(score, 4)})
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:n]

    def list_recent(self, n: int) -> List[Dict[str, Any]]:
        entries = [{'id': k, 'text': v['text'], 'metadata': v['metadata']}
                   for k, v in self._store.items()]
        entries.sort(key=lambda x: x['metadata'].get('timestamp_unix', 0), reverse=True)
        return entries[:n]

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(doc_id)
        if entry is None:
            return None
        return {'id': doc_id, **entry}

    def count(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# MemoryStore (public API)
# ---------------------------------------------------------------------------

class MemoryStore:
    """Thread-safe wrapper around ChromaDB (or keyword fallback).

    All public methods are safe to call from any thread.
    """

    def __init__(self, data_dir: str = _DATA_DIR):
        self._lock = threading.Lock()
        try:
            self._backend = _ChromaBackend(data_dir)
            self.backend_name = 'chromadb'
        except Exception as exc:
            logger.error('memory_store: ChromaDB init failed (%s) — using keyword fallback', exc)
            self._backend = _KeywordBackend()
            self.backend_name = 'keyword'

    # ------------------------------------------------------------------ add

    def add(
        self,
        text:    str,
        source:  str = 'manual',
        url:     str = '',
        title:   str = '',
        pinned:  bool = False,
        extra:   Optional[Dict[str, Any]] = None,
        doc_id:  Optional[str] = None,
    ) -> str:
        """Store a memory.  Returns the doc_id (stable URL-based or UUID)."""
        text = text.strip()[:_MAX_CONTENT_LEN]
        if not text:
            raise ValueError('memory text must be non-empty')

        # Stable ID: for pages use URL hash so re-visits overwrite
        if doc_id is None:
            if url:
                doc_id = 'pg_' + hashlib.sha256(url.encode()).hexdigest()[:16]
            else:
                doc_id = 'mem_' + uuid.uuid4().hex[:16]

        metadata: Dict[str, Any] = {
            'doc_id':         doc_id,
            'source':         source,
            'url':            url,
            'title':          title,
            'pinned':         int(pinned),
            'timestamp_unix': time.time(),
            **(extra or {}),
        }
        with self._lock:
            self._backend.add(doc_id, text, metadata)
        logger.debug('memory_store: added %s (%s)', doc_id, source)
        return doc_id

    # ---------------------------------------------------------------- delete

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            return self._backend.delete(doc_id)

    # ---------------------------------------------------------------- search

    def search(
        self,
        query:       str,
        n:           int  = _TOP_K,
        source_filter: Optional[str] = None,
        apply_decay: bool = True,
        apply_mmr:   bool = True,
    ) -> List[Dict[str, Any]]:
        """Semantic search with optional temporal decay and MMR deduplication."""
        with self._lock:
            raw = self._backend.search(query, n=n * 3)  # over-fetch for MMR

        if source_filter:
            raw = [r for r in raw if r['metadata'].get('source') == source_filter]

        if apply_decay:
            raw = self._apply_temporal_decay(raw)

        if apply_mmr and len(raw) > n:
            raw = self._mmr(raw, n)
        else:
            raw = raw[:n]

        return raw

    # ------------------------------------------------------------- list recent

    def list_recent(self, n: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return self._backend.list_recent(n)

    # ------------------------------------------------------------------- get

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._backend.get(doc_id)

    # ----------------------------------------------------------------- count

    def count(self) -> int:
        with self._lock:
            return self._backend.count()

    # ----------------------------------------------------------------- utils

    @staticmethod
    def _apply_temporal_decay(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        for r in results:
            ts = r['metadata'].get('timestamp_unix', now)
            age_days = (now - ts) / 86400
            # Pinned memories are not decayed
            if r['metadata'].get('pinned'):
                continue
            decay = _DECAY_FACTOR * min(age_days / _DECAY_HORIZON, 1.0)
            r['score'] = round(r['score'] * (1.0 - decay), 4)
        results.sort(key=lambda x: x['score'], reverse=True)
        return results

    @staticmethod
    def _mmr(results: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
        """Maximal Marginal Relevance: balance relevance vs. diversity."""
        def bow(text: str) -> Dict[str, int]:
            words = re.findall(r'\w+', text.lower())
            freq: Dict[str, int] = {}
            for w in words:
                freq[w] = freq.get(w, 0) + 1
            return freq

        def cosine(a: Dict[str, int], b: Dict[str, int]) -> float:
            shared = set(a) & set(b)
            if not shared:
                return 0.0
            dot = sum(a[k] * b[k] for k in shared)
            mag = math.sqrt(sum(v*v for v in a.values())) * math.sqrt(sum(v*v for v in b.values()))
            return dot / mag if mag else 0.0

        bows = [bow(r['text']) for r in results]
        selected: List[int] = []
        remaining = list(range(len(results)))

        while len(selected) < n and remaining:
            if not selected:
                # First pick: highest relevance score
                best = max(remaining, key=lambda i: results[i]['score'])
            else:
                # MMR: relevance - (1-lambda) * max similarity to already selected
                def mmr_score(i: int) -> float:
                    rel = _MMR_LAMBDA * results[i]['score']
                    sim = max(cosine(bows[i], bows[j]) for j in selected)
                    return rel - (1 - _MMR_LAMBDA) * sim
                best = max(remaining, key=mmr_score)
            selected.append(best)
            remaining.remove(best)

        return [results[i] for i in selected]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store_lock = threading.Lock()
_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MemoryStore()
    return _store


# ---------------------------------------------------------------------------
# Convenience helpers used by app.py
# ---------------------------------------------------------------------------

def extract_text_from_html(html: str, max_chars: int = _MAX_CONTENT_LEN) -> str:
    """Strip tags and collapse whitespace.  BS4 used when available."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        for tag in soup(['script', 'style', 'noscript', 'nav', 'footer', 'header']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
    except Exception:
        text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def build_memory_context(query: str, n: int = _INJECT_K) -> str:
    """Return a formatted memory block to inject into the system prompt."""
    store = get_store()
    if store.count() == 0:
        return ''
    results = store.search(query, n=n)
    if not results:
        return ''
    lines = ['## Relevant memories']
    for r in results:
        meta = r['metadata']
        src  = meta.get('source', '?')
        url  = meta.get('url', '')
        ts   = meta.get('timestamp_unix', 0)
        age  = _fmt_age(ts)
        snippet = r['text'][:300].replace('\n', ' ')
        label = f"[{src}] {url or meta.get('title','')[:60]} ({age})"
        lines.append(f'- {label}: {snippet}')
    return '\n'.join(lines)


def _fmt_age(ts: float) -> str:
    age = time.time() - ts
    if age < 3600:
        return f'{int(age/60)}m ago'
    if age < 86400:
        return f'{int(age/3600)}h ago'
    return f'{int(age/86400)}d ago'
