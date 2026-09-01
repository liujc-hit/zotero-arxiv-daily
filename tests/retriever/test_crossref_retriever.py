"""Contracts for the curated Crossref venue retriever."""

# noqa: SIZE_OK - the requested single-file suite enumerates the full source contract.

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from threading import Barrier, Event, Lock
from typing import Final, final, override

import pytest
from loguru import logger
from omegaconf import DictConfig, open_dict

from zotero_arxiv_daily.identifiers import normalize_issn
from zotero_arxiv_daily.retriever import get_retriever_cls
import zotero_arxiv_daily.retriever.crossref_retriever as crossref_module
from zotero_arxiv_daily.retriever.crossref_client import (
    CrossrefHttpStatusError,
    CrossrefInvalidJsonError,
    CrossrefOperation,
    CrossrefTransportError,
    JsonObject,
    JsonValue,
)
from zotero_arxiv_daily.retriever.crossref_retriever import (
    CURATED_ISSNS,
    CrossrefRetriever,
    InvalidCrossrefConfigurationError,
    InvalidCrossrefLookbackDaysError,
)
from zotero_arxiv_daily.retriever.openalex_venue_catalog import ALL_VENUES

CONTACT: Final = "curator+crossref@example.test"
FIXED_NOW: Final = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
TIMEOUT_SECONDS: Final = 5.0
SELECT_FIELDS: Final = {
    "DOI",
    "ISSN",
    "URL",
    "abstract",
    "author",
    "link",
    "publisher",
    "title",
    "type",
}


class _FixedDatetime(datetime):
    @override
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


@dataclass(frozen=True, slots=True)
class _Call:
    issn: str
    params: Mapping[str, str | int]


type _Responder = Callable[[str, Mapping[str, str | int]], JsonObject]


def _work(doi: str, title: str = "Paper") -> JsonObject:
    return {"DOI": doi, "title": [title]}


def _page(items: Sequence[JsonValue], next_cursor: str | None = None) -> JsonObject:
    message: JsonObject = {"items": list(items)}
    if next_cursor is not None:
        message["next-cursor"] = next_cursor
    return {"message": message}


def _install_client(
    monkeypatch: pytest.MonkeyPatch, responder: _Responder
) -> tuple[list[_Call], list[str]]:
    calls: list[_Call] = []
    contacts: list[str] = []
    lock = Lock()

    @final
    class RecordingClient:
        def __init__(self, mailto: str) -> None:
            contacts.append(mailto)

        def list_journal_works(
            self, issn: str, params: Mapping[str, str | int]
        ) -> JsonObject:
            with lock:
                calls.append(_Call(issn, dict(params)))
            return responder(issn, params)

    monkeypatch.setattr(crossref_module, "CrossrefClient", RecordingClient)
    return calls, contacts


@pytest.fixture()
def crossref_config(config: DictConfig) -> DictConfig:
    with open_dict(config.source):
        config.source.crossref = {"mailto": CONTACT, "lookback_days": 1}
    return config


@pytest.fixture()
def one_issn(monkeypatch: pytest.MonkeyPatch) -> str:
    issn = CURATED_ISSNS[0]
    monkeypatch.setattr(crossref_module, "CURATED_ISSNS", (issn,))
    return issn


def test_crossref_is_registered() -> None:
    # Given package import has loaded retriever plugins
    # When the Crossref plugin is resolved
    retriever_class = get_retriever_cls("crossref")
    # Then the curated implementation is registered
    assert retriever_class is CrossrefRetriever


def test_requests_exactly_the_valid_catalog_issns_once(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the ISSNs derived from active catalog entries
    expected = tuple(
        dict.fromkeys(
            normalized
            for venue in ALL_VENUES
            for issn in venue.issns
            if (normalized := normalize_issn(issn)) is not None
        )
    )
    calls, contacts = _install_client(monkeypatch, lambda _issn, _params: _page([]))
    # When every curated venue is retrieved
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then no OpenAlex-only or invalid identifier is requested
    assert papers == []
    assert CURATED_ISSNS == expected
    assert Counter(call.issn for call in calls) == Counter(expected)
    assert contacts == [CONTACT]


@pytest.mark.parametrize(
    ("lookback_days", "expected_filter"),
    [(1, "from-pub-date:2026-08-30,until-pub-date:2026-08-30"),
     (3, "from-pub-date:2026-08-28,until-pub-date:2026-08-30")],
)
def test_requests_completed_utc_publication_days_with_minimal_fields(
    crossref_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
    one_issn: str,
    lookback_days: int,
    expected_filter: str,
) -> None:
    # Given a fixed UTC clock and positive completed-day lookback
    del one_issn
    crossref_config.source.crossref.lookback_days = lookback_days
    monkeypatch.setattr(crossref_module, "datetime", _FixedDatetime)
    calls, _ = _install_client(monkeypatch, lambda _issn, _params: _page([]))
    # When retrieval starts its first cursor page
    CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then the request uses yesterday, rows=1000, and only conversion fields
    params = calls[0].params
    assert params["filter"] == expected_filter
    assert params["rows"] == 1000
    assert params["cursor"] == "*"
    assert set(str(params["select"]).split(",")) == SELECT_FIELDS


@pytest.mark.parametrize(
    ("pages", "expected_cursors"),
    [
        pytest.param((_page([], "unused"),), ["*"], id="empty-page"),
        pytest.param((_page([_work("10.1000/short")], "unused"),), ["*"], id="short-page"),
        pytest.param((_page([_work("10.1000/a"), _work("10.1000/b")]),), ["*"], id="missing-cursor"),
        pytest.param(
            (_page([_work("10.1000/a"), _work("10.1000/b")], "next"), _page([])),
            ["*", "next"],
            id="full-page",
        ),
        pytest.param(
            (
                _page([_work("10.1000/a"), _work("10.1000/b")], "repeat"),
                _page([_work("10.1000/c"), _work("10.1000/d")], "repeat"),
            ),
            ["*", "repeat"],
            id="repeated-cursor",
        ),
    ],
)
def test_cursor_pagination_stops_safely_and_continues_only_full_pages(
    crossref_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
    one_issn: str,
    pages: tuple[JsonObject, ...],
    expected_cursors: list[str],
) -> None:
    # Given scripted Crossref cursor pages
    del one_issn
    monkeypatch.setattr(CrossrefRetriever, "page_size", 2)
    responses = iter(pages)
    calls, _ = _install_client(monkeypatch, lambda _issn, _params: next(responses))
    # When the ISSN is paginated serially
    CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then empty/short/missing/repeated cursors terminate without another call
    assert [call.params["cursor"] for call in calls] == expected_cursors


def test_uses_at_most_three_workers_and_one_client(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given six ISSNs whose fake requests rendezvous in waves of three
    issns = CURATED_ISSNS[:6]
    monkeypatch.setattr(crossref_module, "CURATED_ISSNS", issns)
    barrier = Barrier(3)
    lock = Lock()
    contacts: list[str] = []
    active = 0
    max_active = 0

    class ProbeClient:
        def __init__(self, mailto: str) -> None:
            contacts.append(mailto)

        def list_journal_works(
            self, issn: str, params: Mapping[str, str | int]
        ) -> JsonObject:
            del issn, params
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                _ = barrier.wait(timeout=TIMEOUT_SECONDS)
                return _page([])
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(crossref_module, "CrossrefClient", ProbeClient)
    # When retrieval fans out across venues
    CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then worker concurrency is bounded and the client is shared
    assert max_active == 3
    assert contacts == [CONTACT]


def test_flattens_out_of_order_workers_in_catalog_order(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given three workers forced to finish in reverse catalog order
    issns = CURATED_ISSNS[:3]
    monkeypatch.setattr(crossref_module, "CURATED_ISSNS", issns)
    release_first, release_second = Event(), Event()
    completed: list[str] = []

    def respond(issn: str, _params: Mapping[str, str | int]) -> JsonObject:
        if issn == issns[0]:
            assert release_first.wait(TIMEOUT_SECONDS)
        elif issn == issns[1]:
            assert release_second.wait(TIMEOUT_SECONDS)
        completed.append(issn)
        if issn == issns[2]:
            release_second.set()
        elif issn == issns[1]:
            release_first.set()
        return _page([_work(f"10.1000/{issns.index(issn)}", issn)])

    _install_client(monkeypatch, respond)
    # When the results are flattened
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then completion timing cannot change catalog precedence
    assert completed == list(reversed(issns))
    assert [paper["title"] for paper in papers] == [[issn] for issn in issns]


def test_global_doi_dedup_preserves_the_first_catalog_item(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given equivalent DOI spellings from two curated ISSNs
    issns = CURATED_ISSNS[:2]
    monkeypatch.setattr(crossref_module, "CURATED_ISSNS", issns)

    def respond(issn: str, _params: Mapping[str, str | int]) -> JsonObject:
        doi = "https://doi.org/10.1234/Mixed" if issn == issns[0] else "10.1234/MIXED"
        return _page([_work(doi, f"from {issn}"), _work(f"10.1234/{issn}")])

    _install_client(monkeypatch, respond)
    # When global deduplication runs after deterministic flattening
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then the earliest normalized DOI wins
    assert [paper["title"] for paper in papers] == [
        [f"from {issns[0]}"], ["Paper"], ["Paper"]
    ]


def test_converts_jats_and_crossref_metadata(crossref_config: DictConfig) -> None:
    # Given a typed Crossref record containing JATS, entities, and publication metadata
    raw: JsonObject = {
        "DOI": "https://doi.org/10.1234/MixedCase",
        "title": ["<jats:i>A &amp; B</jats:i>"],
        "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "Robot Consortium"}],
        "abstract": "<jats:p>Use <jats:bold>robots</jats:bold> &amp; tools.</jats:p>",
        "publisher": " Example Publishing ",
        "ISSN": ["0028-0836", "2434561x", "invalid", "0028-0836"],
        "URL": "https://publisher.example/article",
        "link": [
            {"URL": "https://publisher.example/article.html", "content-type": "text/html"},
            {"URL": "https://publisher.example/article.pdf", "content-type": "application/pdf"},
        ],
        "type": "journal-article",
    }
    # When it is converted to the shared Paper model
    paper = CrossrefRetriever(crossref_config).convert_to_paper(raw)
    # Then text and identifiers are normalized without fetching full text
    assert paper is not None
    assert (paper.source, paper.title, paper.abstract) == ("crossref", "A & B", "Use robots & tools.")
    assert paper.authors == ["Ada Lovelace", "Robot Consortium"]
    assert (paper.url, paper.pdf_url, paper.full_text) == (
        "https://publisher.example/article", "https://publisher.example/article.pdf", None
    )
    assert (paper.doi, paper.publisher, paper.issns, paper.is_preprint) == (
        "10.1234/mixedcase", "Example Publishing", ("0028-0836", "2434-561X"), False
    )


@pytest.mark.parametrize(
    ("work_type", "expected"),
    [("posted-content", True), ("preprint", True), ("proceedings-article", False),
     ("journal-article", False), ("book-chapter", None), (None, None)],
)
def test_maps_only_known_crossref_types_to_preprint_status(
    crossref_config: DictConfig, work_type: str | None, expected: bool | None
) -> None:
    # Given a Crossref work type
    raw = _work("10.1234/type")
    raw["type"] = work_type
    # When publication status is converted
    paper = CrossrefRetriever(crossref_config).convert_to_paper(raw)
    # Then only explicit preprint/article variants receive booleans
    assert paper is not None
    assert paper.is_preprint is expected


@pytest.mark.parametrize(
    "raw",
    [
        {"DOI": "not-a-doi", "title": ["Title"]},
        {"title": ["Title"]},
        {"DOI": "10.1234/no-title", "title": []},
        {"DOI": "10.1234/blank", "title": [" <jats:p> </jats:p> "]},
    ],
)
def test_skips_records_without_a_valid_doi_or_title(
    crossref_config: DictConfig, raw: JsonObject
) -> None:
    # Given incomplete Crossref metadata
    # When conversion validates candidate identity
    paper = CrossrefRetriever(crossref_config).convert_to_paper(raw)
    # Then unusable candidates are skipped
    assert paper is None


def test_retrieval_filters_invalid_identity_before_dedup(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch, one_issn: str
) -> None:
    # Given records with a missing DOI, missing title, and one usable identity
    del one_issn
    valid = _work("10.1234/valid", "Usable")
    _install_client(
        monkeypatch,
        lambda _issn, _params: _page(
            [{"title": ["Missing DOI"]}, {"DOI": "10.1234/missing-title"}, valid]
        ),
    )
    # When raw candidates are normalized before global deduplication
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then only the valid DOI-and-title record remains
    assert papers == [valid]


def test_429_stops_queued_issns_without_retry(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given more ISSNs than workers and immediate Crossref exhaustion
    issns = CURATED_ISSNS[:8]
    monkeypatch.setattr(crossref_module, "CURATED_ISSNS", issns)
    calls, _ = _install_client(
        monkeypatch,
        lambda _issn, _params: (_ for _ in ()).throw(
            CrossrefHttpStatusError(CrossrefOperation.LIST_JOURNAL_WORKS, 429)
        ),
    )
    # When a worker observes HTTP 429
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then no retry or queued ISSN request begins after the shared stop
    assert papers == []
    assert 1 <= len(calls) <= 3
    assert set(call.issn for call in calls) <= set(issns[:3])
    assert all(call.params["cursor"] == "*" for call in calls)


@pytest.mark.parametrize(
    "failure",
    [
        CrossrefTransportError(CrossrefOperation.LIST_JOURNAL_WORKS),
        CrossrefHttpStatusError(CrossrefOperation.LIST_JOURNAL_WORKS, 503),
        CrossrefInvalidJsonError(CrossrefOperation.LIST_JOURNAL_WORKS),
    ],
)
def test_other_typed_failures_keep_prior_pages_without_retry(
    crossref_config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
    one_issn: str,
    failure: RuntimeError,
) -> None:
    # Given one full page followed by a typed no-retry client failure
    del one_issn
    monkeypatch.setattr(CrossrefRetriever, "page_size", 2)
    calls, _ = _install_client(
        monkeypatch,
        lambda _issn, params: _page(
            [_work("10.1000/a"), _work("10.1000/b")], "next"
        ) if params["cursor"] == "*" else (_ for _ in ()).throw(failure),
    )
    # When pagination reaches the failed page
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then prior records survive and the failed page has one attempt
    assert [paper["DOI"] for paper in papers] == ["10.1000/a", "10.1000/b"]
    assert [call.params["cursor"] for call in calls] == ["*", "next"]


def test_debug_truncates_to_ten_after_dedup(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch, one_issn: str
) -> None:
    # Given duplicate records before fifteen normalized DOI candidates
    del one_issn
    repeated = [_work("10.1000/0") for _ in range(5)]
    unique = [_work(f"10.1000/{index}") for index in range(15)]
    _install_client(monkeypatch, lambda _issn, _params: _page(repeated + unique))
    crossref_config.executor.debug = True
    # When debug retrieval deduplicates candidates
    papers = CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    # Then truncation applies to the deduplicated order
    assert [paper["DOI"] for paper in papers] == [f"10.1000/{index}" for index in range(10)]


def test_failure_logs_are_static_and_redacted(
    crossref_config: DictConfig, monkeypatch: pytest.MonkeyPatch, one_issn: str
) -> None:
    # Given a typed failure whose rendering contains private values
    private = f"{CONTACT} {one_issn} https://private.example 10.5555/private response-body"

    class SensitiveTransportError(CrossrefTransportError):
        @override
        def __str__(self) -> str:
            return private

    _install_client(
        monkeypatch,
        lambda _issn, _params: (_ for _ in ()).throw(
            SensitiveTransportError(CrossrefOperation.LIST_JOURNAL_WORKS)
        ),
    )
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    try:
        # When the failure is handled at the provider boundary
        CrossrefRetriever(crossref_config)._retrieve_raw_papers()
    finally:
        logger.remove(sink)
    # Then logs identify only the safe operation and failure category
    log_text = "".join(rendered)
    assert "Crossref list_journal_works" in log_text
    assert private not in log_text
    assert all(value not in log_text for value in (CONTACT, one_issn, "private.example", "response-body"))


@pytest.mark.parametrize("mailto", [None, "", " \t", 7])
def test_contact_configuration_errors_are_typed_static_and_redacted(
    crossref_config: DictConfig, mailto: JsonValue
) -> None:
    # Given a malformed or blank contact boundary
    crossref_config.source.crossref.mailto = mailto
    # When retriever construction parses it
    with pytest.raises(InvalidCrossrefConfigurationError) as caught:
        CrossrefRetriever(crossref_config)
    # Then the error retains no supplied value
    assert str(caught.value) == "source.crossref.mailto must be a nonblank string"
    assert CONTACT not in f"{caught.value!s}\n{caught.value!r}"


@pytest.mark.parametrize("lookback_days", [None, 0, -1, True, "1"])
def test_lookback_configuration_errors_are_typed_and_static(
    crossref_config: DictConfig, lookback_days: JsonValue
) -> None:
    # Given a non-positive or non-integer completed-day window
    crossref_config.source.crossref.lookback_days = lookback_days
    # When retriever construction parses it
    with pytest.raises(InvalidCrossrefLookbackDaysError) as caught:
        CrossrefRetriever(crossref_config)
    # Then a stable typed message is returned
    assert str(caught.value) == "source.crossref.lookback_days must be a positive integer"
