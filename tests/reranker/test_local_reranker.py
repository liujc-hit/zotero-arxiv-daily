"""Tests for LocalReranker — requires sentence-transformers, marked slow."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from zotero_arxiv_daily.reranker.local import LocalReranker


@pytest.fixture()
def sentence_transformer_spy(monkeypatch, config):
    config.executor.debug = True
    state = SimpleNamespace(constructor_calls=[], encode_calls=[])

    class FakeEncoder:
        def encode(self, texts, **kwargs):
            state.encode_calls.append((list(texts), kwargs))
            return np.ones((len(texts), 3))

    encoder = FakeEncoder()

    def create_encoder(model, trust_remote_code):
        state.constructor_calls.append((model, trust_remote_code))
        return encoder

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=create_encoder),
    )
    return state


@pytest.mark.slow
def test_local_reranker(config):
    reranker = LocalReranker(config)
    score = reranker.get_similarity_score(["hello", "world"], ["ping"])
    assert score.shape == (2, 1)


def test_local_reranker_defers_sentence_transformer_construction(
    config, sentence_transformer_spy
):
    # Given a patched SentenceTransformer constructor.

    # When only the local reranker is constructed.
    _ = LocalReranker(config)

    # Then no model is loaded before embeddings are requested.
    assert sentence_transformer_spy.constructor_calls == []


def test_local_reranker_reuses_sentence_transformer_instance(
    config, sentence_transformer_spy
):
    # Given one local reranker instance.
    reranker = LocalReranker(config)

    # When similarity is requested twice.
    _ = reranker.get_similarity_score(["first"], ["corpus"])
    _ = reranker.get_similarity_score(["second"], ["corpus"])

    # Then its SentenceTransformer is constructed only once.
    assert sentence_transformer_spy.constructor_calls == [
        (config.reranker.local.model, True)
    ]


def test_local_reranker_get_embeddings_preserves_encode_kwargs(
    config, sentence_transformer_spy
):
    # Given a local reranker with configured encoder kwargs.
    reranker = LocalReranker(config)

    # When one batch is embedded.
    _ = reranker.get_embeddings(["hello"])

    # Then the configured kwargs and progress behavior reach the encoder.
    assert sentence_transformer_spy.encode_calls == [
        (
            ["hello"],
            {"task": "retrieval", "prompt_name": "document", "show_progress_bar": True},
        )
    ]
