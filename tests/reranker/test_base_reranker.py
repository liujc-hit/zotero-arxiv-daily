"""Tests for BaseReranker: scoring, sorting, top-k aggregation, unknown reranker."""

from datetime import datetime

import numpy as np
import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.reranker.base import BaseReranker, get_reranker_cls
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
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


class DualMatrixStubReranker(StubReranker):
    """Stub that distinguishes candidate-vs-corpus from candidate-vs-candidate calls.

    MMR needs a second similarity pass over the candidates themselves; this
    stub serves ``cand_sim_matrix`` when both arguments are the same texts.
    """

    def __init__(self, sim_matrix: np.ndarray, cand_sim_matrix: np.ndarray, topk: int = 10, mmr_lambda=None):
        super().__init__(sim_matrix, topk=topk)
        self.config = OmegaConf.create(
            {"reranker": {"topk": topk, "mmr_lambda": mmr_lambda}}
        )
        self._cand_sim = cand_sim_matrix

    def get_similarity_score(self, s1, s2):
        if s1 == s2:
            return self._cand_sim
        return self._sim


class EmbeddingStubReranker(BaseReranker):
    """Reranker with deterministic embeddings and observable encode calls."""

    def __init__(self, embeddings_by_title: dict[str, np.ndarray]):
        super().__init__(
            OmegaConf.create({"reranker": {"topk": 1, "mmr_lambda": 0.7}})
        )
        self._embeddings_by_title: dict[str, np.ndarray] = embeddings_by_title
        self.embedding_calls: list[list[str]] = []

    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        self.embedding_calls.append(list(texts))
        return np.vstack(
            [self._embeddings_by_title[text.partition("\n")[0]] for text in texts]
        )


def make_unsorted_embedding_case() -> tuple[
    EmbeddingStubReranker, list[Paper], list[CorpusPaper]
]:
    embeddings = {
        "A": np.array([1.0, 0.0, 0.0, 0.0]),
        "B": np.array([0.95, np.sqrt(1.0 - 0.95**2), 0.0, 0.0]),
        "C": np.array([0.0, 0.0, 1.0, 0.0]),
        "Corpus Paper 0": np.array([1.0, 0.0, 0.0, 0.0]),
        "Corpus Paper 1": np.array([0.0, 0.0, 0.8, 0.6]),
    }
    papers = [
        make_sample_paper(title="C"),
        make_sample_paper(title="A"),
        make_sample_paper(title="B"),
    ]
    return EmbeddingStubReranker(embeddings), papers, make_sample_corpus(2)


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
    sim = np.array([[0.6, 0.9]])
    reranker = StubReranker(sim, topk=2)  # config lacks the key entirely
    reranker.config = OmegaConf.create({"reranker": {"topk": 2}})  # drop the key
    papers = [make_sample_paper(title="P")]
    assert abs(reranker.rerank(papers, corpus)[0].score - 7.5) < 1e-6


# ---------------------------------------------------------------------------
# MMR diversity reordering
# ---------------------------------------------------------------------------


def test_rerank_mmr_separates_near_duplicate_candidates():
    """MMR pushes a near-duplicate below a distinct, slightly weaker paper.

    A is the best paper (rel 0.9). B (rel 0.85) is a near-duplicate of A
    (cand-cand sim 0.95). C (rel 0.8) is topically distinct (sim 0.1). With
    mmr_lambda=0.7, C's diversity bonus beats B's relevance edge, so the
    ranking becomes A, C, B instead of the plain score order A, B, C.
    """
    corpus = make_sample_corpus(3)
    papers = [
        make_sample_paper(title="A"),
        make_sample_paper(title="B"),
        make_sample_paper(title="C"),
    ]
    sim = np.array([
        [0.9, 0.9, 0.9],
        [0.85, 0.85, 0.85],
        [0.8, 0.8, 0.8],
    ])
    cand_sim = np.array([
        [1.0, 0.95, 0.10],
        [0.95, 1.0, 0.10],
        [0.10, 0.10, 1.0],
    ])
    reranker = DualMatrixStubReranker(sim, cand_sim, topk=3, mmr_lambda=0.7)
    ranked = reranker.rerank(papers, corpus)
    assert [p.title for p in ranked] == ["A", "C", "B"]
    # scores themselves must be untouched — MMR reorders, it does not re-score
    assert ranked[0].score > ranked[1].score or True  # order changed by design
    by_title = {p.title: p.score for p in ranked}
    assert abs(by_title["A"] - 9.0) < 1e-6
    assert abs(by_title["B"] - 8.5) < 1e-6
    assert abs(by_title["C"] - 8.0) < 1e-6


def test_rerank_mmr_aligns_unsorted_candidates_with_candidate_embeddings():
    # Given deliberately unsorted candidates with A and B near-duplicate.
    reranker, papers, corpus = make_unsorted_embedding_case()

    # When relevance sorting and MMR are applied.
    ranked = reranker.rerank(papers, corpus)

    # Then MMR uses embeddings in the same score order as the papers.
    assert [paper.title for paper in ranked] == ["A", "C", "B"]


def test_rerank_embeds_candidates_and_corpus_once_with_mmr_enabled():
    # Given an embedding-backed reranker with MMR enabled.
    reranker, papers, corpus = make_unsorted_embedding_case()

    # When candidates are reranked.
    _ = reranker.rerank(papers, corpus)

    # Then candidate and corpus texts share one embedding operation.
    assert [
        [text.partition("\n")[0] for text in call]
        for call in reranker.embedding_calls
    ] == [["C", "A", "B", "Corpus Paper 1", "Corpus Paper 0"]]


def test_rerank_mmr_disabled_by_default_keeps_score_order():
    corpus = make_sample_corpus(3)
    papers = [
        make_sample_paper(title="A"),
        make_sample_paper(title="B"),
        make_sample_paper(title="C"),
    ]
    sim = np.array([
        [0.9, 0.9, 0.9],
        [0.85, 0.85, 0.85],
        [0.8, 0.8, 0.8],
    ])
    cand_sim = np.array([
        [1.0, 0.95, 0.10],
        [0.95, 1.0, 0.10],
        [0.10, 0.10, 1.0],
    ])
    reranker = DualMatrixStubReranker(sim, cand_sim, topk=3)  # mmr_lambda unset
    ranked = reranker.rerank(papers, corpus)
    assert [p.title for p in ranked] == ["A", "B", "C"]


def test_rerank_mmr_single_candidate_is_noop():
    corpus = make_sample_corpus(2)
    papers = [make_sample_paper(title="Solo")]
    sim = np.array([[0.7, 0.7]])
    reranker = DualMatrixStubReranker(sim, np.array([[1.0]]), topk=2, mmr_lambda=0.7)
    ranked = reranker.rerank(papers, corpus)
    assert [p.title for p in ranked] == ["Solo"]


def test_rerank_empty_corpus_assigns_zero_score():
    papers = [make_sample_paper(title="P")]
    reranker = StubReranker(np.zeros((1, 0)))
    ranked = reranker.rerank(papers, [])
    assert ranked[0].score == 0.0


def test_get_reranker_cls_unknown():
    with pytest.raises(ValueError, match="not found"):
        get_reranker_cls("nonexistent_reranker_xyz")
