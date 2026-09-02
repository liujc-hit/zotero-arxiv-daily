"""Focused contracts for completed-day PubMed History discovery."""

from datetime import date
from typing import Final

import pytest

from tests.enrichment.http_fakes import Response, install_get
from zotero_arxiv_daily.enrichment.transport import JsonObject, JsonValue
from zotero_arxiv_daily.retriever.pubmed_client import (
    PubMedClient,
    PubMedCountMismatchError,
    PubMedDuplicatePmidError,
    PubMedInvalidSearchResponseError,
    PubMedResultLimitError,
)
from zotero_arxiv_daily.retriever.pubmed_metadata import PubMedRecord


CONTACT: Final = "curator+pubmed@example.test"
API_KEY: Final = "private-nih-key"
QUERY: Final = "(private therapy[Title]) AND 2026[Date - Publication]"
WEBENV: Final = "private-webenv"
QUERY_KEY: Final = "private-query-key"
ESEARCH_URL: Final = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL: Final = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _search(
    count: JsonValue,
    *,
    webenv: JsonValue = WEBENV,
    query_key: JsonValue = QUERY_KEY,
) -> JsonObject:
    result: JsonObject = {
        "count": count,
        "webenv": webenv,
        "querykey": query_key,
    }
    return {"esearchresult": result}


def _article(pmid: str, *, title: str | None = None) -> str:
    return (
        "<PubmedArticle><MedlineCitation>"
        f"<PMID>{pmid}</PMID><Article><ArticleTitle>{title or f'Paper {pmid}'}</ArticleTitle>"
        "</Article></MedlineCitation></PubmedArticle>"
    )


def _xml(*pmids: str) -> bytes:
    return f"<PubmedArticleSet>{''.join(_article(pmid) for pmid in pmids)}</PubmedArticleSet>".encode()


def test_history_searches_once_then_fetches_ordered_pages_of_two_hundred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one History search describing 201 records in two ordered XML pages.
    first_page = tuple(str(index) for index in range(1, 201))
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(payload=_search("201")),
            lambda: Response(content=_xml(*first_page)),
            lambda: Response(content=_xml("201")),
        ),
    )

    # When: completed publication days are discovered through one client.
    records = PubMedClient(CONTACT, api_key=API_KEY, request_rate=100).discover(
        QUERY,
        date(2026, 8, 28),
        date(2026, 8, 30),
    )

    # Then: ESearch is not paginated and EFetch uses only same-run history pages.
    assert [record.pmid for record in records] == [*first_page, "201"]
    assert [call.url for call in calls].count(ESEARCH_URL) == 1
    assert calls[0].params == {
        "db": "pubmed",
        "term": QUERY,
        "datetype": "edat",
        "mindate": "2026/08/28",
        "maxdate": "2026/08/30",
        "sort": "pub_date",
        "retmode": "json",
        "retmax": 0,
        "usehistory": "y",
        "tool": "zotero-arxiv-daily",
        "email": CONTACT,
        "api_key": API_KEY,
    }
    assert calls[0].headers == {"Accept": "application/json"}
    assert [call.params for call in calls[1:]] == [
        {
            "db": "pubmed",
            "WebEnv": WEBENV,
            "query_key": QUERY_KEY,
            "retstart": 0,
            "retmax": 200,
            "retmode": "xml",
            "tool": "zotero-arxiv-daily",
            "email": CONTACT,
            "api_key": API_KEY,
        },
        {
            "db": "pubmed",
            "WebEnv": WEBENV,
            "query_key": QUERY_KEY,
            "retstart": 200,
            "retmax": 200,
            "retmode": "xml",
            "tool": "zotero-arxiv-daily",
            "email": CONTACT,
            "api_key": API_KEY,
        },
    ]
    assert all(call.url == EFETCH_URL for call in calls[1:])
    assert all(call.headers == {"Accept": "application/xml"} for call in calls[1:])
    assert all("id" not in call.params for call in calls[1:])
    assert all(call.allow_redirects is False and call.timeout > 0 for call in calls)


def test_empty_history_result_stops_after_one_anonymous_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_get(monkeypatch, (lambda: Response(payload=_search("0")),))

    records = PubMedClient(CONTACT, request_rate=3).discover(
        QUERY, date(2026, 8, 30), date(2026, 8, 30)
    )

    assert records == []
    assert len(calls) == 1
    assert "api_key" not in calls[0].params


def test_mixed_history_maps_articles_and_books_without_extra_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: ESearch counts a mixed page containing article, book, and article.
    payload = (
        "<PubmedArticleSet>"
        f"{_article('1')}"
        "<PubmedBookArticle><BookDocument><PMID>2</PMID>"
        "<Book><BookTitle>Book Two</BookTitle></Book>"
        "<ArticleTitle>Chapter Two</ArticleTitle>"
        "</BookDocument></PubmedBookArticle>"
        f"{_article('3')}"
        "</PubmedArticleSet>"
    ).encode()
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(payload=_search("3")),
            lambda: Response(content=payload),
        ),
    )

    # When: the complete History result is discovered.
    records = PubMedClient(CONTACT, request_rate=10).discover(
        QUERY, date(2026, 8, 30), date(2026, 8, 30)
    )

    # Then: the declared count matches in order with one search and one fetch.
    assert [record.pmid for record in records] == ["1", "2", "3"]
    assert [call.url for call in calls] == [ESEARCH_URL, EFETCH_URL]


def test_more_than_ten_thousand_hits_fail_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_get(monkeypatch, (lambda: Response(payload=_search("10001")),))

    with pytest.raises(PubMedResultLimitError):
        _ = PubMedClient(CONTACT, request_rate=3).discover(
            QUERY, date(2026, 8, 30), date(2026, 8, 30)
        )

    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"esearchresult": []},
        _search(-1),
        _search(" 1"),
        _search("1", webenv=" "),
        _search("1", query_key=1),
    ],
)
def test_invalid_history_json_is_rejected_with_one_static_error(
    monkeypatch: pytest.MonkeyPatch,
    payload: JsonObject,
) -> None:
    calls = install_get(monkeypatch, (lambda: Response(payload=payload),))

    with pytest.raises(PubMedInvalidSearchResponseError) as caught:
        _ = PubMedClient(CONTACT, request_rate=3).discover(
            QUERY, date(2026, 8, 30), date(2026, 8, 30)
        )

    assert len(calls) == 1
    assert str(caught.value) == "PubMed ESearch returned an invalid history response"


@pytest.mark.parametrize(
    ("xml", "error_type"),
    [
        (_xml("1"), PubMedCountMismatchError),
        (_xml("1", "1"), PubMedDuplicatePmidError),
    ],
)
def test_complete_history_must_match_count_and_have_unique_pmids(
    monkeypatch: pytest.MonkeyPatch,
    xml: bytes,
    error_type: type[PubMedCountMismatchError | PubMedDuplicatePmidError],
) -> None:
    calls = install_get(
        monkeypatch,
        (lambda: Response(payload=_search("2")), lambda: Response(content=xml)),
    )

    with pytest.raises(error_type):
        _ = PubMedClient(CONTACT, request_rate=10).discover(
            QUERY, date(2026, 8, 30), date(2026, 8, 30)
        )

    assert len(calls) == 2


def test_xml_pages_are_mapped_through_pubmed_metadata() -> None:
    payload = b"""
        <PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
          <Journal><ISSN>00280836</ISSN><Title>Journal of Tests</Title></Journal>
          <ArticleTitle>A mapped paper</ArticleTitle>
          <Abstract><AbstractText>Mapped abstract.</AbstractText></Abstract>
          <AuthorList><Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author></AuthorList>
        </Article><MedlineJournalInfo><ISSNLinking>2434561x</ISSNLinking></MedlineJournalInfo>
        </MedlineCitation><PubmedData><ArticleIdList>
          <ArticleId IdType="doi">https://doi.org/10.5555/Mapped</ArticleId>
        </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        _ = install_get(
            monkeypatch,
            (lambda: Response(payload=_search("1")), lambda: Response(content=payload)),
        )

        records = PubMedClient(CONTACT, request_rate=10).discover(
            QUERY, date(2026, 8, 30), date(2026, 8, 30)
        )
    finally:
        monkeypatch.undo()

    assert records == [
        PubMedRecord(
            pmid="12345",
            doi="10.5555/mapped",
            title="A mapped paper",
            authors=("Ada Lovelace",),
            abstract="Mapped abstract.",
            journal="Journal of Tests",
            issns=("0028-0836", "2434-561X"),
            is_preprint=None,
        )
    ]
