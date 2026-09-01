"""No-retry Crossref HTTP access for curated metadata retrieval."""

from collections import deque
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from threading import BoundedSemaphore, Lock
from time import monotonic, sleep
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, final, override
from urllib.parse import quote

import requests

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type _Clock = Callable[[], float]
type _Sleeper = Callable[[float], None]

HTTP_MULTIPLE_CHOICES: Final = 300
_JOURNALS_ENDPOINT: Final = "https://api.crossref.org/journals"
_WORKS_ENDPOINT: Final = "https://api.crossref.org/works"


class CrossrefOperation(StrEnum):
    GET_WORK = "get_work"
    LIST_JOURNAL_WORKS = "list_journal_works"


class CrossrefClientError(RuntimeError):
    """Base class for redacted Crossref client failures."""


class CrossrefContactConfigurationError(CrossrefClientError):
    """Raised when polite-pool contact identity is missing."""

    message: ClassVar[str] = "Crossref requires a nonblank contact mailto"

    def __init__(self) -> None:
        super().__init__(self.message)


class CrossrefTransportError(CrossrefClientError):
    operation: CrossrefOperation

    def __init__(self, operation: CrossrefOperation) -> None:
        self.operation = operation
        super().__init__(f"Crossref {operation.value} transport failed")


class CrossrefHttpStatusError(CrossrefClientError):
    operation: CrossrefOperation
    status_code: int

    def __init__(self, operation: CrossrefOperation, status_code: int) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(f"Crossref {operation.value} returned HTTP status {status_code}")


class CrossrefInvalidJsonError(CrossrefClientError):
    operation: CrossrefOperation

    def __init__(self, operation: CrossrefOperation) -> None:
        self.operation = operation
        super().__init__(f"Crossref {operation.value} returned invalid JSON")


@dataclass(frozen=True, slots=True)
class CrossrefClientTiming:
    """Inject monotonic time and sleeping for deterministic clients."""

    clock: _Clock = monotonic
    sleeper: _Sleeper = sleep


class _HttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    def json(self) -> JsonValue: ...


@dataclass(frozen=True, slots=True)
class _CrossrefRequest:
    operation: CrossrefOperation
    url: str = field(repr=False)
    params: Mapping[str, str | int] = field(repr=False)


@final
class _RequestLimiter:
    """Share sliding-window starts and in-flight capacity across client methods."""

    max_starts: ClassVar[int] = 10
    max_in_flight: ClassVar[int] = 3
    window_seconds: ClassVar[float] = 1.0

    __slots__: ClassVar[tuple[str, ...]] = (
        "_in_flight",
        "_start_lock",
        "_starts",
        "_timing",
    )

    def __init__(self, timing: CrossrefClientTiming) -> None:
        self._in_flight = BoundedSemaphore(self.max_in_flight)
        self._start_lock = Lock()
        self._starts: deque[float] = deque()
        self._timing = timing

    @contextmanager
    def acquire(self) -> Generator[None]:
        """Admit one in-flight request and reserve its start timestamp."""
        with self._in_flight:
            with self._start_lock:
                while True:
                    now = self._timing.clock()
                    while self._starts and now - self._starts[0] >= self.window_seconds:
                        _ = self._starts.popleft()
                    if len(self._starts) < self.max_starts:
                        self._starts.append(now)
                        break
                    wait_seconds = self.window_seconds - (now - self._starts[0])
                    self._timing.sleeper(wait_seconds)
            yield


class CrossrefClient:
    """Fetch Crossref JSON through fixed official endpoints without retries."""

    request_timeout_seconds: ClassVar[float] = 30.0

    __slots__: ClassVar[tuple[str, ...]] = ("_headers", "_limiter", "_mailto")

    _headers: Mapping[str, str]
    _limiter: _RequestLimiter
    _mailto: str

    def __init__(
        self, mailto: str, *, timing: CrossrefClientTiming | None = None
    ) -> None:
        normalized_mailto = mailto.strip()
        if not normalized_mailto:
            raise CrossrefContactConfigurationError from None
        self._mailto = normalized_mailto
        self._headers = MappingProxyType(
            {
                "Accept": "application/json",
                "User-Agent": (
                    "zotero-arxiv-daily/1.0 "
                    "(https://github.com/TideDra/zotero-arxiv-daily; "
                    f"mailto:{normalized_mailto})"
                ),
            }
        )
        self._limiter = _RequestLimiter(timing or CrossrefClientTiming())

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def get_work(self, doi: str) -> JsonObject:
        """Fetch one work by DOI."""
        return self._request(
            _CrossrefRequest(
                operation=CrossrefOperation.GET_WORK,
                url=f"{_WORKS_ENDPOINT}/{quote(doi, safe='')}",
                params={},
            )
        )

    def list_journal_works(
        self, issn: str, params: Mapping[str, str | int]
    ) -> JsonObject:
        """Fetch one page of works for a journal ISSN."""
        return self._request(
            _CrossrefRequest(
                operation=CrossrefOperation.LIST_JOURNAL_WORKS,
                url=f"{_JOURNALS_ENDPOINT}/{quote(issn, safe='')}/works",
                params=params,
            )
        )

    def _request(self, request: _CrossrefRequest) -> JsonObject:
        request_params = dict(request.params)
        request_params["mailto"] = self._mailto
        response: _HttpResponse | None
        with self._limiter.acquire():
            response = self._get(request, request_params)

        if response is None:
            raise CrossrefTransportError(request.operation) from None

        status_code = response.status_code
        if status_code >= HTTP_MULTIPLE_CHOICES:
            del response
            raise CrossrefHttpStatusError(request.operation, status_code) from None

        try:
            payload = response.json()
        except requests.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload

        del response
        raise CrossrefInvalidJsonError(request.operation) from None

    def _get(
        self,
        request: _CrossrefRequest,
        params: Mapping[str, str | int],
    ) -> _HttpResponse | None:
        try:
            return requests.get(
                request.url,
                params=params,
                headers=self._headers,
                timeout=self.request_timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException:
            return None


__all__: Final[tuple[str, ...]] = (
    "CrossrefClient",
    "CrossrefClientError",
    "CrossrefClientTiming",
    "CrossrefContactConfigurationError",
    "CrossrefHttpStatusError",
    "CrossrefInvalidJsonError",
    "CrossrefOperation",
    "CrossrefTransportError",
    "JsonObject",
    "JsonValue",
)
