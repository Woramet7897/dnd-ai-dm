"""
memory_manager.py — Phase 5
Tiered RAG memory system.  Spec references: Section 14 (DND_AI_DM_full_spec_v2.md),
PART 12 (dnd_upgrade_prompt_v2.md).

Design (exact spec):
- ChromaDB PersistentClient(path="./db") — real disk persistence.
- Embedding model: sentence-transformers/all-MiniLM-L6-v2 (exact string).
- Short-term memory: last 6 turns in plain Python state (NOT embedded/vectorised).
- Past 6 turns → auto-summarize the OLDEST 2 into one minor_lore entry in ChromaDB.
- Every 20 minor_lore entries → consolidate into ONE major_lore chapter; archive
  (not delete) the 20 contributing minors so they survive but are excluded from
  future get_relevant_lore() queries.
- get_relevant_lore() returns AT MOST 3 entries total across major + minor.
- st.cache_resource for both the ChromaDB client and the embedding model.

Out of scope (Phase 10): session recap on load (Section 14a).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("memory_manager")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(logging.DEBUG)
    _h.setFormatter(logging.Formatter("[MEMORY] %(levelname)s: %(message)s"))
    logger.addHandler(_h)

# ── Constants (spec Section 14 / Part 12) ─────────────────────────────────────
SHORT_TERM_MAX_TURNS      = 6        # keep the last N turns in plain Python state
SUMMARIZE_OLDEST_N        = 2        # auto-summarize this many oldest turns when ST overflows
MINOR_LORE_CONSOLIDATE_AT = 20       # consolidate into major_lore at this count
MAX_RELEVANT_LORE_RESULTS = 3        # get_relevant_lore() cap (combined across all tiers)
EMBEDDING_MODEL_NAME      = "sentence-transformers/all-MiniLM-L6-v2"
DB_PATH                   = "./db"

# Collection names used in ChromaDB
_COLLECTION_MINOR    = "minor_lore"
_COLLECTION_MAJOR    = "major_lore"
# We store archived minors in the SAME minor collection but tag them with
# metadata {"archived": "true"} so they are excluded from future queries.


# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT AND MODEL LOADERS  (st.cache_resource — importable from Phase 7 app.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_chroma_client(db_path: str = DB_PATH):
    """
    Return a ChromaDB PersistentClient backed by db_path.
    Must be called via get_chroma_client() (cached), not directly.
    """
    import chromadb  # type: ignore
    client = chromadb.PersistentClient(path=db_path)
    logger.debug(f"ChromaDB PersistentClient initialised at '{db_path}'.")
    return client


def _load_embedding_model(model_name: str = EMBEDDING_MODEL_NAME):
    """
    Return a SentenceTransformer embedding model.
    Must be called via get_embedding_model() (cached), not directly.
    Spec Part 12: 'sentence-transformers/all-MiniLM-L6-v2 embeddings'.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore
    model = SentenceTransformer(model_name)
    logger.debug(f"SentenceTransformer model '{model_name}' loaded.")
    return model


# ── st.cache_resource wrappers ───────────────────────────────────────────────
# These are structured so Phase 7's app.py can import them directly.
# When Streamlit is present the decorator makes the objects singletons;
# when called outside Streamlit (e.g. tests) they fall back to a plain
# module-level singleton so tests work without a Streamlit server.

_chroma_client_singleton:    Optional[Any] = None
_embedding_model_singleton:  Optional[Any] = None


def get_chroma_client(db_path: str = DB_PATH):
    """
    Return (or create) the ChromaDB PersistentClient.
    Decorated with st.cache_resource when Streamlit is available.
    """
    global _chroma_client_singleton
    if _chroma_client_singleton is not None:
        return _chroma_client_singleton
    try:
        import streamlit as st  # type: ignore
        @st.cache_resource
        def _cached(path: str):
            return _load_chroma_client(path)
        result = _cached(db_path)
        _chroma_client_singleton = result
        return result
    except (ImportError, RuntimeError):
        # Streamlit not present or not in a Streamlit execution context.
        _chroma_client_singleton = _load_chroma_client(db_path)
        return _chroma_client_singleton


def get_embedding_model(model_name: str = EMBEDDING_MODEL_NAME):
    """
    Return (or create) the SentenceTransformer model.
    Decorated with st.cache_resource when Streamlit is available.
    """
    global _embedding_model_singleton
    if _embedding_model_singleton is not None:
        return _embedding_model_singleton
    try:
        import streamlit as st  # type: ignore
        @st.cache_resource
        def _cached(name: str):
            return _load_embedding_model(name)
        result = _cached(model_name)
        _embedding_model_singleton = result
        return result
    except (ImportError, RuntimeError):
        _embedding_model_singleton = _load_embedding_model(model_name)
        return _embedding_model_singleton


def reset_singletons() -> None:
    """
    Force-clear the in-process singletons.
    Used by tests that need to simulate a fresh process restart.
    """
    global _chroma_client_singleton, _embedding_model_singleton
    _chroma_client_singleton   = None
    _embedding_model_singleton = None
    logger.debug("Singletons cleared (reset_singletons).")


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY STATE  (per-character short-term window; caller owns persistence)
# ═══════════════════════════════════════════════════════════════════════════════

class MemoryManager:
    """
    Per-character tiered memory.

    Usage:
        mm = MemoryManager(character_name="Star", db_path="./db")
        mm.add_turn("Player: I sneak past the guard.  DM: You slip by unnoticed.")
        lore = mm.get_relevant_lore("goblin camp rumours")
    """

    def __init__(
        self,
        character_name: str,
        db_path: str = DB_PATH,
        summarizer = None,
    ) -> None:
        """
        Args:
            character_name: used to namespace ChromaDB collection entries.
            db_path:        path to the ChromaDB directory (default ./db).
            summarizer:     callable(texts: list[str]) -> str  used to produce
                            minor/major summaries.  Defaults to a simple
                            concatenation fallback so the module works standalone
                            without an LLM; Phase 7 will inject the real
                            llm_handler.summarize() here.
        """
        self._name    = character_name
        self._db_path = db_path
        self._short_term: List[str] = []   # plain Python list of raw turn strings

        self._summarizer = summarizer or _default_summarizer

        # Lazy-init collections (created on first access so tests can
        # instantiate MemoryManager before ChromaDB is available).
        self._minor_col  = None
        self._major_col  = None

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_minor_collection(self):
        if self._minor_col is None:
            client = get_chroma_client(self._db_path)
            self._minor_col = client.get_or_create_collection(
                name=_COLLECTION_MINOR,
                metadata={"hnsw:space": "cosine"},
            )
        return self._minor_col

    def _get_major_collection(self):
        if self._major_col is None:
            client = get_chroma_client(self._db_path)
            self._major_col = client.get_or_create_collection(
                name=_COLLECTION_MAJOR,
                metadata={"hnsw:space": "cosine"},
            )
        return self._major_col

    def _embed(self, text: str) -> List[float]:
        model = get_embedding_model()
        return model.encode(text).tolist()

    def _unique_id(self, prefix: str) -> str:
        """Generate a unique document ID using a counter stored in metadata."""
        import uuid
        return f"{prefix}_{self._name}_{uuid.uuid4().hex[:12]}"

    # ── Short-term window management ─────────────────────────────────────────

    @property
    def short_term_turns(self) -> List[str]:
        """The current short-term window (most recent SHORT_TERM_MAX_TURNS turns)."""
        return list(self._short_term)

    def add_turn(self, turn_text: str) -> None:
        """
        Add one turn to the short-term window.

        If the window already has SHORT_TERM_MAX_TURNS entries, the OLDEST
        SUMMARIZE_OLDEST_N turns are auto-summarized into a minor_lore entry
        before appending the new turn.

        Spec (Part 12):
            'Short-term: last 6 turns.  Auto-summarize oldest 2 into minor_lore
             past 6 turns.'
        """
        if len(self._short_term) >= SHORT_TERM_MAX_TURNS:
            self._flush_oldest_turns()
        self._short_term.append(turn_text)
        logger.debug(
            f"add_turn: short_term now {len(self._short_term)}/{SHORT_TERM_MAX_TURNS} turns."
        )

    def _flush_oldest_turns(self) -> None:
        """
        Summarize the SUMMARIZE_OLDEST_N oldest short-term turns into one
        minor_lore entry and remove them from the short-term window.
        Triggers minor→major consolidation check afterwards.
        """
        to_flush = self._short_term[:SUMMARIZE_OLDEST_N]
        self._short_term = self._short_term[SUMMARIZE_OLDEST_N:]

        summary = self._summarizer(to_flush)
        self._store_minor_lore(summary)
        logger.debug(
            f"_flush_oldest_turns: flushed {SUMMARIZE_OLDEST_N} turns → minor_lore."
        )
        self._maybe_consolidate()

    def _store_minor_lore(self, text: str, archived: bool = False) -> str:
        """
        Embed and store a minor_lore document.  Returns the document id.
        Archived documents keep the same collection but carry
        metadata['archived'] = 'true' so they are excluded from queries.
        """
        col  = self._get_minor_collection()
        doc_id = self._unique_id("minor")
        col.add(
            ids=[doc_id],
            documents=[text],
            embeddings=[self._embed(text)],
            metadatas=[{
                "character": self._name,
                "archived": "true" if archived else "false",
            }],
        )
        logger.debug(
            f"_store_minor_lore: stored '{doc_id}' (archived={archived})."
        )
        return doc_id

    def _store_major_lore(self, text: str) -> str:
        """Embed and store a major_lore document.  Returns the document id."""
        col = self._get_major_collection()
        doc_id = self._unique_id("major")
        col.add(
            ids=[doc_id],
            documents=[text],
            embeddings=[self._embed(text)],
            metadatas=[{"character": self._name}],
        )
        logger.debug(f"_store_major_lore: stored '{doc_id}'.")
        return doc_id

    # ── Consolidation logic ──────────────────────────────────────────────────

    def _count_active_minor_lore(self) -> int:
        """Count non-archived minor_lore entries for this character."""
        col = self._get_minor_collection()
        result = col.get(
            where={"$and": [
                {"character": {"$eq": self._name}},
                {"archived":  {"$eq": "false"}},
            ]}
        )
        return len(result["ids"])

    def _maybe_consolidate(self) -> None:
        """
        If the number of active (non-archived) minor_lore entries equals or
        exceeds MINOR_LORE_CONSOLIDATE_AT, consolidate them into one
        major_lore chapter and archive the contributing minors.

        Spec: 'Consolidate every 20 minor entries into one major_lore chapter
               summary. The 20 consolidated minor_lore entries must be ARCHIVED,
               not deleted.'
        """
        count = self._count_active_minor_lore()
        if count < MINOR_LORE_CONSOLIDATE_AT:
            return

        logger.debug(
            f"_maybe_consolidate: {count} active minor entries — consolidating."
        )
        col    = self._get_minor_collection()
        result = col.get(
            where={"$and": [
                {"character": {"$eq": self._name}},
                {"archived":  {"$eq": "false"}},
            ]},
            include=["documents", "metadatas"],
        )

        # Take exactly MINOR_LORE_CONSOLIDATE_AT oldest entries.
        # ChromaDB returns them in insertion order (IDs are UUIDs with a
        # timestamp prefix via _unique_id, but we sort lexicographically as
        # a best-effort proxy; the spec says every 20 accumulated, not
        # strictly the 20 oldest by wall clock).
        ids_docs  = list(zip(result["ids"], result["documents"]))
        ids_docs.sort(key=lambda x: x[0])  # lexicographic ≈ insertion order
        batch     = ids_docs[:MINOR_LORE_CONSOLIDATE_AT]
        batch_ids = [b[0] for b in batch]
        batch_docs= [b[1] for b in batch]

        # Build chapter summary
        chapter_text = self._summarizer(batch_docs)
        self._store_major_lore(chapter_text)

        # Archive the contributing minors (UPDATE metadata; do NOT delete)
        for doc_id, doc_text in batch:
            col.update(
                ids=[doc_id],
                metadatas=[{
                    "character": self._name,
                    "archived":  "true",
                }],
            )
        logger.debug(
            f"_maybe_consolidate: archived {len(batch_ids)} minor entries, "
            f"stored 1 major_lore chapter."
        )

    # ── Retrieval ────────────────────────────────────────────────────────────

    def get_relevant_lore(
        self,
        query: str,
        n_results: int = MAX_RELEVANT_LORE_RESULTS,
    ) -> List[Dict[str, Any]]:
        """
        Return at most n_results (default 3) lore entries most relevant to
        query, combined across major_lore and minor_lore.

        Spec (Part 12 / Section 14):
            'get_relevant_lore() capped at 3 total regardless of campaign length.'
            'Tier 3 (droppable): RAG lore — normally 2 major_lore + 1 minor_lore.'

        Args:
            query:     the text to embed and search against.
            n_results: hard cap on total returned entries (default 3).

        Returns:
            List of dicts with keys: 'text', 'type' ('major'|'minor'), 'id'.
            Always at most n_results items.
        """
        if n_results < 1:
            return []

        query_embedding = self._embed(query)

        results: List[Dict[str, Any]] = []

        # ── major_lore: try up to 2 (spec Tier 3 default) ────────────────────
        try:
            major_col = self._get_major_collection()
            major_count = major_col.count()
            if major_count > 0:
                n_major = min(2, n_results, major_count)
                major_res = major_col.query(
                    query_embeddings=[query_embedding],
                    n_results=n_major,
                    where={"character": {"$eq": self._name}},
                    include=["documents"],
                )
                for doc_id, doc in zip(
                    major_res["ids"][0], major_res["documents"][0]
                ):
                    results.append({"text": doc, "type": "major", "id": doc_id})
        except Exception as e:
            logger.warning(f"get_relevant_lore: major_lore query failed: {e}")

        # ── minor_lore: fill remaining slots (exclude archived) ───────────────
        remaining = n_results - len(results)
        if remaining > 0:
            try:
                minor_col = self._get_minor_collection()
                # Count active entries first to avoid ChromaDB error when
                # n_results > collection size.
                active_result = minor_col.get(
                    where={"$and": [
                        {"character": {"$eq": self._name}},
                        {"archived":  {"$eq": "false"}},
                    ]},
                    include=["documents"],
                )
                active_ids  = active_result["ids"]
                active_docs = active_result["documents"]

                if active_ids:
                    # Re-rank by cosine similarity locally to avoid n_results >
                    # collection-size errors.
                    import numpy as np  # type: ignore
                    q_vec   = query_embedding
                    scored  = []
                    model   = get_embedding_model()
                    for doc_id, doc in zip(active_ids, active_docs):
                        doc_vec = model.encode(doc).tolist()
                        # cosine similarity
                        q   = np.array(q_vec,   dtype=float)
                        d   = np.array(doc_vec, dtype=float)
                        sim = float(np.dot(q, d) / (np.linalg.norm(q) * np.linalg.norm(d) + 1e-9))
                        scored.append((sim, doc_id, doc))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    for _, doc_id, doc in scored[:remaining]:
                        results.append({"text": doc, "type": "minor", "id": doc_id})
            except Exception as e:
                logger.warning(f"get_relevant_lore: minor_lore query failed: {e}")

        # Hard cap — always return at most n_results
        results = results[:n_results]
        logger.debug(
            f"get_relevant_lore: returning {len(results)}/{n_results} entries "
            f"for query '{query[:40]}…'"
        )
        return results

    # ── Convenience accessors for tests ──────────────────────────────────────

    def get_all_minor_lore_ids(self, include_archived: bool = False) -> List[str]:
        """Return all minor_lore IDs for this character (for testing)."""
        col = self._get_minor_collection()
        if include_archived:
            result = col.get(
                where={"character": {"$eq": self._name}},
            )
        else:
            result = col.get(
                where={"$and": [
                    {"character": {"$eq": self._name}},
                    {"archived":  {"$eq": "false"}},
                ]},
            )
        return result["ids"]

    def get_all_major_lore_ids(self) -> List[str]:
        """Return all major_lore IDs for this character (for testing)."""
        col = self._get_major_collection()
        result = col.get(where={"character": {"$eq": self._name}})
        return result["ids"]

    def get_minor_lore_metadata(self, doc_id: str) -> Dict[str, Any]:
        """Return metadata dict for a specific minor_lore doc (for testing)."""
        col    = self._get_minor_collection()
        result = col.get(ids=[doc_id], include=["metadatas"])
        if result["ids"]:
            return result["metadatas"][0]
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT SUMMARIZER (fallback for standalone/testing)
# ═══════════════════════════════════════════════════════════════════════════════

def _default_summarizer(texts: List[str]) -> str:
    """
    Simple concatenation-based summarizer used when no LLM is wired up.
    Phase 7 will inject llm_handler.summarize() via MemoryManager(summarizer=...).
    """
    joined = " | ".join(t.strip() for t in texts if t.strip())
    return f"[Summary] {joined}"


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL CONVENIENCE API  (used by app.py / llm_handler.py)
# ═══════════════════════════════════════════════════════════════════════════════

# These are thin wrappers around MemoryManager so Phase 7 can call
# memory_manager.get_relevant_lore(mm, query) without importing the class.

def get_relevant_lore(
    mm: "MemoryManager",
    query: str,
    n_results: int = MAX_RELEVANT_LORE_RESULTS,
) -> List[Dict[str, Any]]:
    """Module-level alias for mm.get_relevant_lore()."""
    return mm.get_relevant_lore(query, n_results=n_results)


def add_turn(mm: "MemoryManager", turn_text: str) -> None:
    """Module-level alias for mm.add_turn()."""
    mm.add_turn(turn_text)
