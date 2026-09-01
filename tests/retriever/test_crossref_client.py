"""Focused security and rate-limit contracts for the Crossref HTTP client."""

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Event, Lock
from typing import Final, final
from urllib.parse import quote

import pytest
import requests

from zotero_arxiv_daily.retriever.crossref_client import (
    CrossrefClient,
    CrossrefClientTiming,
    CrossrefContactConfigurationError,
    CrossrefHttpStatusError,
    CrossrefInvalidJsonError,
    CrossrefOperation,
    CrossrefTransportError,
    JsonObject,
    JsonValue,
)

CONTACT_MAILTO: Final = "curator+crossref@example.test"
PRIVATE_DOI: Final = "10.5555/private?redirect=https://attacker.example"
PRIVATE_ISSN: Final = "1234-5678?mailto=attacker@example.test"
DANGEROUS_BODY: Final = "private response body with curator+crossref@example.test"
CONCURRENCY_TIMEOUT_SECONDS: Final = 5.0


@final
class _Response:
    """Mutable response fake that counts boundary JSON parses."""

    __slots__ = ("_invalid_body", "_payload", "json_calls", "status_code")

    def __init__(
        self,
        status_code: int = 200,
        payload: JsonValue = None,
        invalid_body: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._invalid_body = invalid_body
        self.json_calls = 0

    def json(self) -> JsonValue:
        self.json_calls += 1
        if self._invalid_body is not None:
            raise requests.JSONDecodeError("invalid Crossref JSON", self._invalid_body, 0)
        return self._payload


type _Outcome = Callable[[], _Response]


@dataclass(frozen=True, slots=True)
class _Call:
    url: str
    params: Mapping[str, str | int]
    headers: Mapping[str, str]
    timeout: float
    allow_redirects: bool


@final
class _FakeTime:
    def __init__(self) -> None:
        self._now = 0.0
        self._sleeps: list[float] = []
        self._lock = Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._sleeps.append(seconds)
            self._now += seconds

    @property
    def sleeps(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._sleeps)


def _timing(fake_time: _FakeTime) -> CrossrefClientTiming:
    return CrossrefClientTiming(fake_time.monotonic, fake_time.sleep)


def _install_get(
    monkeypatch: pytest.MonkeyPatch, outcomes: Iterable[_Outcome]
) -> list[_Call]:
    outcome_iterator = iter(outcomes)
    calls: list[_Call] = []

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _Response:
        calls.append(_Call(url, dict(params), dict(headers), timeout, allow_redirects))
        return next(outcome_iterator)()

    monkeypatch.setattr(requests, "get", get)
    return calls


@final
class _ConcurrencyProbe:
    """Block HTTP calls while recording the client's maximum in-flight count."""

    def __init__(self) -> None:
        self.first_wave_started = Event()
        self.release = Event()
        self._lock = Lock()
        self._active = 0
        self.call_count = 0
        self.max_active = 0

    def __call__(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> _Response:
        del url, params, headers, timeout, allow_redirects
        with self._lock:
            self._active += 1
            self.call_count += 1
            self.max_active = max(self.max_active, self._active)
            if self._active == 3:
                self.first_wave_started.set()
        try:
            assert self.release.wait(CONCURRENCY_TIMEOUT_SECONDS)
            return _Response(payload={"message": "ok"})
        finally:
            with self._lock:
                self._active -= 1


@pytest.mark.parametrize("mailto", ["", " ", "\t\n"])
def test_constructor_requires_a_nonblank_contact_mailto(mailto: str) -> None:
    with pytest.raises(CrossrefContactConfigurationError):
        _ = CrossrefClient(mailto)


def test_client_repr_exposes_no_contact_value() -> None:
    client = CrossrefClient(CONTACT_MAILTO)

    rendered = repr(client)

    assert rendered == "CrossrefClient()"
    assert CONTACT_MAILTO not in rendered


def test_get_work_uses_only_the_fixed_encoded_endpoint_and_polite_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(payload={"message": {"DOI": PRIVATE_DOI}})
    calls = _install_get(monkeypatch, (lambda: response,))
    client = CrossrefClient(CONTACT_MAILTO)

    payload = client.get_work(PRIVATE_DOI)

    call = calls[0]
    assert (payload, response.json_calls) == ({"message": {"DOI": PRIVATE_DOI}}, 1)
    assert call.url == f"https://api.crossref.org/works/{quote(PRIVATE_DOI, safe='')}"
    assert call.params == {"mailto": CONTACT_MAILTO}
    assert call.headers["Accept"] == "application/json"
    assert "zotero-arxiv-daily" in call.headers["User-Agent"]
    assert CONTACT_MAILTO in call.headers["User-Agent"]
    assert call.timeout > 0
    assert call.allow_redirects is False


def test_list_journal_works_uses_only_the_fixed_endpoint_and_forces_mailto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(payload={"message": {"items": []}})
    calls = _install_get(monkeypatch, (lambda: response,))
    params: dict[str, str | int] = {"rows": 25, "mailto": "ignored@example.test"}
    client = CrossrefClient(CONTACT_MAILTO)

    payload = client.list_journal_works(PRIVATE_ISSN, params)

    assert payload == {"message": {"items": []}}
    assert calls[0].url == f"https://api.crossref.org/journals/{quote(PRIVATE_ISSN, safe='')}/works"
    assert calls[0].params == {"rows": 25, "mailto": CONTACT_MAILTO}
    assert params == {"rows": 25, "mailto": "ignored@example.test"}


def test_transport_failure_has_one_attempt_and_static_redacted_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    detail = f"{CONTACT_MAILTO} {PRIVATE_DOI} {PRIVATE_ISSN} {DANGEROUS_BODY}"

    def fail() -> _Response:
        raise requests.ConnectionError(detail)

    calls = _install_get(monkeypatch, (fail,))
    client = CrossrefClient(CONTACT_MAILTO)

    with pytest.raises(CrossrefTransportError) as caught:
        _ = client.get_work(PRIVATE_DOI)

    assert len(calls) == 1
    assert caught.value.operation is CrossrefOperation.GET_WORK
    observable = f"{caught.value!s}\n{caught.value!r}\n{client!r}\n{caplog.text}"
    assert all(value not in observable for value in (CONTACT_MAILTO, PRIVATE_DOI, PRIVATE_ISSN, DANGEROUS_BODY))


@pytest.mark.parametrize("status_code", [404, 429, 503])
def test_http_failures_have_one_attempt_and_retain_only_safe_fields(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    calls = _install_get(monkeypatch, (lambda: _Response(status_code, {"message": DANGEROUS_BODY}),))
    client = CrossrefClient(CONTACT_MAILTO)

    with pytest.raises(CrossrefHttpStatusError) as caught:
        _ = client.get_work(PRIVATE_DOI)

    assert len(calls) == 1
    assert caught.value.status_code == status_code
    assert caught.value.operation is CrossrefOperation.GET_WORK
    observable = f"{caught.value!s}\n{caught.value!r}"
    assert all(value not in observable for value in (CONTACT_MAILTO, PRIVATE_DOI, DANGEROUS_BODY))


@pytest.mark.parametrize(
    "response",
    [_Response(invalid_body=DANGEROUS_BODY), _Response(payload=DANGEROUS_BODY)],
    ids=["decoder-error", "non-object-root"],
)
def test_invalid_json_is_parsed_once_and_raises_a_redacted_typed_error(
    monkeypatch: pytest.MonkeyPatch, response: _Response
) -> None:
    calls = _install_get(monkeypatch, (lambda: response,))
    client = CrossrefClient(CONTACT_MAILTO)

    with pytest.raises(CrossrefInvalidJsonError) as caught:
        _ = client.list_journal_works(PRIVATE_ISSN, {"rows": 1})

    assert len(calls) == 1
    assert response.json_calls == 1
    assert caught.value.operation is CrossrefOperation.LIST_JOURNAL_WORKS
    observable = f"{caught.value!s}\n{caught.value!r}"
    assert all(value not in observable for value in (CONTACT_MAILTO, PRIVATE_ISSN, DANGEROUS_BODY))


def test_shared_limiter_allows_only_ten_starts_per_sliding_second(
    monkeypatch: pytest.MonkeyPatch,
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
    ) -> _Response:
        del url, params, headers, timeout, allow_redirects
        starts.append(fake_time.monotonic())
        return _Response(payload={"message": "ok"})

    monkeypatch.setattr(requests, "get", get)
    client = CrossrefClient(CONTACT_MAILTO, timing=_timing(fake_time))

    for index in range(21):
        if index % 2 == 0:
            _ = client.get_work(f"10.5555/{index}")
        else:
            _ = client.list_journal_works("1234-5678", {"offset": index})

    assert len(starts) == 21
    assert all(starts[index] - starts[index - 10] >= 1.0 for index in range(10, 21))
    assert fake_time.sleeps == (1.0, 1.0)


def test_client_caps_simultaneous_http_requests_at_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(requests, "get", probe)
    client = CrossrefClient(CONTACT_MAILTO)
    start = Barrier(7)

    def retrieve(index: int) -> JsonObject:
        _ = start.wait(timeout=CONCURRENCY_TIMEOUT_SECONDS)
        return client.get_work(f"10.5555/{index}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(retrieve, index) for index in range(6)]
        _ = start.wait(timeout=CONCURRENCY_TIMEOUT_SECONDS)
        try:
            assert probe.first_wave_started.wait(CONCURRENCY_TIMEOUT_SECONDS)
        finally:
            probe.release.set()
        payloads = [future.result(timeout=CONCURRENCY_TIMEOUT_SECONDS) for future in futures]

    assert payloads == [{"message": "ok"}] * 6
    assert probe.call_count == 6
    assert probe.max_active == 3
