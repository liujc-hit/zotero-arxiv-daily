"""Focused Executor construction and pre-rerank integration tests."""

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from collections.abc import Callable

from omegaconf import DictConfig, OmegaConf
import pytest

import zotero_arxiv_daily.enrichment.pipeline as pipeline_module
import zotero_arxiv_daily.executor as executor_module
from zotero_arxiv_daily.enrichment.pipeline import PipelineEnrichers
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.sent_doi_state import DisabledSentDoiStateStore

type ConfigValue = str | int | float | bool | None | list[str]


@dataclass(frozen=True, slots=True)
class PaperOptions:
    abstract: str = "Abstract."
    score: float | None = 1.0
    doi: str | None = None
    publisher: str | None = None
    issns: tuple[str, ...] = ()
    is_preprint: bool | None = None


@dataclass(frozen=True, slots=True)
class StubRetriever:
    callback: Callable[[], list[Paper]]

    def retrieve_papers(self) -> list[Paper]:
        return self.callback()


class RecordingReranker:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events: list[str] | None = events
        self.candidates: list[Paper] = []
        self.candidate_list_id: int | None = None

    def rerank(
        self, candidates: list[Paper], corpus: list[CorpusPaper]
    ) -> list[Paper]:
        assert corpus
        if self.events is not None:
            self.events.append("rerank")
        self.candidate_list_id = id(candidates)
        self.candidates = list(candidates)
        return candidates


class SensitiveSourceError(RuntimeError):
    """A source failure whose message must not reach logs."""


def paper(title: str, options: PaperOptions = PaperOptions()) -> Paper:
    return Paper(
        source="fixture",
        title=title,
        authors=[f"{title} author"],
        abstract=options.abstract,
        url=f"https://papers.example/{title}",
        score=options.score,
        doi=options.doi,
        publisher=options.publisher,
        issns=options.issns,
        is_preprint=options.is_preprint,
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


def make_runtime_executor() -> Executor:
    executor = Executor.__new__(Executor)
    executor.config = executor_config()
    executor.sent_doi_state_store = DisabledSentDoiStateStore()
    executor.pipeline_enrichers = PipelineEnrichers()
    executor.fetch_zotero_corpus = lambda: [
        CorpusPaper("Corpus", "Corpus abstract", datetime(2026, 1, 1), [])
    ]
    executor.filter_corpus = lambda corpus: corpus
    return executor


def test_run_merges_doi_variants_before_rerank_and_keeps_invalid_dois_distinct() -> None:
    # Given: one DOI duplicate pair plus invalid and missing DOI papers.
    executor = make_runtime_executor()
    winner = paper(
        "winner",
        PaperOptions(
            doi=" HTTPS://DOI.ORG/10.5555/ABC.Def ",
            abstract="",
            issns=("00280836",),
        ),
    )
    duplicate = paper(
        "duplicate",
        PaperOptions(
            doi="10.5555/abc.def",
            abstract="  Merged abstract.  ",
            publisher="  Merged Publisher  ",
            is_preprint=False,
            issns=("2434-561x",),
        ),
    )
    invalid_first = paper("invalid-first", PaperOptions(doi="not-a-doi"))
    missing_first = paper("missing-first")
    invalid_second = paper("invalid-second", PaperOptions(doi="not-a-doi"))
    missing_second = paper("missing-second")
    reranker = RecordingReranker()
    setattr(
        executor,
        "retrievers",
        {
            "first": StubRetriever(lambda: [winner, invalid_first, missing_first]),
            "second": StubRetriever(
                lambda: [duplicate, invalid_second, missing_second]
            ),
        },
    )
    setattr(executor, "reranker", reranker)

    # When: Executor retrieves and merges candidates.
    executor.run()

    # Then: only the valid DOI pair collapses and the first identity wins in place.
    assert reranker.candidates == [
        winner,
        invalid_first,
        missing_first,
        invalid_second,
        missing_second,
    ]
    assert reranker.candidates[0] is winner
    assert (winner.doi, winner.abstract, winner.publisher, winner.is_preprint) == (
        "10.5555/abc.def",
        "Merged abstract.",
        "Merged Publisher",
        False,
    )
    assert winner.issns == ("0028-0836", "2434-561X")


def test_run_enriches_full_deduplicated_list_before_rerank() -> None:
    # Given: three candidates and ordered stages that mutate data used by ranking.
    executor = make_runtime_executor()
    papers = [paper("first"), paper("second"), paper("third")]
    events: list[str] = []
    stage_calls: list[tuple[str, int, tuple[int, ...]]] = []

    class AbstractStage:
        def enrich(self, papers: list[Paper]) -> None:
            events.append("abstract")
            stage_calls.append(
                ("abstract", id(papers), tuple(id(paper) for paper in papers))
            )
            for paper in papers:
                paper.abstract = f"Enriched {paper.title}"

    class VenueStage:
        def enrich(self, papers: list[Paper]) -> None:
            events.append("venue")
            stage_calls.append(
                ("venue", id(papers), tuple(id(paper) for paper in papers))
            )
            for index, paper in enumerate(papers, start=1):
                paper.venue_citation_proxy = index / 10

    reranker = RecordingReranker(events)
    setattr(executor, "retrievers", {"source": StubRetriever(lambda: papers)})
    executor.pipeline_enrichers = PipelineEnrichers(AbstractStage(), VenueStage())
    setattr(executor, "reranker", reranker)

    # When: Executor runs the pre-rerank portion of the pipeline.
    executor.run()

    # Then: both stages and ranking see one full list in strict stage order.
    identities = tuple(id(paper) for paper in papers)
    assert events == ["abstract", "venue", "rerank"]
    assert stage_calls == [
        ("abstract", reranker.candidate_list_id, identities),
        ("venue", reranker.candidate_list_id, identities),
    ]
    assert [paper.venue_citation_proxy for paper in reranker.candidates] == [
        0.1,
        0.2,
        0.3,
    ]


def test_init_preserves_source_order_builds_pipeline_then_disables_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured sources, recording factories, and a pipeline builder.
    constructed: list[str] = []
    pipeline_inputs: list[tuple[str, ...]] = []
    openai_arguments: list[dict[str, str | int]] = []
    pipeline = PipelineEnrichers()

    def retriever_class(source: str):
        class Retriever:
            def __init__(self, config) -> None:
                del config
                self.source = source
                constructed.append(source)

        return Retriever

    class Reranker:
        def __init__(self, config) -> None:
            del config

    def build(config, retrievers):
        del config
        pipeline_inputs.append(tuple(retrievers))
        assert [retriever.source for retriever in retrievers.values()] == constructed
        return pipeline

    def openai(**kwargs):
        openai_arguments.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(executor_module, "get_retriever_cls", retriever_class)
    monkeypatch.setattr(executor_module, "get_reranker_cls", lambda name: Reranker)
    monkeypatch.setattr(executor_module, "build_pipeline_enrichers", build)
    monkeypatch.setattr(executor_module, "OpenAI", openai)

    # When: Executor constructs its runtime dependencies.
    executor = Executor(executor_config(source=["second", "first"]))

    # Then: source order and retriever instances reach the pipeline before the SDK.
    assert list(executor.retrievers) == ["second", "first"]
    assert constructed == ["second", "first"]
    assert pipeline_inputs == [("second", "first")]
    assert executor.pipeline_enrichers is pipeline
    assert openai_arguments == [{
        "api_key": "test-key",
        "base_url": "https://llm.example/v1",
        "max_retries": 0,
    }]


@pytest.mark.parametrize(
    "enrichment_config",
    [
        pytest.param(None, id="absent"),
        pytest.param({"enabled": False}, id="disabled"),
    ],
)
def test_init_disabled_stages_construct_no_network_clients(
    monkeypatch: pytest.MonkeyPatch,
    enrichment_config,
) -> None:
    # Given: absent/disabled enrichment and forbidden optional client constructors.
    def forbidden(*args, **kwargs):
        del args, kwargs
        pytest.fail("disabled pipeline constructed an optional network client")

    class Reranker:
        def __init__(self, config) -> None:
            del config

    config = executor_config()
    if enrichment_config is not None:
        config.enrichment = enrichment_config
    for name in ("CrossrefClient", "OpenAlexClient"):
        monkeypatch.setattr(pipeline_module, name, forbidden)
    monkeypatch.setattr(executor_module, "get_reranker_cls", lambda name: Reranker)
    monkeypatch.setattr(executor_module, "OpenAI", lambda **kwargs: SimpleNamespace())

    # When: Executor builds its optional pipeline.
    executor = Executor(config)

    # Then: historical no-op behavior needs no enrichment clients.
    assert executor.pipeline_enrichers == PipelineEnrichers()
