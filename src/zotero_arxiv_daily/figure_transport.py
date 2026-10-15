"""Small proxy-free, public-IP-pinned HTTPS transport for optional figures.

The caller's wait includes DNS and is deadline bounded. libc DNS cannot be
cancelled: at most four daemon workers may linger in getaddrinfo; saturation
fails closed rather than accumulating workers or blocking email delivery.
Compressed responses are deliberately unsupported (including compression bombs).
"""

from contextlib import closing
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from ipaddress import ip_address
from queue import Empty, Queue
import socket
import ssl
from threading import BoundedSemaphore, Thread, Timer
from time import monotonic
from typing import Final, final, override
from urllib.parse import SplitResult, urljoin, urlsplit

LIMIT: Final = 2 * 1024 * 1024
HOSTS: Final = frozenset({"arxiv.org", "www.arxiv.org", "biorxiv.org",
                         "www.biorxiv.org", "medrxiv.org", "www.medrxiv.org"})
_WORKERS: Final = BoundedSemaphore(4)


class FigureFetchError(Exception):
    """An expected policy or transport failure; never contains a remote URL."""


@dataclass(frozen=True, slots=True)
class Resource:
    data: bytes
    media_type: str
    url: str


def checked_url(url: str) -> SplitResult:
    if any(ord(char) <= 32 or ord(char) >= 127 for char in url) or "\\" in url:
        raise FigureFetchError
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in HOSTS
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or parts.fragment or parts.query):
        raise FigureFetchError
    return parts


def remaining(deadline: float) -> float:
    left = deadline - monotonic()
    if left <= 0:
        raise TimeoutError
    return min(5.0, left)


def public_address(host: str) -> str:
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = [str(answer[4][0]) for answer in answers]
    if not addresses or any(not ip_address(address).is_global for address in addresses):
        raise FigureFetchError
    return addresses[0]


@final
class _PinnedHTTPS(HTTPSConnection):
    """Connect directly to a validated numeric address, with hostname TLS/SNI."""

    def __init__(self, host: str, address: str, deadline: float):
        self.tls_context = ssl.create_default_context()
        super().__init__(host, timeout=remaining(deadline), context=self.tls_context)
        self.address = address
        self.deadline = deadline

    @override
    def connect(self) -> None:
        connect_deadline = min(self.deadline, monotonic() + 5.0)
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        self.sock = raw
        raw.settimeout(remaining(connect_deadline))
        try:
            raw.connect((self.address, 443))
            raw.settimeout(remaining(connect_deadline))
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
            self.sock.settimeout(remaining(self.deadline))
        except (OSError, ValueError):
            raw.close()
            raise


def _connection(host: str, address: str, deadline: float) -> HTTPConnection:
    return _PinnedHTTPS(host, address, deadline)


def _abort(connection: HTTPConnection) -> None:
    if connection.sock is not None:
        try:
            connection.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            return


def _fetch(url: str, deadline: float) -> Resource:
    for redirects in range(4):
        parts = checked_url(url)
        host = parts.hostname
        assert host is not None  # checked_url guarantees an allowlisted hostname.
        remaining(deadline)
        address = public_address(host)
        remaining(deadline)
        with closing(_connection(host, address, deadline)) as connection:
            timer = Timer(deadline - monotonic(), _abort, args=(connection,))
            timer.daemon = True
            timer.start()
            try:
                connection.request("GET", parts.path or "/", headers={
                    "Accept-Encoding": "identity", "User-Agent": "zotero-arxiv-daily/figures",
                    "Connection": "close",
                })
                with connection.getresponse() as response:
                    remaining(deadline)
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if redirects == 3 or not location:
                            raise FigureFetchError
                        url = urljoin(url, location)
                        checked_url(url)
                        continue
                    if response.status != 200:
                        raise FigureFetchError
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise FigureFetchError
                    length = response.getheader("Content-Length")
                    if length is not None and (not length.isdecimal() or int(length) > LIMIT):
                        raise FigureFetchError
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        if connection.sock is not None:
                            connection.sock.settimeout(remaining(deadline))
                        chunk = response.read1(min(65536, LIMIT + 1 - total))
                        remaining(deadline)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > LIMIT:
                            raise FigureFetchError
                        chunks.append(chunk)
                    if length is not None and total != int(length):
                        raise FigureFetchError
                    media = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                    return Resource(b"".join(chunks), media, url)
            finally:
                timer.cancel()
    raise FigureFetchError


def fetch_resource(url: str, deadline: float) -> Resource:
    """Fetch within the shared paper deadline, including uncancellable DNS waits."""
    checked_url(url)
    remaining(deadline)
    if not _WORKERS.acquire(blocking=False):
        raise FigureFetchError
    result: Queue[Resource | FigureFetchError] = Queue(maxsize=1)

    def work() -> None:
        try:
            result.put(_fetch(url, deadline))
        except (OSError, HTTPException, ValueError, FigureFetchError):
            result.put(FigureFetchError())
        finally:
            _WORKERS.release()

    Thread(target=work, daemon=True, name="figure-fetch").start()
    try:
        value = result.get(timeout=max(0.0, deadline - monotonic()))
    except Empty:
        raise TimeoutError from None
    if isinstance(value, FigureFetchError):
        raise value
    return value
