"""Retrieve completed PubMed entry-date windows through History discovery."""

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import ClassVar, Final, Protocol, override

from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from ..protocol import Paper
from .base import BaseRetriever, register_retriever
from .pubmed_client import PubMedClient
from .pubmed_metadata import PubMedRecord


type _ConfigValue = str | int | float | bool | None | DictConfig | ListConfig
type _RawPubMedBoundary = object  # noqa: OBJECT_OK - required by BaseRetriever's generic boundary.


class _ConfigSelector(Protocol):
    def __call__(self, cfg: DictConfig, key: str) -> _ConfigValue: ...


class PubMedSourceConfigurationError(ValueError):
    """Base class for static, redacted source.pubmed failures."""


class _StaticPubMedSourceConfigurationError(PubMedSourceConfigurationError):
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidPubMedQueryError(_StaticPubMedSourceConfigurationError):
    message: ClassVar[str] = "source.pubmed.query must be a nonblank string"


class InvalidPubMedContactEmailError(_StaticPubMedSourceConfigurationError):
    message: ClassVar[str] = (
        "source.pubmed.contact_email must be a nonblank string"
    )


class InvalidPubMedApiKeyError(_StaticPubMedSourceConfigurationError):
    message: ClassVar[str] = "source.pubmed.api_key must be a string or null"


class InvalidPubMedLookbackDaysError(_StaticPubMedSourceConfigurationError):
    message: ClassVar[str] = (
        "source.pubmed.lookback_days must be a positive integer"
    )


class InvalidPubMedRequestRateError(_StaticPubMedSourceConfigurationError):
    message: ClassVar[str] = (
        "source.pubmed.request_rate must be finite and positive"
    )


def _select_config(
    selector: _ConfigSelector,
    config: DictConfig,
    key: str,
    *,
    on_error: type[_StaticPubMedSourceConfigurationError] | None = None,
) -> _ConfigValue:
    try:
        return selector(config, key)
    except OmegaConfBaseException:
        if on_error is None:
            raise
        raise on_error() from None


@register_retriever("pubmed")
class PubMedRetriever(BaseRetriever):
    """Retrieve one validated PubMed History window in EFetch order."""

    _client: PubMedClient

    def __init__(self, config: DictConfig) -> None:
        query_value = _select_config(
            OmegaConf.select,
            config,
            "source.pubmed.query",
            on_error=InvalidPubMedQueryError,
        )
        if not isinstance(query_value, str) or not query_value.strip():
            raise InvalidPubMedQueryError from None

        contact_value = _select_config(
            OmegaConf.select,
            config,
            "source.pubmed.contact_email",
            on_error=InvalidPubMedContactEmailError,
        )
        if not isinstance(contact_value, str) or not contact_value.strip():
            raise InvalidPubMedContactEmailError from None

        api_key_value = _select_config(
            OmegaConf.select,
            config,
            "source.pubmed.api_key",
            on_error=InvalidPubMedApiKeyError,
        )
        if api_key_value is not None and not isinstance(api_key_value, str):
            raise InvalidPubMedApiKeyError from None
        api_key = (
            api_key_value.strip() or None
            if isinstance(api_key_value, str)
            else None
        )

        lookback_value = _select_config(
            OmegaConf.select,
            config,
            "source.pubmed.lookback_days",
            on_error=InvalidPubMedLookbackDaysError,
        )
        if (
            not isinstance(lookback_value, int)
            or isinstance(lookback_value, bool)
            or lookback_value < 1
        ):
            raise InvalidPubMedLookbackDaysError from None

        request_rate_value = _select_config(
            OmegaConf.select,
            config,
            "source.pubmed.request_rate",
            on_error=InvalidPubMedRequestRateError,
        )
        if (
            isinstance(request_rate_value, bool)
            or not isinstance(request_rate_value, (int, float))
            or not isfinite(request_rate_value)
            or request_rate_value <= 0
        ):
            raise InvalidPubMedRequestRateError from None

        super().__init__(config)
        self.query: str = query_value.strip()
        self.lookback_days: int = lookback_value
        self._client = PubMedClient(
            contact_value.strip(),
            api_key=api_key,
            request_rate=float(request_rate_value),
        )

    @property
    def client(self) -> PubMedClient:
        return self._client

    @override
    def _retrieve_raw_papers(self) -> list[PubMedRecord]:
        end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=self.lookback_days - 1)
        records = self._client.discover(self.query, start_date, end_date)
        debug = _select_config(OmegaConf.select, self.config, "executor.debug")
        return records[:10] if debug is True else records

    @override
    def convert_to_paper(self, raw_paper: _RawPubMedBoundary) -> Paper | None:
        if not isinstance(raw_paper, PubMedRecord):
            return None
        return Paper(
            source="pubmed",
            title=raw_paper.title,
            authors=list(raw_paper.authors),
            abstract=raw_paper.abstract,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{raw_paper.pmid}/",
            pdf_url=None,
            full_text=None,
            doi=raw_paper.doi,
            publisher=None,
            issns=raw_paper.issns,
            is_preprint=raw_paper.is_preprint,
            journal=raw_paper.journal,
        )


__all__: Final[tuple[str, ...]] = (
    "InvalidPubMedApiKeyError",
    "InvalidPubMedContactEmailError",
    "InvalidPubMedLookbackDaysError",
    "InvalidPubMedQueryError",
    "InvalidPubMedRequestRateError",
    "PubMedRetriever",
    "PubMedSourceConfigurationError",
)
