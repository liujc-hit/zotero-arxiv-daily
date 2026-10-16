"""Elsevier abstract-view contracts at the HTTP boundary."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Final
from urllib.parse import parse_qs, urlsplit

import pytest

from zotero_arxiv_daily.enrichment import providers
from zotero_arxiv_daily.enrichment.providers import ElsevierAdapter
from zotero_arxiv_daily.enrichment.settings import ElsevierSettings
from zotero_arxiv_daily.retriever.crossref_client import JsonValue

from .http_fakes import Call, Response, install_get


SECRET: Final = "test-elsevier-key"
DOI: Final = "10.1016/example?part=1"


def test_elsevier_requests_only_abstract_view_when_fetching_doi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an available Elsevier record
    calls = install_get(monkeypatch, (lambda: Response(payload={}),))
    adapter = ElsevierAdapter(ElsevierSettings(enabled=True, api_key=SECRET))

    # When the adapter retrieves a DOI containing reserved characters
    adapter.fetch_abstract(DOI)

    # Then one Scopus request carries the view in query and the key only in headers
    assert calls == [
        Call(
            "https://api.elsevier.com/content/abstract/doi/"
            "10.1016%2Fexample%3Fpart%3D1",
            {"view": "META_ABS"},
            {"Accept": "application/json", "X-ELS-APIKey": SECRET},
            30.0,
            False,
        )
    ]


def test_elsevier_returns_description_when_abstract_view_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an HTTP boundary that omits the description in the default META view
    def respond() -> Response:
        coredata: dict[str, JsonValue] = {"dc:title": "Metadata title, not the abstract"}
        if calls[0].params.get("view") == "META_ABS":
            coredata["dc:description"] = "<p>Actual &amp; available abstract.</p>"
        return Response(payload={"abstracts-retrieval-response": {"coredata": coredata}})

    calls = install_get(monkeypatch, (respond,))
    adapter = ElsevierAdapter(ElsevierSettings(enabled=True, api_key=SECRET))

    # When the real adapter requests the record
    abstract = adapter.fetch_abstract(DOI)

    # Then it returns the actual cleaned description rather than metadata
    assert abstract == "Actual & available abstract."
    assert len(calls) == 1


def test_elsevier_returns_none_when_record_has_no_abstract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a record lacking a description even in META_ABS view
    calls = install_get(
        monkeypatch,
        (lambda: Response(payload={
            "abstracts-retrieval-response": {"coredata": {"dc:title": "Title only"}}
        }),),
    )
    adapter = ElsevierAdapter(ElsevierSettings(enabled=True, api_key=SECRET))

    # When the record is fetched
    abstract = adapter.fetch_abstract(DOI)

    # Then no abstract or fallback request is invented
    assert abstract is None
    assert len(calls) == 1


@pytest.mark.parametrize("status_code", [404, 401, 403])
def test_elsevier_returns_none_without_retry_when_http_request_fails(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    # Given an unavailable or inaccessible Elsevier record
    calls = install_get(monkeypatch, (lambda: Response(status_code=status_code),))
    adapter = ElsevierAdapter(ElsevierSettings(enabled=True, api_key=SECRET))

    # When the record is fetched
    abstract = adapter.fetch_abstract(DOI)

    # Then the failure remains a single attempt with no abstract
    assert abstract is None
    assert len(calls) == 1


def test_elsevier_transmits_view_and_parses_abstract_over_real_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a local HTTP server implementing metadata-only and abstract views
    received: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append((self.path, self.headers.get("X-ELS-APIKey")))
            query = parse_qs(urlsplit(self.path).query)
            body = b'{"abstracts-retrieval-response":{"coredata":{}}}'
            if query.get("view") == ["META_ABS"]:
                body = (
                    b'{"abstracts-retrieval-response":{"coredata":'
                    b'{"dc:description":"Abstract delivered over HTTP."}}}'
                )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        monkeypatch.setattr(
            providers,
            "_ELSEVIER_ENDPOINT",
            f"http://127.0.0.1:{server.server_port}/content/abstract/doi",
        )
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # When the adapter uses real requests against the local endpoint
            abstract = ElsevierAdapter(
                ElsevierSettings(enabled=True, api_key=SECRET)
            ).fetch_abstract(DOI)
        finally:
            server.shutdown()
            thread.join()

    # Then the wire query selects abstracts and the JSON description is returned
    assert abstract == "Abstract delivered over HTTP."
    assert received == [
        (
            "/content/abstract/doi/10.1016%2Fexample%3Fpart%3D1?view=META_ABS",
            SECRET,
        )
    ]
