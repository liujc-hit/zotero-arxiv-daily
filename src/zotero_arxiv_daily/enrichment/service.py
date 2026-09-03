"""Deterministic two-attempt abstract enrichment orchestration."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final, Protocol, final

from ..protocol import Paper
from .providers import ElsevierAdapter, IEEEAdapter, PubMedAdapter, SpringerAdapter
from .settings import EnrichmentSettings


_IEEE_PUBLISHERS: Final[tuple[str, ...]] = ("ieee",)
_ELSEVIER_PUBLISHERS: Final[tuple[str, ...]] = ("elsevier",)
_SPRINGER_PUBLISHERS: Final[tuple[str, ...]] = (
    "springer",
    "nature",
    "bmc",
    "palgrave",
)
_PUBLISHER_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


class AbstractAdapter(Protocol):
    def fetch_abstract(self, doi: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class EnrichmentAdapters:
    pubmed: AbstractAdapter
    ieee: AbstractAdapter
    elsevier: AbstractAdapter
    springer: AbstractAdapter


@dataclass(frozen=True, slots=True)
class _Candidate:
    paper: Paper
    doi: str


def _issn_intersects(
    paper_issns: tuple[str, ...],
    configured_issns: tuple[str, ...],
) -> bool:
    paper_values = {value.strip().casefold() for value in paper_issns if value.strip()}
    configured_values = {
        value.strip().casefold() for value in configured_issns if value.strip()
    }
    return not paper_values.isdisjoint(configured_values)


def _doi_has_prefix(doi: str, prefixes: tuple[str, ...]) -> bool:
    normalized_doi = doi.strip().casefold()
    return any(
        normalized_doi.startswith(f"{prefix.strip().casefold().rstrip('/')}/")
        for prefix in prefixes
        if prefix.strip()
    )


def _publisher_has_token(
    publisher: str | None,
    tokens: tuple[str, ...],
) -> bool:
    if publisher is None:
        return False
    words = frozenset(_PUBLISHER_WORD_PATTERN.findall(publisher.casefold()))
    return any(token in words for token in tokens)


@final
class AbstractEnricher:
    """Mutate eligible papers through at most two matched vertical adapters."""

    def __init__(
        self,
        settings: EnrichmentSettings,
        adapters: EnrichmentAdapters | None = None,
    ) -> None:
        self._settings = settings
        self._adapters = adapters or EnrichmentAdapters(
            pubmed=PubMedAdapter(settings.pubmed),
            ieee=IEEEAdapter(settings.ieee),
            elsevier=ElsevierAdapter(settings.elsevier),
            springer=SpringerAdapter(settings.springer),
        )

    def enrich(self, papers: list[Paper]) -> None:
        """Enrich at most max_papers eligible objects with at most workers threads."""
        if self._settings.workers <= 0 or self._settings.max_papers <= 0:
            return
        candidates: list[_Candidate] = []
        for paper in papers:
            doi = paper.doi
            if (
                paper.is_preprint is False
                and not paper.abstract.strip()
                and doi is not None
                and bool(doi.strip())
            ):
                candidates.append(_Candidate(paper=paper, doi=doi))
                if len(candidates) == self._settings.max_papers:
                    break
        if not candidates:
            return
        with ThreadPoolExecutor(
            max_workers=min(self._settings.workers, len(candidates))
        ) as executor:
            _ = tuple(executor.map(self._enrich_candidate, candidates))

    def _enrich_candidate(self, candidate: _Candidate) -> None:
        for adapter in self._route(candidate):
            abstract = adapter.fetch_abstract(candidate.doi)
            if abstract is not None and abstract.strip():
                candidate.paper.abstract = abstract.strip()
                return

    def _route(self, candidate: _Candidate) -> tuple[AbstractAdapter, ...]:
        paper = candidate.paper
        settings = self._settings
        routes = (
            (
                self._adapters.ieee,
                settings.ieee.available
                and (
                    _doi_has_prefix(candidate.doi, settings.ieee.doi_prefixes)
                    or _publisher_has_token(paper.publisher, _IEEE_PUBLISHERS)
                    or _issn_intersects(paper.issns, settings.ieee.issns)
                ),
            ),
            (
                self._adapters.elsevier,
                settings.elsevier.available
                and (
                    _doi_has_prefix(candidate.doi, settings.elsevier.doi_prefixes)
                    or _publisher_has_token(paper.publisher, _ELSEVIER_PUBLISHERS)
                    or _issn_intersects(paper.issns, settings.elsevier.issns)
                ),
            ),
            (
                self._adapters.springer,
                settings.springer.available
                and (
                    _doi_has_prefix(candidate.doi, settings.springer.doi_prefixes)
                    or _publisher_has_token(paper.publisher, _SPRINGER_PUBLISHERS)
                    or _issn_intersects(paper.issns, settings.springer.issns)
                ),
            ),
            (
                self._adapters.pubmed,
                settings.pubmed.available
                and _issn_intersects(paper.issns, settings.pubmed.issns),
            ),
        )
        return tuple(adapter for adapter, matched in routes if matched)[:2]


__all__: Final[tuple[str, ...]] = ("AbstractEnricher", "EnrichmentAdapters")
