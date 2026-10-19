"""Exceptional encoder-contract recovery repairs rows without inflating hits."""

import numpy as np
import pytest
from loguru import logger

from tests.canned_responses import make_sample_corpus, make_sample_paper
from tests.reranker.test_compute_cache_integration import cached_local
from zotero_arxiv_daily.reranker.cache_integration import cache_session


@pytest.mark.parametrize("old_row", [np.array([1., 2.]), np.array([1., 2., 3.], dtype=np.float32)])
def test_incompatible_hits_are_repaired_and_reported_as_misses(cached_local, old_row):
    # Given individually valid cached rows contradicting the actual encoder.
    reranker, cache, calls = cached_local
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper(title="Private A"), make_sample_paper(title="Private B")]
    reranker.config.compute_cache.enabled = False
    expected = [(p.title, p.score) for p in reranker.rerank(papers, corpus)]
    calls.clear()
    reranker.config.compute_cache.enabled = True
    session = cache_session(reranker)
    assert session is not None
    texts = [f"{p.title}\n{p.abstract}".strip() for p in papers + corpus]
    old_keys = [cache.make_key("vector", [session.identity, text]) for text in (texts[0], texts[2])]
    for key in old_keys:
        cache.put_vector(key, old_row)
    records = []
    sink = logger.add(lambda message: records.append(message.record))
    try:
        # When the fresh miss reveals the incompatible old format.
        ranked = reranker.rerank(papers, corpus)
    finally:
        logger.remove(sink)

    # Then real math matches cold, every unique text is encoded once, and counts are honest.
    assert [(p.title, p.score) for p in ranked] == expected
    assert calls == [[texts[1]], [texts[0], texts[2]]]
    assert len(records) == 1
    assert records[0]["level"].name == "WARNING"
    assert records[0]["message"] == (
        "Compute cache: vector_hits=0 vector_misses=3 score_hits=0 score_misses=1 bytes=0 errors=1"
    )
    for key in old_keys:
        row = cache.get_vector(key)
        assert row is not None
        assert row.shape == (3,)
        assert row.dtype == np.dtype("float64")
    # A new score context must reuse repaired vectors, not hide behind the snapshot.
    next_ranked = reranker.rerank(list(reversed(papers)), corpus)
    assert {p.title: p.score for p in next_ranked} == dict(expected)
    assert calls == [[texts[1]], [texts[0], texts[2]]]


@pytest.mark.parametrize("malformed", [np.ones((0, 3)), np.ones((1, 2)), np.ones((1, 3), dtype=np.float32)])
def test_malformed_refresh_is_not_masked_by_old_cached_rows(cached_local, monkeypatch, malformed):
    # Given a valid first fresh batch but an encoder producing a broken repair batch.
    reranker, cache, calls = cached_local
    session = cache_session(reranker)
    assert session is not None
    key = cache.make_key("vector", [session.identity, "old"])
    cache.put_vector(key, np.array([1., 2.]))
    batches = iter([np.ones((1, 3)), malformed])

    def encode(texts):
        calls.append(list(texts))
        return next(batches)

    monkeypatch.setattr(reranker, "get_embeddings", encode)

    # When incompatible old rows need the malformed second batch.
    with pytest.raises(ValueError):
        session.embeddings(["new", "old"])

    # Then the model bug surfaces instead of returning stale or promoted vectors.
    assert calls == [["new"], ["old"]]
