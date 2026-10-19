"""Cache failures never replace ranking math or alter custom plugin calls."""

import numpy as np
import pytest

from tests.canned_responses import make_sample_corpus, make_sample_paper
from tests.reranker.test_compute_cache_integration import cached_local
from zotero_arxiv_daily.compute_cache import ScoreSnapshot
from zotero_arxiv_daily.reranker.base import BaseReranker


@pytest.mark.parametrize("scores,order", [
    ((float("nan"),), (0,)), ((1.0,), (1,)),
    ((1.0,), (True,)), ((1.0, 2.0), (0,)),
])
def test_invalid_snapshot_falls_back_to_real_math(cached_local, scores, order):
    # Given an untrusted snapshot replacing the store's normal validation.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)
    expected = papers[0].score
    key = next(iter(cache.scores))
    cache.scores[key] = ScoreSnapshot(scores=scores, order=order)
    papers[0].score = -123.0

    # When ranking sees the invalid cache boundary value.
    reranker.rerank(papers, corpus)

    # Then the actual math is used with warm vectors rather than the bad scores.
    assert papers[0].score == expected
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", [
    np.array([np.nan, 1., 2.]), np.array([1., 2.]),
    np.array([1, 2, 3]), np.ones((1, 3)),
])
def test_invalid_vector_rows_are_rejected_before_encoding(cached_local, invalid):
    # Given warm vectors, no score snapshot and one incompatible vector row.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)
    expected = papers[0].score
    cache.scores.clear()
    key = next(iter(cache.vectors))
    cache.vectors[key] = invalid

    # When partial hits are validated before making any encoder request.
    reranker.rerank(papers, corpus)

    # Then invalid rows cannot affect scoring and one combined repair call suffices.
    assert papers[0].score == expected
    assert [len(call) for call in calls] == [2, 2]


def test_empty_corpus_flushes_without_encoding(cached_local):
    # Given enabled caching and no library rows.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]

    # When ranking succeeds on an empty library.
    ranked = reranker.rerank(papers, [])

    # Then historical zero scores and object identity remain, with status flushed.
    assert ranked is papers
    assert papers[0].score == 0.0
    assert calls == []
    assert cache.flushes == 1


def test_empty_candidates_flush_without_encoding(cached_local):
    # Given an enabled cache but no candidates to score.
    reranker, cache, calls = cached_local

    # When the public ranking surface receives an empty candidate sequence.
    ranked = reranker.rerank([], make_sample_corpus(1))

    # Then there is no numerical work, but successful status is flushed.
    assert ranked == []
    assert calls == []
    assert cache.flushes == 1


def test_custom_plugin_without_identity_bypasses_both_layers(cached_local):
    # Given a custom legacy similarity implementation and configured cache.
    local, cache, calls = cached_local
    similarity_calls = []

    class Custom(BaseReranker):
        def get_similarity_score(self, s1, s2):
            similarity_calls.append((s1, s2))
            return np.full((len(s1), len(s2)), 0.25)

    reranker = Custom(local.config)
    reranker._compute_cache = cache
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)

    # When custom scoring is invoked twice.
    reranker.rerank(papers, corpus)
    reranker.rerank(papers, corpus)

    # Then no caching changes its authoritative legacy method calls.
    assert len(similarity_calls) == 2
    assert papers[0].score == 2.5
    assert cache.score_reads == cache.vector_reads == 0


def test_failed_vector_read_does_not_probe_or_retry_encoder(cached_local, monkeypatch):
    # Given a cache read failure at the optional adapter boundary.
    reranker, cache, calls = cached_local

    def fail(key):
        raise OSError("private cache error must not be logged")

    monkeypatch.setattr(cache, "get_vector", fail)

    # When ranking needs candidates and library vectors.
    reranker.rerank([make_sample_paper(title="A")], make_sample_corpus(1))

    # Then exactly one normal encoder request occurs.
    assert [len(call) for call in calls] == [2]


def test_flush_failure_does_not_mask_scores(cached_local, monkeypatch):
    # Given a cache whose durable flush fails.
    reranker, cache, calls = cached_local

    def fail():
        raise OSError("private path must not be logged")

    monkeypatch.setattr(cache, "flush", fail)

    # When normal ranking finishes successfully.
    ranked = reranker.rerank([make_sample_paper(title="A")], make_sample_corpus(1))

    # Then optional persistence cannot mask the returned numerical result.
    assert np.isfinite(ranked[0].score)
    assert len(calls) == 1


def test_missing_revision_bypasses_both_cache_layers(cached_local):
    # Given enabled caching without an explicit immutable revision.
    reranker, cache, calls = cached_local
    reranker.config.compute_cache.model_revision = None
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)

    # When ranking is requested twice.
    reranker.rerank(papers, corpus)
    reranker.rerank(papers, corpus)

    # Then the historical encoder path is unchanged.
    assert len(calls) == 2
    assert cache.score_reads == cache.vector_reads == 0
