"""Executor integration contracts for sent-DOI delivery state."""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Final

from omegaconf import DictConfig, OmegaConf
import pytest

import zotero_arxiv_daily.executor as executor_module
from zotero_arxiv_daily.enrichment.pipeline import PipelineEnrichers
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.sent_doi_state import (
    DisabledSentDoiStateStore,
    SentDoiStateWriteError,
    filter_sent_doi_candidates,
)


OLD_DOI: Final = "10.1000/old"
PINNED_DOI: Final = "10.1000/pinned"
TOP_DOI: Final = "10.1000/top"
EXCLUDED_DOI: Final = "10.1000/excluded"


class StateLoadFailure(RuntimeError):
    """Mark an injected state load failure."""


class SmtpFailure(RuntimeError):
    """Mark an injected SMTP failure."""


@dataclass(frozen=True, slots=True)
class StubRetriever:
    callback: Callable[[], list[Paper]]

    def retrieve_papers(self) -> list[Paper]:
        return self.callback()


class RecordingReranker:
    """Record the exact candidates reaching ranking."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.candidates: list[Paper] = []

    def rerank(
        self,
        candidates: list[Paper],
        corpus: list[CorpusPaper],
    ) -> list[Paper]:
        assert corpus
        self.events.append("rerank")
        self.candidates = list(candidates)
        return candidates


class RecordingEnricher:
    """Record the exact candidates reaching pre-rerank enrichment."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.candidates: list[Paper] = []

    def enrich(self, papers: list[Paper]) -> None:
        self.events.append("pipeline")
        self.candidates = list(papers)


class RecordingStateStore:
    """Mutable state-store test double for lifecycle assertions."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.loaded = frozenset[str]()
        self.load_failure: StateLoadFailure | None = None
        self.save_failure: SentDoiStateWriteError | None = None
        self.saved: list[frozenset[str]] = []

    def load(self) -> frozenset[str]:
        self.events.append("load")
        if self.load_failure is not None:
            raise self.load_failure
        return self.loaded

    def save(self, dois: Collection[str]) -> None:
        self.events.append("save")
        self.saved.append(frozenset(dois))
        if self.save_failure is not None:
            raise self.save_failure


@dataclass(frozen=True, slots=True)
class RuntimeHarness:
    executor: Executor
    store: RecordingStateStore
    reranker: RecordingReranker
    events: list[str]


def paper(title: str, doi: str | None = None, score: float = 1.0) -> Paper:
    return Paper(
        source="fixture",
        title=title,
        authors=[],
        abstract="Abstract.",
        url=f"https://papers.example/{title}",
        full_text="Full text.",
        score=score,
        doi=doi,
    )


def executor_config() -> DictConfig:
    return OmegaConf.create(
        {
            "zotero": {"include_path": None, "ignore_path": None},
            "executor": {
                "source": [],
                "reranker": "stub",
                "send_empty": False,
                "max_paper_num": 1,
                "min_score": None,
                "pin_keywords": None,
                "max_pinned_num": 20,
                "enrich_workers": 1,
            },
            "llm": {"api": {"key": "test", "base_url": "https://llm.example"}},
            "reranker": {},
        }
    )


def make_runtime(monkeypatch: pytest.MonkeyPatch) -> RuntimeHarness:
    events: list[str] = []
    store = RecordingStateStore(events)
    reranker = RecordingReranker(events)
    executor = Executor.__new__(Executor)
    executor.config = executor_config()
    executor.sent_doi_state_store = store
    executor.pipeline_enrichers = PipelineEnrichers()
    executor.retrievers = {}
    setattr(executor, "reranker", reranker)

    def fetch_corpus() -> list[CorpusPaper]:
        events.append("zotero")
        return [CorpusPaper("Corpus", "Abstract.", datetime(2026, 1, 1), [])]

    monkeypatch.setattr(executor, "fetch_zotero_corpus", fetch_corpus)
    monkeypatch.setattr(executor, "filter_corpus", lambda corpus: corpus)
    monkeypatch.setattr(executor, "_enrich_papers", lambda papers: events.append("final"))
    monkeypatch.setattr(
        executor_module,
        "render_email",
        lambda papers: events.append("render") or "email",
    )
    monkeypatch.setattr(
        executor_module,
        "send_email",
        lambda config, content: events.append("smtp"),
    )
    return RuntimeHarness(executor, store, reranker, events)


@pytest.fixture()
def runtime(monkeypatch: pytest.MonkeyPatch) -> RuntimeHarness:
    return make_runtime(monkeypatch)


def test_init_constructs_disabled_store_when_state_is_not_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a config without the exact sent-state opt-in and inert dependencies.
    class Reranker:
        def __init__(self, config: DictConfig) -> None:
            del config

    monkeypatch.setattr(executor_module, "get_reranker_cls", lambda name: Reranker)
    monkeypatch.setattr(
        executor_module,
        "build_pipeline_enrichers",
        lambda config, retrievers: PipelineEnrichers(),
    )
    monkeypatch.setattr(executor_module, "OpenAI", lambda **kwargs: SimpleNamespace())

    # When: Executor constructs its delivery dependencies.
    executor = Executor(executor_config())

    # Then: the existing builder supplies disabled no-op behavior.
    assert isinstance(executor.sent_doi_state_store, DisabledSentDoiStateStore)


def test_run_propagates_load_failure_before_zotero_or_source_work(
    runtime: RuntimeHarness,
) -> None:
    # Given: state loading fails and source work is observable.
    runtime.store.load_failure = StateLoadFailure()
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: runtime.events.append("source") or [])},
    )

    # When: the run starts at the state boundary.
    with pytest.raises(StateLoadFailure):
        runtime.executor.run()

    # Then: failure is fail-closed before every other operation.
    assert runtime.events == ["load"]
    assert runtime.store.saved == []


def test_run_merges_before_filtering_and_keeps_invalid_dois_eligible(
    runtime: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a sent duplicate pair plus invalid and missing DOI candidates.
    runtime.store.loaded = frozenset({OLD_DOI})
    winner = paper("winner", "HTTPS://DOI.ORG/10.1000/OLD")
    winner.abstract = ""
    duplicate = paper("duplicate", OLD_DOI)
    duplicate.abstract = "Merged abstract."
    invalid = paper("invalid", "not-a-doi")
    missing = paper("missing")
    runtime.executor.config.executor.max_paper_num = 0

    def retrieve() -> list[Paper]:
        runtime.events.append("source")
        return [winner, duplicate, invalid, missing]

    setattr(runtime.executor, "retrievers", {"source": StubRetriever(retrieve)})
    enricher = RecordingEnricher(runtime.events)
    runtime.executor.pipeline_enrichers = PipelineEnrichers(abstract=enricher)
    merged_inputs: list[list[Paper]] = []

    def record_filter(papers: list[Paper], sent_dois: Collection[str]) -> list[Paper]:
        runtime.events.append("filter")
        merged_inputs.append(list(papers))
        return filter_sent_doi_candidates(papers, sent_dois)

    monkeypatch.setattr(executor_module, "filter_sent_doi_candidates", record_filter)

    # When: Executor retrieves, merges, filters, enriches, and reranks.
    runtime.executor.run()

    # Then: filtering sees the merged winner, while invalid values continue.
    assert runtime.events[:6] == [
        "load",
        "zotero",
        "source",
        "filter",
        "pipeline",
        "rerank",
    ]
    assert merged_inputs == [[winner, invalid, missing]]
    assert (winner.doi, winner.abstract) == (OLD_DOI, "Merged abstract.")
    assert enricher.candidates == [invalid, missing]
    assert runtime.reranker.candidates == [invalid, missing]
    assert runtime.store.saved == []
