"""No-retry PubMed History discovery through fixed NCBI endpoints."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import ClassVar, Final, final, override

from ..enrichment.pacing import ProviderTiming, StartPacer
from ..enrichment.transport import (
    HttpResponse,
    ProviderName,
    ProviderRequest,
    parse_json,
    request_once,
)
from .pubmed_metadata import PubMedRecord, parse_pubmed_article_set


_ESEARCH_ENDPOINT: Final = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)
_EFETCH_ENDPOINT: Final = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)
_TOOL: Final = "zotero-arxiv-daily"
_PAGE_SIZE: Final = 200
_MAX_RESULTS: Final = 10_000
_ANONYMOUS_RATE: Final = 3.0
_KEYED_RATE: Final = 10.0

type _ConfigBoundary = str | int | float | bool | None


class PubMedOperation(StrEnum):
    ESEARCH = "ESearch"
    EFETCH = "EFetch"


class PubMedClientError(RuntimeError):
    """Base class for redacted PubMed discovery failures."""


class _StaticPubMedClientError(PubMedClientError):
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)


class PubMedClientConfigurationError(_StaticPubMedClientError):
    """Raised when direct client inputs cannot form a safe request."""

    message: ClassVar[str] = "PubMed client configuration is invalid"


class PubMedInvalidSearchResponseError(_StaticPubMedClientError):
    """Raised when ESearch does not provide a complete History result."""

    message: ClassVar[str] = "PubMed ESearch returned an invalid history response"


class PubMedResultLimitError(_StaticPubMedClientError):
    """Raised rather than silently truncating oversized History results."""

    message: ClassVar[str] = "PubMed ESearch exceeded the 10000-record limit"


class PubMedCountMismatchError(_StaticPubMedClientError):
    """Raised when all EFetch pages do not reproduce the ESearch count."""

    message: ClassVar[str] = "PubMed EFetch record count did not match ESearch"


class PubMedDuplicatePmidError(_StaticPubMedClientError):
    """Raised when a History result contains duplicate publication identity."""

    message: ClassVar[str] = "PubMed EFetch returned duplicate PMIDs"


class PubMedRequestError(PubMedClientError):
    """One failed, non-retried NCBI operation."""

    operation: PubMedOperation

    def __init__(self, operation: PubMedOperation) -> None:
        self.operation = operation
        super().__init__(f"PubMed {operation.value} request failed")


@dataclass(frozen=True, slots=True)
class _History:
    count: int
    webenv: str = field(repr=False)
    query_key: str = field(repr=False)


@final
class PubMedClient:
    """Discover and fetch one bounded PubMed History result in source order."""

    __slots__: ClassVar[tuple[str, ...]] = (
        "_api_key",
        "_contact_email",
        "_effective_request_rate",
        "_pacer",
    )
    _api_key: str | None
    _contact_email: str
    _effective_request_rate: float
    _pacer: StartPacer

    def __init__(
        self,
        contact_email: _ConfigBoundary,
        api_key: _ConfigBoundary = None,
        request_rate: _ConfigBoundary = _KEYED_RATE,
        *,
        timing: ProviderTiming | None = None,
    ) -> None:
        if not isinstance(contact_email, str) or not contact_email.strip():
            raise PubMedClientConfigurationError from None
        if api_key is not None and not isinstance(api_key, str):
            raise PubMedClientConfigurationError from None
        if (
            isinstance(request_rate, bool)
            or not isinstance(request_rate, (int, float))
            or not isfinite(request_rate)
            or request_rate <= 0
        ):
            raise PubMedClientConfigurationError from None

        self._contact_email = contact_email.strip()
        self._api_key = api_key.strip() or None if api_key is not None else None
        rate_limit = _KEYED_RATE if self._api_key is not None else _ANONYMOUS_RATE
        self._effective_request_rate = min(float(request_rate), rate_limit)
        self._pacer = StartPacer(
            self._effective_request_rate,
            timing or ProviderTiming(),
        )

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    @property
    def effective_request_rate(self) -> float:
        return self._effective_request_rate

    def discover(
        self,
        query: _ConfigBoundary,
        start_date: date,
        end_date: date,
    ) -> list[PubMedRecord]:
        """Run one History ESearch and fetch every declared record once."""
        if not isinstance(query, str) or not query.strip():
            raise PubMedClientConfigurationError from None

        identity: dict[str, str] = {
            "tool": _TOOL,
            "email": self._contact_email,
        }
        if self._api_key is not None:
            identity["api_key"] = self._api_key

        search_response = self._request(
            PubMedOperation.ESEARCH,
            ProviderRequest(
                provider=ProviderName.PUBMED,
                url=_ESEARCH_ENDPOINT,
                params={
                    "db": "pubmed",
                    "term": query.strip(),
                    "datetype": "edat",
                    "mindate": start_date.strftime("%Y/%m/%d"),
                    "maxdate": end_date.strftime("%Y/%m/%d"),
                    "sort": "pub_date",
                    "retmode": "json",
                    "retmax": 0,
                    "usehistory": "y",
                    **identity,
                },
                headers={"Accept": "application/json"},
            ),
        )
        history = self._parse_history(search_response)
        if history.count > _MAX_RESULTS:
            raise PubMedResultLimitError from None
        if history.count == 0:
            return []

        records: list[PubMedRecord] = []
        for retstart in range(0, history.count, _PAGE_SIZE):
            fetch_response = self._request(
                PubMedOperation.EFETCH,
                ProviderRequest(
                    provider=ProviderName.PUBMED,
                    url=_EFETCH_ENDPOINT,
                    params={
                        "db": "pubmed",
                        "WebEnv": history.webenv,
                        "query_key": history.query_key,
                        "retstart": retstart,
                        "retmax": _PAGE_SIZE,
                        "retmode": "xml",
                        **identity,
                    },
                    headers={"Accept": "application/xml"},
                ),
            )
            records.extend(parse_pubmed_article_set(fetch_response.content))

        if len(records) != history.count:
            raise PubMedCountMismatchError from None
        if len({record.pmid for record in records}) != history.count:
            raise PubMedDuplicatePmidError from None
        return records

    def _request(
        self,
        operation: PubMedOperation,
        request: ProviderRequest,
    ) -> HttpResponse:
        response = request_once(request, self._pacer)
        if response is None:
            raise PubMedRequestError(operation) from None
        return response

    @staticmethod
    def _parse_history(response: HttpResponse) -> _History:
        payload = parse_json(response, ProviderName.PUBMED)
        if payload is None:
            raise PubMedInvalidSearchResponseError from None
        result = payload.get("esearchresult")
        if not isinstance(result, dict):
            raise PubMedInvalidSearchResponseError from None

        count_value = result.get("count")
        webenv = result.get("webenv")
        query_key = result.get("querykey")
        if (
            not isinstance(count_value, str)
            or not count_value.isascii()
            or not count_value.isdecimal()
            or not isinstance(webenv, str)
            or not webenv
            or webenv != webenv.strip()
            or not isinstance(query_key, str)
            or not query_key
            or query_key != query_key.strip()
        ):
            raise PubMedInvalidSearchResponseError from None
        return _History(int(count_value), webenv, query_key)


__all__: Final[tuple[str, ...]] = (
    "PubMedClient",
    "PubMedClientConfigurationError",
    "PubMedClientError",
    "PubMedCountMismatchError",
    "PubMedDuplicatePmidError",
    "PubMedInvalidSearchResponseError",
    "PubMedOperation",
    "PubMedRequestError",
    "PubMedResultLimitError",
)
