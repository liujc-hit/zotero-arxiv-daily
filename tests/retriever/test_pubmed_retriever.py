"""Contracts for the registered PubMed discovery retriever."""

from datetime import date, datetime, timezone, tzinfo
from typing import Final, final, override

import pytest
from omegaconf import DictConfig, open_dict

import zotero_arxiv_daily.retriever.pubmed_retriever as pubmed_module
from zotero_arxiv_daily.retriever import get_retriever_cls
from zotero_arxiv_daily.retriever.pubmed_metadata import PubMedRecord
from zotero_arxiv_daily.retriever.pubmed_retriever import (
    InvalidPubMedApiKeyError,
    InvalidPubMedContactEmailError,
    InvalidPubMedLookbackDaysError,
    InvalidPubMedQueryError,
    InvalidPubMedRequestRateError,
    PubMedRetriever,
)


CONTACT: Final = "curator+pubmed@example.test"
API_KEY: Final = "private-nih-key"
QUERY: Final = "robotics[Title]"
FIXED_NOW: Final = datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @override
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


@pytest.fixture()
def pubmed_config(config: DictConfig) -> DictConfig:
    with open_dict(config.source):
        config.source.pubmed = {
            "query": QUERY,
            "contact_email": CONTACT,
            "api_key": API_KEY,
            "lookback_days": 3,
            "request_rate": 100.0,
        }
    return config


def test_explicit_module_import_registers_pubmed() -> None:
    assert get_retriever_cls("pubmed") is PubMedRetriever


def test_retriever_normalizes_config_and_requests_completed_utc_days(
    pubmed_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls: list[tuple[str, str | None, float]] = []
    discoveries: list[tuple[str, date, date]] = []

    @final
    class RecordingClient:
        def __init__(
            self,
            contact_email: str,
            api_key: str | None = None,
            request_rate: float = 10.0,
        ) -> None:
            constructor_calls.append((contact_email, api_key, request_rate))

        def discover(self, query: str, start_date: date, end_date: date) -> list[PubMedRecord]:
            discoveries.append((query, start_date, end_date))
            return []

    monkeypatch.setattr(pubmed_module, "PubMedClient", RecordingClient)
    monkeypatch.setattr(pubmed_module, "datetime", _FixedDatetime)
    pubmed_config.source.pubmed.query = f"  {QUERY}  "
    pubmed_config.source.pubmed.contact_email = f"  {CONTACT}  "
    pubmed_config.source.pubmed.api_key = f"  {API_KEY}  "

    records = PubMedRetriever(pubmed_config)._retrieve_raw_papers()

    assert records == []
    assert constructor_calls == [(CONTACT, API_KEY, 100.0)]
    assert discoveries == [(QUERY, date(2026, 8, 28), date(2026, 8, 30))]


def test_blank_optional_api_key_selects_anonymous_client(
    pubmed_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_keys: list[str | None] = []

    @final
    class RecordingClient:
        def __init__(
            self,
            contact_email: str,
            api_key: str | None = None,
            request_rate: float = 10.0,
        ) -> None:
            del contact_email, request_rate
            api_keys.append(api_key)

        def discover(self, query: str, start_date: date, end_date: date) -> list[PubMedRecord]:
            del query, start_date, end_date
            return []

    monkeypatch.setattr(pubmed_module, "PubMedClient", RecordingClient)
    pubmed_config.source.pubmed.api_key = " \t "

    _ = PubMedRetriever(pubmed_config)

    assert api_keys == [None]


@pytest.mark.parametrize(
    ("field", "value", "error_type", "message"),
    [
        ("query", None, InvalidPubMedQueryError, "source.pubmed.query must be a nonblank string"),
        ("query", "  ", InvalidPubMedQueryError, "source.pubmed.query must be a nonblank string"),
        (
            "contact_email",
            7,
            InvalidPubMedContactEmailError,
            "source.pubmed.contact_email must be a nonblank string",
        ),
        (
            "contact_email",
            " ",
            InvalidPubMedContactEmailError,
            "source.pubmed.contact_email must be a nonblank string",
        ),
        ("api_key", 7, InvalidPubMedApiKeyError, "source.pubmed.api_key must be a string or null"),
        (
            "lookback_days",
            True,
            InvalidPubMedLookbackDaysError,
            "source.pubmed.lookback_days must be a positive integer",
        ),
        (
            "lookback_days",
            0,
            InvalidPubMedLookbackDaysError,
            "source.pubmed.lookback_days must be a positive integer",
        ),
        (
            "request_rate",
            float("nan"),
            InvalidPubMedRequestRateError,
            "source.pubmed.request_rate must be finite and positive",
        ),
        (
            "request_rate",
            float("inf"),
            InvalidPubMedRequestRateError,
            "source.pubmed.request_rate must be finite and positive",
        ),
        (
            "request_rate",
            True,
            InvalidPubMedRequestRateError,
            "source.pubmed.request_rate must be finite and positive",
        ),
        (
            "request_rate",
            0,
            InvalidPubMedRequestRateError,
            "source.pubmed.request_rate must be finite and positive",
        ),
    ],
)
def test_source_configuration_is_strict_typed_static_and_redacted(
    pubmed_config: DictConfig,
    field: str,
    value: str | int | float | bool | None,
    error_type: type[ValueError],
    message: str,
) -> None:
    pubmed_config.source.pubmed[field] = value

    with pytest.raises(error_type) as caught:
        _ = PubMedRetriever(pubmed_config)

    observable = f"{caught.value!s}\n{caught.value!r}"
    assert str(caught.value) == message
    assert CONTACT not in observable
    assert API_KEY not in observable
    assert QUERY not in observable


def test_debug_truncates_only_after_client_returns_complete_records(
    pubmed_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed: list[bool] = []
    records = [
        PubMedRecord(
            pmid=str(index),
            doi=None,
            title=f"Paper {index}",
            authors=(),
            abstract="",
            journal=None,
            issns=(),
            is_preprint=None,
        )
        for index in range(15)
    ]

    @final
    class ValidatingClient:
        def __init__(
            self,
            contact_email: str,
            api_key: str | None = None,
            request_rate: float = 10.0,
        ) -> None:
            del contact_email, api_key, request_rate

        def discover(self, query: str, start_date: date, end_date: date) -> list[PubMedRecord]:
            del query, start_date, end_date
            completed.append(True)
            return records

    monkeypatch.setattr(pubmed_module, "PubMedClient", ValidatingClient)
    pubmed_config.executor.debug = True

    retrieved = PubMedRetriever(pubmed_config)._retrieve_raw_papers()

    assert completed == [True]
    assert [record.pmid for record in retrieved] == [str(index) for index in range(10)]


@pytest.mark.parametrize("is_preprint", [True, False, None])
def test_convert_to_paper_preserves_pubmed_fields_and_preprint_state(
    pubmed_config: DictConfig,
    is_preprint: bool | None,
) -> None:
    record = PubMedRecord(
        pmid="12345",
        doi="10.5555/mapped",
        title="A mapped paper",
        authors=("Ada Lovelace", "The Consortium"),
        abstract="Mapped abstract.",
        journal="Journal of Tests",
        issns=("0028-0836", "2434-561X"),
        is_preprint=is_preprint,
    )

    paper = PubMedRetriever(pubmed_config).convert_to_paper(record)

    assert paper is not None
    assert (paper.source, paper.title, paper.authors, paper.abstract) == (
        "pubmed",
        "A mapped paper",
        ["Ada Lovelace", "The Consortium"],
        "Mapped abstract.",
    )
    assert (paper.url, paper.pdf_url, paper.full_text) == (
        "https://pubmed.ncbi.nlm.nih.gov/12345/",
        None,
        None,
    )
    assert (paper.doi, paper.journal, paper.issns, paper.is_preprint) == (
        "10.5555/mapped",
        "Journal of Tests",
        ("0028-0836", "2434-561X"),
        is_preprint,
    )
