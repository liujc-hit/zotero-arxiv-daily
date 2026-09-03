"""Paper-count, worker-count, and identity contracts for enrichment."""

import pytest

from zotero_arxiv_daily.enrichment.service import AbstractEnricher, EnrichmentAdapters
from zotero_arxiv_daily.enrichment.settings import EnrichmentSettings, IeeeSettings
from zotero_arxiv_daily.protocol import Paper

from .service_fakes import AdapterProbe, ConcurrentAdapter, blank_published_paper


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

    # When enrichment applies its paper bound
    result = AbstractEnricher(settings, probe.bundle()).enrich(papers)

    # Then only the first two eligible objects are mutated in original list order
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


def test_worker_pool_never_exceeds_configured_threads() -> None:
    # Given four eligible papers and one adapter synchronized in worker pairs
    papers: list[Paper] = []
    for index in range(4):
        paper = blank_published_paper()
        paper.doi = f"10.1109/{index}"
        papers.append(paper)
    shared_ieee = ConcurrentAdapter(parties=2)
    unused = AdapterProbe()
    adapters = EnrichmentAdapters(
        pubmed=unused.pubmed,
        ieee=shared_ieee,
        elsevier=unused.elsevier,
        springer=unused.springer,
    )

    # When enrichment runs with two workers
    AbstractEnricher(
        EnrichmentSettings(
            ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
            workers=2,
            max_papers=4,
        ),
        adapters,
    ).enrich(papers)

    # Then no third concurrent paper reaches the shared adapter
    assert shared_ieee.max_active == 2


@pytest.mark.parametrize(
    ("workers", "max_papers"),
    [(0, 5), (-1, 5), (2, 0), (2, -1)],
)
def test_nonpositive_bounds_process_no_papers(workers: int, max_papers: int) -> None:
    # Given a routable paper but a nonpositive worker or paper bound
    paper = blank_published_paper()
    paper.doi = "10.1109/disabled"
    probe = AdapterProbe()

    # When enrichment receives the disabled bound
    AbstractEnricher(
        EnrichmentSettings(
            ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
            workers=workers,
            max_papers=max_papers,
        ),
        probe.bundle(),
    ).enrich([paper])

    # Then no provider work starts
    assert (
        probe.pubmed.calls,
        probe.ieee.calls,
        probe.elsevier.calls,
        probe.springer.calls,
    ) == ([], [], [], [])
