from abc import ABC, abstractmethod
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
        k = min(self._topk(), n_corpus)
        if k >= n_corpus:
            topk_sim = sim
        else:
            # indices of the k largest similarities per candidate row
            topk_idx = np.argpartition(sim, -k, axis=1)[:, -k:]
            rows = np.arange(len(candidates))[:, None]
            topk_sim = sim[rows, topk_idx]
        scores = topk_sim.mean(axis=1) * 10.0  # keep the historical ~0-10 score scale

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