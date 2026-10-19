"""Built-in backend identities and encrypted-store integration without network."""

from types import SimpleNamespace

from cryptography.fernet import Fernet
from omegaconf import open_dict
import pytest

from tests.canned_responses import make_sample_corpus, make_sample_paper
from tests.reranker.test_compute_cache_integration import MemoryCache, cached_local
from zotero_arxiv_daily.reranker.api import ApiReranker
from zotero_arxiv_daily.reranker.local import LocalReranker


@pytest.mark.parametrize("field,value", [
    ("base_url", "https://example.invalid/another/deployment/v1"),
    ("base_url", "https://example.invalid/v1?deployment=other"),
    ("base_url", "https://other-user@EXAMPLE.invalid/v1"),
    ("model", "different-embedding-model"), ("key", "another-fake-key"),
])
def test_api_identity_changes_encode_all_rows(config, monkeypatch, field, value):
    # Given a deterministic local replacement for the existing API client seam.
    with open_dict(config):
        config.compute_cache = {"enabled": True, "model_revision": "immutable-v1"}
    config.reranker.api.base_url = "https://example.invalid/v1"
    calls = []

    def create(*, input, model):
        calls.append(list(input))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1., 2., 3.]) for _ in input])

    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kwargs:
                        SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    reranker = ApiReranker(config)
    reranker._compute_cache = MemoryCache()
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)

    # When the full deployment route, model or credential scope changes.
    config.reranker.api[field] = value
    reranker.rerank(papers, corpus)

    # Then old vectors cannot alias a different encoder or route.
    assert [len(call) for call in calls] == [2, 2]


def test_api_warm_snapshot_and_vector_hits_make_no_probe_requests(config, monkeypatch):
    # Given the existing API batching seam with a batch size of one.
    with open_dict(config):
        config.compute_cache = {"enabled": True, "model_revision": "immutable-v1"}
    config.reranker.api.batch_size = 1
    calls = []

    def create(*, input, model):
        calls.append(list(input))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1., 2.]) for _ in input])

    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kwargs:
                        SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    reranker = ApiReranker(config)
    reranker._compute_cache = MemoryCache()
    papers = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(1)
    reranker.rerank(papers, corpus)
    reranker.rerank(papers, corpus)

    # When ordered candidates change, producing a score miss but vector hits.
    reranker.rerank(list(reversed(papers)), corpus)

    # Then only the original three normal batches were requested.
    assert [len(call) for call in calls] == [1, 1, 1]


def test_encrypted_store_reopens_with_scores_for_current_objects(cached_local, tmp_path):
    # Given a real isolated encrypted store, without keys read from the environment.
    reranker, fake, calls = cached_local
    with open_dict(reranker.config):
        reranker.config.compute_cache.key = Fernet.generate_key().decode()
        reranker.config.compute_cache.path = str(tmp_path / "compute")
    del reranker._compute_cache
    original = [make_sample_paper(title="A"), make_sample_paper(title="B")]
    corpus = make_sample_corpus(1)
    expected = [(paper.title, paper.score) for paper in reranker.rerank(original, corpus)]
    reopened = LocalReranker(reranker.config)
    reopened._encoder = reranker._encoder
    current = [make_sample_paper(title="A"), make_sample_paper(title="B")]

    # When a new reranker opens the persisted store and ranks current paper objects.
    result = reopened.rerank(current, corpus)

    # Then the encrypted snapshot is sufficient without vector reads or model calls.
    assert [(paper.title, paper.score) for paper in result] == expected
    assert len(calls) == 1
    assert all(any(paper is item for item in current) for paper in result)
    assert reopened._compute_cache is not None
    assert reopened._compute_cache.stats().score_hits == 1
    assert reopened._compute_cache.stats().vector_hits == 0


def test_opened_cache_is_reused_within_reranker(cached_local, monkeypatch):
    # Given a configured object without an acquired store yet.
    reranker, cache, calls = cached_local
    del reranker._compute_cache
    opens = []

    def open_cache(config):
        opens.append(config)
        return cache

    monkeypatch.setattr("zotero_arxiv_daily.compute_cache.open_compute_cache", open_cache)
    papers = [make_sample_paper(title="A")]
    corpus = make_sample_corpus(1)

    # When two rankings use one reranker object.
    reranker.rerank(papers, corpus)
    reranker.rerank(papers, corpus)

    # Then the public store factory is called once with the current config.
    assert opens == [reranker.config]
    assert len(calls) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_api_failure_has_no_adapter_retry_or_probe(config, monkeypatch, enabled):
    # Given an API endpoint failure and either cache activation state.
    with open_dict(config):
        config.compute_cache = {"enabled": enabled, "model_revision": "immutable-v1"}
    calls = []

    def create(*, input, model):
        calls.append(list(input))
        raise LookupError

    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kwargs:
                        SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    reranker = ApiReranker(config)
    reranker._compute_cache = MemoryCache()

    # When ranking reaches the existing backend error boundary.
    with pytest.raises(LookupError):
        reranker.rerank([make_sample_paper(title="A")], make_sample_corpus(1))

    # Then the adapter neither probes the route nor adds a retry.
    assert len(calls) == 1
