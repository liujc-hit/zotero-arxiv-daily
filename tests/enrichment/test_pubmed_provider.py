"""DOI-scoped PubMed adapter contracts."""

from collections.abc import Mapping
from threading import Lock
from typing import Final, final

import pytest
import requests

from zotero_arxiv_daily.enrichment.pacing import ProviderTiming
from zotero_arxiv_daily.enrichment.providers import PubMedAdapter
from zotero_arxiv_daily.enrichment.settings import PubMedSettings

from .http_fakes import Response, install_get


CONTACT: Final = "curator@example.test"
SECRET: Final = "nih-secret"
DOI: Final = "10.1186/example"


@final
class _FakeTime:
    def __init__(self) -> None:
        self._lock = Lock()
        self._now = 0.0

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


def test_pubmed_searches_only_the_doi_then_fetches_that_pmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an exact DOI ESearch hit and a structured PubMed abstract
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(payload={"esearchresult": {"idlist": ["12345"]}}),
            lambda: Response(
                content=(
                    b"<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
                    b"<Abstract><AbstractText Label='BACKGROUND'>First <i>part</i>."
                    b"</AbstractText><AbstractText>Second section.</AbstractText></Abstract>"
                    b"</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
                )
            ),
        ),
    )
    adapter = PubMedAdapter(
        PubMedSettings(
            enabled=True,
            contact_email=CONTACT,
            api_key=SECRET,
        )
    )

    # When PubMed enrichment is explicitly requested for that DOI
    abstract = adapter.fetch_abstract(DOI)

    # Then only DOI-in-PubMed discovery and one PMID fetch occur
    assert abstract == "First part. Second section."
    assert len(calls) == 2
    search, fetch = calls
    assert search.url == (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    )
    assert search.params == {
        "db": "pubmed",
        "term": f"{DOI}[doi]",
        "retmode": "json",
        "retmax": 1,
        "tool": "zotero-arxiv-daily",
        "email": CONTACT,
        "api_key": SECRET,
    }
    assert search.headers == {"Accept": "application/json"}
    assert fetch.url == (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    )
    assert fetch.params == {
        "db": "pubmed",
        "id": "12345",
        "retmode": "xml",
        "tool": "zotero-arxiv-daily",
        "email": CONTACT,
        "api_key": SECRET,
    }
    assert fetch.headers == {"Accept": "application/xml"}
    assert all(call.allow_redirects is False and call.timeout > 0 for call in calls)


def test_anonymous_pubmed_omits_api_key_and_stops_on_no_doi_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an explicitly enabled anonymous PubMed adapter with no DOI match
    calls = install_get(
        monkeypatch,
        (lambda: Response(payload={"esearchresult": {"idlist": []}}),),
    )

    # When the DOI is searched
    abstract = PubMedAdapter(
        PubMedSettings(enabled=True, contact_email=CONTACT)
    ).fetch_abstract(DOI)

    # Then one scoped search occurs without an API key or EFetch
    assert abstract is None
    assert len(calls) == 1
    assert calls[0].params["term"] == f"{DOI}[doi]"
    assert "api_key" not in calls[0].params


@pytest.mark.parametrize("status_code", [404, 429, 503])
def test_pubmed_search_http_failure_has_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    # Given an ESearch HTTP failure
    calls = install_get(
        monkeypatch,
        (lambda: Response(status_code=status_code),),
    )

    # When PubMed handles the failed search
    abstract = PubMedAdapter(
        PubMedSettings(enabled=True, contact_email=CONTACT)
    ).fetch_abstract(DOI)

    # Then neither the search nor another database is retried
    assert abstract is None
    assert len(calls) == 1


def test_pubmed_invalid_xml_stops_after_two_total_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a valid DOI search followed by malformed XML
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(payload={"esearchresult": {"idlist": ["12345"]}}),
            lambda: Response(content=b"<PubmedArticleSet>private"),
        ),
    )

    # When EFetch parsing fails
    abstract = PubMedAdapter(
        PubMedSettings(enabled=True, contact_email=CONTACT)
    ).fetch_abstract(DOI)

    # Then the two allowed calls are not retried
    assert abstract is None
    assert len(calls) == 2


def test_pubmed_invalid_search_json_has_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an invalid ESearch JSON body
    calls = install_get(
        monkeypatch,
        (lambda: Response(invalid_json_body="private PubMed body"),),
    )

    # When JSON parsing fails
    abstract = PubMedAdapter(
        PubMedSettings(enabled=True, contact_email=CONTACT)
    ).fetch_abstract(DOI)

    # Then the body is not retried or passed to EFetch
    assert abstract is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("api_key", "interval"),
    [(None, 1 / 3), (SECRET, 0.1)],
)
def test_pubmed_enforces_anonymous_and_keyed_start_rates(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str | None,
    interval: float,
) -> None:
    # Given four no-hit searches and a deterministic injected clock
    fake_time = _FakeTime()
    starts: list[float] = []

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        del url, params, headers, timeout, allow_redirects
        starts.append(fake_time.monotonic())
        return Response(payload={"esearchresult": {"idlist": []}})

    monkeypatch.setattr(requests, "get", get)
    adapter = PubMedAdapter(
        PubMedSettings(
            enabled=True,
            contact_email=CONTACT,
            api_key=api_key,
            request_rate=100,
        ),
        timing=ProviderTiming(fake_time.monotonic, fake_time.sleep),
    )

    # When four DOI searches are started serially
    results = tuple(adapter.fetch_abstract(f"10.1186/{index}") for index in range(4))

    # Then starts are capped at three without a key and ten with a key
    assert results == (None, None, None, None)
    assert starts == [0.0, interval, 2 * interval, 3 * interval]


def test_pubmed_without_contact_never_starts_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an enabled setting lacking required NCBI contact identity
    def forbidden_get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        del url, params, headers, timeout, allow_redirects
        pytest.fail("disabled PubMed attempted HTTP")

    monkeypatch.setattr(requests, "get", forbidden_get)

    # When enrichment is requested directly
    abstract = PubMedAdapter(
        PubMedSettings(enabled=True, api_key=SECRET)
    ).fetch_abstract(DOI)

    # Then contact gating makes the adapter a no-op
    assert abstract is None
