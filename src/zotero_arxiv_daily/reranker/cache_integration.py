"""Optional encrypted compute-cache adapter; no paper objects enter the store."""

from __future__ import annotations

from collections import defaultdict, deque
from math import isfinite
from typing import TYPE_CHECKING, final

import numpy as np
from loguru import logger
from omegaconf import DictConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from .cache_identity import encoder_identity
from .cache_contracts import CacheStore, RankingBackend

if TYPE_CHECKING:
    from ..compute_cache import CacheValue
    from ..protocol import CorpusPaper, Paper
    from .scoring import ScoreContext


class IncompatibleEncoderOutput(ValueError):
    """The encoder returned mutually incompatible batches within one call."""


def cache_session(reranker: RankingBackend) -> RankingCache | None:
    """Open once on the first configured call, without touching custom encoders."""
    config = getattr(reranker, "config", None)
    if not isinstance(config, DictConfig) or reranker._cache_backend() is None:
        return None
    try:
        if OmegaConf.select(config, "compute_cache.enabled", default=False) is not True:
            return None
        revision = OmegaConf.select(config, "compute_cache.model_revision")
    except OmegaConfBaseException:
        logger.warning("Compute cache bypassed")
        return None
    if not isinstance(revision, str) or not revision.strip():
        return None
    if not hasattr(reranker, "_compute_cache"):
        from ..compute_cache import open_compute_cache

        try:
            reranker._compute_cache = open_compute_cache(config)
        except (ValueError, TypeError, OSError, OverflowError, RecursionError, OmegaConfBaseException):
            reranker._compute_cache = None
            logger.warning("Compute cache bypassed")
    cache = reranker._compute_cache
    if cache is None:
        return None
    try:
        identity = encoder_identity(reranker, cache)
    except (ValueError, TypeError, OSError, OverflowError, RecursionError, OmegaConfBaseException):
        logger.warning("Compute cache bypassed")
        return None
    return RankingCache(reranker, cache, identity)


@final
class RankingCache:
    """Per-call accumulator for numerical cache operations and sanitized counts."""

    def __init__(self, reranker: RankingBackend, cache: CacheStore, identity: CacheValue):
        self.reranker = reranker
        self.cache = cache
        self.identity = identity
        self.vector_hits = 0
        self.vector_misses = 0
        self.score_hits = 0
        self.score_misses = 0
        self.errors = 0

    def score_key(
        self, candidates: list[Paper], corpus: list[CorpusPaper], context: ScoreContext,
    ) -> str | None:
        """Key every consumed numerical input, including frozen weight bytes."""
        try:
            weights = context.recency_weights
            venue = context.venue
            parts: CacheValue = {
                "algorithm": "topk-cosine-mmr-v1", "encoder": self.identity,
                "candidates": [f"{p.title}\n{p.abstract}".strip() for p in candidates],
                "corpus": [[f"{p.title}\n{p.abstract}".strip(), p.added_date.isoformat()] for p in corpus],
                "topk": context.topk, "half_life": context.half_life,
                "weights": None if weights is None else [weights.dtype.str, list(weights.shape), weights.tobytes().hex()],
                "mmr_lambda": context.mmr_lambda, "mmr_target": context.mmr_target,
                "venue": None if venue is None else [venue.weight, venue.max_multiplier],
                "proxies": None if venue is None else [
                    p.venue_citation_proxy if p.venue_citation_proxy is not None
                    and not isinstance(p.venue_citation_proxy, bool)
                    and isfinite(p.venue_citation_proxy) and p.venue_citation_proxy >= 0 else None
                    for p in candidates
                ],
            }
            return self.cache.make_key("scores", parts)
        except (AttributeError, ValueError, TypeError, OSError, OverflowError, RecursionError, OmegaConfBaseException):
            self.errors += 1
            return None

    def restore(self, key: str | None, candidates: list[Paper]) -> list[Paper] | None:
        try:
            snapshot = None if key is None else self.cache.get_scores(key, len(candidates))
        except (ValueError, TypeError, OSError):
            self.errors += 1
            snapshot = None
        if snapshot is not None and (
            len(snapshot.scores) == len(candidates)
            and all(isfinite(score) for score in snapshot.scores)
            and len(snapshot.order) == len(candidates)
            and all(type(index) is int for index in snapshot.order)
            and sorted(snapshot.order) == list(range(len(candidates)))
        ):
            self.score_hits += 1
            for paper, score in zip(candidates, snapshot.scores):
                paper.score = score
            return [candidates[index] for index in snapshot.order]
        self.score_misses += 1
        return None

    def save(self, key: str | None, candidates: list[Paper], ranked: list[Paper]) -> None:
        from ..compute_cache import ScoreSnapshot

        if key is None:
            return
        positions: defaultdict[int, deque[int]] = defaultdict(deque)
        for index, paper in enumerate(candidates):
            positions[id(paper)].append(index)
        scores: list[float] = []
        for paper in candidates:
            if paper.score is None:
                return
            scores.append(paper.score)
        snapshot = ScoreSnapshot(
            scores=tuple(scores),
            order=tuple(positions[id(p)].popleft() for p in ranked),
        )
        try:
            self.cache.put_scores(key, snapshot)
        except (ValueError, TypeError, OSError):
            self.errors += 1

    def embeddings(self, texts: list[str]) -> np.ndarray:
        """Encode ordered unique misses once, then reconstruct ordered duplicates."""
        unique = list(dict.fromkeys(texts))
        try:
            keys = [self.cache.make_key("vector", [self.identity, text]) for text in unique]
            rows = [self.cache.get_vector(key) for key in keys]
        except (ValueError, TypeError, OSError):
            self.errors += 1
            keys = []
            rows = [None] * len(unique)
        hits = [row for row in rows if row is not None]
        if hits and (
            not all(row.ndim == 1 and row.size > 0 and row.dtype in (np.dtype("float32"), np.dtype("float64"))
                    and np.isfinite(row).all() for row in hits)
            or len({(row.shape, row.dtype.str) for row in hits}) != 1
        ):
            rows = [None] * len(unique)
        missing = [index for index, row in enumerate(rows) if row is None]
        self.vector_misses += len(missing)
        self.vector_hits += len(unique) - len(missing)
        if missing:
            encoded = self.reranker.get_embeddings([unique[index] for index in missing])
            # Unsupported encoder outputs retain their normal scoring behavior,
            # but are never admitted to the persistent vector cache.
            valid = encoded.ndim == 2 and len(encoded) == len(missing) and encoded.shape[1] > 0
            compatible = valid and all(
                row is None or (row.shape == encoded.shape[1:] and row.dtype == encoded.dtype)
                for row in rows
            )
            if not compatible:
                # A revision contract was violated: never combine incompatible
                # cached rows with new vectors. Re-encode only those old rows.
                old = [index for index, row in enumerate(rows) if row is not None]
                if old:
                    self.vector_hits -= len(old)
                    self.vector_misses += len(old)
                    self.errors += 1
                    if not valid:
                        raise IncompatibleEncoderOutput
                    refreshed = self.reranker.get_embeddings([unique[index] for index in old])
                    if (refreshed.ndim != 2 or len(refreshed) != len(old)
                            or refreshed.shape[1:] != encoded.shape[1:]
                            or refreshed.dtype != encoded.dtype):
                        raise IncompatibleEncoderOutput
                    encoded = np.concatenate((encoded, refreshed), axis=0)
                    missing.extend(old)
            persist = valid and encoded.dtype in (np.dtype("float32"), np.dtype("float64")) and np.isfinite(encoded).all()
            for index, row in zip(missing, encoded):
                rows[index] = row
                if keys and persist:
                    try:
                        self.cache.put_vector(keys[index], row)
                    except (ValueError, TypeError, OSError):
                        self.errors += 1
        by_text = {text: row for text, row in zip(unique, rows) if row is not None}
        return np.stack([by_text[text] for text in texts])

    def finish(self) -> None:
        bytes_used = 0
        try:
            _ = self.cache.flush()
            bytes_used = self.cache.stats().bytes_used
        except (ValueError, TypeError, OSError):
            self.errors += 1
        log = logger.warning if self.errors else logger.info
        log("Compute cache: vector_hits={} vector_misses={} score_hits={} score_misses={} bytes={} errors={}",
            self.vector_hits, self.vector_misses, self.score_hits, self.score_misses, bytes_used, self.errors)
