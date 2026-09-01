"""Focused contract tests for the OpenAlex HTTP client."""

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock, get_ident
from typing import Final, assert_never, final
from urllib.parse import urlparse

import pytest
import requests

from zotero_arxiv_daily.retriever.openalex_client import (
    JsonValue, MissingOpenAlexCredentialsError, OpenAlexClient, OpenAlexClientTiming,
    OpenAlexCredentialConfigurationError, OpenAlexCredentialsExhaustedError,
    OpenAlexHttpStatusError, OpenAlexServerError, OpenAlexTransportError,
    OpenAlexUnsafeQueryParameterError, SlidingWindowRateLimiter,
)

PRIMARY_KEY: Final = "test-primary-openalex-key"
SECONDARY_KEY: Final = "test-secondary-openalex-key"
TERTIARY_KEY: Final = "test-tertiary-openalex-key"
CONCURRENCY_TIMEOUT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    payload: JsonValue
    headers: Mapping[str, str]

    def json(self) -> JsonValue:
        return self.payload


@dataclass(frozen=True, slots=True)
class _Call:
    url: str
    params: Mapping[str, str | int]
    headers: Mapping[str, str]
    allow_redirects: bool | None


class _FakeTime:
    """Provide deterministic, thread-safe monotonic time for limiter tests."""

    def __init__(self) -> None:
        self._now: float = 0.0
        self._sleeps: list[float] = []
        self._lock: Lock = Lock()

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


@final
class _RaceGate:
    def __init__(self) -> None:
        self.started, self.release = Event(), Event()
        self._thread_id: int | None = None

    def block_marked_once(self) -> None:
        if get_ident() != self._thread_id or self.started.is_set():
            return
        self.started.set()
        assert self.release.wait(CONCURRENCY_TIMEOUT_SECONDS)

    def _call_marked[T](self, action: Callable[[], T]) -> T:
        self._thread_id = get_ident()
        return action()

    def run[T, U](self, blocked: Callable[[], T], concurrent: Callable[[], U]) -> tuple[U, T]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            blocked_future = pool.submit(self._call_marked, blocked)
            try:
                assert self.started.wait(CONCURRENCY_TIMEOUT_SECONDS)
                concurrent_result = pool.submit(concurrent).result(timeout=CONCURRENCY_TIMEOUT_SECONDS)
            finally:
                self.release.set()
            return concurrent_result, blocked_future.result(timeout=CONCURRENCY_TIMEOUT_SECONDS)


def _response(status_code: int = 200, *, marker: str = "ok", remaining: str | None = None) -> _Response:
    headers = {} if remaining is None else {"X-RateLimit-Remaining": remaining}
    return _Response(status_code, {"marker": marker}, headers)


def _install_get(monkeypatch: pytest.MonkeyPatch, outcomes: Iterable[_Response | requests.RequestException]) -> list[_Call]:
    outcome_iterator = iter(outcomes)
    calls: list[_Call] = []

    def get(url: str, *, params: Mapping[str, str | int], headers: Mapping[str, str], timeout: float, allow_redirects: bool | None = None) -> _Response:
        del timeout
        calls.append(_Call(url, dict(params), dict(headers), allow_redirects))
        outcome = next(outcome_iterator)
        match outcome:
            case requests.RequestException():
                raise outcome
            case _Response():
                return outcome
            case unreachable:
                assert_never(unreachable)

    monkeypatch.setattr(requests, "get", get)
    return calls


def _timing(fake_time: _FakeTime) -> OpenAlexClientTiming:
    return OpenAlexClientTiming(fake_time.monotonic, fake_time.sleep)


def test_bearer_auth_is_header_only_and_redacted_from_observable_text(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    calls = _install_get(monkeypatch, (_response(401),))
    client = OpenAlexClient((PRIMARY_KEY,))

    with pytest.raises(OpenAlexHttpStatusError) as caught:
        _ = client.get_json({"filter": "from_publication_date:2026-08-30"})

    call = calls[0]
    assert call.headers["Authorization"] == f"Bearer {PRIMARY_KEY}"
    assert call.allow_redirects is False
    assert "api_key" not in call.params
    assert urlparse(call.url).query == ""
    observable_text = "\n".join((call.url, str(caught.value), repr(caught.value), repr(client), caplog.text))
    assert PRIMARY_KEY not in observable_text


def test_redirect_status_is_an_immediate_redacted_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_get(monkeypatch, (_response(302),))
    client = OpenAlexClient((PRIMARY_KEY,))

    with pytest.raises(OpenAlexHttpStatusError) as caught:
        _ = client.get_json({"filter": "redirect"})

    assert caught.value.status_code == 302
    assert len(calls) == 1
    assert PRIMARY_KEY not in f"{caught.value!s}\n{caught.value!r}"


def test_api_key_query_parameter_is_rejected_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_get(monkeypatch, (_response(),))
    client = OpenAlexClient((PRIMARY_KEY,))

    with pytest.raises(OpenAlexUnsafeQueryParameterError):
        _ = client.get_json({"api_key": PRIMARY_KEY})

    assert calls == []


def test_429_rotates_permanently_from_primary_to_secondary(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = _FakeTime()
    calls = _install_get(monkeypatch, (_response(429), _response(marker="first"), _response(marker="second")))
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY), timing=_timing(fake_time))

    first = client.get_json({"filter": "first"})
    second = client.get_json({"filter": "second"})

    assert (first, second) == ({"marker": "first"}, {"marker": "second"})
    assert [call.headers["Authorization"] for call in calls] == [f"Bearer {PRIMARY_KEY}", f"Bearer {SECONDARY_KEY}", f"Bearer {SECONDARY_KEY}"]
    assert fake_time.sleeps == ()


def test_limiter_waiter_reselects_after_primary_rotates(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = _FakeTime()
    gate = _RaceGate()
    calls = _install_get(monkeypatch, (_response(429), _response(marker="rotator"), _response(marker="stale")))

    def acquire(_: SlidingWindowRateLimiter) -> float:
        gate.block_marked_once()
        return fake_time.monotonic()

    monkeypatch.setattr(SlidingWindowRateLimiter, "acquire", acquire)
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY), timing=_timing(fake_time))
    rotator_payload, stale_payload = gate.run(
        lambda: client.get_json({"filter": "stale"}),
        lambda: client.get_json({"filter": "rotate"}),
    )

    assert (rotator_payload, stale_payload) == ({"marker": "rotator"}, {"marker": "stale"})
    assert [call.headers["Authorization"] for call in calls] == [f"Bearer {PRIMARY_KEY}", f"Bearer {SECONDARY_KEY}", f"Bearer {SECONDARY_KEY}"]


@pytest.mark.parametrize("delayed_status", [pytest.param(200, id="success"), pytest.param(429, id="rate-limited")])
def test_reserved_primary_completion_never_reactivates_it(monkeypatch: pytest.MonkeyPatch, delayed_status: int) -> None:
    fake_time = _FakeTime()
    gate = _RaceGate()
    calls: list[_Call] = []
    delayed_secondary = (_response(marker="delayed"),) if delayed_status == 429 else ()
    outcomes = iter((_response(429), _response(marker="rotator"), *delayed_secondary, _response(marker="next")))

    def get(url: str, *, params: Mapping[str, str | int], headers: Mapping[str, str], timeout: float, allow_redirects: bool) -> _Response:
        del timeout
        calls.append(_Call(url, dict(params), dict(headers), allow_redirects))
        if not gate.started.is_set():
            gate.block_marked_once()
            return _response(delayed_status, marker="reserved")
        return next(outcomes)

    monkeypatch.setattr(requests, "get", get)
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY), timing=_timing(fake_time))
    rotator_payload, reserved_payload = gate.run(
        lambda: client.get_json({"filter": "reserved"}),
        lambda: client.get_json({"filter": "rotate"}),
    )
    next_payload = client.get_json({"filter": "next"})

    reserved_marker = "delayed" if delayed_status == 429 else "reserved"
    assert (rotator_payload, reserved_payload, next_payload) == ({"marker": "rotator"}, {"marker": reserved_marker}, {"marker": "next"})
    secondary_calls = 3 if delayed_status == 429 else 2
    assert [call.headers["Authorization"] for call in calls] == [f"Bearer {PRIMARY_KEY}", f"Bearer {PRIMARY_KEY}", *[f"Bearer {SECONDARY_KEY}"] * secondary_calls]


def test_zero_remaining_returns_json_then_rotates_before_next_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_get(monkeypatch, (_response(marker="primary", remaining="0"), _response(marker="secondary")))
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY))

    first = client.get_json({"filter": "first"})
    second = client.get_json({"filter": "second"})

    assert (first, second) == ({"marker": "primary"}, {"marker": "secondary"})
    assert [call.headers["Authorization"] for call in calls] == [f"Bearer {PRIMARY_KEY}", f"Bearer {SECONDARY_KEY}"]


def test_anonymous_fallback_is_an_explicit_final_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = _FakeTime()
    calls = _install_get(monkeypatch, (_response(429), _response(marker="anonymous")))
    client = OpenAlexClient((PRIMARY_KEY,), anonymous_fallback=True, timing=_timing(fake_time))

    payload = client.get_json({"filter": "works"})

    assert payload == {"marker": "anonymous"}
    assert calls[0].headers["Authorization"] == f"Bearer {PRIMARY_KEY}"
    assert "Authorization" not in calls[1].headers
    assert fake_time.sleeps == ()


def test_missing_credentials_fail_without_anonymous_identity() -> None:
    with pytest.raises(MissingOpenAlexCredentialsError):
        _ = OpenAlexClient(())


@pytest.mark.parametrize("api_keys", [pytest.param((PRIMARY_KEY, f" {PRIMARY_KEY} "), id="duplicate-normalized"), pytest.param((PRIMARY_KEY, SECONDARY_KEY, TERTIARY_KEY), id="more-than-two")])
def test_invalid_key_configuration_is_typed_static_and_redacted(api_keys: tuple[str, ...]) -> None:
    with pytest.raises(OpenAlexCredentialConfigurationError) as caught:
        _ = OpenAlexClient(api_keys)

    rendered = f"{caught.value!s}\n{caught.value!r}"
    assert all(key.strip() not in rendered for key in api_keys)


def test_429_exhaustion_is_fail_closed_without_more_http(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = _FakeTime()
    calls = _install_get(monkeypatch, (_response(429), _response(429)))
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY), timing=_timing(fake_time))
    with pytest.raises(OpenAlexCredentialsExhaustedError):
        _ = client.get_json({"filter": "exhaust"})
    exhausted_call_count = len(calls)

    with pytest.raises(OpenAlexCredentialsExhaustedError):
        _ = client.get_json({"filter": "still-exhausted"})

    assert exhausted_call_count == 2
    assert len(calls) == exhausted_call_count
    assert fake_time.sleeps == ()


@pytest.mark.parametrize(("outcomes", "error_type"), [pytest.param(tuple(requests.ConnectionError(f"offline {PRIMARY_KEY}") for _ in range(3)), OpenAlexTransportError, id="network"), pytest.param(tuple(_response(503) for _ in range(3)), OpenAlexServerError, id="server")])
def test_network_and_5xx_use_three_total_same_identity_attempts(monkeypatch: pytest.MonkeyPatch, outcomes: tuple[_Response | requests.RequestException, ...], error_type: type[OpenAlexTransportError | OpenAlexServerError]) -> None:
    fake_time = _FakeTime()
    calls = _install_get(monkeypatch, outcomes)
    client = OpenAlexClient((PRIMARY_KEY, SECONDARY_KEY), timing=_timing(fake_time))

    with pytest.raises(error_type) as caught:
        _ = client.get_json({"filter": "retry"})

    assert len(calls) == 3
    assert {call.headers["Authorization"] for call in calls} == {f"Bearer {PRIMARY_KEY}"}
    assert fake_time.sleeps == (1.0, 1.0)
    assert PRIMARY_KEY not in str(caught.value)


def test_every_retry_failover_and_anonymous_attempt_acquires_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = _FakeTime()
    calls = _install_get(monkeypatch, (requests.ConnectionError("offline"), _response(429), _response()))
    acquisitions: list[SlidingWindowRateLimiter] = []

    def acquire(limiter: SlidingWindowRateLimiter) -> float:
        acquisitions.append(limiter)
        return fake_time.monotonic()

    monkeypatch.setattr(SlidingWindowRateLimiter, "acquire", acquire)
    client = OpenAlexClient((PRIMARY_KEY,), anonymous_fallback=True, timing=_timing(fake_time))

    _ = client.get_json({"filter": "limited"})

    assert len(acquisitions) == len(calls) == 3
    assert {call.allow_redirects for call in calls} == {False}
