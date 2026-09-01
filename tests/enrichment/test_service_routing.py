"""Positive-evidence and priority contracts for vertical routing."""

from dataclasses import dataclass

import pytest

from zotero_arxiv_daily.enrichment.service import AbstractEnricher
from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    PubMedSettings,
    SpringerSettings,
)

from .service_fakes import AdapterProbe, RecordingCrossref, blank_published_paper


PUBMED_ISSN = "0028-0836"
IEEE_ISSN = "0018-9219"
ELSEVIER_ISSN = "0001-6918"
SPRINGER_ISSN = "1432-0541"


@dataclass(frozen=True, slots=True)
class _RouteCase:
    doi: str
    publisher: str | None
    issns: tuple[str, ...]
    expected: str


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


def _route_id(case: _RouteCase) -> str:
    return f"{case.expected}-{case.doi.rsplit('/', 1)[-1]}"


@pytest.mark.parametrize(
    "case",
    [
        _RouteCase("10.5555/pubmed", None, (PUBMED_ISSN,), "pubmed"),
        _RouteCase("10.1109/doi", None, (), "ieee"),
        _RouteCase("10.5555/ieee-publisher", "IEEE Computer Society", (), "ieee"),
        _RouteCase("10.5555/ieee-issn", None, (IEEE_ISSN,), "ieee"),
        _RouteCase("10.1016/doi", None, (), "elsevier"),
        _RouteCase("10.5555/elsevier-publisher", "Elsevier B.V.", (), "elsevier"),
        _RouteCase("10.5555/elsevier-issn", None, (ELSEVIER_ISSN,), "elsevier"),
        _RouteCase("10.1007/springer", None, (), "springer"),
        _RouteCase("10.1038/nature", None, (), "springer"),
        _RouteCase("10.1057/palgrave", None, (), "springer"),
        _RouteCase("10.1186/bmc", None, (), "springer"),
        _RouteCase("10.5555/springer", "Springer Nature", (), "springer"),
        _RouteCase("10.5555/nature", "Nature Portfolio", (), "springer"),
        _RouteCase("10.5555/bmc", "BMC Medicine", (), "springer"),
        _RouteCase("10.5555/palgrave", "Palgrave Macmillan", (), "springer"),
        _RouteCase("10.5555/springer-issn", None, (SPRINGER_ISSN,), "springer"),
    ],
    ids=_route_id,
)
def test_each_positive_signal_selects_exactly_one_vertical(case: _RouteCase) -> None:
    # Given one published blank paper and Crossref without additional evidence
    paper = blank_published_paper()
    paper.doi = case.doi
    paper.publisher = case.publisher
    paper.issns = case.issns
    crossref = RecordingCrossref(lambda _doi: {"message": {}})
    probe = AdapterProbe()

    # When routed enrichment runs
    AbstractEnricher(crossref, _enabled_settings(), probe.bundle()).enrich([paper])

    # Then only the positively matched vertical is called
    calls = {
        "pubmed": probe.pubmed.calls,
        "ieee": probe.ieee.calls,
        "elsevier": probe.elsevier.calls,
        "springer": probe.springer.calls,
    }
    assert calls[case.expected] == [case.doi]
    assert sum(len(provider_calls) for provider_calls in calls.values()) == 1
    assert paper.abstract == f"{case.expected} abstract"
    assert crossref.calls == [case.doi]


@pytest.mark.parametrize(
    "case",
    [
        _RouteCase("10.1109/priority", "Elsevier", (PUBMED_ISSN,), "pubmed"),
        _RouteCase("10.1016/priority", "IEEE", (), "ieee"),
        _RouteCase("10.1007/priority", "Elsevier", (), "elsevier"),
    ],
)
def test_routing_priority_wins_when_multiple_signals_match(case: _RouteCase) -> None:
    # Given a paper carrying evidence for multiple enabled verticals
    paper = blank_published_paper()
    paper.doi = case.doi
    paper.publisher = case.publisher
    paper.issns = case.issns
    probe = AdapterProbe()

    # When the route is selected
    AbstractEnricher(
        RecordingCrossref(lambda _doi: {"message": {}}),
        _enabled_settings(),
        probe.bundle(),
    ).enrich([paper])

    # Then PubMed > IEEE > Elsevier > Springer priority is respected
    calls = {
        "pubmed": probe.pubmed.calls,
        "ieee": probe.ieee.calls,
        "elsevier": probe.elsevier.calls,
        "springer": probe.springer.calls,
    }
    assert calls[case.expected] == [case.doi]
    assert sum(len(provider_calls) for provider_calls in calls.values()) == 1


def test_pubmed_has_no_doi_publisher_or_generic_fallback_route() -> None:
    # Given an enabled PubMed adapter but no configured-ISSN intersection
    paper = blank_published_paper()
    paper.doi = "10.5555/biomedical"
    paper.publisher = "National Library of Medicine"
    paper.issns = ("2049-3630",)
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        pubmed=PubMedSettings(
            enabled=True,
            contact_email="curator@example.test",
            issns=(PUBMED_ISSN,),
        ),
        workers=1,
    )

    # When Crossref has no abstract and routing runs
    AbstractEnricher(
        RecordingCrossref(lambda _doi: {"message": {}}),
        settings,
        probe.bundle(),
    ).enrich([paper])

    # Then PubMed is not used as global biomedical retrieval
    assert probe.pubmed.calls == []
    assert paper.abstract == ""
