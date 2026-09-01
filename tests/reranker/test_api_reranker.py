"""Tests for ApiReranker — uses stub OpenAI client via monkeypatch."""

from types import SimpleNamespace

from zotero_arxiv_daily.reranker.api import ApiReranker


def test_api_reranker_similarity_shape(config, patch_openai):
    reranker = ApiReranker(config)
    score = reranker.get_similarity_score(["hello", "world"], ["ping"])
    assert score.shape == (2, 1)


def test_api_reranker_batching(config, patch_openai):
    reranker = ApiReranker(config)
    s1 = [f"text {i}" for i in range(5)]
    s2 = [f"corpus {i}" for i in range(3)]
    score = reranker.get_similarity_score(s1, s2)
    assert score.shape == (5, 3)


def test_api_reranker_get_embeddings_preserves_batching(config, monkeypatch):
    # Given five texts and an API batch size of two.
    batches: list[list[str]] = []

    def create_embeddings(*, input, model):
        batches.append(list(input))
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in input]
        )

    client = SimpleNamespace(
        embeddings=SimpleNamespace(create=create_embeddings)
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.reranker.api.OpenAI", lambda **kwargs: client
    )
    config.reranker.api.batch_size = 2
    reranker = ApiReranker(config)
    texts = [f"text {index}" for index in range(5)]

    # When embeddings are requested through the shared embedding seam.
    embeddings = reranker.get_embeddings(texts)

    # Then the API receives the same ordered texts in configured batches.
    assert embeddings.shape == (5, 3)
    assert batches == [["text 0", "text 1"], ["text 2", "text 3"], ["text 4"]]
