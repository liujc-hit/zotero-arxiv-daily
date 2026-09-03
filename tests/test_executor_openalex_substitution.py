"""Executor construction contracts for OpenAlex-to-Crossref substitution."""

from datetime import datetime
from types import SimpleNamespace
from typing import Final

import pytest
from omegaconf import DictConfig, OmegaConf

import zotero_arxiv_daily.executor as executor_module
import zotero_arxiv_daily.retriever.crossref_retriever as crossref_module
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.retriever.crossref_retriever import (
    CrossrefRetriever,
    InvalidCrossrefConfigurationError,
)
from zotero_arxiv_daily.retriever.openalex_errors import (
    MissingOpenAlexCredentialsError,
)
from zotero_arxiv_daily.retriever.openalex_retriever import (
    InvalidOpenAlexConfigurationError,
    OpenAlexRetriever,
)

CROSSREF_MAILTO: Final = "curator@example.test"


def executor_config(sources: list[str]) -> DictConfig:
    return OmegaConf.create(
        {
            "zotero": {"include_path": None, "ignore_path": None},
            "source": {
                "arxiv": {"category": ["cs.AI"], "include_cross_list": False},
                "crossref": {"mailto": CROSSREF_MAILTO, "lookback_days": 1},
                "openalex": {
                    "api_keys": [],
                    "allow_anonymous": False,
                    "lookback_days": 30,
                },
            },
            "executor": {
                "source": sources,
                "reranker": "stub",
                "send_empty": False,
                "max_paper_num": 0,
                "min_score": None,
                "pin_keywords": None,
                "max_pinned_num": 20,
                "enrich_workers": 8,
            },
            "llm": {
                "api": {"key": "test-key", "base_url": "https://llm.example/v1"},
                "api_mode": "chat_completion",
                "language": "English",
                "generation_kwargs": {"model": "gpt-4o-mini", "max_tokens": 512},
            },
            "reranker": {},
        }
    )


def stub_unrelated_executor_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reranker:
        def __init__(self, config: DictConfig) -> None:
            del config

    monkeypatch.setattr(executor_module, "get_reranker_cls", lambda name: Reranker)
    monkeypatch.setattr(executor_module, "OpenAI", lambda **kwargs: SimpleNamespace())


def test_init_substitutes_crossref_when_openalex_has_no_request_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: OpenAlex is explicitly requested without a key or anonymous access.
    stub_unrelated_executor_dependencies(monkeypatch)
    config = executor_config(["openalex"])
    configured_source_config = config.executor.source
    configured_sources = list(configured_source_config)

    # When: Executor constructs its requested sources.
    executor = Executor(config)

    # Then: Crossref occupies OpenAlex's effective position without mutating config.
    assert list(executor.retrievers) == ["crossref"]
    assert isinstance(executor.retrievers["crossref"], CrossrefRetriever)
    assert config.executor.source is configured_source_config
    assert list(config.executor.source) == configured_sources


@pytest.mark.parametrize(
    ("api_keys", "allow_anonymous"),
    [
        pytest.param(["openalex-key"], False, id="keyed"),
        pytest.param([], True, id="anonymous"),
    ],
)
def test_init_keeps_usable_openalex_without_crossref_substitution(
    monkeypatch: pytest.MonkeyPatch,
    api_keys: list[str],
    allow_anonymous: bool,
) -> None:
    # Given: OpenAlex has either a key or explicit anonymous access.
    stub_unrelated_executor_dependencies(monkeypatch)
    config = executor_config(["openalex"])
    config.source.openalex.api_keys = api_keys
    config.source.openalex.allow_anonymous = allow_anonymous

    # When: Executor constructs its requested source.
    executor = Executor(config)

    # Then: the effective source remains OpenAlex.
    assert list(executor.retrievers) == ["openalex"]
    assert isinstance(executor.retrievers["openalex"], OpenAlexRetriever)


def test_init_does_not_substitute_crossref_for_malformed_openalex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: OpenAlex credential configuration is malformed rather than missing.
    stub_unrelated_executor_dependencies(monkeypatch)
    config = executor_config(["openalex"])
    config.source.openalex.api_keys = "not-a-list"

    # When/Then: the exact OpenAlex configuration error propagates.
    with pytest.raises(InvalidOpenAlexConfigurationError):
        _ = Executor(config)


def test_init_propagates_crossref_config_error_for_openalex_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: missing OpenAlex identity requires Crossref, whose mailto is absent.
    stub_unrelated_executor_dependencies(monkeypatch)
    config = executor_config(["openalex"])
    config.source.crossref.mailto = None

    # When/Then: Crossref's existing typed configuration error remains visible.
    with pytest.raises(InvalidCrossrefConfigurationError):
        _ = Executor(config)


@pytest.mark.parametrize(
    ("configured_sources", "effective_sources"),
    [
        pytest.param(
            ["openalex", "arxiv", "crossref"],
            ["crossref", "arxiv"],
            id="substitution-first",
        ),
        pytest.param(
            ["crossref", "arxiv", "openalex"],
            ["crossref", "arxiv"],
            id="explicit-first",
        ),
        pytest.param(
            ["arxiv", "openalex", "crossref"],
            ["arxiv", "crossref"],
            id="substitution-after-peer",
        ),
    ],
)
def test_init_keeps_first_effective_crossref_position_without_duplicate_client(
    monkeypatch: pytest.MonkeyPatch,
    configured_sources: list[str],
    effective_sources: list[str],
) -> None:
    # Given: missing-identity OpenAlex and explicit Crossref share one source list.
    stub_unrelated_executor_dependencies(monkeypatch)
    contacts: list[str] = []

    class RecordingCrossrefClient:
        def __init__(self, mailto: str) -> None:
            contacts.append(mailto)

    monkeypatch.setattr(crossref_module, "CrossrefClient", RecordingCrossrefClient)
    config = executor_config(configured_sources)

    # When: sources are constructed in configured order.
    executor = Executor(config)

    # Then: first effective position wins and Crossref is constructed exactly once.
    assert list(executor.retrievers) == effective_sources
    assert contacts == [CROSSREF_MAILTO]


def test_run_does_not_invoke_crossref_for_retrieval_time_openalex_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: keyed OpenAlex constructs successfully but loses identity at retrieval.
    stub_unrelated_executor_dependencies(monkeypatch)
    config = executor_config(["openalex"])
    config.source.openalex.api_keys = ["openalex-key"]
    crossref_contacts: list[str] = []

    class RecordingCrossrefClient:
        def __init__(self, mailto: str) -> None:
            crossref_contacts.append(mailto)

    def fail_during_retrieval(self: OpenAlexRetriever) -> list[Paper]:
        del self
        raise MissingOpenAlexCredentialsError from None

    monkeypatch.setattr(crossref_module, "CrossrefClient", RecordingCrossrefClient)
    monkeypatch.setattr(OpenAlexRetriever, "retrieve_papers", fail_during_retrieval)
    executor = Executor(config)
    executor.fetch_zotero_corpus = lambda: [
        CorpusPaper("Corpus", "Corpus abstract", datetime(2026, 1, 1), [])
    ]
    executor.filter_corpus = lambda corpus: corpus

    # When: the configured OpenAlex source fails after construction.
    executor.run()

    # Then: retrieval handling does not construct or invoke Crossref as a fallback.
    assert list(executor.retrievers) == ["openalex"]
    assert crossref_contacts == []
