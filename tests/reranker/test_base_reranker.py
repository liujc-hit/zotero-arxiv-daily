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

    Accepts an optional ``topk`` so tests can exercise top-k aggregation with a
    known window size without depending on config plumbing.
    """

    def __init__(self, sim_matrix: np.ndarray, topk: int = 10):
        self.config = OmegaConf.create({"reranker": {"topk": topk}})
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


def test_rerank_empty_corpus_assigns_zero_score():
    papers = [make_sample_paper(title="P")]
    reranker = StubReranker(np.zeros((1, 0)))
    ranked = reranker.rerank(papers, [])
    assert ranked[0].score == 0.0


def test_get_reranker_cls_unknown():
    with pytest.raises(ValueError, match="not found"):
        get_reranker_cls("nonexistent_reranker_xyz")
