"""Retrieve recent Crossref works from curated catalog ISSNs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import ClassVar, Final, Protocol, TypeGuard, override

from loguru import logger
from omegaconf import DictConfig, ListConfig, OmegaConf

from ..identifiers import normalize_doi, normalize_issn
from ..protocol import Paper
from .base import BaseRetriever, register_retriever
from .crossref_client import (
    CrossrefClient,
    CrossrefHttpStatusError,
    CrossrefInvalidJsonError,
    CrossrefTransportError,
    JsonObject,
)
from .crossref_metadata import (
    clean_crossref_text,
    crossref_title,
    json_items,
    json_mapping,
    parse_crossref_publication_metadata,
)
from .openalex_venue_catalog import ALL_VENUES

type _ConfigValue = str | int | float | bool | None | DictConfig | ListConfig
type JsonBoundary = object


class _ConfigSelector(Protocol):
    def __call__(self, cfg: DictConfig, key: str) -> _ConfigValue: ...


class InvalidCrossrefConfigurationError(TypeError):
    """Raised without retaining a malformed Crossref contact value."""

    message: ClassVar[str] = "source.crossref.mailto must be a nonblank string"

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidCrossrefLookbackDaysError(ValueError):
    """Raised without retaining a malformed completed-day window."""

    message: ClassVar[str] = (
        "source.crossref.lookback_days must be a positive integer"
    )

    def __init__(self) -> None:
        super().__init__(self.message)


CURATED_ISSNS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(
        normalized
        for venue in ALL_VENUES
        for issn in venue.issns
        if (normalized := normalize_issn(issn)) is not None
    )
)

_SELECT_FIELDS: Final = (
    "DOI,title,author,abstract,publisher,ISSN,URL,link,type"
)


def _select_config(selector: _ConfigSelector, config: DictConfig, key: str) -> _ConfigValue:
    return selector(config, key)


def _is_json_object(value: JsonBoundary) -> TypeGuard[JsonObject]:
    return isinstance(value, dict)


@register_retriever("crossref")
class CrossrefRetriever(BaseRetriever):
    """Retrieve completed publication days for exact curated ISSNs."""

    page_size: ClassVar[int] = 1000
    max_workers: ClassVar[int] = 3
    _client: CrossrefClient

    def __init__(self, config: DictConfig) -> None:
        mailto_value = _select_config(OmegaConf.select, config, "source.crossref.mailto")
        if not isinstance(mailto_value, str) or not mailto_value.strip():
            raise InvalidCrossrefConfigurationError from None

        lookback_value = _select_config(OmegaConf.select, config, "source.crossref.lookback_days")
        if (
            not isinstance(lookback_value, int)
            or isinstance(lookback_value, bool)
            or lookback_value < 1
        ):
            raise InvalidCrossrefLookbackDaysError from None

        super().__init__(config)
        self.lookback_days: int = lookback_value
        self._client = CrossrefClient(mailto_value.strip())

    @property
    def client(self) -> CrossrefClient:
        return self._client

    def _retrieve_issn(
        self, issn: str, date_filter: str, stop: Event
    ) -> list[JsonObject]:
        collection: list[JsonObject] = []
        cursor = "*"
        seen_cursors: set[str] = set()

        while not stop.is_set() and cursor not in seen_cursors:
            seen_cursors.add(cursor)
            params: dict[str, str | int] = {
                "filter": date_filter,
                "rows": self.page_size,
                "cursor": cursor,
                "select": _SELECT_FIELDS,
            }
            try:
                payload = self._client.list_journal_works(issn, params)
            except CrossrefHttpStatusError as error:
                if error.status_code == 429:
                    stop.set()
                logger.warning(
                    "Crossref list_journal_works returned HTTP status {}; skipping page",
                    error.status_code,
                )
                break
            except CrossrefTransportError:
                logger.warning(
                    "Crossref list_journal_works transport failure; skipping page"
                )
                break
            except CrossrefInvalidJsonError:
                logger.warning(
                    "Crossref list_journal_works invalid JSON; skipping page"
                )
                break

            message = json_mapping(payload.get("message"))
            values = json_items(message.get("items"))
            collection.extend(value for value in values if isinstance(value, dict))
            if len(values) < self.page_size:
                break

            next_cursor_value = message.get("next-cursor")
            next_cursor = (
                next_cursor_value.strip()
                if isinstance(next_cursor_value, str)
                else ""
            )
            if not next_cursor or next_cursor in seen_cursors:
                break
            cursor = next_cursor

        return collection

    @override
    def _retrieve_raw_papers(self) -> list[JsonObject]:
        if not CURATED_ISSNS:
            return []

        end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=self.lookback_days - 1)
        date_filter = (
            f"from-pub-date:{start_date.isoformat()},"
            f"until-pub-date:{end_date.isoformat()}"
        )
        stop = Event()
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(CURATED_ISSNS))
        ) as pool:
            futures = tuple(
                pool.submit(self._retrieve_issn, issn, date_filter, stop)
                for issn in CURATED_ISSNS
            )
            ordered_batches = tuple(future.result() for future in futures)

        deduplicated: list[JsonObject] = []
        seen_dois: set[str] = set()
        for batch in ordered_batches:
            for work in batch:
                doi_value = work.get("DOI")
                doi = normalize_doi(doi_value if isinstance(doi_value, str) else None)
                if doi is None or not crossref_title(work) or doi in seen_dois:
                    continue
                seen_dois.add(doi)
                deduplicated.append(work)

        debug = _select_config(OmegaConf.select, self.config, "executor.debug")
        return deduplicated[:10] if debug is True else deduplicated

    @override
    def convert_to_paper(self, raw_paper: JsonBoundary) -> Paper | None:
        if not _is_json_object(raw_paper):
            return None
        doi_value = raw_paper.get("DOI")
        doi = normalize_doi(doi_value if isinstance(doi_value, str) else None)
        title = crossref_title(raw_paper)
        if doi is None or not title:
            return None
        metadata = parse_crossref_publication_metadata(raw_paper)

        authors: list[str] = []
        for author_value in json_items(raw_paper.get("author")):
            author = json_mapping(author_value)
            name = clean_crossref_text(author.get("name"))
            if not name:
                name = " ".join(
                    part
                    for part in (
                        clean_crossref_text(author.get("given")),
                        clean_crossref_text(author.get("family")),
                    )
                    if part
                )
            if name:
                authors.append(name)

        pdf_url: str | None = None
        for link_value in json_items(raw_paper.get("link")):
            link = json_mapping(link_value)
            candidate = clean_crossref_text(link.get("URL"))
            content_type = clean_crossref_text(link.get("content-type")).casefold()
            if candidate and (
                content_type == "application/pdf"
                or candidate.casefold().endswith(".pdf")
            ):
                pdf_url = candidate
                break

        url = clean_crossref_text(raw_paper.get("URL")) or f"https://doi.org/{doi}"
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=metadata.abstract,
            url=url,
            pdf_url=pdf_url,
            full_text=None,
            doi=doi,
            publisher=metadata.publisher,
            issns=metadata.issns,
            is_preprint=metadata.is_preprint,
        )


__all__: Final[tuple[str, ...]] = (
    "CURATED_ISSNS",
    "CrossrefRetriever",
    "InvalidCrossrefConfigurationError",
    "InvalidCrossrefLookbackDaysError",
)
