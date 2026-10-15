"""Real local HTTP exercises through an explicitly test-only connection seam."""

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
import zlib

import pytest

from zotero_arxiv_daily import figure_transport as transport
from zotero_arxiv_daily import figures
from zotero_arxiv_daily.protocol import Paper
from tests.test_figures import raster


@contextmanager
def local_server(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes[self.path]
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if isinstance(body, Event):
                body.wait(2)
                return
            self.wfile.write(body)

        def log_message(self, format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=1)


def bind_local(monkeypatch, port):
    # Only the socket factory is replaced: production URL/redirect policy still runs.
    monkeypatch.setattr(transport, "public_address", lambda host: "93.184.216.34")
    def connect(host, address, deadline):
        assert host == "arxiv.org" and address == "93.184.216.34"
        return HTTPConnection("127.0.0.1", port, timeout=transport.remaining(deadline))
    monkeypatch.setattr(transport, "_connection", connect)


def test_fetch_first_figure_when_served_over_real_local_http(monkeypatch):
    # Given
    data = raster()
    article = b'<figure><img src="f.png"><figcaption>Figure 1: Composite</figcaption></figure>'
    routes = {
        "/html/2501.01234v1": (200, {"Content-Type": "text/html"}, article),
        "/html/2501.01234v1/f.png": (200, {"Content-Type": "image/png"}, data),
    }
    with local_server(routes) as port:
        bind_local(monkeypatch, port)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        # When
        result = figures.fetch_first_figure(Paper("arxiv", "T", [], "", "https://arxiv.org/abs/2501.01234v1"))
    # Then
    assert result is not None and result.data == data


@pytest.mark.parametrize("headers,body", [
    ({"Content-Encoding": "gzip"}, zlib.compress(b"x" * transport.LIMIT * 2)),
    ({"Content-Encoding": "br"}, b"compressed"),
    ({"Content-Length": str(transport.LIMIT + 1)}, b""),
    ({}, b"x" * (transport.LIMIT + 1)),
    ({"Content-Length": "20"}, b"truncated"),
    ({"Content-Length": "-1"}, b""),
])
def test_response_when_encoded_oversized_or_truncated_fails_closed(monkeypatch, headers, body):
    # Given
    with local_server({"/bad": (200, headers, body)}) as port:
        bind_local(monkeypatch, port)
        # When / Then
        with pytest.raises(transport.FigureFetchError):
            transport.fetch_resource("https://arxiv.org/bad", monotonic() + 2)


@pytest.mark.parametrize("target", [
    "http://arxiv.org/next", "https://127.0.0.1/next", "https://evil.test/next",
    "https://user:secret@arxiv.org/next", "https://arxiv.org:444/next",
])
def test_redirect_when_disallowed_never_connects_to_target(monkeypatch, target):
    # Given
    with local_server({"/start": (302, {"Location": target}, b"")}) as port:
        bind_local(monkeypatch, port)
        # When / Then
        with pytest.raises(transport.FigureFetchError):
            transport.fetch_resource("https://arxiv.org/start", monotonic() + 2)


@pytest.mark.parametrize("hops,allowed", [(3, True), (4, False)])
def test_redirect_budget_when_relative_locations_are_used(monkeypatch, hops, allowed):
    # Given
    routes = {f"/{n}": (302, {"Location": f"/{n + 1}"}, b"") for n in range(hops)}
    routes[f"/{hops}"] = (200, {"Content-Type": "text/html"}, b"ok")
    with local_server(routes) as port:
        bind_local(monkeypatch, port)
        # When / Then
        if allowed:
            result = transport.fetch_resource("https://arxiv.org/0", monotonic() + 2)
            assert result.data == b"ok" and result.url.endswith("/3")
        else:
            with pytest.raises(transport.FigureFetchError):
                transport.fetch_resource("https://arxiv.org/0", monotonic() + 2)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "::ffff:192.168.1.1"])
def test_dns_when_any_answer_is_private_rejects_all(monkeypatch, address):
    # Given
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", (address, 443))])
    # When / Then
    with pytest.raises(transport.FigureFetchError):
        transport.public_address("arxiv.org")


def test_dns_when_stalled_returns_by_deadline_and_caps_workers(monkeypatch):
    # Given: a libc resolver call cannot be cancelled, but callers need not wait.
    entered, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(transport, "_WORKERS", BoundedSemaphore(1))
    def blocked(host):
        entered.set()
        release.wait(2)
        finished.set()
        raise OSError
    monkeypatch.setattr(transport, "public_address", blocked)
    start = monotonic()
    try:
        # When / Then
        with pytest.raises(TimeoutError):
            transport.fetch_resource("https://arxiv.org/a", start + 0.05)
        assert entered.is_set() and monotonic() - start < 1
        with pytest.raises(transport.FigureFetchError):
            transport.fetch_resource("https://arxiv.org/a", monotonic() + 1)
    finally:
        release.set()
        assert finished.wait(1)


def test_expired_deadline_when_fetching_does_not_start_dns(monkeypatch):
    # Given
    def forbidden(host):
        pytest.fail("expired request started DNS")
    monkeypatch.setattr(transport, "public_address", forbidden)
    # When / Then
    with pytest.raises(TimeoutError):
        transport.fetch_resource("https://arxiv.org/a", monotonic() - 1)


def test_body_when_stalled_is_bounded_by_total_deadline(monkeypatch):
    # Given
    release = Event()
    with local_server({"/stall": (200, {"Content-Length": "1"}, release)}) as port:
        bind_local(monkeypatch, port)
        start = monotonic()
        try:
            # When / Then
            with pytest.raises((TimeoutError, transport.FigureFetchError)):
                transport.fetch_resource("https://arxiv.org/stall", start + 0.05)
            assert monotonic() - start < 1
        finally:
            release.set()


def test_optional_failure_when_real_server_returns_missing_html(monkeypatch):
    # Given
    with local_server({"/html/2501.01234": (404, {}, b"missing")}) as port:
        bind_local(monkeypatch, port)
        # When / Then
        assert figures.fetch_first_figure(Paper("arxiv", "T", [], "", "https://arxiv.org/abs/2501.01234")) is None


def test_https_when_connecting_uses_validated_ip_and_original_tls_hostname(monkeypatch):
    # Given: narrow socket/TLS fakes expose the actual binding, not a second DNS lookup.
    events = []
    class Socket:
        def settimeout(self, timeout):
            assert 0 < timeout <= 5
        def connect(self, target):
            events.append(target)
        def close(self):
            return
    class TLS:
        def wrap_socket(self, raw, server_hostname):
            events.append(server_hostname)
            return raw
    monkeypatch.setattr(transport.socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(transport.ssl, "create_default_context", TLS)
    # When
    connection = transport._PinnedHTTPS("arxiv.org", "93.184.216.34", monotonic() + 1)
    connection.connect()
    # Then
    assert events == [("93.184.216.34", 443), "arxiv.org"]
