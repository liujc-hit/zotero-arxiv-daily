"""Paper-count, worker-count, and identity contracts for enrichment."""

from threading import Barrier, Lock

import pytest

from zotero_arxiv_daily.enrichment.service import AbstractEnricher
from zotero_arxiv_daily.enrichment.settings import EnrichmentSettings, IeeeSettings
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retriever.crossref_client import JsonObject

from .service_fakes import AdapterProbe, RecordingCrossref, blank_published_paper


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
    crossref = RecordingCrossref(lambda _doi: {"message": {}})
    probe = AdapterProbe()
    settings = EnrichmentSettings(
        ieee=IeeeSettings(enabled=True, api_key="ieee-secret"),
        workers=1,
        max_papers=2,
    )

    # When enrichment applies its paper bound
    result = AbstractEnricher(crossref, settings, probe.bundle()).enrich(papers)

    # Then only the first two eligible objects are mutated in original list order
    assert result is None
    assert tuple(id(paper) for paper in papers) == identities
    assert crossref.calls == ["10.1109/0", "10.1109/1"]
    assert probe.ieee.calls == ["10.1109/0", "10.1109/1"]
    assert [paper.abstract for paper in papers] == [
        "Existing abstract",
        "ieee abstract",
        "ieee abstract",
        "",
        "",
    ]


def test_worker_pool_never_exceeds_configured_threads() -> None:
    # Given four eligible papers and a Crossref probe synchronized in pairs
    barrier = Barrier(2)
    lock = Lock()
    active = 0
    max_active = 0

    class ConcurrencyCrossref:
        def get_work(self, doi: str) -> JsonObject:
            del doi
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                _ = barrier.wait(timeout=5)
                return {"message": {}}
            finally:
                with lock:
                    active -= 1

    papers: list[Paper] = []
    for index in range(4):
        paper = blank_published_paper()
        paper.doi = f"10.5555/{index}"
        papers.append(paper)

    # When enrichment runs with two workers
    AbstractEnricher(
        ConcurrencyCrossref(),
        EnrichmentSettings(workers=2, max_papers=4),
        AdapterProbe().bundle(),
    ).enrich(papers)

    # Then no third concurrent paper reaches Crossref
    assert max_active == 2


@pytest.mark.parametrize(
    ("workers", "max_papers"),
    [(0, 5), (-1, 5), (2, 0), (2, -1)],
)
def test_nonpositive_bounds_process_no_papers(workers: int, max_papers: int) -> None:
    # Given a valid paper but a nonpositive worker or paper bound
    paper = blank_published_paper()
    crossref = RecordingCrossref(lambda _doi: {"message": {}})
    probe = AdapterProbe()

    # When enrichment receives the disabled bound
    AbstractEnricher(
        crossref,
        EnrichmentSettings(workers=workers, max_papers=max_papers),
        probe.bundle(),
    ).enrich([paper])

    # Then no Crossref or vertical work starts
    assert crossref.calls == []
    assert (
        probe.pubmed.calls,
        probe.ieee.calls,
        probe.elsevier.calls,
        probe.springer.calls,
    ) == ([], [], [], [])
