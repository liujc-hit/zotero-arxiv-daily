"""Contract tests for the OpenAlex venue retriever."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from zotero_arxiv_daily.retriever import get_retriever_cls
import zotero_arxiv_daily.retriever.openalex_retriever as openalex_module
from zotero_arxiv_daily.retriever.openalex_retriever import OpenAlexRetriever
from zotero_arxiv_daily.retriever.openalex_venue_catalog import ALL_VENUES, CONFERENCE_VENUES, JOURNAL_VENUES, VenueSpec


FIXED_NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
PRIMARY_KEY = "test-primary-openalex-key"


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


def _response(results=(), next_cursor=None, status_code=200):
    payload = {"results": list(results), "meta": {"next_cursor": next_cursor}}
    response = SimpleNamespace(status_code=status_code, headers={})
    response.json = lambda: payload
    return response


def _work(work_id: str, doi: str | None = None, title: str | None = "Paper"):
    return {"id": work_id, "doi": doi, "title": title, "authorships": [],
            "primary_location": {}, "best_oa_location": None}


def _set_venues(monkeypatch, journals, conferences):
    monkeypatch.setattr(openalex_module, "JOURNAL_VENUES", tuple(journals), raising=False)
    monkeypatch.setattr(openalex_module, "CONFERENCE_VENUES", tuple(conferences), raising=False)
    monkeypatch.setattr(openalex_module, "ALL_VENUES", tuple(journals) + tuple(conferences), raising=False)


def _install_get(monkeypatch, responder):
    calls = []

    def get(url, **kwargs):
        params = dict(kwargs["params"])
        calls.append((url, params))
        return responder(params)

    monkeypatch.setattr(requests, "get", get)
    return calls


def _request_identifiers(params):
    prefixes = ("primary_location.source.id:", "primary_location.source.issn:")
    return {
        identifier
        for clause in params["filter"].split(",")
        for prefix in prefixes
        if clause.startswith(prefix)
        for identifier in clause.removeprefix(prefix).split("|")
    }


def _venue_identifiers(venues):
    return {identifier for venue in venues for identifier in venue.openalex_source_ids + venue.issns}


@pytest.fixture()
def fixed_now(monkeypatch):
    monkeypatch.setattr(openalex_module, "datetime", _FixedDatetime)


@pytest.fixture()
def single_venue(monkeypatch):
    _set_venues(monkeypatch, (VenueSpec("journal", "Journal", "journal", (), ("1111-1111",)),), ())


@pytest.fixture()
def openalex_config(config):
    config.source.openalex.api_keys = [PRIMARY_KEY]
    config.source.openalex.allow_anonymous = False
    return config


def test_openalex_is_registered():
    assert get_retriever_cls("openalex") is OpenAlexRetriever


def test_openalex_request_uses_per_page_equals_one_hundred_on_every_call(
    openalex_config, monkeypatch, single_venue
):
    calls = _install_get(monkeypatch, lambda _: _response())

    OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    assert calls, "expected OpenAlex retriever to issue at least one request"
    for _, params in calls:
        assert "per-page" not in params
        assert params["per_page"] == 100


def test_active_catalog_uses_exact_identifiers_in_separate_logical_batches(openalex_config, monkeypatch):
    calls = _install_get(monkeypatch, lambda _: _response())
    active_conferences = tuple(venue for venue in CONFERENCE_VENUES if isinstance(venue, VenueSpec))
    journal_ids = _venue_identifiers(JOURNAL_VENUES)
    conference_ids = _venue_identifiers(active_conferences)

    OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    batches = [_request_identifiers(params) for _, params in calls]
    filters = [params["filter"] for _, params in calls]
    assert set().union(*batches) == _venue_identifiers(ALL_VENUES)
    assert all(batch and not (batch & journal_ids and batch & conference_ids) for batch in batches)
    assert all("primary_location.source.type" not in expression for expression in filters)


def test_identifiers_are_batched_at_exactly_one_hundred(openalex_config, monkeypatch):
    venues = tuple(
        VenueSpec(f"v{i}", f"Venue {i}", "journal", (f"S{10_000 + i}",), ())
        for i in range(201)
    )
    _set_venues(monkeypatch, venues, ())
    calls = _install_get(monkeypatch, lambda _: _response())

    OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    assert [len(_request_identifiers(params)) for _, params in calls] == [100, 100, 1]


@pytest.mark.parametrize(
    ("lookback_days", "start", "end"),
    [(1, "2026-08-30", "2026-08-30"), (3, "2026-08-28", "2026-08-30")],
)
def test_date_window_contains_only_completed_utc_days(
    openalex_config, monkeypatch, single_venue, fixed_now, lookback_days, start, end
):
    calls = _install_get(monkeypatch, lambda _: _response())
    openalex_config.source.openalex.lookback_days = lookback_days

    OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    clauses = set(calls[0][1]["filter"].split(","))
    assert {f"from_publication_date:{start}", f"to_publication_date:{end}"} <= clauses


@pytest.mark.parametrize(
    ("pages", "expected_cursors"),
    [
        pytest.param(
            (_response([_work("https://openalex.org/W1")], "next"), _response([], "unused")),
            ["*", "next"],
            id="empty-results",
        ),
        pytest.param(
            (_response([_work("https://openalex.org/W1")], None),),
            ["*"],
            id="null-cursor",
        ),
        pytest.param(
            (
                _response([_work("https://openalex.org/W1")], "repeated"),
                _response([_work("https://openalex.org/W2")], "repeated"),
            ),
            ["*", "repeated"],
            id="repeated-cursor",
        ),
    ],
)
def test_cursor_starts_at_star_and_stops_safely(
    openalex_config, monkeypatch, single_venue, pages, expected_cursors
):
    responses = iter(pages)
    calls = _install_get(monkeypatch, lambda _: next(responses))

    OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    assert [params["cursor"] for _, params in calls] == expected_cursors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            _work("https://openalex.org/W1")
            | {"abstract_inverted_index": {"robot": [0, 3], "learns": [2], "fast": [5]}},
            "robot learns robot fast",
        ),
        (_work("https://openalex.org/W2") | {"abstract_inverted_index": {}}, ""),
        (_work("https://openalex.org/W3"), ""),
    ],
)
def test_abstract_is_reconstructed_by_global_position(openalex_config, raw, expected):
    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    assert paper is not None
    assert paper.abstract == expected


def test_convert_to_paper_maps_openalex_fields_and_prefers_best_links(openalex_config):
    raw = _work("https://openalex.org/W1",
                doi="https://doi.org/10.1234/MixedCase", title="A paper")
    raw |= {
        "authorships": [
            {"author": {"display_name": "Ada Lovelace"}},
            {"author": {"display_name": "Grace Hopper"}},
        ],
        "abstract_inverted_index": {"An": [0], "abstract": [1]},
        "primary_location": {
            "landing_page_url": "https://publisher.example/article",
            "pdf_url": "https://publisher.example/article.pdf",
        },
        "best_oa_location": {"pdf_url": "https://oa.example/best.pdf"},
    }

    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    assert paper is not None
    assert (paper.source, paper.title) == ("openalex", "A paper")
    assert paper.authors == ["Ada Lovelace", "Grace Hopper"]
    assert paper.abstract == "An abstract"
    assert paper.url == "https://doi.org/10.1234/MixedCase"
    assert paper.pdf_url == "https://oa.example/best.pdf"
    assert paper.full_text is None


@pytest.mark.parametrize(
    ("raw", "expected_url"),
    [
        (
            _work("https://openalex.org/W1")
            | {"primary_location": {"landing_page_url": "https://publisher.example/article"}},
            "https://publisher.example/article",
        ),
        (_work("https://openalex.org/W2"), "https://openalex.org/W2"),
    ],
)
def test_convert_to_paper_uses_landing_page_then_work_id(openalex_config, raw, expected_url):
    paper = OpenAlexRetriever(openalex_config).convert_to_paper(raw)

    assert paper is not None
    assert paper.url == expected_url


def test_convert_to_paper_skips_missing_title(openalex_config):
    assert OpenAlexRetriever(openalex_config).convert_to_paper(
        _work("https://openalex.org/W1", title=None)
    ) is None


def test_dedup_is_global_by_normalized_doi_then_work_id(openalex_config, monkeypatch):
    journal = VenueSpec("journal", "Journal", "journal", (), ("1111-1111",))
    conference = VenueSpec("conference", "Conference", "conference", ("S2222",), ())
    _set_venues(monkeypatch, (journal,), (conference,))
    doi = "https://doi.org/10.1234/Mixed"
    doi_less = "https://openalex.org/W3"

    def responder(params):
        identifiers = _request_identifiers(params)
        if "1111-1111" in identifiers and params["cursor"] == "*":
            return _response([_work("https://openalex.org/W1", doi), _work(doi_less)], "j2")
        if "1111-1111" in identifiers:
            return _response([_work("https://openalex.org/W2", doi.lower())])
        return _response([_work("https://openalex.org/W4", doi.upper()), _work(doi_less)])

    _install_get(monkeypatch, responder)

    raw = OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    assert len(raw) == 2
    assert sum(item.get("doi") is not None for item in raw) == 1
    assert {str(item["id"]) for item in raw if item.get("doi") is None} == {doi_less}


def test_debug_truncates_to_ten_after_dedup(openalex_config, monkeypatch, single_venue):
    repeated = [_work("https://openalex.org/W0") for _ in range(5)]
    unique = [_work(f"https://openalex.org/W{i}") for i in range(15)]
    _install_get(monkeypatch, lambda _: _response(repeated + unique))
    openalex_config.executor.debug = True

    raw = OpenAlexRetriever(openalex_config)._retrieve_raw_papers()

    assert [item["id"] for item in raw] == [
        f"https://openalex.org/W{i}" for i in range(10)
    ]
