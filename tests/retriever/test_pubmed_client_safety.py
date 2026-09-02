"""Failure, redaction, configuration, and pacing contracts for PubMed."""

from collections.abc import Mapping
from datetime import date
from threading import Lock
from typing import Final, final

import pytest
import requests
from loguru import logger

from tests.enrichment.http_fakes import Response, install_get
from zotero_arxiv_daily.enrichment.pacing import ProviderTiming
from zotero_arxiv_daily.enrichment.transport import JsonObject
from zotero_arxiv_daily.retriever.pubmed_client import (
    PubMedClient,
    PubMedClientConfigurationError,
    PubMedOperation,
    PubMedRequestError,
)


CONTACT: Final = "curator+pubmed@example.test"
API_KEY: Final = "private-nih-key"
QUERY: Final = "(private therapy[Title]) AND 2026[Date - Publication]"
WEBENV: Final = "private-webenv"
QUERY_KEY: Final = "private-query-key"


def _search(count: str) -> JsonObject:
    result: JsonObject = {
        "count": count,
        "webenv": WEBENV,
        "querykey": QUERY_KEY,
    }
    return {"esearchresult": result}


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


def test_search_transport_failure_has_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def fail(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        del url, params, headers, timeout, allow_redirects
        nonlocal attempts
        attempts += 1
        raise requests.ConnectionError("private response body")

    monkeypatch.setattr(requests, "get", fail)

    with pytest.raises(PubMedRequestError) as caught:
        _ = PubMedClient(CONTACT, request_rate=3).discover(
            QUERY, date(2026, 8, 30), date(2026, 8, 30)
        )

    assert attempts == 1
    assert caught.value.operation is PubMedOperation.ESEARCH


def test_fetch_failure_is_one_attempt_and_all_observables_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_pmid = "987654321"
    private_doi = "10.5555/private"
    private_body = f"{private_pmid} {private_doi} private response body"
    calls = install_get(
        monkeypatch,
        (
            lambda: Response(payload=_search("1")),
            lambda: Response(status_code=503, content=private_body.encode()),
        ),
    )
    rendered: list[str] = []
    sink = logger.add(rendered.append, format="{message}")
    client = PubMedClient(CONTACT, api_key=API_KEY, request_rate=10)
    try:
        with pytest.raises(PubMedRequestError) as caught:
            _ = client.discover(QUERY, date(2026, 8, 30), date(2026, 8, 30))
    finally:
        logger.remove(sink)

    observable = f"{caught.value!s}\n{caught.value!r}\n{client!r}\n{''.join(rendered)}"
    assert len(calls) == 2
    assert caught.value.operation is PubMedOperation.EFETCH
    assert all(
        value not in observable
        for value in (
            CONTACT,
            API_KEY,
            QUERY,
            WEBENV,
            QUERY_KEY,
            private_pmid,
            private_doi,
            private_body,
        )
    )


@pytest.mark.parametrize(
    ("api_key", "configured_rate", "interval"),
    [
        (None, 100.0, 1 / 3),
        (API_KEY, 100.0, 0.1),
        (None, 2.0, 0.5),
        (API_KEY, 4.0, 0.25),
    ],
)
def test_one_pacer_enforces_configured_and_ncbi_start_rates(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str | None,
    configured_rate: float,
    interval: float,
) -> None:
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
        return Response(payload=_search("0"))

    monkeypatch.setattr(requests, "get", get)
    client = PubMedClient(
        CONTACT,
        api_key=api_key,
        request_rate=configured_rate,
        timing=ProviderTiming(fake_time.monotonic, fake_time.sleep),
    )

    for index in range(4):
        assert client.discover(
            f"query-{index}", date(2026, 8, 30), date(2026, 8, 30)
        ) == []

    assert starts == [0.0, interval, 2 * interval, 3 * interval]


@pytest.mark.parametrize(
    ("contact", "api_key", "request_rate"),
    [
        (" ", None, 3.0),
        (CONTACT, 7, 3.0),
        (CONTACT, None, 0.0),
        (CONTACT, None, -1.0),
        (CONTACT, None, float("nan")),
        (CONTACT, None, float("inf")),
        (CONTACT, None, True),
    ],
)
def test_client_configuration_errors_are_typed_static_and_redacted(
    contact: str,
    api_key: str | int | None,
    request_rate: float | bool,
) -> None:
    with pytest.raises(PubMedClientConfigurationError) as caught:
        _ = PubMedClient(contact, api_key=api_key, request_rate=request_rate)

    assert str(caught.value) == "PubMed client configuration is invalid"
    assert CONTACT not in f"{caught.value!s}\n{caught.value!r}"
