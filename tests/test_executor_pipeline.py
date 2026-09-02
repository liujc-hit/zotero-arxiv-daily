"""Focused integration tests for Executor's recommendation pipeline."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
import json
from threading import Event
from types import SimpleNamespace

from loguru import logger
from omegaconf import DictConfig, OmegaConf
import pytest

import zotero_arxiv_daily.executor as executor_module
from zotero_arxiv_daily.enrichment.pipeline import PipelineEnrichers
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.sent_doi_state import DisabledSentDoiStateStore

type ConfigValue = str | int | float | bool | None | list[str]


@dataclass(frozen=True, slots=True)
class StubRetriever:
    callback: Callable[[], list[Paper]]

    def retrieve_papers(self) -> list[Paper]:
        return self.callback()

    def fetch_full_text(self, paper: Paper) -> str | None:
        return paper.full_text


class RecordingReranker:
    def __init__(self) -> None:
        self.candidates: list[Paper] = []

    def rerank(
        self, candidates: list[Paper], corpus: list[CorpusPaper]
    ) -> list[Paper]:
        assert corpus
        self.candidates = list(candidates)
        return candidates


class SensitiveSourceError(RuntimeError):
    """A source failure whose message must not reach logs."""


def paper(title: str, score: float | None = 1.0) -> Paper:
    return Paper(
        source="fixture",
        title=title,
        authors=[f"{title} author"],
        abstract="Abstract.",
        url=f"https://papers.example/{title}",
        full_text="Full text.",
        score=score,
    )


def executor_config(
    **executor_values: str | int | float | bool | None | list[str],
) -> DictConfig:
    executor: dict[str, ConfigValue] = {
        "source": [],
        "reranker": "stub",
        "send_empty": False,
        "max_paper_num": 0,
        "min_score": None,
        "pin_keywords": None,
        "max_pinned_num": 20,
        "enrich_workers": 8,
    }
    executor.update(executor_values)
    return OmegaConf.create(
        {
            "zotero": {"include_path": None, "ignore_path": None},
            "executor": executor,
            "llm": {
                "api": {"key": "test-key", "base_url": "https://llm.example/v1"},
                "api_mode": "chat_completion",
                "language": "English",
                "generation_kwargs": {"model": "gpt-4o-mini", "max_tokens": 512},
            },
            "reranker": {},
        }
    )


@pytest.fixture()
def runtime_executor(monkeypatch: pytest.MonkeyPatch) -> Executor:
    executor = Executor.__new__(Executor)
    executor.config = executor_config()
    executor.sent_doi_state_store = DisabledSentDoiStateStore()
    executor.include_path_patterns = None
    executor.ignore_path_patterns = None
    executor.pipeline_enrichers = PipelineEnrichers()
    monkeypatch.setattr(executor, "openai_client", SimpleNamespace(), raising=False)
    executor.fetch_zotero_corpus = lambda: [
        CorpusPaper("Corpus", "Corpus abstract", datetime(2026, 1, 1), [])
    ]
    executor.filter_corpus = lambda corpus: corpus
    monkeypatch.setattr(executor_module, "render_email", lambda papers: "email")
    monkeypatch.setattr(executor_module, "send_email", lambda config, content: None)
    return executor


def test_run_retrieves_concurrently_but_reranks_in_configured_source_order(
    runtime_executor: Executor,
) -> None:
    # Given: the first configured source can finish only after the second one.
    second_finished = Event()
    completion_order: list[str] = []
    first = paper("first")
    second = paper("second")

    def retrieve_first() -> list[Paper]:
        assert second_finished.wait(timeout=2)
        completion_order.append("first")
        return [first]

    def retrieve_second() -> list[Paper]:
        completion_order.append("second")
        second_finished.set()
        return [second]

    reranker = RecordingReranker()
    setattr(
        runtime_executor,
        "retrievers",
        {
            "configured-first": StubRetriever(retrieve_first),
            "configured-second": StubRetriever(retrieve_second),
        },
    )
    setattr(runtime_executor, "reranker", reranker)

    # When: Executor runs retrieval and ranking.
    runtime_executor.run()

    # Then: completion overlaps, while ranking preserves configured order.
    assert completion_order == ["second", "first"]
    assert reranker.candidates == [first, second]


def test_run_isolates_source_failure_without_logging_exception_text(
    runtime_executor: Executor,
) -> None:
    # Given: one source leaks sensitive text in its exception and one succeeds.
    secret = "token=private 10.5555/LEAK https://private.example/work"
    healthy = paper("healthy")

    def fail() -> list[Paper]:
        raise SensitiveSourceError(secret)

    reranker = RecordingReranker()
    setattr(
        runtime_executor,
        "retrievers",
        {
            "broken": StubRetriever(fail),
            "healthy": StubRetriever(lambda: [healthy]),
        },
    )
    setattr(runtime_executor, "reranker", reranker)
    output = StringIO()
    sink = logger.add(output, format="{message}")
    try:
        # When: Executor collects both source results.
        runtime_executor.run()
    finally:
        logger.remove(sink)

    # Then: the healthy source reaches ranking and sensitive details stay redacted.
    assert reranker.candidates == [healthy]
    assert "SensitiveSourceError" in output.getvalue()
    assert secret not in output.getvalue()


@pytest.mark.parametrize("malformed_workers", [None, "not-an-integer"])
def test_run_passes_pinned_then_normal_to_final_enrichment_with_default_workers(
    runtime_executor: Executor,
    monkeypatch: pytest.MonkeyPatch,
    malformed_workers,
) -> None:
    # Given: one pinned and one normal selection plus malformed worker config.
    pinned = paper("Pinned topic", 1.0)
    normal = paper("Normal topic", 9.0)
    runtime_executor.config = executor_config(
        max_paper_num=1,
        pin_keywords=["pinned"],
        enrich_workers=malformed_workers,
    )
    setattr(
        runtime_executor,
        "retrievers",
        {"source": StubRetriever(lambda: [normal, pinned])},
    )
    setattr(runtime_executor, "reranker", RecordingReranker())
    calls: list[tuple[list[Paper], int]] = []

    def enrich_final(papers, retrievers, client, llm_params, workers):
        del retrievers, client, llm_params
        calls.append((list(papers), workers))
        return papers

    def forbidden_legacy_call(*args, **kwargs):
        del args, kwargs
        pytest.fail("Executor called a legacy two-step generation wrapper")

    monkeypatch.setattr(
        executor_module, "enrich_final_papers", enrich_final, raising=False
    )
    monkeypatch.setattr(Paper, "generate_tldr", forbidden_legacy_call)
    monkeypatch.setattr(Paper, "generate_affiliations", forbidden_legacy_call)

    # When: Executor enriches its final selection.
    runtime_executor.run()

    # Then: one ordered batch uses the defensive default and no legacy wrapper.
    assert calls == [([pinned, normal], 8)]


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_run_selected_paper_uses_exactly_one_sdk_create_call(
    runtime_executor: Executor,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
) -> None:
    # Given: one selected paper, both SDK surfaces, and forbidden legacy wrappers.
    selected = paper("Selected", 9.0)
    runtime_executor.config = executor_config(max_paper_num=1, enrich_workers=1)
    runtime_executor.config.llm.api_mode = api_mode
    setattr(
        runtime_executor,
        "retrievers",
        {"fixture": StubRetriever(lambda: [selected])},
    )
    setattr(runtime_executor, "reranker", RecordingReranker())
    calls: list[str] = []
    digest = json.dumps(
        {"tldr": "One combined digest.", "affiliations": ["Example University"]}
    )

    def record_chat(**kwargs):
        del kwargs
        calls.append("chat_completion")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=digest))]
        )

    def record_response(**kwargs):
        del kwargs
        calls.append("response")
        return SimpleNamespace(output_text=digest)

    monkeypatch.setattr(
        runtime_executor,
        "openai_client",
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=record_chat)),
            responses=SimpleNamespace(create=record_response),
        ),
    )

    def forbidden_legacy_call(*args, **kwargs):
        del args, kwargs
        pytest.fail("Executor called a legacy two-step generation wrapper")

    monkeypatch.setattr(Paper, "generate_tldr", forbidden_legacy_call)
    monkeypatch.setattr(Paper, "generate_affiliations", forbidden_legacy_call)

    # When: Executor performs final enrichment.
    runtime_executor.run()

    # Then: one combined request populates both final fields.
    assert len(calls) == 1
    assert (selected.tldr, selected.affiliations) == (
        "One combined digest.",
        ["Example University"],
    )
