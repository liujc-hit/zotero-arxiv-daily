"""Contract tests for concurrent source retrieval and DOI merging."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import StringIO
from threading import Barrier, Event, Lock

import pytest
from loguru import logger

import zotero_arxiv_daily.retrieval as retrieval
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retrieval import retrieve_and_merge


@dataclass(frozen=True, slots=True)
class StubRetriever:
    callback: Callable[[], list[Paper]]

    def retrieve_papers(self) -> list[Paper]:
        return self.callback()


class SensitiveSourceError(RuntimeError):
    """Failure whose message must never cross the logging boundary."""


def _paper(title: str, doi: str | None = None) -> Paper:
    return Paper(
        source="fixture",
        title=title,
        authors=[f"{title} author"],
        abstract="",
        url=f"https://papers.example/{title}",
        doi=doi,
    )


def test_retrieval_overlaps_sources_with_a_fixed_four_worker_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: eight sources that rendezvous in groups of four.
    barrier = Barrier(4)
    lock = Lock()
    active = 0
    peak_active = 0
    worker_counts: list[int] = []

    def recording_pool(max_workers: int) -> ThreadPoolExecutor:
        worker_counts.append(max_workers)
        return ThreadPoolExecutor(max_workers=max_workers)

    def retrieve() -> list[Paper]:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            _ = barrier.wait(timeout=2)
            return []
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(retrieval, "ThreadPoolExecutor", recording_pool)

    # When: all configured sources are retrieved.
    _ = retrieve_and_merge(
        {f"source-{index}": StubRetriever(retrieve) for index in range(8)}
    )

    # Then: retrieval overlaps, while the executor is capped at four workers.
    assert worker_counts == [4]
    assert peak_active == 4


def test_retrieval_restores_configured_order_after_out_of_order_completion() -> None:
    # Given: the first source cannot finish until the second source has finished.
    second_finished = Event()
    completion_order: list[str] = []
    first = _paper("first")
    second = _paper("second")

    def retrieve_first() -> list[Paper]:
        assert second_finished.wait(timeout=2)
        completion_order.append("first")
        return [first]

    def retrieve_second() -> list[Paper]:
        completion_order.append("second")
        second_finished.set()
        return [second]

    # When: the configured sources complete in reverse order.
    result = retrieve_and_merge(
        {
            "configured-first": StubRetriever(retrieve_first),
            "configured-second": StubRetriever(retrieve_second),
        }
    )

    # Then: flattening follows configured order, not completion order.
    assert completion_order == ["second", "first"]
    assert result == [first, second]


def test_retrieval_isolates_source_failure_and_redacts_exception_details() -> None:
    # Given: one source exposes secrets in its exception and one source succeeds.
    safe_paper = _paper("safe")
    log_output = StringIO()
    sink_id = logger.add(log_output, format="{message}")

    def fail() -> list[Paper]:
        raise SensitiveSourceError(
            "https://private.example/?token=secret 10.5555/LEAK password=hunter2"
        )

    try:
        # When: both source futures are collected.
        result = retrieve_and_merge(
            {
                "broken": StubRetriever(fail),
                "healthy": StubRetriever(lambda: [safe_paper]),
            }
        )
    finally:
        logger.remove(sink_id)

    # Then: the failed source contributes nothing and only safe fields are logged.
    assert result == [safe_paper]
    assert log_output.getvalue().strip() == (
        "source=broken type=SensitiveSourceError"
    )


def test_same_source_doi_duplicates_keep_first_paper_identity_and_position() -> None:
    # Given: one source returns an unrelated paper followed by two DOI duplicates.
    unrelated = _paper("unrelated")
    winner = _paper("winner", "10.1000/shared")
    duplicate = _paper("duplicate", "10.1000/shared")

    # When: the source-local sequence is deduplicated.
    result = retrieve_and_merge(
        {"source": StubRetriever(lambda: [unrelated, winner, duplicate])}
    )

    # Then: the first DOI paper remains in its original position and identity.
    assert result == [unrelated, winner]
    assert result[1] is winner


def test_doi_url_and_case_variants_collapse_to_canonical_winner() -> None:
    # Given: equivalent DOI representations from two configured sources.
    winner = _paper("winner", " HTTPS://DOI.ORG/10.5555/ABC.Def ")
    duplicate = _paper("duplicate", "10.5555/abc.def")

    # When: the ordered source results are deduplicated.
    result = retrieve_and_merge(
        {
            "first": StubRetriever(lambda: [winner]),
            "second": StubRetriever(lambda: [duplicate]),
        }
    )

    # Then: the first object wins and its DOI is canonicalized.
    assert result == [winner]
    assert result[0] is winner
    assert winner.doi == "10.5555/abc.def"


@pytest.mark.parametrize(
    ("missing_abstract", "missing_publisher"),
    [("", None), (" \t", " \n")],
)
def test_only_allowed_missing_metadata_is_filled_and_text_is_stripped(
    missing_abstract: str,
    missing_publisher: str | None,
) -> None:
    # Given: a winner with missing metadata and a duplicate with conflicting data.
    winner = Paper(
        source="winner-source",
        title="Winner title",
        authors=["Winner author"],
        abstract=missing_abstract,
        url="https://papers.example/winner",
        pdf_url="https://papers.example/winner.pdf",
        full_text="Winner full text",
        tldr="Winner TLDR",
        affiliations=["Winner University"],
        score=0.9,
        pinned=True,
        doi="https://doi.org/10.7777/MERGE",
        publisher=missing_publisher,
        is_preprint=None,
        venue_citation_proxy=0.8,
    )
    duplicate = Paper(
        source="duplicate-source",
        title="Duplicate title",
        authors=["Duplicate author"],
        abstract="  Filled abstract.  ",
        url="https://papers.example/duplicate",
        pdf_url="https://papers.example/duplicate.pdf",
        full_text="Duplicate full text",
        tldr="Duplicate TLDR",
        affiliations=["Duplicate University"],
        score=0.1,
        doi="10.7777/merge",
        publisher="  Filled Publisher  ",
        is_preprint=False,
        venue_citation_proxy=0.2,
    )

    # When: the later duplicate is merged into the winner.
    result = retrieve_and_merge(
        {
            "first": StubRetriever(lambda: [winner]),
            "second": StubRetriever(lambda: [duplicate]),
        }
    )

    # Then: only the permitted missing metadata is filled.
    assert result == [winner]
    assert (
        winner.abstract,
        winner.publisher,
        winner.is_preprint,
        winner.doi,
    ) == ("Filled abstract.", "Filled Publisher", False, "10.7777/merge")
    assert (
        winner.source,
        winner.title,
        winner.authors,
        winner.url,
        winner.pdf_url,
        winner.full_text,
        winner.tldr,
        winner.affiliations,
        winner.score,
        winner.pinned,
        winner.venue_citation_proxy,
    ) == (
        "winner-source",
        "Winner title",
        ["Winner author"],
        "https://papers.example/winner",
        "https://papers.example/winner.pdf",
        "Winner full text",
        "Winner TLDR",
        ["Winner University"],
        0.9,
        True,
        0.8,
    )


@pytest.mark.parametrize(("winner_value", "duplicate_value"), [(True, False), (False, True)])
def test_known_preprint_conflict_keeps_winner(
    winner_value: bool, duplicate_value: bool
) -> None:
    # Given: two DOI duplicates with conflicting known preprint values.
    winner = _paper("winner", "10.8888/conflict")
    winner.is_preprint = winner_value
    duplicate = _paper("duplicate", "10.8888/conflict")
    duplicate.is_preprint = duplicate_value

    # When: the duplicate metadata is merged.
    _ = retrieve_and_merge({"source": StubRetriever(lambda: [winner, duplicate])})

    # Then: a known winner value is never overwritten.
    assert winner.is_preprint is winner_value


def test_issn_merge_is_valid_normalized_stable_union() -> None:
    # Given: winner-first ISSNs with invalid and repeated variants.
    winner = _paper("winner", "10.9999/issns")
    winner.issns = ("00280836", "invalid", "2434-561x", "0028-0836")
    duplicate = _paper("duplicate", "10.9999/issns")
    duplicate.issns = ("2434561X", "20493630", "0028-0836", "bad")

    # When: duplicate ISSNs are merged.
    _ = retrieve_and_merge({"source": StubRetriever(lambda: [winner, duplicate])})

    # Then: only canonical valid first occurrences remain in stable order.
    assert winner.issns == ("0028-0836", "2434-561X", "2049-3630")


def test_missing_blank_and_invalid_dois_remain_independent_and_unchanged() -> None:
    # Given: papers whose DOI values cannot form a valid deduplication key.
    papers = [
        _paper("none", None),
        _paper("blank", "  \t"),
        _paper("invalid-first", "not-a-doi"),
        _paper("invalid-second", "not-a-doi"),
    ]
    original_dois = [paper.doi for paper in papers]

    # When: retrieval performs DOI deduplication.
    result = retrieve_and_merge({"source": StubRetriever(lambda: papers)})

    # Then: every invalid-key paper and its original DOI representation survives.
    assert result == papers
    assert [paper.doi for paper in result] == original_dois
    assert all(actual is expected for actual, expected in zip(result, papers, strict=True))
