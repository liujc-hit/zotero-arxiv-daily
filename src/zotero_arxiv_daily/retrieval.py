"""Concurrent configured-source retrieval with deterministic DOI merging."""

from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Final, Protocol

from loguru import logger

from .identifiers import normalize_doi, normalize_issn
from .protocol import Paper


MAX_SOURCE_WORKERS: Final = 4


class SourceRetriever(Protocol):
    """A configured source capable of returning papers in source-local order."""

    def retrieve_papers(self) -> list[Paper]: ...


def retrieve_and_merge(
    retrievers: Mapping[str, SourceRetriever],
) -> list[Paper]:
    """Retrieve all configured sources concurrently, then merge DOI duplicates."""
    source_count = len(retrievers)
    if source_count == 0:
        return []

    configured_sources = tuple(retrievers.items())
    with ThreadPoolExecutor(
        max_workers=min(MAX_SOURCE_WORKERS, source_count)
    ) as executor:
        source_futures = tuple(
            (source_name, executor.submit(retriever.retrieve_papers))
            for source_name, retriever in configured_sources
        )
        ordered_papers: list[Paper] = []
        for source_name, future in source_futures:
            try:
                source_papers = future.result()
            except Exception as exc:
                logger.warning(
                    "source={} type={}", source_name, type(exc).__name__
                )
                source_papers = []
            ordered_papers.extend(source_papers)

    return _deduplicate_by_doi(ordered_papers)


def _deduplicate_by_doi(papers: Iterable[Paper]) -> list[Paper]:
    winners_by_doi: dict[str, Paper] = {}
    merged: list[Paper] = []

    for paper in papers:
        canonical_doi = normalize_doi(paper.doi)
        if canonical_doi is None:
            merged.append(paper)
            continue

        winner = winners_by_doi.get(canonical_doi)
        if winner is None:
            paper.doi = canonical_doi
            paper.issns = _normalized_issns(paper.issns)
            winners_by_doi[canonical_doi] = paper
            merged.append(paper)
            continue

        _merge_missing_metadata(winner, paper)

    return merged


def _merge_missing_metadata(winner: Paper, duplicate: Paper) -> None:
    if winner.abstract.strip() == "":
        duplicate_abstract = duplicate.abstract.strip()
        if duplicate_abstract != "":
            winner.abstract = duplicate_abstract

    if winner.publisher is None or winner.publisher.strip() == "":
        if duplicate.publisher is not None:
            duplicate_publisher = duplicate.publisher.strip()
            if duplicate_publisher != "":
                winner.publisher = duplicate_publisher

    if winner.journal is None or winner.journal.strip() == "":
        if duplicate.journal is not None:
            duplicate_journal = duplicate.journal.strip()
            if duplicate_journal != "":
                winner.journal = duplicate_journal

    if winner.is_preprint is None and duplicate.is_preprint is not None:
        winner.is_preprint = duplicate.is_preprint

    winner.issns = _normalized_issns((*winner.issns, *duplicate.issns))


def _normalized_issns(values: Iterable[str]) -> tuple[str, ...]:
    normalized_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_issn(value)
        if normalized is not None and normalized not in seen:
            normalized_values.append(normalized)
            seen.add(normalized)
    return tuple(normalized_values)
