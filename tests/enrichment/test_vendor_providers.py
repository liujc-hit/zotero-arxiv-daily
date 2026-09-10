"""One-attempt contracts for IEEE, Elsevier, and Springer adapters."""

from collections.abc import Callable, Mapping
from typing import Final, Protocol
from urllib.parse import quote

import pytest
import requests
from loguru import logger

from zotero_arxiv_daily.enrichment.providers import (
    ElsevierAdapter,
    IEEEAdapter,
    SpringerAdapter,
)
from zotero_arxiv_daily.enrichment.settings import (
    ElsevierSettings,
    IeeeSettings,
    SpringerSettings,
)

from .http_fakes import Call, Response, install_get


PRIVATE_DOI: Final = "10.1016/S1234?private=1"
SECRET: Final = "provider-secret"
PRIVATE_BODY: Final = "private response body"


class _Adapter(Protocol):
    def fetch_abstract(self, doi: str) -> str | None: ...


type _AdapterFactory = Callable[[], _Adapter]


def test_ieee_uses_fixed_endpoint_and_first_matching_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an IEEE response whose first article has a different DOI
    doi = "10.1109/Target"
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(
                payload={
                    "articles": [
                        {"doi": "10.1109/other", "abstract": "Wrong"},
                        {
                            "doi": "https://doi.org/10.1109/TARGET",
                            "abstract": "<p>Wanted &amp; clean.</p>",
                        },
                    ]
                }
            ),
        ),
    )

    # When the configured adapter retrieves that DOI
    abstract = IEEEAdapter(IeeeSettings(enabled=True, api_key=SECRET)).fetch_abstract(doi)

    # Then one exact API request is made and the matching abstract is cleaned
    assert abstract == "Wanted & clean."
    assert calls == [
        Call(
            "https://ieeexploreapi.ieee.org/api/v1/search/articles",
            {"apikey": SECRET, "doi": doi},
            {
                "Accept": "application/json",
                "User-Agent": "zotero-arxiv-daily",
            },
            calls[0].timeout,
            False,
        )
    ]
    assert calls[0].timeout > 0


def test_elsevier_uses_encoded_doi_header_and_stops_after_zero_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the final successful Elsevier quota response
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(
                payload={
                    "abstracts-retrieval-response": {
                        "coredata": {"dc:description": " Elsevier abstract. "}
                    }
                },
                headers={"X-RateLimit-Remaining": "0"},
            ),
        ),
    )
    adapter = ElsevierAdapter(ElsevierSettings(enabled=True, api_key=SECRET))

    # When the quota-reaching DOI and a later DOI are requested
    first = adapter.fetch_abstract(PRIVATE_DOI)
    second = adapter.fetch_abstract("10.1016/later")

    # Then the current payload is used but the provider is disabled for the run
    assert (first, second) == ("Elsevier abstract.", None)
    assert len(calls) == 1
    assert calls[0].url == (
        "https://api.elsevier.com/content/abstract/doi/"
        f"{quote(PRIVATE_DOI, safe='')}"
    )
    assert calls[0].params == {}
    assert calls[0].headers == {
        "Accept": "application/json",
        "X-ELS-APIKey": SECRET,
    }
    assert calls[0].allow_redirects is False


def _meta_v2_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "doi": "10.1007/s11370-026-00752-z",
        "contentType": "Article",
        "publicationType": "Journal",
        "genre": ["OriginalPaper", "Original Research Paper "],
        "title": "Example Springer article title",
        "abstract": "<p>Springer abstract.</p>",
        "issn": "2470-9459",
        "eIssn": "2470-9467",
        "publisherName": "Springer Nature",
    }
    record.update(overrides)
    return record


def _meta_v2_payload(records: object) -> dict[str, object]:
    return {
        "apiMessage": None,
        "facets": [],
        "query": "doi:10.1007/s11370-026-00752-z",
        "records": records,
        "result": {"total": 1},
    }


def test_springer_returns_first_meta_v2_article_abstract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a meta v2 record whose bare DOI matches the queried DOI caselessly
    doi = "10.1007/s11370-026-00752-z"
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(
                payload=_meta_v2_payload([_meta_v2_record()])
            ),
        ),
    )

    # When the DOI is fetched from Springer Meta in a different case
    abstract = SpringerAdapter(
        SpringerSettings(enabled=True, api_key=SECRET)
    ).fetch_abstract(doi.upper())

    # Then the abstract is cleaned and the fixed meta v2 query shape is used
    assert abstract == "Springer abstract."
    assert calls[0].url == "https://api.springernature.com/meta/v2/json"
    assert calls[0].params == {
        "q": f"doi:{doi.upper()}",
        "api_key": SECRET,
        "p": 1,
        "s": 1,
    }
    assert calls[0].headers == {"Accept": "application/json"}


@pytest.mark.parametrize(
    "record_overrides",
    [
        {"contentType": "Chapter"},
        {"doi": "10.1007/different-doi"},
        {"doi": None},
        {"abstract": "   "},
        {"abstract": None},
    ],
)
def test_springer_rejects_non_article_or_mismatched_meta_v2_records(
    monkeypatch: pytest.MonkeyPatch,
    record_overrides: dict[str, object],
) -> None:
    # Given a meta v2 record failing the article or DOI gate
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(
                payload=_meta_v2_payload([_meta_v2_record(**record_overrides)])
            ),
        ),
    )

    # When the original DOI is fetched from Springer Meta
    abstract = SpringerAdapter(
        SpringerSettings(enabled=True, api_key=SECRET)
    ).fetch_abstract("10.1007/s11370-026-00752-z")

    # Then no abstract is returned for the gated record
    assert abstract is None
    assert len(calls) == 1


def test_springer_returns_none_when_meta_v2_has_no_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an empty meta v2 record list
    calls = install_get(
        monkeypatch,
        (lambda: Response(payload=_meta_v2_payload([])),),
    )

    # When the DOI is fetched from Springer Meta
    abstract = SpringerAdapter(
        SpringerSettings(enabled=True, api_key=SECRET)
    ).fetch_abstract("10.1007/s11370-026-00752-z")

    # Then no abstract is returned without further requests
    assert abstract is None
    assert len(calls) == 1


@pytest.mark.parametrize("status_code", [404, 429, 503])
def test_http_status_failure_has_exactly_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    # Given an IEEE HTTP failure that includes a private payload
    calls = install_get(
        monkeypatch,
        (lambda: Response(status_code=status_code, payload={"private": PRIVATE_BODY}),),
    )

    # When the adapter handles the response
    abstract = IEEEAdapter(IeeeSettings(enabled=True, api_key=SECRET)).fetch_abstract(
        "10.1109/private"
    )

    # Then the failure is skipped without a retry or body parse
    assert abstract is None
    assert len(calls) == 1


def test_invalid_json_has_exactly_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an Elsevier success status with invalid private JSON
    calls = install_get(
        monkeypatch,
        (lambda: Response(invalid_json_body=PRIVATE_BODY),),
    )

    # When JSON parsing fails
    abstract = ElsevierAdapter(
        ElsevierSettings(enabled=True, api_key=SECRET)
    ).fetch_abstract(PRIVATE_DOI)

    # Then no parse retry or alternate request occurs
    assert abstract is None
    assert len(calls) == 1


def test_transport_log_names_only_provider_and_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a transport exception containing every sensitive value
    detail = f"{SECRET} {PRIVATE_DOI} {PRIVATE_BODY}"

    def fail() -> Response:
        raise requests.ConnectionError(detail)

    calls = install_get(monkeypatch, (fail,))
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    try:
        # When the provider boundary handles the exception
        abstract = SpringerAdapter(
            SpringerSettings(enabled=True, api_key=SECRET)
        ).fetch_abstract(PRIVATE_DOI)
    finally:
        logger.remove(sink)

    # Then the one safe log cannot disclose request or exception material
    log_text = "".join(rendered)
    assert abstract is None
    assert len(calls) == 1
    assert "Springer transport failure" in log_text
    assert all(value not in log_text for value in (SECRET, PRIVATE_DOI, PRIVATE_BODY))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: IEEEAdapter(IeeeSettings(enabled=True)),
        lambda: ElsevierAdapter(ElsevierSettings(enabled=False, api_key=SECRET)),
        lambda: SpringerAdapter(SpringerSettings(enabled=True, api_key=" \t")),
    ],
)
def test_disabled_vendor_adapter_never_starts_http(
    monkeypatch: pytest.MonkeyPatch,
    factory: _AdapterFactory,
) -> None:
    # Given a vendor adapter disabled by its explicit gate or missing key
    def forbidden_get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        del url, params, headers, timeout, allow_redirects
        pytest.fail("disabled provider attempted HTTP")

    monkeypatch.setattr(requests, "get", forbidden_get)

    # When an abstract is requested directly
    abstract = factory().fetch_abstract(PRIVATE_DOI)

    # Then the adapter is a no-op
    assert abstract is None
