"""Crossref-first, single-route abstract enrichment orchestration."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final, Protocol, final

from loguru import logger

from ..protocol import Paper
from ..retriever.crossref_client import (
    CrossrefHttpStatusError,
    CrossrefInvalidJsonError,
    CrossrefTransportError,
    JsonObject,
)
from ..retriever.crossref_metadata import parse_crossref_work_payload
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


class CrossrefWorkClient(Protocol):
    def get_work(self, doi: str) -> JsonObject: ...


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
    """Mutate eligible Paper objects using Crossref and at most one vertical."""

    def __init__(
        self,
        crossref_client: CrossrefWorkClient,
        settings: EnrichmentSettings,
        adapters: EnrichmentAdapters | None = None,
    ) -> None:
        self._crossref_client = crossref_client
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
        paper = candidate.paper
        try:
            payload = self._crossref_client.get_work(candidate.doi)
        except CrossrefHttpStatusError as error:
            logger.warning("Crossref get_work HTTP status {}", error.status_code)
        except CrossrefTransportError:
            logger.warning("Crossref get_work transport failure")
        except CrossrefInvalidJsonError:
            logger.warning("Crossref get_work invalid JSON")
        else:
            metadata = parse_crossref_work_payload(payload)
            if metadata is not None:
                if paper.publisher is None or not paper.publisher.strip():
                    paper.publisher = metadata.publisher
                if not paper.issns:
                    paper.issns = metadata.issns
                if paper.is_preprint is None:
                    paper.is_preprint = metadata.is_preprint
                if metadata.abstract:
                    paper.abstract = metadata.abstract
                    return

        adapter = self._select_adapter(candidate)
        if adapter is None:
            return
        abstract = adapter.fetch_abstract(candidate.doi)
        if abstract is not None and abstract.strip():
            paper.abstract = abstract.strip()

    def _select_adapter(self, candidate: _Candidate) -> AbstractAdapter | None:
        paper = candidate.paper
        settings = self._settings
        if settings.pubmed.available and _issn_intersects(
            paper.issns,
            settings.pubmed.issns,
        ):
            return self._adapters.pubmed
        if settings.ieee.available and (
            _doi_has_prefix(candidate.doi, settings.ieee.doi_prefixes)
            or _publisher_has_token(paper.publisher, _IEEE_PUBLISHERS)
            or _issn_intersects(paper.issns, settings.ieee.issns)
        ):
            return self._adapters.ieee
        if settings.elsevier.available and (
            _doi_has_prefix(candidate.doi, settings.elsevier.doi_prefixes)
            or _publisher_has_token(paper.publisher, _ELSEVIER_PUBLISHERS)
            or _issn_intersects(paper.issns, settings.elsevier.issns)
        ):
            return self._adapters.elsevier
        if settings.springer.available and (
            _doi_has_prefix(candidate.doi, settings.springer.doi_prefixes)
            or _publisher_has_token(paper.publisher, _SPRINGER_PUBLISHERS)
            or _issn_intersects(paper.issns, settings.springer.issns)
        ):
            return self._adapters.springer
        return None


__all__: Final[tuple[str, ...]] = ("AbstractEnricher", "EnrichmentAdapters")
