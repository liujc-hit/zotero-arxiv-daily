"""Ranking cache behavior through the built-in local encoder seam."""

import hashlib
import hmac
import json

import numpy as np
import pytest
from omegaconf import open_dict

from tests.canned_responses import make_sample_corpus, make_sample_paper
from zotero_arxiv_daily.reranker.local import LocalReranker
from zotero_arxiv_daily.compute_cache import CacheStats, CacheValue, FloatArray, ScoreSnapshot


class MemoryCache:
    """Mutable in-memory store for isolated ranking adapter tests."""

    def __init__(self) -> None:
        self.vectors: dict[str, FloatArray] = {}
        self.scores: dict[str, ScoreSnapshot] = {}
        self.vector_reads = 0
        self.score_reads = 0
        self.flushes = 0

    def make_key(self, kind: str, parts: CacheValue) -> str:
        return hmac.new(
            b"test-only-secret", json.dumps([kind, parts], sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()

    def get_vector(self, key: str) -> FloatArray | None:
        self.vector_reads += 1
        return self.vectors.get(key)

    def put_vector(self, key: str, array: FloatArray) -> None:
        self.vectors[key] = array.copy()

    def get_scores(self, key: str, candidate_count: int) -> ScoreSnapshot | None:
        self.score_reads += 1
        return self.scores.get(key)

    def put_scores(self, key: str, snapshot: ScoreSnapshot) -> None:
        self.scores[key] = snapshot

    def flush(self) -> bool:
        self.flushes += 1
        return True

    def stats(self) -> CacheStats:
        return CacheStats()


@pytest.fixture()
def cached_local(config, monkeypatch):
    with open_dict(config):
        config.compute_cache = {"enabled": True, "model_revision": "immutable-v1"}
    reranker = LocalReranker(config)
    cache = MemoryCache()
    reranker._compute_cache = cache
    calls = []

    class Encoder:
        def encode(self, texts, **kwargs):
            calls.append(list(texts))
            return np.asarray([
                [1.0, 1.0 + sum(text.encode()) % 13, 1.0 + len(text) % 7]
                for text in texts
            ])

    monkeypatch.setattr(reranker, "_encoder", Encoder())
    return reranker, cache, calls


def test_warm_snapshot_uses_current_objects_without_vector_reads(cached_local):
    # Given a completed cold ranking.
    reranker, cache, calls = cached_local
    papers = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(2)
    cold = reranker.rerank(papers, corpus)
    expected = [(paper.title, paper.score) for paper in cold]
    reads = cache.vector_reads
    current = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    current[0].abstract = papers[0].abstract

    # When the same numerical inputs arrive as new paper objects.
    warm = reranker.rerank(current, corpus)

    # Then cached scores/order apply to current objects with no encoder/read work.
    assert len(calls) == 1
    assert cache.vector_reads == reads
    assert [(paper.title, paper.score) for paper in warm] == expected
    assert all(any(paper is item for item in current) for paper in warm)
    assert cache.flushes == 2


def test_cold_deduplicates_and_change_encodes_one_row(cached_local):
    # Given duplicate candidate inputs sharing one corpus text.
    reranker, cache, calls = cached_local
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper(title="A"), make_sample_paper(title="A")]
    reranker.rerank(papers, corpus)

    # When only one candidate text changes.
    papers[1].abstract = "changed abstract"
    reranker.rerank(papers, corpus)

    # Then each cold distinct input and the single new row are encoded once.
    assert [len(call) for call in calls] == [2, 1]
    assert calls[1] == ["A\nchanged abstract"]


def test_disabled_keeps_combined_legacy_encoder_call(cached_local):
    # Given caching explicitly disabled with duplicated inputs.
    reranker, cache, calls = cached_local
    reranker.config.compute_cache.enabled = False
    papers = [make_sample_paper(title="A"), make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)

    # When the legacy public ranking surface is used twice.
    reranker.rerank(papers, corpus)
    reranker.rerank(papers, corpus)

    # Then duplicates remain in the one combined call per ranking.
    assert [len(call) for call in calls] == [3, 3]
    assert cache.score_reads == cache.vector_reads == 0
