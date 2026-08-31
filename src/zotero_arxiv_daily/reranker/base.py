from abc import ABC, abstractmethod
from datetime import datetime
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type

# Default number of most-similar corpus papers to average over when scoring a
# candidate. See BaseReranker.rerank for why top-k aggregation is preferred over
# a weighted mean over the whole library.
DEFAULT_TOPK = 10


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
        sim = self.get_similarity_score(cand_texts, corp_texts)
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

        for s, c in zip(scores, candidates):
            c.score = float(s)
        candidates = sorted(candidates, key=lambda x: x.score, reverse=True)
        return candidates
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

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