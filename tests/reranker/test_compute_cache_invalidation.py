"""Numerical contexts invalidate scores without discarding reusable vectors."""

from datetime import datetime
from typing import Literal, assert_never

import numpy as np
import pytest
from omegaconf import open_dict

from tests.canned_responses import make_sample_corpus, make_sample_paper
from tests.reranker.test_compute_cache_integration import cached_local
from zotero_arxiv_daily.protocol import CorpusPaper


@pytest.mark.parametrize("setting,value", [
    ("topk", 1), ("mmr_lambda", 0.4), ("recency_half_life_days", 1.5),
    ("venue_prestige", {"enabled": True, "weight": 0.2, "max_multiplier": 1.4}),
])
def test_score_settings_reuse_vectors(cached_local, setting, value):
    # Given reusable vectors and a previous score snapshot.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(3)
    reranker.rerank(papers, corpus)

    # When a consumed score setting changes.
    with open_dict(reranker.config):
        reranker.config.reranker[setting] = value
    reranker.rerank(papers, corpus)

    # Then a new snapshot is scored entirely from warm vectors.
    assert len(cache.scores) == 2
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["revision", "model", "encode_kwargs"])
def test_encoder_changes_invalidate_all_vectors(
    cached_local, change: Literal["revision", "model", "encode_kwargs"],
):
    # Given a cold ranking under one explicit encoder identity.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)

    # When the revision, model or actually consumed encode kwargs change.
    match change:
        case "revision":
            reranker.config.compute_cache.model_revision = "immutable-v2"
        case "model":
            reranker.config.reranker.local.model = "different-model"
        case "encode_kwargs":
            reranker._encode_kwargs = {"normalize_embeddings": True}
        case unreachable:
            assert_never(unreachable)
    reranker.rerank(papers, corpus)

    # Then both rows are encoded again, without an encoder probe.
    assert [len(call) for call in calls] == [2, 2]


def test_reordered_candidates_preserve_current_metadata(cached_local):
    # Given prior scores for two candidates.
    reranker, cache, calls = cached_local
    original = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(1)
    reranker.rerank(original, corpus)
    current = [make_sample_paper(title="B"), make_sample_paper(title="A")]
    current[0].venue_citation_proxy = 99.0
    expected = {paper.title: paper.score for paper in original}

    # When input order changes and non-scoring metadata changes.
    ranked = reranker.rerank(current, corpus)

    # Then ordered context misses, vectors hit, and returned objects are current.
    assert len(calls) == 1
    assert len(cache.scores) == 2
    assert all(any(paper is value for value in current) for paper in ranked)
    assert {paper.title: paper.score for paper in ranked} == expected
    assert current[0].venue_citation_proxy == 99.0


def test_corpus_text_change_encodes_only_new_row(cached_local):
    # Given an existing corpus vector.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(2)
    reranker.rerank(papers, corpus)

    # When exactly one Zotero abstract changes.
    corpus[0].abstract = "new corpus abstract"
    reranker.rerank(papers, corpus)

    # Then the update is incremental.
    assert [len(call) for call in calls] == [3, 1]
    assert calls[1] == ["Corpus Paper 0\nnew corpus abstract"]


def test_frozen_recency_weights_invalidate_at_integer_age_boundary(cached_local, monkeypatch):
    # Given two additions on the same day but on opposite sides of an age boundary.
    reranker, cache, calls = cached_local
    with open_dict(reranker.config):
        reranker.config.reranker.recency_half_life_days = 2.0
    corpus = [
        CorpusPaper(title="Recent", abstract="x", paths=[], added_date=datetime(2026, 1, 1, 13)),
        CorpusPaper(title="Older", abstract="y", paths=[], added_date=datetime(2026, 1, 1, 11)),
    ]
    times = iter([datetime(2026, 1, 2, 12), datetime(2026, 1, 2, 14)])
    clock_reads = []

    class Clock:
        @staticmethod
        def now():
            value = next(times)
            clock_reads.append(value)
            return value

    monkeypatch.setattr("zotero_arxiv_daily.reranker.base.datetime", Clock)
    papers = [make_sample_paper(title="A")]
    reranker.rerank(papers, corpus)
    first = papers[0].score

    # When the first addition crosses a whole-day age boundary in the same UTC day.
    reranker.rerank(papers, corpus)

    # Then key and math consume one frozen weight vector per call, never a day bucket.
    assert len(clock_reads) == 2
    assert len(cache.scores) == 2
    assert len(calls) == 1
    vectors = np.asarray([[1., 1. + sum(t.encode()) % 13, 1. + len(t) % 7]
                          for t in [calls[0][0], "Recent\nx", "Older\ny"]])
    unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    sim = unit[0] @ unit[1:].T
    assert first == float((sim * np.exp(-np.array([0., 1.]) / 2)).sum()
                          / np.exp(-np.array([0., 1.]) / 2).sum() * 10)
    weights = np.exp(-np.array([1., 1.]) / 2)
    assert papers[0].score == float((sim * weights).sum() / weights.sum() * 10)


def test_venue_proxy_change_invalidates_only_scores(cached_local):
    # Given enabled venue weighting and cached numerical inputs.
    reranker, cache, calls = cached_local
    with open_dict(reranker.config):
        reranker.config.reranker.venue_prestige = {
            "enabled": True, "weight": 0.1, "max_multiplier": 1.5,
        }
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)

    # When the consumed candidate proxy changes.
    papers[0].venue_citation_proxy = 20.0
    reranker.rerank(papers, corpus)

    # Then numerical scores are recomputed without re-encoding texts.
    assert len(calls) == 1
    assert len(cache.scores) == 2
