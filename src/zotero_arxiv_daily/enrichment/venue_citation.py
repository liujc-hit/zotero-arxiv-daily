"""Optional in-memory OpenAlex venue citation proxy enrichment."""

from collections.abc import Iterable, Mapping
from math import isfinite
from statistics import median
from typing import Final, Protocol, final

from ..identifiers import normalize_issn
from ..protocol import Paper
from ..retriever.openalex_client import JsonValue


_BATCH_SIZE: Final = 100


class OpenAlexSourcesClient(Protocol):
    def get_sources_json(self, params: Mapping[str, str | int]) -> JsonValue: ...


def _normalized_issns(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        issn = normalize_issn(value)
        if issn is not None and issn not in normalized:
            normalized.append(issn)
    return tuple(normalized)


def _source_issns(source: Mapping[str, JsonValue]) -> tuple[str, ...]:
    listed = source.get("issn")
    values = listed if isinstance(listed, list) else []
    linked = source.get("issn_l")
    strings = (
        value
        for value in (*values, linked)
        if isinstance(value, str)
    )
    return _normalized_issns(strings)


def _source_proxy(source: Mapping[str, JsonValue]) -> float | None:
    summary = source.get("summary_stats")
    if not isinstance(summary, dict):
        return None
    raw_value = summary.get("2yr_mean_citedness")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return None
    value = float(raw_value)
    return value if isfinite(value) and value >= 0.0 else None


@final
class VenueCitationEnricher:
    """Mutate candidate proxies while retaining only a per-instance memory cache."""

    __slots__ = (
        "_client",
        "_enabled",
        "_proxy_by_issn",
        "_queried_issns",
        "_samples_by_source_issns",
    )

    def __init__(
        self,
        client: OpenAlexSourcesClient,
        *,
        enabled: bool = False,
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._proxy_by_issn: dict[str, float] = {}
        self._queried_issns: set[str] = set()
        self._samples_by_source_issns: dict[tuple[str, ...], float] = {}

    def enrich(self, papers: list[Paper]) -> None:
        """Fetch published venue values, then assign their source-sample median."""
        if not self._enabled:
            return

        pending: list[str] = []
        seen: set[str] = set()
        for paper in papers:
            if paper.is_preprint is not False:
                continue
            for issn in _normalized_issns(paper.issns):
                if (
                    issn not in seen
                    and issn not in self._queried_issns
                    and issn not in self._proxy_by_issn
                ):
                    seen.add(issn)
                    pending.append(issn)

        for offset in range(0, len(pending), _BATCH_SIZE):
            batch = tuple(pending[offset : offset + _BATCH_SIZE])
            payload = self._client.get_sources_json(
                {
                    "filter": f"issn:{'|'.join(batch)}",
                    "per_page": _BATCH_SIZE,
                }
            )
            self._queried_issns.update(batch)
            self._collect_sources(payload, frozenset(batch))

        for paper in papers:
            if paper.is_preprint is not False:
                continue
            for issn in _normalized_issns(paper.issns):
                proxy = self._proxy_by_issn.get(issn)
                if proxy is not None:
                    paper.venue_citation_proxy = proxy
                    break

        if not self._samples_by_source_issns:
            return
        preprint_proxy = float(median(self._samples_by_source_issns.values()))
        for paper in papers:
            if paper.is_preprint is True:
                paper.venue_citation_proxy = preprint_proxy

    def _collect_sources(
        self,
        payload: JsonValue,
        requested: frozenset[str],
    ) -> None:
        if not isinstance(payload, dict):
            return
        results = payload.get("results")
        if not isinstance(results, list):
            return
        for source in results:
            if not isinstance(source, dict):
                continue
            source_issns = _source_issns(source)
            if requested.isdisjoint(source_issns):
                continue
            proxy = _source_proxy(source)
            if proxy is None:
                continue
            source_key = tuple(sorted(source_issns))
            _ = self._samples_by_source_issns.setdefault(source_key, proxy)
            for issn in source_issns:
                _ = self._proxy_by_issn.setdefault(issn, proxy)


__all__: Final[tuple[str, ...]] = ("OpenAlexSourcesClient", "VenueCitationEnricher")
