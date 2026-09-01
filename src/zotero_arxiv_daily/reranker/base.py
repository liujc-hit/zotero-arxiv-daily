from abc import ABC
from datetime import datetime
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type

from .venue_citation_weighting import (
    VenueCitationWeighting,
    apply_venue_citation_weighting,
    resolve_venue_citation_weighting,
)

# Default number of most-similar corpus papers to average over when scoring a
# candidate. See BaseReranker.rerank for why top-k aggregation is preferred over
# a weighted mean over the whole library.
DEFAULT_TOPK = 10


def cosine_similarity(
    first_embeddings: np.ndarray, second_embeddings: np.ndarray
) -> np.ndarray:
    """Return pairwise cosine similarities between two embedding matrices."""
    first_normalized = first_embeddings / np.linalg.norm(
        first_embeddings, axis=1, keepdims=True
    )
    second_normalized = second_embeddings / np.linalg.norm(
        second_embeddings, axis=1, keepdims=True
    )
    return np.dot(first_normalized, second_normalized.T)


class BaseReranker(ABC):
    def __init__(self, config:DictConfig):
        self.config = config

    def _topk(self) -> int:
        """Resolve the top-k aggregation size from config, defensively.

        Returns DEFAULT_TOPK when config/attribute is missing so that test
        doubles (which may set ``self.config = None``) keep working.
        """
        cfg = getattr(self, "config", None)
        reranker_cfg = getattr(cfg, "reranker", None) if cfg is not None else None
        if reranker_cfg is None:
            return DEFAULT_TOPK
        try:
            val = (
                reranker_cfg.get("topk", DEFAULT_TOPK)
                if hasattr(reranker_cfg, "get")
                else getattr(reranker_cfg, "topk", DEFAULT_TOPK)
            )
        except Exception:
            return DEFAULT_TOPK
        try:
            return max(1, int(val))
        except Exception:
            return DEFAULT_TOPK

    def _recency_half_life_days(self) -> float | None:
        """Resolve reranker.recency_half_life_days defensively; None disables.

        When set, the top-k matched corpus papers are weighted by
        exp(-age_days / half_life) so recently added Zotero papers (the user's
        current research direction) dominate the score.
        """
        cfg = getattr(self, "config", None)
        reranker_cfg = getattr(cfg, "reranker", None) if cfg is not None else None
        if reranker_cfg is None:
            return None
        try:
            val = (
                reranker_cfg.get("recency_half_life_days", None)
                if hasattr(reranker_cfg, "get")
                else getattr(reranker_cfg, "recency_half_life_days", None)
            )
        except Exception:
            return None
        if val is None:
            return None
        try:
            half_life = float(val)
        except Exception:
            return None
        return half_life if half_life > 0 else None

    def _recency_weights(self, corpus: list[CorpusPaper], half_life: float) -> np.ndarray:
        """exp-decay weight per corpus paper based on Zotero added_date age."""
        now = datetime.now()
        ages = np.array(
            [max((now - c.added_date).days, 0) for c in corpus], dtype=float
        )
        return np.exp(-ages / half_life)

    def _mmr_lambda(self) -> float | None:
        """Resolve reranker.mmr_lambda defensively; None disables reordering."""
        cfg = getattr(self, "config", None)
        reranker_cfg = getattr(cfg, "reranker", None) if cfg is not None else None
        if reranker_cfg is None:
            return None
        try:
            val = (
                reranker_cfg.get("mmr_lambda", None)
                if hasattr(reranker_cfg, "get")
                else getattr(reranker_cfg, "mmr_lambda", None)
            )
        except Exception:
            return None
        if val is None:
            return None
        try:
            lam = float(val)
        except Exception:
            return None
        # clamp to [0, 1]: 1 = pure relevance (no diversity term)
        return min(max(lam, 0.0), 1.0)

    def _venue_citation_weighting(self) -> VenueCitationWeighting | None:
        """Resolve a complete opt-in venue citation weighting configuration."""
        config = getattr(self, "config", None)
        if not isinstance(config, DictConfig):
            return None
        return resolve_venue_citation_weighting(config)

    def _mmr_target(self, n_candidates: int) -> int:
        """How many of the top candidates MMR should reorder (the email slots)."""
        cfg = getattr(self, "config", None)
        executor_cfg = getattr(cfg, "executor", None) if cfg is not None else None
        if executor_cfg is None:
            return n_candidates
        try:
            val = (
                executor_cfg.get("max_paper_num", None)
                if hasattr(executor_cfg, "get")
                else getattr(executor_cfg, "max_paper_num", None)
            )
            if val is None:
                return n_candidates
            target = int(val)
        except Exception:
            return n_candidates
        return max(1, min(target, n_candidates))

    def _mmr_reorder(
        self, candidates: list[Paper], cand_sim: np.ndarray, lam: float
    ) -> list[Paper]:
        """Greedy Maximal Marginal Relevance over the top candidates.

        Near-duplicate candidates (same work announced twice, minor variants)
        waste email slots; MMR trades a little raw relevance for topical
        diversity: next = argmax(lam*rel_i - (1-lam)*max_sim(i, selected)).
        Only the first ``mmr_target`` slots are reordered; the tail keeps the
        score-sorted order. Scores are NOT modified — this is ordering only.
        """
        if len(candidates) <= 1:
            return candidates
        target = self._mmr_target(len(candidates))
        if target <= 1:
            return candidates

        rel = np.array(
            [c.score if c.score is not None else 0.0 for c in candidates], dtype=float
        )
        max_rel = rel.max()
        if max_rel <= 0:
            return candidates
        rel = rel / max_rel

        n = len(candidates)
        selected = [int(np.argmax(rel))]
        remaining = [i for i in range(n) if i != selected[0]]
        while remaining and len(selected) < target:
            best_i = max(
                remaining,
                key=lambda i: lam * rel[i]
                - (1.0 - lam) * max(float(cand_sim[i, j]) for j in selected),
            )
            selected.append(best_i)
            remaining.remove(best_i)

        tail = [i for i in range(n) if i not in selected]
        return [candidates[i] for i in selected + tail]

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        if len(corpus) == 0:
            for c in candidates:
                c.score = 0.0
            return candidates

        corpus = sorted(corpus, key=lambda x: x.added_date, reverse=True)
        n_corpus = len(corpus)

        # Embed title + abstract. Titles carry the strongest topical signal and
        # were previously discarded, which diluted the relevance signal.
        cand_texts = [f"{c.title}\n{c.abstract}".strip() for c in candidates]
        corp_texts = [f"{c.title}\n{c.abstract}".strip() for c in corpus]
        candidate_embeddings: np.ndarray | None
        try:
            embeddings = self.get_embeddings(cand_texts + corp_texts)
        except NotImplementedError:
            candidate_embeddings = None
            sim = self.get_similarity_score(cand_texts, corp_texts)
        else:
            candidate_embeddings = embeddings[: len(candidates)]
            corpus_embeddings = embeddings[len(candidates) :]
            sim = cosine_similarity(candidate_embeddings, corpus_embeddings)
        assert sim.shape == (len(candidates), n_corpus)

        # Top-k mean aggregation: score each candidate by the mean of its k
        # highest similarities to the corpus, rather than a (recency-weighted)
        # mean over the *entire* library. Averaging over the whole library is a
        # dilution trap: it pushes broadly-mediocre papers above papers that are
        # strongly relevant to a focused slice of the library — the main cause of
        # "recommended papers feel unrelated". Taking the best k matches keeps a
        # paper's score driven by the corpus it actually aligns with, while still
        # tolerating multiple research directions.
        #
        # Optional recency weighting (reranker.recency_half_life_days): within
        # those k best matches, papers added to Zotero more recently get an
        # exp(-age/half_life) weight, so the user's current research direction
        # dominates. Disabled by default (null) — plain top-k mean.
        k = min(self._topk(), n_corpus)
        if k >= n_corpus:
            topk_idx = np.tile(np.arange(n_corpus), (len(candidates), 1))
        else:
            # indices of the k largest similarities per candidate row
            topk_idx = np.argpartition(sim, -k, axis=1)[:, -k:]
        topk_sim = np.take_along_axis(sim, topk_idx, axis=1)

        half_life = self._recency_half_life_days()
        if half_life is not None:
            rec_weights = self._recency_weights(corpus, half_life)[topk_idx]  # (n_cand, k)
            denom = rec_weights.sum(axis=1)
            denom[denom == 0] = 1.0  # all-decayed edge case: fall back to mean
            scores = (topk_sim * rec_weights).sum(axis=1) / denom
        else:
            scores = topk_sim.mean(axis=1)
        scores = scores * 10.0  # keep the historical ~0-10 score scale

        venue_weighting = self._venue_citation_weighting()
        if venue_weighting is not None:
            scores = apply_venue_citation_weighting(
                scores,
                candidates,
                venue_weighting,
            )

        # Score adjustments belong here so papers and features share one final
        # stable score-order permutation before optional MMR.
        for s, c in zip(scores, candidates):
            c.score = float(s)
        score_order = np.argsort(-scores, kind="stable")
        candidates = [candidates[index] for index in score_order]
        if candidate_embeddings is not None:
            candidate_embeddings = candidate_embeddings[score_order]

        # Optional MMR diversity pass (reranker.mmr_lambda): reorders the top
        # candidates so near-duplicates do not fill consecutive email slots.
        # Disabled (null) by default — pure score order.
        mmr_lam = self._mmr_lambda()
        if mmr_lam is not None:
            if candidate_embeddings is None:
                cand_sim = self.get_similarity_score(cand_texts, cand_texts)
                cand_sim = cand_sim[np.ix_(score_order, score_order)]
            else:
                cand_sim = cosine_similarity(candidate_embeddings, candidate_embeddings)
            candidates = self._mmr_reorder(candidates, cand_sim, mmr_lam)
        return candidates

    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        """Encode texts for rerankers that expose reusable embeddings."""
        raise NotImplementedError

    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        """Preserve the public pairwise-similarity interface for callers."""
        embeddings = self.get_embeddings(s1 + s2)
        return cosine_similarity(embeddings[: len(s1)], embeddings[len(s1) :])

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> Type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]
