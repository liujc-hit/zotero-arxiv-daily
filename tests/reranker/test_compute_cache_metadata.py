"""Optional score metadata must not reject legacy inputs accepted by ranking."""

from types import SimpleNamespace

import pytest
from loguru import logger
from omegaconf.errors import OmegaConfBaseException

from tests.canned_responses import make_sample_paper
from tests.reranker.test_compute_cache_integration import cached_local
from zotero_arxiv_daily.reranker.cache_integration import cache_session
from zotero_arxiv_daily.reranker.scoring import ScoreContext
from zotero_arxiv_daily.reranker.venue_citation_weighting import VenueCitationWeighting


@pytest.fixture()
def legacy_corpus() -> list[SimpleNamespace]:
    return [SimpleNamespace(
        title="PRIVATE_LEGACY_TITLE", abstract="PRIVATE_LEGACY_ABSTRACT",
        added_date=None, paths=["PRIVATE_LEGACY_PATH"],
    )]


def test_missing_legacy_date_preserves_disabled_scores_and_current_objects(
    cached_local, legacy_corpus, monkeypatch,
):
    # Given a legacy single corpus row accepted with recency disabled.
    reranker, cache, calls = cached_local
    reranker.config.compute_cache.enabled = False
    paper = make_sample_paper(title=legacy_corpus[0].title, abstract=legacy_corpus[0].abstract)
    expected = reranker.rerank([paper], legacy_corpus)[0].score
    calls.clear()
    reranker.config.compute_cache.enabled = True
    current = make_sample_paper(title=paper.title, abstract=paper.abstract)

    def unexpected_score_operation(*args):
        raise AssertionError("Invalid score metadata must bypass get_scores and put_scores")

    monkeypatch.setattr(cache, "get_scores", unexpected_score_operation)
    monkeypatch.setattr(cache, "put_scores", unexpected_score_operation)
    records = []
    sink = logger.add(lambda message: records.append(message.record))
    try:
        # When enabled ranking sees the exact same legacy metadata.
        ranked = reranker.rerank([current], legacy_corpus)
    finally:
        logger.remove(sink)

    # Then scoring is exact, current objects and legacy metadata remain untouched.
    assert ranked[0] is current
    assert current.score == expected
    assert legacy_corpus[0].added_date is None
    assert legacy_corpus[0].paths == ["PRIVATE_LEGACY_PATH"]
    assert calls == [[f"{paper.title}\n{paper.abstract}"]]
    assert cache.score_reads == 0
    assert cache.scores == {}
    assert len(records) == 1
    assert records[0]["level"].name == "WARNING"
    assert records[0]["message"] == (
        "Compute cache: vector_hits=0 vector_misses=1 score_hits=0 score_misses=1 bytes=0 errors=1"
    )
    # Score-cache bypass does not prevent warm vector reuse.
    reranker.rerank([current], legacy_corpus)
    assert len(calls) == 1


@pytest.mark.parametrize("error", [
    AttributeError, ValueError, TypeError, OSError, OverflowError, RecursionError,
    OmegaConfBaseException,
])
def test_score_key_metadata_failures_return_none(cached_local, legacy_corpus, monkeypatch, error):
    # Given cache-only date serialization raising an expected boundary error.
    reranker, cache, calls = cached_local
    session = cache_session(reranker)
    assert session is not None

    class LegacyDate:
        def isoformat(self):
            raise error("PRIVATE_METADATA_ERROR")

    monkeypatch.setattr(legacy_corpus[0], "added_date", LegacyDate())
    context = ScoreContext(10, None, None, None, None, 1)

    # When constructing the optional score identity, not the actual score.
    key = session.score_key([], legacy_corpus, context)

    # Then no invalid identity or store access escapes the private boundary.
    assert key is None
    assert session.errors == 1
    assert cache.score_reads == 0
    assert calls == []


def test_recency_enabled_invalid_date_still_raises(cached_local, legacy_corpus):
    # Given the same legacy row with recency explicitly enabled.
    reranker, cache, calls = cached_local
    reranker.config.reranker.recency_half_life_days = 2.0

    # When the actual scoring algorithm needs the invalid date.
    with pytest.raises(TypeError):
        reranker.rerank([make_sample_paper()], legacy_corpus)

    # Then optional caching has not invented a date or masked the scorer error.
    assert calls == []


@pytest.mark.parametrize("enabled", [False, True])
def test_multiple_invalid_dates_preserve_sort_failure(cached_local, legacy_corpus, enabled):
    # Given dates that fail the original multi-row sorting algorithm.
    reranker, cache, calls = cached_local
    reranker.config.compute_cache.enabled = enabled

    # When ranking needs to compare two invalid dates.
    with pytest.raises(TypeError):
        reranker.rerank([make_sample_paper()], legacy_corpus * 2)

    # Then cache handling does not repair or swallow the baseline sort error.
    assert calls == []
    assert cache.score_reads == 0


def test_malformed_proxy_bypasses_score_key(cached_local, monkeypatch):
    # Given a malformed legacy proxy that cannot become private key metadata.
    reranker, cache, calls = cached_local
    session = cache_session(reranker)
    assert session is not None
    paper = make_sample_paper()
    monkeypatch.setattr(paper, "venue_citation_proxy", [1.0])
    context = ScoreContext(10, None, None, VenueCitationWeighting(0.1, 2.0), None, 1)

    # When the optional cache examines that proxy, without invoking the scorer.
    key = session.score_key([paper], [], context)

    # Then invalid metadata bypasses the score store rather than crashing key building.
    assert key is None
    assert session.errors == 1
    assert cache.score_reads == 0
    assert calls == []
