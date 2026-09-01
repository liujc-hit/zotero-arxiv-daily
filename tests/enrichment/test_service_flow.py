"""Crossref-first and no-fallback enrichment flow contracts."""

from typing import override

import pytest
from loguru import logger

from zotero_arxiv_daily.enrichment.service import AbstractEnricher
from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    SpringerSettings,
)
from zotero_arxiv_daily.retriever.crossref_client import (
    CrossrefOperation,
    CrossrefTransportError,
    JsonObject,
)

from .service_fakes import AdapterProbe, RecordingCrossref, blank_published_paper


def test_eligibility_requires_published_blank_abstract_and_present_doi() -> None:
    # Given one exact eligible paper and each excluded eligibility state
    eligible = blank_published_paper()
    eligible.abstract = " \t"
    eligible.doi = "10.5555/eligible"
    preprint = blank_published_paper()
    preprint.doi = "10.5555/preprint"
    preprint.is_preprint = True
    unknown = blank_published_paper()
    unknown.doi = "10.5555/unknown"
    unknown.is_preprint = None
    populated = blank_published_paper()
    populated.doi = "10.5555/populated"
    populated.abstract = "Existing abstract"
    missing_doi = blank_published_paper()
    missing_doi.doi = None
    blank_doi = blank_published_paper()
    blank_doi.doi = " \t"
    crossref = RecordingCrossref(lambda _doi: {"message": {}})

    # When bounded enrichment examines all candidates
    AbstractEnricher(
        crossref,
        EnrichmentSettings(workers=2),
        AdapterProbe().bundle(),
    ).enrich([eligible, preprint, unknown, populated, missing_doi, blank_doi])

    # Then only exact False + stripped-blank + nonblank DOI is processed
    assert crossref.calls == ["10.5555/eligible"]


def test_crossref_abstract_short_circuits_vertical_and_merges_missing_metadata() -> None:
    # Given an IEEE DOI whose Crossref work contains a usable JATS abstract
    paper = blank_published_paper()
    paper.doi = "10.1109/crossref-first"
    crossref = RecordingCrossref(
        lambda _doi: {
            "message": {
                "abstract": "<jats:p>Crossref <jats:b>abstract</jats:b>.</jats:p>",
                "publisher": "Crossref Publisher",
                "ISSN": ["0028-0836"],
                "type": "posted-content",
            }
        }
    )
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
        workers=1,
    )

    # When enrichment runs
    AbstractEnricher(crossref, settings, probe.bundle()).enrich([paper])

    # Then Crossref fills missing fields, preserves known status, and stops routing
    assert paper.abstract == "Crossref abstract."
    assert (paper.publisher, paper.issns, paper.is_preprint) == (
        "Crossref Publisher",
        ("0028-0836",),
        False,
    )
    assert probe.ieee.calls == []
    assert crossref.calls == ["10.1109/crossref-first"]


def test_crossref_never_overwrites_present_publication_metadata() -> None:
    # Given a paper whose publication metadata is already known
    paper = blank_published_paper()
    paper.publisher = "Original Publisher"
    paper.issns = ("2049-3630",)
    crossref = RecordingCrossref(
        lambda _doi: {
            "message": {
                "publisher": "Replacement Publisher",
                "ISSN": ["0028-0836"],
                "type": "preprint",
            }
        }
    )

    # When Crossref metadata is merged
    AbstractEnricher(
        crossref,
        EnrichmentSettings(workers=1),
        AdapterProbe().bundle(),
    ).enrich([paper])

    # Then publisher, ISSNs, and explicit published status remain unchanged
    assert (paper.publisher, paper.issns, paper.is_preprint) == (
        "Original Publisher",
        ("2049-3630",),
        False,
    )


def test_crossref_metadata_can_supply_positive_vertical_evidence() -> None:
    # Given an otherwise unroutable paper whose Crossref publisher is Elsevier
    paper = blank_published_paper()
    crossref = RecordingCrossref(
        lambda _doi: {
            "message": {
                "publisher": "Elsevier B.V.",
                "ISSN": ["0001-6918"],
                "abstract": " ",
            }
        }
    )
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        elsevier=ElsevierSettings(enabled=True, api_key="elsevier-secret"),
        workers=1,
    )

    # When Crossref-first routing runs
    AbstractEnricher(crossref, settings, probe.bundle()).enrich([paper])

    # Then merged publisher evidence selects only Elsevier
    assert paper.publisher == "Elsevier B.V."
    assert paper.issns == ("0001-6918",)
    assert probe.elsevier.calls == ["10.5555/example"]
    assert paper.abstract == "elsevier abstract"


def test_crossref_failure_continues_from_existing_paper_metadata() -> None:
    # Given a known IEEE DOI and a typed Crossref transport failure
    paper = blank_published_paper()
    paper.doi = "10.1109/existing"

    def fail(_doi: str) -> JsonObject:
        raise CrossrefTransportError(CrossrefOperation.GET_WORK)

    crossref = RecordingCrossref(fail)
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
        workers=1,
    )

    # When enrichment handles the failed Crossref call
    AbstractEnricher(crossref, settings, probe.bundle()).enrich([paper])

    # Then Crossref is not a routing gate and IEEE is attempted once
    assert crossref.calls == ["10.1109/existing"]
    assert probe.ieee.calls == ["10.1109/existing"]
    assert paper.abstract == "ieee abstract"


@pytest.mark.parametrize("provider_result", [None, " \t"], ids=["failure", "blank"])
def test_chosen_vertical_failure_or_blank_never_falls_through(
    provider_result: str | None,
) -> None:
    # Given Elsevier-priority evidence plus lower-priority Springer evidence
    paper = blank_published_paper()
    paper.doi = "10.1016/no-fallback"
    paper.publisher = "Springer Nature"
    probe = AdapterProbe()
    probe.elsevier.result = provider_result
    settings = EnrichmentSettings(
        elsevier=ElsevierSettings(enabled=True, api_key="elsevier-secret"),
        springer=SpringerSettings(enabled=True, api_key="springer-secret"),
        workers=1,
    )

    # When the chosen Elsevier adapter cannot supply a usable abstract
    AbstractEnricher(
        RecordingCrossref(lambda _doi: {"message": {}}),
        settings,
        probe.bundle(),
    ).enrich([paper])

    # Then no other database is attempted
    assert probe.elsevier.calls == ["10.1016/no-fallback"]
    assert probe.springer.calls == []
    assert probe.pubmed.calls == []
    assert paper.abstract == ""


def test_crossref_failure_log_never_renders_exception_or_doi() -> None:
    # Given a typed failure whose string form contains private data
    private = "10.1109/private secret exception detail"

    class SensitiveTransportError(CrossrefTransportError):
        @override
        def __str__(self) -> str:
            return private

    def fail(_doi: str) -> JsonObject:
        raise SensitiveTransportError(CrossrefOperation.GET_WORK)

    paper = blank_published_paper()
    paper.doi = "10.1109/private"
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    try:
        # When the service handles the Crossref failure
        AbstractEnricher(
            RecordingCrossref(fail),
            EnrichmentSettings(workers=1),
            AdapterProbe().bundle(),
        ).enrich([paper])
    finally:
        logger.remove(sink)

    # Then only provider, operation, and failure category are observable
    log_text = "".join(rendered)
    assert "Crossref get_work transport failure" in log_text
    assert private not in log_text
