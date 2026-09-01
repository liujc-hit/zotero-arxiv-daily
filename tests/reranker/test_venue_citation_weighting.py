"""Focused ranking tests for optional venue citation proxy weighting."""

from math import log1p, sqrt
from struct import pack
from typing import final, override

import numpy as np
import pytest
from omegaconf import DictConfig, OmegaConf

from tests.canned_responses import make_sample_corpus, make_sample_paper
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.reranker.base import BaseReranker
from zotero_arxiv_daily.retriever.openalex_client import JsonObject


@final
class _MatrixReranker(BaseReranker):
    def __init__(self, similarities: np.ndarray, config: DictConfig) -> None:
        super().__init__(config)
        self._similarities = similarities

    @override
    def get_similarity_score(
        self,
        s1: list[str],
        s2: list[str],
    ) -> np.ndarray:
        del s1, s2
        return self._similarities


@final
class _EmbeddingReranker(BaseReranker):
    def __init__(
        self,
        embeddings_by_title: dict[str, np.ndarray],
        config: DictConfig,
    ) -> None:
        super().__init__(config)
        self._embeddings_by_title = embeddings_by_title

    @override
    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        return np.vstack(
            [self._embeddings_by_title[text.partition("\n")[0]] for text in texts]
        )


def _enabled_config(
    weight: float,
    max_multiplier: float,
    mmr_lambda: float | None = None,
) -> DictConfig:
    return OmegaConf.create(
        {
            "reranker": {
                "topk": 1,
                "mmr_lambda": mmr_lambda,
                "venue_prestige": {
                    "enabled": True,
                    "weight": weight,
                    "max_multiplier": max_multiplier,
                },
            }
        }
    )


def _score_bytes(papers: list[Paper]) -> bytes:
    scores = [paper.score for paper in papers]
    assert all(score is not None for score in scores)
    return b"".join(pack("!d", score) for score in scores if score is not None)


def test_absent_weighting_is_byte_equivalent_even_when_proxies_are_present() -> None:
    # Given identical candidates with and without proxy values under legacy config.
    similarities = np.array([[0.4, 0.2], [0.8, 0.6]])
    config = OmegaConf.create({"reranker": {"topk": 2}})
    plain = [
        make_sample_paper(title="lower"),
        make_sample_paper(title="higher"),
    ]
    enriched = [
        make_sample_paper(title="lower", venue_citation_proxy=100.0),
        make_sample_paper(title="higher", venue_citation_proxy=0.0),
    ]

    # When both sets are ranked with the subtree absent.
    plain_ranked = _MatrixReranker(similarities, config).rerank(
        plain, make_sample_corpus(2)
    )
    enriched_ranked = _MatrixReranker(similarities, config).rerank(
        enriched, make_sample_corpus(2)
    )

    # Then order and binary float scores are exactly the historical result.
    assert [paper.title for paper in enriched_ranked] == [
        paper.title for paper in plain_ranked
    ]
    assert _score_bytes(enriched_ranked) == _score_bytes(plain_ranked)


@pytest.mark.parametrize(
    "venue_settings",
    [
        pytest.param(None, id="null-subtree"),
        pytest.param("invalid", id="wrong-subtree-type"),
        pytest.param({"enabled": False}, id="disabled-with-missing-fields"),
        pytest.param(
            {"enabled": True, "weight": 0.2},
            id="missing-max-multiplier",
        ),
        pytest.param(
            {"enabled": True, "weight": -0.1, "max_multiplier": 2.0},
            id="negative-weight",
        ),
        pytest.param(
            {"enabled": True, "weight": "0.1", "max_multiplier": 2.0},
            id="wrong-weight-type",
        ),
        pytest.param(
            {"enabled": True, "weight": 0.1, "max_multiplier": 0.9},
            id="small-max-multiplier",
        ),
        pytest.param(
            {"enabled": True, "weight": float("nan"), "max_multiplier": 2.0},
            id="non-finite-weight",
        ),
        pytest.param(
            {"enabled": "true", "weight": 0.1, "max_multiplier": 2.0},
            id="wrong-enabled-type",
        ),
    ],
)
def test_invalid_or_disabled_settings_resolve_to_neutral(
    venue_settings: JsonObject | str | None,
) -> None:
    # Given an unusable future config subtree and a large proxy value.
    config = OmegaConf.create(
        {
            "reranker": {
                "topk": 1,
                "venue_prestige": venue_settings,
            }
        }
    )
    paper = make_sample_paper(venue_citation_proxy=1_000.0)

    # When ranking resolves the settings defensively.
    ranked = _MatrixReranker(np.array([[0.8]]), config).rerank(
        [paper], make_sample_corpus(1)
    )

    # Then the historical score is unchanged and no exception escapes.
    assert ranked[0].score == 8.0


@pytest.mark.parametrize(
    ("similarity", "proxy", "weight", "max_multiplier", "expected"),
    [
        pytest.param(
            0.4,
            3.0,
            0.5,
            3.0,
            4.0 * (1.0 + 0.5 * log1p(3.0)),
            id="formula",
        ),
        pytest.param(0.6, 100.0, 1.0, 1.25, 7.5, id="multiplier-cap"),
        pytest.param(0.9, 3.0, 1.0, 3.0, 10.0, id="upper-score-clip"),
        pytest.param(-0.2, 3.0, 1.0, 3.0, 0.0, id="lower-score-clip"),
    ],
)
def test_enabled_weighting_uses_formula_cap_and_score_clip(
    similarity: float,
    proxy: float,
    weight: float,
    max_multiplier: float,
    expected: float,
) -> None:
    # Given one candidate with an available finite nonnegative proxy.
    paper = make_sample_paper(venue_citation_proxy=proxy)
    reranker = _MatrixReranker(
        np.array([[similarity]]),
        _enabled_config(weight, max_multiplier),
    )

    # When relevance scoring and optional weighting are applied.
    ranked = reranker.rerank([paper], make_sample_corpus(1))

    # Then the historical score is multiplied, bounded, and clipped exactly once.
    assert ranked[0].score == pytest.approx(expected)


@pytest.mark.parametrize(
    "proxy",
    [
        pytest.param(None, id="missing"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(-0.1, id="negative"),
    ],
)
def test_unavailable_proxy_has_a_neutral_multiplier(proxy: float | None) -> None:
    # Given enabled weighting but no usable proxy on the paper.
    paper = make_sample_paper(venue_citation_proxy=proxy)
    reranker = _MatrixReranker(
        np.array([[0.7]]),
        _enabled_config(weight=1.0, max_multiplier=3.0),
    )

    # When the candidate is ranked.
    ranked = reranker.rerank([paper], make_sample_corpus(1))

    # Then its multiplier is exactly neutral.
    assert ranked[0].score == 7.0


def test_weighted_score_permutation_keeps_mmr_embeddings_aligned() -> None:
    # Given C whose proxy moves it between A and near-duplicate B by score.
    embeddings = {
        "A": np.array([1.0, 0.0, 0.0, 0.0]),
        "B": np.array([0.95, sqrt(1.0 - 0.95**2), 0.0, 0.0]),
        "C": np.array([0.0, 0.0, 1.0, 0.0]),
        "Corpus Paper 0": np.array([1.0, 0.0, 0.0, 0.0]),
        "Corpus Paper 1": np.array([0.0, 0.0, 0.8, 0.6]),
    }
    papers = [
        make_sample_paper(title="A"),
        make_sample_paper(title="B"),
        make_sample_paper(title="C", venue_citation_proxy=3.0),
    ]
    reranker = _EmbeddingReranker(
        embeddings,
        _enabled_config(weight=0.15, max_multiplier=2.0, mmr_lambda=0.7),
    )

    # When weighting changes the stable score permutation before MMR.
    ranked = reranker.rerank(papers, make_sample_corpus(2))

    # Then MMR observes embeddings under that same A, C, B permutation.
    assert [paper.title for paper in ranked] == ["A", "C", "B"]
    by_title = {paper.title: paper.score for paper in ranked}
    assert by_title["C"] == pytest.approx(8.0 * (1.0 + 0.15 * log1p(3.0)))
