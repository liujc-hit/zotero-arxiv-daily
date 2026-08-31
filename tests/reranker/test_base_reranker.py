"""Tests for BaseReranker: scoring, sorting, top-k aggregation, unknown reranker."""

from datetime import datetime

import numpy as np
import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.reranker.base import BaseReranker, get_reranker_cls
from zotero_arxiv_daily.protocol import CorpusPaper
from tests.canned_responses import make_sample_paper, make_sample_corpus


class StubReranker(BaseReranker):
    """Reranker with a controlled similarity matrix for deterministic tests.

    Accepts optional ``topk`` and ``recency_half_life_days`` so tests can
    exercise aggregation behavior with a known window size without depending
    on config plumbing.
    """

    def __init__(self, sim_matrix: np.ndarray, topk: int = 10, recency_half_life_days=None):
        self.config = OmegaConf.create(
            {"reranker": {"topk": topk, "recency_half_life_days": recency_half_life_days}}
        )
        self._sim = sim_matrix

    def get_similarity_score(self, s1, s2):
        return self._sim


def test_rerank_scores_and_sorts():
    corpus = make_sample_corpus(3)
    papers = [make_sample_paper(title=f"Paper {i}") for i in range(2)]

    # Paper 1 has higher similarity to all corpus papers
    sim = np.array([
        [0.1, 0.1, 0.1],  # paper 0 — low
        [0.9, 0.9, 0.9],  # paper 1 — high
    ])
    reranker = StubReranker(sim)
    ranked = reranker.rerank(papers, corpus)
    assert ranked[0].title == "Paper 1"
    assert ranked[1].title == "Paper 0"
    assert ranked[0].score > ranked[1].score


def test_rerank_topk_favors_specific_relevance_over_broad_mediocrity():
    """The core fix for the 'recommended papers feel unrelated' problem.

    With top-k aggregation a paper strongly relevant to a FOCUSED slice of the
    library outranks one only weakly relevant to everything. The previous
    weighted-mean aggregation averaged over the whole library and would rank the
    mediocre-but-broad paper first (dilution).
    """
    corpus = make_sample_corpus(5)
    sim = np.array([
        [0.9, 0.0, 0.0, 0.0, 0.0],  # paper A — strongly relevant to 1 paper
        [0.2, 0.2, 0.2, 0.2, 0.2],  # paper B — weakly relevant to all 5
    ])
    papers = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    # top-1 mean: A = 0.9, B = 0.2 -> A wins.
    reranker = StubReranker(sim, topk=1)
    ranked = reranker.rerank(papers, corpus)
    assert ranked[0].title == "A"
    assert ranked[1].title == "B"


def test_rerank_topk_averages_only_k_best():
    """Score must be the mean of only the k largest similarities, not all of them."""
    corpus = make_sample_corpus(4)
    sim = np.array([[0.8, 0.6, 0.1, 0.1]])  # top-2 mean = (0.8 + 0.6) / 2 = 0.7
    papers = [make_sample_paper(title="P")]
    reranker = StubReranker(sim, topk=2)
    ranked = reranker.rerank(papers, corpus)
    assert abs(ranked[0].score - 0.7 * 10) < 1e-6


def test_rerank_uses_title_and_abstract_for_embedding_input():
    """Titles carry strong topical signal and must be fed to the encoder."""
    captured: dict = {}

    class CapturingReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            captured["cand"] = s1
            captured["corp"] = s2
            return np.ones((len(s1), len(s2)))

    corpus = [
        CorpusPaper(
            title="Corpus Title",
            abstract="corpus abstract",
            added_date=datetime(2026, 1, 1),
            paths=[],
        )
    ]
    papers = [make_sample_paper(title="Cand Title", abstract="cand abstract")]
    CapturingReranker(np.ones((1, 1))).rerank(papers, corpus)
    assert "Corpus Title" in captured["corp"][0]
    assert "corpus abstract" in captured["corp"][0]
    assert "Cand Title" in captured["cand"][0]
    assert "cand abstract" in captured["cand"][0]


def test_rerank_single_candidate_single_corpus():
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper()]
    sim = np.array([[0.5]])
    reranker = StubReranker(sim)
    ranked = reranker.rerank(papers, corpus)
    assert len(ranked) == 1
    assert ranked[0].score is not None


def test_rerank_recency_weighting_reweights_topk_matches():
    """recency_half_life_days shifts weight toward recently-added corpus papers.

    The candidate is more similar to the OLD paper (0.9) than the RECENT one
    (0.6). With recency off, the top-k mean is 0.75. With a short half-life,
    the old paper's weight decays to ~0 and the score collapses onto the
    recent paper's similarity (~0.6).
    """
    corpus = [
        CorpusPaper(title="Old match", abstract="a", added_date=datetime(2020, 1, 1), paths=[]),
        CorpusPaper(title="Recent match", abstract="b", added_date=datetime(2026, 1, 1), paths=[]),
    ]
    # rerank() sorts the corpus by added_date desc before embedding, so the
    # similarity matrix columns follow the SORTED order [Recent, Old].
    sim = np.array([[0.6, 0.9]])
    papers = [make_sample_paper(title="P")]

    off = StubReranker(sim, topk=2)
    assert abs(off.rerank(papers, corpus)[0].score - 7.5) < 1e-6

    on = StubReranker(sim, topk=2, recency_half_life_days=90)
    weighted = on.rerank(papers, corpus)[0].score
    # exp(-age_old/90) is ~1e-12 while exp(-age_recent/90) is ~0.07, so the
    # weighted mean converges to the recent similarity (0.6 -> score 6.0).
    assert abs(weighted - 6.0) < 0.5


def test_rerank_recency_disabled_by_default():
    """No recency_half_life_days in config -> exact plain top-k mean."""
    corpus = [
        CorpusPaper(title="Old match", abstract="a", added_date=datetime(2020, 1, 1), paths=[]),
        CorpusPaper(title="Recent match", abstract="b", added_date=datetime(2026, 1, 1), paths=[]),
    ]
    sim = np.array([[0.9, 0.6]])
    reranker = StubReranker(sim, topk=2)  # config lacks the key entirely
    reranker.config = OmegaConf.create({"reranker": {"topk": 2}})  # drop the key
    papers = [make_sample_paper(title="P")]
    assert abs(reranker.rerank(papers, corpus)[0].score - 7.5) < 1e-6


def test_rerank_empty_corpus_assigns_zero_score():
    papers = [make_sample_paper(title="P")]
    reranker = StubReranker(np.zeros((1, 0)))
    ranked = reranker.rerank(papers, [])
    assert ranked[0].score == 0.0


def test_get_reranker_cls_unknown():
    with pytest.raises(ValueError, match="not found"):
        get_reranker_cls("nonexistent_reranker_xyz")
