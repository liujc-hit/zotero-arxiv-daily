"""Offline contracts for the optional first research figure."""

from time import monotonic
import zlib

import pytest
import pymupdf

from zotero_arxiv_daily.protocol import Paper


def raster(kind="PNG", size=(8, 9)):
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, *size), False)
    pixmap.clear_with(255)
    return pixmap.tobytes(kind.lower())


def paper(url="https://arxiv.org/abs/2501.01234v2"):
    return Paper(source="arxiv", title="Title", authors=[], abstract="", url=url)


def test_original_figure_when_logo_and_figure10_precede_it(monkeypatch):
    # Given: only the explicitly captioned first figure is eligible.
    from zotero_arxiv_daily import figures
    from zotero_arxiv_daily.figure_transport import Resource

    data = raster()
    html = b'''<img src="logo.png"><figure><img src="ten.png">
    <figcaption>Figure 10: Other</figcaption></figure>
    <figure class="ltx_figure" id="S1.F1"><img src="x1.png">
    <figcaption>Figure 1: Original caption.</figcaption></figure>'''
    urls = []

    def fetch(url, deadline):
        urls.append(url)
        assert 0 < deadline - monotonic() <= 15
        return Resource(html, "text/html", url) if len(urls) == 1 else Resource(data, "image/png", url)

    monkeypatch.setattr(figures, "fetch_resource", fetch)
    # When
    result = figures.fetch_first_figure(paper())
    # Then
    assert result is not None
    assert result.data == data
    assert result.caption == "Figure 1: Original caption."
    assert result.source_url == "https://arxiv.org/html/x1.png"
    assert urls == ["https://arxiv.org/html/2501.01234v2", result.source_url]


@pytest.mark.parametrize("body", [
    '<img src="logo.png">',
    '<figure><img src="x.png"><figcaption>Figure 10: not one</figcaption></figure>',
    '<figure><img src="a.png"><img src="b.png"><figcaption>Figure 1: panels</figcaption></figure>',
    '<figure><img src="x.svg"><figcaption>Figure 1: vector</figcaption></figure>',
    '<figure><svg/><img src="x.png"><figcaption>Figure 1: mixed</figcaption></figure>',
    '<figure><img src="x.png"><figcaption>Figure 1a: panel</figcaption></figure>',
    '<figure><img src="x.png"><figcaption>Figure 1.1: subsection</figcaption></figure>',
    '<figure><img src="x.png"><figcaption>Figure 1: first</figcaption></figure>' * 2,
])
def test_rejects_unverified_or_ambiguous_figure_when_parsing(body):
    # Given / When
    from zotero_arxiv_daily.figure_parser import first_image
    result = first_image(body.encode(), "https://arxiv.org/html/2501.01234v2")
    # Then
    assert result is None


@pytest.mark.parametrize("url", [
    "https://example.com/abs/2501.01234", "https://arxiv.org/abs/nope",
    "https://arxiv.org/abs/2501.01234?token=secret", "http://arxiv.org/abs/2501.01234",
    "https://user:pass@arxiv.org/abs/2501.01234", "https://arxiv.org:444/abs/2501.01234",
    "https://api.biorxiv.org/details/biorxiv/2d", "https://doi.org/10.1101/123456",
])
def test_unsupported_article_when_fetching_never_uses_network(url, monkeypatch):
    from zotero_arxiv_daily import figures
    # Given
    def forbidden(*args):
        pytest.fail("unsupported URL reached transport")
    monkeypatch.setattr(figures, "fetch_resource", forbidden)
    # When / Then
    assert figures.fetch_first_figure(paper(url)) is None


@pytest.mark.parametrize("kind,media", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_raster_when_valid_preserves_bytes(kind, media):
    from zotero_arxiv_daily.figure_raster import verified_figure
    from zotero_arxiv_daily.figure_transport import Resource
    # Given
    data = raster(kind)
    # When
    result = verified_figure(Resource(data, media, "https://arxiv.org/x.png"), "Figure 1")
    # Then
    assert result is not None and result.data == data and result.media_type == media


@pytest.mark.parametrize("data,media", [
    (b"<svg/>", "image/png"), (b"%PDF-1.7", "image/jpeg"),
    (raster(), "image/jpeg"), (raster(), "image/svg+xml"),
    (raster(size=(4001, 4000)), "image/png"), (raster()[:40], "image/png"),
    (b"x" * (2 * 1024 * 1024 + 1), "image/png"),
    (b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9", "image/jpeg"),
])
def test_raster_when_spoofed_oversized_or_broken_is_rejected(data, media):
    from zotero_arxiv_daily.figure_raster import verified_figure
    from zotero_arxiv_daily.figure_transport import Resource
    # Given / When / Then
    assert verified_figure(Resource(data, media, "https://arxiv.org/x.png"), "Figure 1") is None


def test_biorxiv_when_labeled_composite_has_safe_relative_srcset():
    from zotero_arxiv_daily.figure_parser import first_image, article_url
    # Given
    url = "https://www.biorxiv.org/content/10.1101/2025.01.01.123456v1.full.pdf"
    body = b'''<div class="fig" id="F1"><div class="fig-caption">Fig. 1. Results</div>
    <img srcset="/content/fig1.jpg 1x, /content/fig1.large.jpg 2x"></div>'''
    # When / Then
    article = article_url(url)
    assert article == url.removesuffix(".pdf")
    assert article is not None
    assert first_image(body, article) == (
        "https://www.biorxiv.org/content/fig1.jpg", "Fig. 1. Results")


def test_expected_transport_failure_when_fetching_is_optional(monkeypatch):
    from zotero_arxiv_daily import figures
    # Given
    def fail(url, deadline):
        raise TimeoutError
    monkeypatch.setattr(figures, "fetch_resource", fail)
    # When / Then
    assert figures.fetch_first_figure(paper()) is None


def test_png_when_decompressed_content_exceeds_dimensions_is_rejected():
    from zotero_arxiv_daily.figure_raster import verified_figure
    from zotero_arxiv_daily.figure_transport import Resource
    # Given: valid CRCs, tiny dimensions, oversized deflate output.
    data = raster(size=(1, 1))
    payload = b"IDAT" + zlib.compress(b"x" * 1_000_000)
    chunk = (len(payload) - 4).to_bytes(4) + payload + zlib.crc32(payload).to_bytes(4)
    bomb = data[:33] + chunk + data[-12:]
    # When / Then
    assert verified_figure(Resource(bomb, "image/png", "https://arxiv.org/x.png"), "Figure 1") is None


@pytest.mark.parametrize("source", ["https://evil.test/x.png", "http://arxiv.org/x.png", "data:image/png;base64,x", "//127.0.0.1/x.png", "x.pdf"])
def test_image_when_source_is_unsafe_or_vector_is_rejected(source):
    from zotero_arxiv_daily.figure_parser import first_image
    # Given
    body = f'<figure><img src="{source}"><figcaption>Figure 1: Results</figcaption></figure>'
    # When / Then
    assert first_image(body.encode(), "https://arxiv.org/html/2501.01234") is None


def test_html_when_oversized_is_rejected_before_parse():
    from zotero_arxiv_daily.figure_parser import first_image
    # Given / When / Then
    assert first_image(b"x" * (2 * 1024 * 1024 + 1), "https://arxiv.org/html/2501.01234") is None
