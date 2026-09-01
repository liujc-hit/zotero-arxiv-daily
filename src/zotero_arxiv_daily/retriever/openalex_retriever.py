"""Retrieve recent papers from exact OpenAlex venue identifiers."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Final, Protocol, TypeGuard, override

from omegaconf import DictConfig, ListConfig, OmegaConf

from ..identifiers import normalize_doi
from ..protocol import Paper
from .base import BaseRetriever, register_retriever
from .openalex_client import JsonObject, JsonValue, OpenAlexClient
from .openalex_metadata import parse_openalex_publication_metadata
from .openalex_venue_catalog import (
    ALL_VENUES,
    CONFERENCE_VENUES,
    JOURNAL_VENUES,
    VenueSpec,
)


type JsonBoundary = object


class _ConfigSelector(Protocol):
    def __call__(self, cfg: DictConfig, key: str) -> JsonBoundary: ...


__all__: Final[tuple[str, ...]] = (
    "ALL_VENUES",
    "CONFERENCE_VENUES",
    "InvalidOpenAlexConfigurationError",
    "JOURNAL_VENUES",
    "OpenAlexRetriever",
    "VenueSpec",
)


class InvalidOpenAlexConfigurationError(TypeError):
    """Raised without retaining malformed or secret credential values."""

    message: ClassVar[str] = "source.openalex credential configuration is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidLookbackDaysError(ValueError):
    """Raised when the OpenAlex completed-day window is empty."""

    def __init__(self, lookback_days: JsonBoundary) -> None:
        self.lookback_days: JsonBoundary = lookback_days
        super().__init__(
            f"source.openalex.lookback_days must be at least 1, got {lookback_days}"
        )


class InvalidOpenAlexJsonError(TypeError):
    """Raised when an OpenAlex response contains a non-JSON value."""

    def __init__(self, value_type: str) -> None:
        self.value_type: str = value_type
        super().__init__(f"OpenAlex response contains unsupported value type {value_type}")


def _is_boundary_list(value: JsonBoundary) -> TypeGuard[list[JsonBoundary]]:
    return isinstance(value, list)


def _is_boundary_mapping(
    value: JsonBoundary,
) -> TypeGuard[dict[JsonBoundary, JsonBoundary]]:
    return isinstance(value, dict)


def _is_config_list(value: JsonBoundary) -> TypeGuard[Sequence[JsonBoundary]]:
    return isinstance(value, (list, ListConfig))


def _select_config(
    selector: _ConfigSelector, config: DictConfig, key: str
) -> JsonBoundary:
    return selector(config, key)


def _parse_json(value: JsonBoundary) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _is_boundary_list(value):
        return [_parse_json(item) for item in value]
    if _is_boundary_mapping(value):
        parsed: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidOpenAlexJsonError(type(key).__name__)
            parsed[key] = _parse_json(item)
        return parsed
    raise InvalidOpenAlexJsonError(type(value).__name__)


def _mapping(value: JsonValue | None) -> Mapping[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _items(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


@register_retriever("openalex")
class OpenAlexRetriever(BaseRetriever):
    """Retrieve papers from the built-in exact OpenAlex venue catalog."""

    identifier_batch_size: ClassVar[int] = 100
    page_size: ClassVar[int] = 100
    _client: OpenAlexClient

    def __init__(self, config: DictConfig) -> None:
        super().__init__(config)
        self._typed_config: DictConfig = config
        lookback_days = _select_config(
            OmegaConf.select, config, "source.openalex.lookback_days"
        )
        if not isinstance(lookback_days, int) or isinstance(lookback_days, bool):
            raise InvalidLookbackDaysError(lookback_days)
        self.lookback_days: int = lookback_days
        if self.lookback_days < 1:
            raise InvalidLookbackDaysError(self.lookback_days)

        api_keys = _select_config(OmegaConf.select, config, "source.openalex.api_keys")
        if not _is_config_list(api_keys):
            raise InvalidOpenAlexConfigurationError from None
        normalized_api_keys: list[str] = []
        for api_key in api_keys:
            if api_key is None:
                continue
            if not isinstance(api_key, str):
                raise InvalidOpenAlexConfigurationError from None
            if normalized_api_key := api_key.strip():
                normalized_api_keys.append(normalized_api_key)

        allow_anonymous = _select_config(
            OmegaConf.select, config, "source.openalex.allow_anonymous"
        )
        if not isinstance(allow_anonymous, bool):
            raise InvalidOpenAlexConfigurationError from None
        self._client = OpenAlexClient(
            tuple(normalized_api_keys), anonymous_fallback=allow_anonymous
        )

    @property
    def client(self) -> OpenAlexClient:
        return self._client

    def _retrieve_batch(
        self,
        identifier_filter: str,
        date_filter: str,
    ) -> list[JsonObject]:
        collection: list[JsonObject] = []
        cursor = "*"
        seen_cursors: set[str] = set()
        while cursor not in seen_cursors:
            seen_cursors.add(cursor)
            params: dict[str, str | int] = {
                "filter": f"{identifier_filter},{date_filter}",
                "per_page": self.page_size,
                "cursor": cursor,
            }
            payload = _mapping(self._client.get_json(params))
            results = _items(payload.get("results"))
            if not results:
                break
            collection.extend(result for result in results if isinstance(result, dict))

            next_cursor_value = _mapping(payload.get("meta")).get("next_cursor")
            next_cursor = next_cursor_value if isinstance(next_cursor_value, str) else None
            if next_cursor is None or next_cursor in seen_cursors:
                break
            cursor = next_cursor
        return collection

    @override
    def _retrieve_raw_papers(self) -> list[JsonObject]:
        end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=self.lookback_days - 1)
        date_filter = (
            f"from_publication_date:{start_date.isoformat()},"
            f"to_publication_date:{end_date.isoformat()}"
        )

        collection: list[JsonObject] = []
        for partition in (JOURNAL_VENUES, CONFERENCE_VENUES):
            venues = tuple(venue for venue in partition if isinstance(venue, VenueSpec))
            source_ids = tuple(
                source_id for venue in venues for source_id in venue.openalex_source_ids
            )
            issns = tuple(issn for venue in venues for issn in venue.issns)
            identifier_groups = (
                ("primary_location.source.id", source_ids),
                ("primary_location.source.issn", issns),
            )
            for filter_name, identifiers in identifier_groups:
                for offset in range(0, len(identifiers), self.identifier_batch_size):
                    batch = identifiers[offset : offset + self.identifier_batch_size]
                    identifier_filter = f"{filter_name}:{'|'.join(batch)}"
                    collection.extend(self._retrieve_batch(identifier_filter, date_filter))

        deduplicated: list[JsonObject] = []
        seen_keys: set[str] = set()
        for work in collection:
            doi_value = work.get("doi")
            normalized_doi = normalize_doi(
                doi_value if isinstance(doi_value, str) else None
            )
            if normalized_doi is not None:
                key = f"doi:{normalized_doi}"
            else:
                key = f"id:{_text(work['id']).strip().lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduplicated.append(work)

        debug = _select_config(OmegaConf.select, self._typed_config, "executor.debug")
        if debug is True:
            return deduplicated[:10]
        return deduplicated

    @override
    def convert_to_paper(self, raw_paper: JsonBoundary) -> Paper | None:
        parsed = _parse_json(raw_paper)
        if not isinstance(parsed, dict):
            return None
        raw_paper = parsed

        title = _text(raw_paper.get("title")).strip()
        if not title:
            return None

        authors: list[str] = []
        for authorship_value in _items(raw_paper.get("authorships")):
            authorship = _mapping(authorship_value)
            author = _mapping(authorship.get("author"))
            name = _text(author.get("display_name")).strip()
            if name:
                authors.append(name)

        inverted_index = _mapping(raw_paper.get("abstract_inverted_index"))
        occurrences: list[tuple[int, str]] = []
        for token, positions_value in inverted_index.items():
            for position in _items(positions_value):
                if isinstance(position, int):
                    occurrences.append((position, token))
        occurrences.sort()
        abstract = " ".join(token for _, token in occurrences)

        primary_location = _mapping(raw_paper.get("primary_location"))
        source = _mapping(primary_location.get("source"))
        metadata = parse_openalex_publication_metadata(raw_paper, source)
        best_oa_location = _mapping(raw_paper.get("best_oa_location"))
        url_value = (
            raw_paper.get("doi")
            or primary_location.get("landing_page_url")
            or raw_paper["id"]
        )
        pdf_value = best_oa_location.get("pdf_url") or primary_location.get("pdf_url")
        pdf_url = None if pdf_value is None else _text(pdf_value)
        return Paper(
            source="openalex",
            title=title,
            authors=authors,
            abstract=abstract,
            url=_text(url_value),
            pdf_url=pdf_url,
            full_text=None,
            doi=metadata.doi,
            publisher=metadata.publisher,
            issns=metadata.issns,
            is_preprint=metadata.is_preprint,
        )
