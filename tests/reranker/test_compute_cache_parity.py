"""Deterministic scoring parity and fine-grained numerical invalidation."""

from datetime import datetime, timedelta

import numpy as np
from omegaconf import open_dict

from tests.canned_responses import make_sample_corpus, make_sample_paper
from tests.reranker.test_compute_cache_integration import cached_local


def test_disabled_cold_and_warm_are_byte_identical_with_all_adjustments(cached_local, monkeypatch):
    # Given a deterministic encoder and frozen scoring instant with all adjustments.
    reranker, cache, calls = cached_local
    with open_dict(reranker.config):
        reranker.config.reranker.topk = 2
        reranker.config.reranker.recency_half_life_days = 100.0
        reranker.config.reranker.mmr_lambda = 0.4
        reranker.config.reranker.venue_prestige = {
            "enabled": True, "weight": 0.01, "max_multiplier": 1.2,
        }
    corpus = make_sample_corpus(3)

    class Clock:
        @staticmethod
        def now():
            return datetime(2026, 4, 5, 13)

    monkeypatch.setattr("zotero_arxiv_daily.reranker.base.datetime", Clock)
    batches = [[make_sample_paper(title=title) for title in ["C", "A", "B", "A"]]
               for _ in range(3)]
    for papers in batches:
        for index, paper in enumerate(papers):
            paper.venue_citation_proxy = float(index)
    reranker.config.compute_cache.enabled = False
    disabled = reranker.rerank(batches[0], corpus)
    reranker.config.compute_cache.enabled = True

    # When the same numerical inputs use first a cold cache and then a snapshot.
    cold = reranker.rerank(batches[1], corpus)
    warm = reranker.rerank(batches[2], corpus)

    # Then stable ties, MMR order and floating-point scores match exactly.
    assert [paper.title for paper in disabled] == [paper.title for paper in cold] == [paper.title for paper in warm]
    assert np.array([paper.score for paper in disabled]).tobytes() == np.array([paper.score for paper in cold]).tobytes()
    assert np.array([paper.score for paper in cold]).tobytes() == np.array([paper.score for paper in warm]).tobytes()
    assert [len(call) for call in calls] == [7, 6]


def test_changed_addition_time_invalidates_scores_without_new_vectors(cached_local):
    # Given a ranking over sorted Zotero additions.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(2)
    reranker.rerank(papers, corpus)

    # When addition metadata changes without any embedding text change.
    corpus[0].added_date += timedelta(hours=1)
    reranker.rerank(papers, corpus)

    # Then the exact corpus context changes, but every vector remains usable.
    assert len(cache.scores) == 2
    assert len(calls) == 1


def test_new_candidate_encodes_only_new_text(cached_local):
    # Given a fully warm ranking.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)
    current = [make_sample_paper(title="A"), make_sample_paper(title="B")]

    # When one new candidate appears beside a new object for the old text.
    ranked = reranker.rerank(current, corpus)

    # Then only the new candidate is encoded and every output is a current object.
    assert [len(call) for call in calls] == [2, 1]
    assert calls[1][0].startswith("B\n")
    assert all(any(paper is value for value in current) for paper in ranked)


def test_mmr_target_changes_reuse_vectors(cached_local):
    # Given MMR and one cached target size.
    reranker, cache, calls = cached_local
    with open_dict(reranker.config):
        reranker.config.reranker.mmr_lambda = 0.2
    reranker.config.executor.max_paper_num = 1
    papers = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)

    # When the downstream slot count changes the MMR target.
    reranker.config.executor.max_paper_num = 2
    reranker.rerank(papers, corpus)

    # Then only the score/order snapshot is invalidated.
    assert len(cache.scores) == 2
    assert len(calls) == 1


def test_warm_hit_preserves_current_non_scoring_metadata(cached_local):
    # Given cached scores with venue weighting disabled.
    reranker, cache, calls = cached_local
    corpus = make_sample_corpus(1)
    reranker.rerank([make_sample_paper(title="A")], corpus)
    current = make_sample_paper(title="A")
    current.authors = ["Current author"]
    current.venue_citation_proxy = 123.0

    # When only downstream metadata and unrelated configuration change.
    with open_dict(reranker.config):
        reranker.config.executor.min_score = 9.0
    ranked = reranker.rerank([current], corpus)

    # Then the warm hit mutates only the score on the current object.
    assert ranked[0] is current
    assert current.authors == ["Current author"]
    assert current.venue_citation_proxy == 123.0
    assert len(cache.scores) == 1
    assert len(calls) == 1
