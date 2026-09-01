"""Focused contract tests for the fixed OpenAlex Sources operation."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, assert_never

import pytest
import requests

from zotero_arxiv_daily.retriever.openalex_client import (
    JsonValue,
    OpenAlexClient,
    OpenAlexClientError,
    OpenAlexClientTiming,
    OpenAlexSourcesHttpStatusError,
    OpenAlexSourcesInvalidJsonError,
    OpenAlexSourcesTransportError,
    OpenAlexUnsafeQueryParameterError,
)


_PRIMARY_KEY: Final = "sources-client-secret"
_SOURCES_URL: Final = "https://api.openalex.org/sources"


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    payload: JsonValue
    headers: Mapping[str, str]
    invalid_json: bool = False

    def json(self) -> JsonValue:
        if self.invalid_json:
            raise requests.JSONDecodeError("invalid", "", 0)
        return self.payload


@dataclass(frozen=True, slots=True)
class _Call:
    url: str
    params: Mapping[str, str | int]
    headers: Mapping[str, str]
    allow_redirects: bool


def test_sources_operation_uses_only_the_fixed_endpoint_and_existing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one configured identity and an exact ISSN filter.
    calls: list[_Call] = []
    payload: JsonValue = {"results": []}

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _Response:
        del timeout
        calls.append(_Call(url, dict(params), dict(headers), allow_redirects))
        return _Response(200, payload, {})

    monkeypatch.setattr(requests, "get", get)
    client = OpenAlexClient((_PRIMARY_KEY,))
    params = {"filter": "issn:0028-0836", "per_page": 100}

    # When the Sources operation is invoked.
    actual = client.get_sources_json(params)

    # Then one fixed-endpoint request reuses header identity and safe transport rules.
    assert actual == payload
    assert calls == [
        _Call(
            url=_SOURCES_URL,
            params=params,
            headers={
                **OpenAlexClient.request_headers,
                "Authorization": f"Bearer {_PRIMARY_KEY}",
            },
            allow_redirects=False,
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        pytest.param(
            requests.ConnectionError(f"offline {_PRIMARY_KEY}"),
            OpenAlexSourcesTransportError,
            id="transport",
        ),
        pytest.param(
            _Response(503, {"detail": _PRIMARY_KEY}, {}),
            OpenAlexSourcesHttpStatusError,
            id="status",
        ),
        pytest.param(
            _Response(200, None, {}, invalid_json=True),
            OpenAlexSourcesInvalidJsonError,
            id="invalid-json",
        ),
    ],
)
def test_sources_failures_are_one_attempt_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    outcome: _Response | requests.RequestException,
    error_type: type[OpenAlexClientError],
) -> None:
    # Given one source request whose transport, status, or JSON boundary fails.
    calls: list[_Call] = []
    sleeps: list[float] = []
    query_marker = "private-query-marker"

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _Response:
        del timeout
        calls.append(_Call(url, dict(params), dict(headers), allow_redirects))
        match outcome:
            case requests.RequestException():
                raise outcome
            case _Response():
                return outcome
            case unreachable:
                assert_never(unreachable)

    monkeypatch.setattr(requests, "get", get)
    client = OpenAlexClient(
        (_PRIMARY_KEY,),
        timing=OpenAlexClientTiming(lambda: 0.0, sleeps.append),
    )

    # When the fixed Sources operation fails.
    with pytest.raises(error_type) as caught:
        _ = client.get_sources_json(
            {"filter": f"issn:0028-0836|{query_marker}", "per_page": 100}
        )

    # Then no retry occurs and observable failure text contains no request data.
    assert len(calls) == 1
    assert sleeps == []
    observable = f"{caught.value!s}\n{caught.value!r}\n{client!r}"
    assert _PRIMARY_KEY not in observable
    assert query_marker not in observable


def test_sources_rate_limit_rotates_only_for_the_next_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a primary identity that is rate limited and a secondary identity.
    secondary_key = "sources-secondary-secret"
    calls: list[_Call] = []
    responses = iter(
        (
            _Response(429, None, {}),
            _Response(200, {"results": []}, {}),
        )
    )

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _Response:
        del timeout
        calls.append(_Call(url, dict(params), dict(headers), allow_redirects))
        return next(responses)

    monkeypatch.setattr(requests, "get", get)
    client = OpenAlexClient((_PRIMARY_KEY, secondary_key))

    # When one batch receives 429 and a later batch is requested.
    with pytest.raises(OpenAlexSourcesHttpStatusError):
        _ = client.get_sources_json({"filter": "issn:0028-0836"})
    payload = client.get_sources_json({"filter": "issn:2041-1723"})

    # Then the first batch is not retried and the next batch uses the next identity.
    assert payload == {"results": []}
    assert [call.headers["Authorization"] for call in calls] == [
        f"Bearer {_PRIMARY_KEY}",
        f"Bearer {secondary_key}",
    ]


def test_sources_rejects_query_credentials_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a Sources client and a forbidden credential query parameter.
    calls: list[str] = []

    def get(url: str, **_: JsonValue) -> _Response:
        calls.append(url)
        return _Response(200, None, {})

    monkeypatch.setattr(requests, "get", get)
    client = OpenAlexClient((_PRIMARY_KEY,))

    # When query-string credential transport is attempted.
    with pytest.raises(OpenAlexUnsafeQueryParameterError):
        _ = client.get_sources_json({"api_key": _PRIMARY_KEY})

    # Then no request leaves the process.
    assert calls == []
