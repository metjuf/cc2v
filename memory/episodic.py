"""Eigy AI Assistant — Episodic memory via ChromaDB + multilingual-e5-large.

Stores user-assistant exchanges as vector embeddings for semantic retrieval.
Lazy-loads the embedding model (~2 GB) on first use.
Gracefully degrades if chromadb/sentence-transformers are not installed.
"""

from __future__ import annotations

import logging
import math
import re
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Temporal decay parameters for retrieval reranking
_DECAY_HALF_LIFE = 30.0   # days — half-life for recency score
_W_SIMILARITY = 0.7
_W_RECENCY = 0.2
_W_IMPORTANCE = 0.1

# Deduplication thresholds (cosine distance scale: 0 = identical, 2 = opposite)
_DEDUP_THRESHOLD = 0.08            # storage: skip if closer than this
_RETRIEVAL_DEDUP_THRESHOLD = 0.05  # retrieval: merge if distance-diff below this

_CHROMADB_AVAILABLE = False
try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    """Check if episodic memory dependencies are installed."""
    return _CHROMADB_AVAILABLE


class E5EmbeddingFunction:
    """Custom ChromaDB embedding function for multilingual-e5-large.

    Handles the required "query: " and "passage: " prefixes.
    Lazy-loads the SentenceTransformer model on first call.
    """

    MODEL_NAME = "intfloat/multilingual-e5-large"

    def __init__(self) -> None:
        self._model = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        logger.info("Loading embedding model %s ...", self.MODEL_NAME)
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self.MODEL_NAME)
        logger.info("Embedding model loaded.")

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        """Embed documents with 'passage: ' prefix."""
        self._load_model()
        prefixed = [f"passage: {doc}" for doc in documents]
        embeddings = self._model.encode(prefixed, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """Embed queries with 'query: ' prefix."""
        self._load_model()
        prefixed = [f"query: {q}" for q in queries]
        embeddings = self._model.encode(prefixed, normalize_embeddings=True)
        return embeddings.tolist()

    def __call__(self, input: list[str]) -> list[list[float]]:
        """ChromaDB embedding function protocol — called for document storage."""
        return self.embed_documents(input)


class EpisodicMemory:
    """Vector-based episodic memory for past conversations.

    Each exchange (user message + assistant response) is stored as
    a single document with metadata (session_id, timestamp, importance).
    """

    COLLECTION_NAME = "eigy_episodes"

    def __init__(
        self,
        db_path: str | Path,
        debug_callback: Callable[[str], None] | None = None,
    ) -> None:
        if not _CHROMADB_AVAILABLE:
            raise ImportError("chromadb is not installed")

        self._db_path = Path(db_path)
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._dbg = debug_callback

        self._embedding_fn = E5EmbeddingFunction()
        self._client = chromadb.PersistentClient(path=str(self._db_path))
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "Episodic memory initialized (%d episodes stored)",
            self._collection.count(),
        )

    def store_exchange(
        self,
        user_msg: str,
        assistant_msg: str,
        session_id: int,
        importance: float | None = None,
    ) -> None:
        """Embed and store a single exchange."""
        if not user_msg.strip() or not assistant_msg.strip():
            return

        if importance is None:
            importance = self._compute_importance(user_msg)

        doc_text = f"Uživatel: {user_msg}\nAsistent: {assistant_msg}"

        # Truncate to ~2000 chars to stay within 512 token limit
        if len(doc_text) > 2000:
            doc_text = doc_text[:2000]

        # Deduplication check: skip if a very similar episode already exists
        if self._collection.count() > 0:
            try:
                query_embedding = self._embedding_fn.embed_documents([doc_text])
                check = self._collection.query(
                    query_embeddings=query_embedding, n_results=1,
                )
                if (check and check["distances"] and check["distances"][0]
                        and check["distances"][0][0] < _DEDUP_THRESHOLD):
                    logger.debug(
                        "Skipping duplicate episode (dist=%.4f): %s",
                        check["distances"][0][0], user_msg[:50],
                    )
                    if self._dbg:
                        self._dbg(f"Epizoda přeskočena (duplikát, dist={check['distances'][0][0]:.3f})")
                    return
            except Exception:
                pass  # store anyway if dedup check fails

        doc_id = f"s{session_id}_t{int(time.time() * 1000)}"

        intents = self._detect_assistant_intents(assistant_msg)
        if self._dbg and intents:
            self._dbg(f"Intenty asistenta: {intents}")

        try:
            self._collection.add(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "session_id": session_id,
                    "timestamp": time.time(),
                    "importance": importance,
                    "user_msg_preview": user_msg[:100],
                    "assistant_intents": ",".join(intents),
                }],
            )
        except Exception as e:
            logger.warning("Failed to store episode: %s", e)

    def retrieve_relevant(
        self,
        query: str,
        top_k: int = 5,
        min_importance: float = 0.0,
    ) -> list[dict]:
        """Retrieve the most relevant past exchanges for a query.

        Returns list of dicts: {"document": str, "distance": float, "metadata": dict}
        Uses embed_queries() for proper "query: " prefix.
        """
        if not query.strip():
            return []

        count = self._collection.count()
        if count == 0:
            return []

        fetch_k = min(top_k * 3, count)

        try:
            # Use embed_queries for proper "query: " prefix
            query_embedding = self._embedding_fn.embed_queries([query])

            where = {"importance": {"$gte": min_importance}} if min_importance > 0 else None

            results = self._collection.query(
                query_embeddings=query_embedding,
                n_results=fetch_k,
                where=where,
            )
        except Exception as e:
            logger.warning("Episodic retrieval failed: %s", e)
            return []

        if not results or not results["documents"] or not results["documents"][0]:
            return []

        episodes = []
        for i, doc in enumerate(results["documents"][0]):
            episodes.append({
                "document": doc,
                "distance": results["distances"][0][i] if results["distances"] else 0.0,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            })

        # Temporal decay reranking
        now = time.time()
        for ep in episodes:
            ts = ep["metadata"].get("timestamp", now)
            age_days = (now - ts) / 86400.0
            recency = math.exp(-age_days * math.log(2) / _DECAY_HALF_LIFE)
            imp = ep["metadata"].get("importance", 0.5)
            similarity = max(0.0, 1.0 - ep["distance"])
            ep["final_score"] = (
                _W_SIMILARITY * similarity
                + _W_RECENCY * recency
                + _W_IMPORTANCE * imp
            )
        episodes.sort(key=lambda e: e["final_score"], reverse=True)

        if self._dbg and episodes:
            self._dbg(
                f"Episodic: {len(episodes)} vzpomínek "
                f"(best={episodes[0]['final_score']:.2f})"
            )

        # Retrieval-time deduplication: keep diverse results
        if len(episodes) > 1:
            deduplicated = [episodes[0]]
            for ep in episodes[1:]:
                is_dup = False
                for kept in deduplicated:
                    if abs(ep["distance"] - kept["distance"]) < _RETRIEVAL_DEDUP_THRESHOLD:
                        # Near-duplicate — keep the newer one
                        ep_ts = ep["metadata"].get("timestamp", 0)
                        kept_ts = kept["metadata"].get("timestamp", 0)
                        if ep_ts > kept_ts:
                            deduplicated.remove(kept)
                            deduplicated.append(ep)
                        is_dup = True
                        break
                if not is_dup:
                    deduplicated.append(ep)
            episodes = deduplicated

        return episodes[:top_k]

    def clear_all(self) -> None:
        """Delete all episodes (for /forget command)."""
        try:
            self._client.delete_collection(self.COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                embedding_function=self._embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("Episodic memory cleared.")
        except Exception as e:
            logger.warning("Failed to clear episodic memory: %s", e)

    def count(self) -> int:
        """Return the number of stored episodes."""
        return self._collection.count()

    def prune_old_episodes(
        self,
        max_age_days: int = 180,
        min_importance: float = 0.6,
    ) -> int:
        """Remove old episodes with low importance.

        Deletes episodes older than max_age_days that have importance
        below min_importance. Returns the number of deleted episodes.
        """
        if self._collection.count() == 0:
            return 0

        cutoff_timestamp = time.time() - (max_age_days * 86400.0)

        try:
            results = self._collection.get(
                where={
                    "$and": [
                        {"timestamp": {"$lt": cutoff_timestamp}},
                        {"importance": {"$lt": min_importance}},
                    ]
                },
            )
        except Exception as e:
            logger.warning("Failed to query old episodes for pruning: %s", e)
            return 0

        if not results or not results["ids"]:
            return 0

        ids_to_delete = results["ids"]
        try:
            self._collection.delete(ids=ids_to_delete)
            logger.info(
                "Pruned %d old episodes (older than %d days, importance < %.2f)",
                len(ids_to_delete), max_age_days, min_importance,
            )
            return len(ids_to_delete)
        except Exception as e:
            logger.warning("Failed to delete old episodes: %s", e)
            return 0

    # ── Assistant Intent Detection ─────────────────────────────────

    _INTENT_PATTERNS: dict[str, list[str]] = {
        "recommendation": [
            r"\bdoporuč", r"\bzkus\b", r"\bnabíz", r"\bnavrhuj",
            r"\bmůžeš zkusit\b", r"\bzvaž\b",
        ],
        "promise": [
            r"\bpřipomenu\b", r"\bzapamatuj", r"\bpoznačím\b",
            r"\buděl[aá]m\b", r"\bpodívám se\b", r"\bzjistím\b",
        ],
        "suggestion": [
            r"\bco kdybys\b", r"\bco třeba\b", r"\ba co\b",
            r"\bchceš\b.*\?",
        ],
        "opinion": [
            r"\bmyslím\b", r"\bpodle mě\b", r"\bosobně\b",
            r"\břekla bych\b", r"\bzdá se mi\b",
        ],
    }

    @staticmethod
    def _detect_assistant_intents(assistant_msg: str) -> list[str]:
        """Detect intents in assistant's message (recommendation, promise, etc.)."""
        intents = []
        text_lower = assistant_msg.lower()
        for intent, patterns in EpisodicMemory._INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    intents.append(intent)
                    break
        return intents

    @staticmethod
    def _compute_importance(user_msg: str) -> float:
        """Heuristic importance scoring."""
        if "?" in user_msg:
            return 0.7
        if len(user_msg) > 50:
            return 0.6
        return 0.5
