"""Authenticated OpenAlex HTTP access with credential failover and rate limiting."""

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic, sleep
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, override

import requests

from .openalex_errors import (
    MissingOpenAlexCredentialsError,
    OpenAlexClientError,
    OpenAlexCredentialConfigurationError,
    OpenAlexCredentialsExhaustedError,
    OpenAlexHttpStatusError,
    OpenAlexInvalidJsonError,
    OpenAlexServerError,
    OpenAlexSourcesHttpStatusError,
    OpenAlexSourcesInvalidJsonError,
    OpenAlexSourcesTransportError,
    OpenAlexTransportError,
    OpenAlexUnsafeQueryParameterError,
)

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type QueryValue = str | int
type _Clock = Callable[[], float]
type _Sleeper = Callable[[float], None]

HTTP_MULTIPLE_CHOICES: Final = 300
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_INTERNAL_SERVER_ERROR: Final = 500


class _HttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def json(self) -> JsonValue: ...


@dataclass(frozen=True, slots=True)
class OpenAlexClientTiming:
    """Inject monotonic time and sleeping for deterministic clients."""

    clock: _Clock = monotonic
    sleeper: _Sleeper = sleep


@dataclass(frozen=True, slots=True, repr=False)
class _Identity:
    api_key: str | None = field(repr=False)


_ANONYMOUS_IDENTITY: Final = _Identity(None)


class SlidingWindowRateLimiter:
    """Serialize starts through a per-instance 100 request/second window."""

    max_starts: ClassVar[int] = 100
    window_seconds: ClassVar[float] = 1.0

    __slots__: ClassVar[tuple[str, ...]] = ("_lock", "_starts", "_timing")

    _lock: Lock
    _starts: deque[float]
    _timing: OpenAlexClientTiming

    def __init__(self, timing: OpenAlexClientTiming | None = None) -> None:
        self._timing = timing or OpenAlexClientTiming()
        self._starts = deque()
        self._lock = Lock()

    def acquire(self) -> float:
        """Reserve one request start and return its monotonic timestamp."""
        with self._lock:
            while True:
                now = self._timing.clock()
                while self._starts and now - self._starts[0] >= self.window_seconds:
                    _ = self._starts.popleft()
                if len(self._starts) < self.max_starts:
                    self._starts.append(now)
                    return now
                wait_seconds = self.window_seconds - (now - self._starts[0])
                self._timing.sleeper(wait_seconds)


class OpenAlexClient:
    """Fetch OpenAlex JSON without exposing or reusing exhausted credentials."""

    api_url: ClassVar[str] = "https://api.openalex.org/works"
    sources_api_url: ClassVar[str] = "https://api.openalex.org/sources"
    max_attempts: ClassVar[int] = 3
    retry_delay_seconds: ClassVar[float] = 1.0
    request_timeout_seconds: ClassVar[float] = 60.0
    request_headers: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "User-Agent": (
                "zotero-arxiv-daily OpenAlex retriever "
                "(https://github.com/TideDra/zotero-arxiv-daily)"
            ),
            "Accept": "application/json",
        }
    )

    __slots__: ClassVar[tuple[str, ...]] = (
        "_identities",
        "_identity_index",
        "_identity_lock",
        "_limiter",
        "_timing",
    )

    _identities: tuple[_Identity, ...]
    _identity_index: int
    _identity_lock: Lock
    _limiter: SlidingWindowRateLimiter
    _timing: OpenAlexClientTiming

    def __init__(
        self,
        api_keys: tuple[str, ...],
        *,
        anonymous_fallback: bool = False,
        timing: OpenAlexClientTiming | None = None,
    ) -> None:
        normalized_keys = tuple(
            normalized for key in api_keys if (normalized := key.strip())
        )
        if len(normalized_keys) > 2 or len(set(normalized_keys)) != len(normalized_keys):
            raise OpenAlexCredentialConfigurationError from None
        keyed_identities = tuple(_Identity(key) for key in normalized_keys)
        anonymous_identities: tuple[_Identity, ...] = (
            (_ANONYMOUS_IDENTITY,) if anonymous_fallback else ()
        )
        identities = keyed_identities + anonymous_identities
        if not identities:
            raise MissingOpenAlexCredentialsError from None

        self._identities = identities
        self._identity_index = 0
        self._identity_lock = Lock()
        self._timing = timing or OpenAlexClientTiming()
        self._limiter = SlidingWindowRateLimiter(self._timing)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}(identity_count={len(self._identities)})"

    def _current_identity(self) -> tuple[int, _Identity]:
        with self._identity_lock:
            if self._identity_index >= len(self._identities):
                raise OpenAlexCredentialsExhaustedError from None
            return self._identity_index, self._identities[self._identity_index]

    def _reserve_attempt(self, identity_index: int) -> bool:
        _ = self._limiter.acquire()
        with self._identity_lock:
            return self._identity_index == identity_index

    def _advance_identity(self, identity_index: int) -> None:
        with self._identity_lock:
            next_index = identity_index + 1
            if next_index > self._identity_index:
                self._identity_index = next_index

    def _send(
        self,
        params: Mapping[str, QueryValue],
        headers: Mapping[str, str],
        *,
        sources: bool = False,
    ) -> _HttpResponse | None:
        try:
            return requests.get(
                self.sources_api_url if sources else self.api_url,
                params=params,
                headers=headers,
                timeout=self.request_timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None

    def get_sources_json(self, params: Mapping[str, QueryValue]) -> JsonValue:
        """Return one no-retry payload from the fixed OpenAlex Sources endpoint."""
        request_params = dict(params)
        if any(name.casefold() == "api_key" for name in request_params):
            raise OpenAlexUnsafeQueryParameterError from None

        while True:
            identity_index, identity = self._current_identity()
            headers = dict(self.request_headers)
            if identity.api_key is not None:
                headers["Authorization"] = f"Bearer {identity.api_key}"
            if not self._reserve_attempt(identity_index):
                continue

            response = self._send(request_params, headers, sources=True)
            if response is None:
                raise OpenAlexSourcesTransportError from None
            status_code = response.status_code
            if status_code == HTTP_TOO_MANY_REQUESTS:
                self._advance_identity(identity_index)
            if status_code >= HTTP_MULTIPLE_CHOICES:
                del response
                raise OpenAlexSourcesHttpStatusError(status_code) from None
            if response.headers.get("X-RateLimit-Remaining", "").strip() == "0":
                self._advance_identity(identity_index)
            try:
                return response.json()
            except requests.JSONDecodeError:
                raise OpenAlexSourcesInvalidJsonError from None

    def get_json(self, params: Mapping[str, QueryValue]) -> JsonValue:
        """Return one OpenAlex JSON payload using monotonic identity failover."""
        request_params = dict(params)
        if any(name.casefold() == "api_key" for name in request_params):
            raise OpenAlexUnsafeQueryParameterError from None

        while True:
            identity_index, identity = self._current_identity()
            headers = dict(self.request_headers)
            if identity.api_key is not None:
                headers["Authorization"] = f"Bearer {identity.api_key}"

            for attempt in range(1, self.max_attempts + 1):
                if not self._reserve_attempt(identity_index):
                    break
                response = self._send(request_params, headers)
                if response is None:
                    if attempt == self.max_attempts:
                        raise OpenAlexTransportError from None
                    self._timing.sleeper(self.retry_delay_seconds)
                    continue

                status_code = response.status_code
                if status_code == HTTP_TOO_MANY_REQUESTS:
                    self._advance_identity(identity_index)
                    del response
                    break
                if status_code >= HTTP_INTERNAL_SERVER_ERROR:
                    if attempt == self.max_attempts:
                        del response
                        raise OpenAlexServerError from None
                    del response
                    self._timing.sleeper(self.retry_delay_seconds)
                    continue
                if status_code >= HTTP_MULTIPLE_CHOICES:
                    del response
                    raise OpenAlexHttpStatusError(status_code) from None

                if response.headers.get("X-RateLimit-Remaining", "").strip() == "0":
                    self._advance_identity(identity_index)
                try:
                    return response.json()
                except requests.JSONDecodeError:
                    raise OpenAlexInvalidJsonError from None


__all__: Final[tuple[str, ...]] = (
    "JsonObject",
    "JsonValue",
    "MissingOpenAlexCredentialsError",
    "OpenAlexClient",
    "OpenAlexClientError",
    "OpenAlexClientTiming",
    "OpenAlexCredentialConfigurationError",
    "OpenAlexCredentialsExhaustedError",
    "OpenAlexHttpStatusError",
    "OpenAlexInvalidJsonError",
    "OpenAlexSourcesHttpStatusError",
    "OpenAlexSourcesInvalidJsonError",
    "OpenAlexSourcesTransportError",
    "OpenAlexServerError",
    "OpenAlexTransportError",
    "OpenAlexUnsafeQueryParameterError",
    "SlidingWindowRateLimiter",
)
