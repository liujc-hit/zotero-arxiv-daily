"""Construction and ordering contracts for pre-rerank enrichment."""

from collections.abc import Mapping
from typing import Never, final

import pytest
from loguru import logger
from omegaconf import OmegaConf

import zotero_arxiv_daily.enrichment.pipeline as pipeline_module
from zotero_arxiv_daily.enrichment.pipeline import (
    PipelineEnrichers,
    build_pipeline_enrichers,
)
from zotero_arxiv_daily.enrichment.settings import EnrichmentSettings
from zotero_arxiv_daily.retriever.openalex_client import (
    JsonValue,
    OpenAlexClient,
    OpenAlexClientError,
)
from zotero_arxiv_daily.retriever.openalex_retriever import OpenAlexRetriever

from .pipeline_fakes import (
    OPENALEX_KEY,
    FailingVenue,
    RecordingStage,
    enabled_config,
    paper,
)


def _forbidden_constructor(*args: Never, **kwargs: Never) -> Never:
    del args, kwargs
    pytest.fail("disabled enrichment constructed a runtime dependency")


@pytest.mark.parametrize(
    "root",
    [
        pytest.param({}, id="absent"),
        pytest.param({"enrichment": None}, id="null"),
        pytest.param({"enrichment": []}, id="non-mapping"),
        pytest.param({"enrichment": {}}, id="missing-enabled"),
        pytest.param({"enrichment": {"enabled": False}}, id="disabled"),
        pytest.param({"enrichment": {"enabled": 1}}, id="integer-enabled"),
    ],
)
def test_disabled_config_constructs_no_clients_or_enrichers(
    monkeypatch: pytest.MonkeyPatch,
    root: dict[str, object],
) -> None:
    # Given every disabled boundary and constructors that fail if touched.
    for name in (
        "OpenAlexClient",
        "AbstractEnricher",
        "VenueCitationEnricher",
    ):
        monkeypatch.setattr(pipeline_module, name, _forbidden_constructor)

    # When the runtime pipeline is built and exercised.
    enrichers = build_pipeline_enrichers(OmegaConf.create(root), {})
    result = enrichers.enrich_before_rerank([paper("untouched")])

    # Then the disabled pipeline is a constructor-free no-op.
    assert result is None


@pytest.mark.parametrize(
    "abstract_config",
    [pytest.param(None, id="absent"), pytest.param({"enabled": False}, id="disabled")],
)
def test_venue_only_config_builds_only_venue_stage(
    monkeypatch: pytest.MonkeyPatch,
    abstract_config: dict[str, object] | None,
) -> None:
    # Given enabled venue weighting and OpenAlex identity without abstract opt-in.
    root: dict[str, object] = {
        "source": {
            "openalex": {
                "api_keys": [OPENALEX_KEY],
                "allow_anonymous": False,
            }
        },
        "reranker": {
            "venue_prestige": {
                "enabled": True,
                "weight": 0.2,
                "max_multiplier": 2.0,
            }
        },
    }
    if abstract_config is not None:
        root["enrichment"] = abstract_config
    monkeypatch.setattr(pipeline_module, "AbstractEnricher", _forbidden_constructor)

    # When independently optional stages are built.
    enrichers = build_pipeline_enrichers(OmegaConf.create(root), {})

    # Then only venue enrichment exists.
    assert enrichers.abstract is None
    assert enrichers.venue_citation is not None


def test_enabled_abstract_uses_settings_and_reuses_configured_openalex_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given enabled stages and an OpenAlex retriever that already owns its client.
    config = enabled_config()
    openalex = OpenAlexRetriever(config)
    abstract_settings: list[EnrichmentSettings] = []
    openalex_clients: list[OpenAlexClient] = []
    stage_calls: list[tuple[str, int, tuple[int, ...]]] = []

    def recording_abstract(
        settings: EnrichmentSettings,
    ) -> RecordingStage:
        abstract_settings.append(settings)
        return RecordingStage("abstract", stage_calls)

    def recording_venue(
        client: OpenAlexClient,
        *,
        enabled: bool = False,
    ) -> RecordingStage:
        assert enabled is True
        openalex_clients.append(client)
        return RecordingStage("venue", stage_calls)

    monkeypatch.setattr(pipeline_module, "OpenAlexClient", _forbidden_constructor)
    monkeypatch.setattr(pipeline_module, "AbstractEnricher", recording_abstract)
    monkeypatch.setattr(pipeline_module, "VenueCitationEnricher", recording_venue)

    # When the pipeline is built without any abstract retrieval identity.
    enrichers = build_pipeline_enrichers(
        config,
        {"openalex": openalex},
    )
    enrichers.enrich_before_rerank([])

    # Then abstract construction receives only settings and OpenAlex reuses its client.
    assert abstract_settings == [EnrichmentSettings()]
    assert openalex_clients == [openalex.client]
    with pytest.raises(AttributeError):
        setattr(openalex, "client", openalex.client)


def test_missing_openalex_retriever_constructs_needed_client_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given enabled stages, OpenAlex identity, and no configured retriever.
    config = enabled_config()
    openalex_identities: list[tuple[tuple[str, ...], bool]] = []

    @final
    class RecordingOpenAlexClient:
        def __init__(
            self,
            api_keys: tuple[str, ...],
            *,
            anonymous_fallback: bool = False,
        ) -> None:
            openalex_identities.append((api_keys, anonymous_fallback))

        def get_sources_json(self, params: Mapping[str, str | int]) -> JsonValue:
            del params
            return {"results": []}

    def recording_abstract(settings: EnrichmentSettings) -> RecordingStage:
        del settings
        return RecordingStage("abstract", [])

    monkeypatch.setattr(pipeline_module, "AbstractEnricher", recording_abstract)
    monkeypatch.setattr(pipeline_module, "OpenAlexClient", RecordingOpenAlexClient)

    # When one pipeline is built.
    enrichers = build_pipeline_enrichers(config, {})
    enrichers.enrich_before_rerank([])

    # Then the required OpenAlex client is normalized and constructed exactly once.
    assert openalex_identities == [((OPENALEX_KEY,), False)]


def test_unavailable_openalex_identity_disables_only_venue_with_static_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given enabled stages without usable OpenAlex identity.
    config = enabled_config()
    OmegaConf.update(config, "source.openalex.api_keys", [None, " "])
    calls: list[str] = []
    stage_calls: list[tuple[str, int, tuple[int, ...]]] = []

    def recording_abstract(
        settings: EnrichmentSettings,
    ) -> RecordingStage:
        del settings
        calls.append("abstract")
        return RecordingStage("abstract", stage_calls)

    def recording_venue(
        client: OpenAlexClient,
        *,
        enabled: bool = False,
    ) -> RecordingStage:
        del client
        assert enabled is True
        calls.append("venue")
        return RecordingStage("venue", stage_calls)

    monkeypatch.setattr(pipeline_module, "AbstractEnricher", recording_abstract)
    monkeypatch.setattr(pipeline_module, "VenueCitationEnricher", recording_venue)
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    try:
        # When pipeline construction and enrichment complete.
        build_pipeline_enrichers(config, {}).enrich_before_rerank([])
    finally:
        logger.remove(sink)

    # Then only venue construction is disabled and one safe warning explains it.
    assert calls == ["abstract"]
    log_text = "".join(rendered)
    assert "openalex" in log_text.casefold()
    assert OPENALEX_KEY not in log_text


def test_abstract_runs_before_venue_on_the_same_full_list() -> None:
    # Given two stages and three candidate identities.
    papers = [paper("first"), paper("second"), paper("third")]
    calls: list[tuple[str, int, tuple[int, ...]]] = []
    pipeline = PipelineEnrichers(
        RecordingStage("abstract", calls),
        RecordingStage("venue", calls),
    )

    # When pre-rerank enrichment runs.
    pipeline.enrich_before_rerank(papers)

    # Then both stages see the original full list object in strict order.
    identities = tuple(id(paper) for paper in papers)
    assert calls == [
        ("abstract", id(papers), identities),
        ("venue", id(papers), identities),
    ]


def test_typed_venue_failure_is_not_retried_or_allowed_to_block() -> None:
    # Given one typed OpenAlex failure carrying private material.
    private = "private OpenAlex failure detail"
    venue = FailingVenue(OpenAlexClientError(private))
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    try:
        # When venue enrichment fails at the pipeline boundary.
        result = PipelineEnrichers(None, venue).enrich_before_rerank([paper("paper")])
    finally:
        logger.remove(sink)

    # Then the pipeline continues neutrally after one static categorized warning.
    assert result is None
    assert venue.calls == 1
    log_text = "".join(rendered)
    assert "OpenAlex" in log_text
    assert private not in log_text


def test_non_client_venue_failure_propagates() -> None:
    # Given a programming failure outside the typed client contract.
    venue = FailingVenue(AssertionError("programming failure"))

    # When venue enrichment runs, then the bug remains observable.
    with pytest.raises(AssertionError, match="programming failure"):
        PipelineEnrichers(None, venue).enrich_before_rerank([])
    assert venue.calls == 1
