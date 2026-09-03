"""Bounded concurrency and one-fallback enrichment flow contracts."""

import pytest

from zotero_arxiv_daily.enrichment.service import AbstractEnricher, EnrichmentAdapters
from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    PubMedSettings,
    SpringerSettings,
)
from zotero_arxiv_daily.protocol import Paper

from .service_fakes import AdapterProbe, ConcurrentAdapter, blank_published_paper


PUBMED_ISSN = "0028-0836"
IEEE_ISSN = "0018-9219"
ELSEVIER_ISSN = "0001-6918"
SPRINGER_ISSN = "1432-0541"


class UnexpectedAdapterError(RuntimeError):
    """Unexpected adapter defect used to verify propagation."""


def _enabled_settings() -> EnrichmentSettings:
    return EnrichmentSettings(
        pubmed=PubMedSettings(
            enabled=True,
            contact_email="curator@example.test",
            issns=(PUBMED_ISSN,),
        ),
        ieee=IeeeSettings(
            enabled=True,
            api_key="ieee-secret",
            issns=(IEEE_ISSN,),
        ),
        elsevier=ElsevierSettings(
            enabled=True,
            api_key="elsevier-secret",
            issns=(ELSEVIER_ISSN,),
        ),
        springer=SpringerSettings(
            enabled=True,
            api_key="springer-secret",
            issns=(SPRINGER_ISSN,),
        ),
        workers=1,
    )


def test_eligibility_requires_published_blank_abstract_and_present_doi() -> None:
    # Given one exact eligible paper and each excluded eligibility state
    eligible = blank_published_paper()
    eligible.abstract = " \t"
    eligible.doi = "10.1109/eligible"
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
    probe = AdapterProbe()

    # When bounded enrichment examines all candidates
    AbstractEnricher(
        EnrichmentSettings(
            ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
            workers=2,
        ),
        probe.bundle(),
    ).enrich([eligible, preprint, unknown, populated, missing_doi, blank_doi])

    # Then only exact False + stripped-blank + nonblank DOI is processed
    assert probe.ieee.calls == ["10.1109/eligible"]


def test_constructor_needs_only_settings_when_adapters_are_not_injected() -> None:
    # Given default enrichment settings and no external service client
    enricher = AbstractEnricher(EnrichmentSettings())

    # When no papers require enrichment
    result = enricher.enrich([])

    # Then construction and the no-work path complete without another dependency
    assert result is None


def test_max_papers_bounds_eligible_work_without_reordering_or_replacing() -> None:
    # Given one ineligible paper followed by four eligible IEEE papers
    ineligible = blank_published_paper()
    ineligible.doi = "10.1109/existing"
    ineligible.abstract = "Existing abstract"
    papers = [ineligible]
    for index in range(4):
        paper = blank_published_paper()
        paper.doi = f"10.1109/{index}"
        papers.append(paper)
    identities = tuple(id(paper) for paper in papers)
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
        workers=1,
        max_papers=2,
    )

    # When enrichment applies its eligible-paper bound
    result = AbstractEnricher(settings, probe.bundle()).enrich(papers)

    # Then only the first two eligible objects are mutated in input order
    assert result is None
    assert tuple(id(paper) for paper in papers) == identities
    assert probe.ieee.calls == ["10.1109/0", "10.1109/1"]
    assert [paper.abstract for paper in papers] == [
        "Existing abstract",
        "ieee abstract",
        "ieee abstract",
        "",
        "",
    ]


@pytest.mark.parametrize(
    ("workers", "max_papers"),
    [(0, 5), (-1, 5), (2, 0), (2, -1)],
)
def test_nonpositive_bounds_process_no_papers(workers: int, max_papers: int) -> None:
    # Given a routable paper but a nonpositive worker or paper bound
    paper = blank_published_paper()
    paper.doi = "10.1109/disabled"
    probe = AdapterProbe()

    # When bounded enrichment is disabled
    AbstractEnricher(
        EnrichmentSettings(
            ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
            workers=workers,
            max_papers=max_papers,
        ),
        probe.bundle(),
    ).enrich([paper])

    # Then no adapter work starts
    assert probe.attempts == []


def test_workers_process_papers_concurrently_through_one_shared_adapter() -> None:
    # Given four eligible papers and one adapter synchronized in worker pairs
    papers: list[Paper] = []
    for index in range(4):
        paper = blank_published_paper()
        paper.doi = f"10.1109/concurrent-{index}"
        papers.append(paper)
    shared_ieee = ConcurrentAdapter(parties=2)
    unused = AdapterProbe()
    adapters = EnrichmentAdapters(
        pubmed=unused.pubmed,
        ieee=shared_ieee,
        elsevier=unused.elsevier,
        springer=unused.springer,
    )

    # When two workers enrich papers through that shared provider adapter
    AbstractEnricher(
        EnrichmentSettings(
            ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
            workers=2,
            max_papers=4,
        ),
        adapters,
    ).enrich(papers)

    # Then papers overlap, the worker cap holds, and every paper is enriched
    assert shared_ieee.max_active == 2
    assert sorted(shared_ieee.calls) == [
        "10.1109/concurrent-0",
        "10.1109/concurrent-1",
        "10.1109/concurrent-2",
        "10.1109/concurrent-3",
    ]
    assert [paper.abstract for paper in papers] == ["concurrent abstract"] * 4


def test_primary_success_stops_without_attempting_a_matched_alternate() -> None:
    # Given IEEE publisher evidence overlapping a configured PubMed ISSN
    paper = blank_published_paper()
    paper.publisher = "IEEE Computer Society"
    paper.issns = (PUBMED_ISSN,)
    probe = AdapterProbe()
    probe.ieee.result = "  primary abstract \t"

    # When the primary returns a usable abstract
    AbstractEnricher(_enabled_settings(), probe.bundle()).enrich([paper])

    # Then the normalized primary result stops the per-paper route
    assert probe.attempts == ["ieee"]
    assert paper.abstract == "primary abstract"


@pytest.mark.parametrize("primary_result", [None, " \t"], ids=["none", "blank"])
def test_primary_none_or_blank_falls_back_once_to_next_unique_match(
    primary_result: str | None,
) -> None:
    # Given three IEEE signals plus one lower-priority PubMed ISSN signal
    paper = blank_published_paper()
    paper.doi = "10.1109/fallback"
    paper.publisher = "IEEE Computer Society"
    paper.issns = (IEEE_ISSN, PUBMED_ISSN)
    probe = AdapterProbe()
    probe.ieee.result = primary_result

    # When the primary cannot supply a usable abstract
    AbstractEnricher(_enabled_settings(), probe.bundle()).enrich([paper])

    # Then IEEE is not duplicated and exactly one PubMed fallback succeeds
    assert probe.attempts == ["ieee", "pubmed"]
    assert probe.ieee.calls == ["10.1109/fallback"]
    assert probe.pubmed.calls == ["10.1109/fallback"]
    assert paper.abstract == "pubmed abstract"


def test_only_first_two_matches_are_attempted_when_both_return_blank() -> None:
    # Given positive signals for all four providers in priority order
    paper = blank_published_paper()
    paper.doi = "10.1109/two-attempt-limit"
    paper.publisher = "Elsevier Springer Nature"
    paper.issns = (PUBMED_ISSN,)
    probe = AdapterProbe()
    probe.ieee.result = None
    probe.elsevier.result = " \t"

    # When both admitted routes fail to supply a usable abstract
    AbstractEnricher(_enabled_settings(), probe.bundle()).enrich([paper])

    # Then IEEE and Elsevier are the only attempts; no third route runs
    assert probe.attempts == ["ieee", "elsevier"]
    assert probe.springer.calls == []
    assert probe.pubmed.calls == []
    assert paper.abstract == ""


def test_unexpected_adapter_exception_propagates_without_fallback() -> None:
    # Given a matched primary that raises and a positively matched alternate
    paper = blank_published_paper()
    paper.doi = "10.1109/unexpected"
    paper.issns = (PUBMED_ISSN,)
    probe = AdapterProbe()
    failure = UnexpectedAdapterError("adapter defect")
    probe.ieee.error = failure

    # When enrichment reaches the defective primary adapter
    with pytest.raises(UnexpectedAdapterError) as raised:
        AbstractEnricher(_enabled_settings(), probe.bundle()).enrich([paper])

    # Then the original error escapes and no alternate is attempted
    assert raised.value is failure
    assert probe.attempts == ["ieee"]
    assert probe.pubmed.calls == []
    assert paper.abstract == ""
