"""Real arXiv relative paths and conservative offline article reconciliation."""

from time import monotonic

import pytest

from zotero_arxiv_daily import figures
from zotero_arxiv_daily.figure_parser import first_image
from zotero_arxiv_daily.figure_transport import Resource
from zotero_arxiv_daily.protocol import Paper
from tests.test_figure_transport import bind_local, local_server
from tests.test_figures import raster


@pytest.mark.parametrize("article,source,expected", [
    ("2408.00714", "2408.00714v2/teaser_ai.png",
     "https://arxiv.org/html/2408.00714v2/teaser_ai.png"),
    ("2510.00001", "2510.00001v1/example-of-semantic-mapping.png",
     "https://arxiv.org/html/2510.00001v1/example-of-semantic-mapping.png"),
])
def test_revision_prefixed_image_when_resolving_real_arxiv_markup(
    article: str, source: str, expected: str,
) -> None:
    # Given: the single-image/caption structure observed in the public articles.
    document = f'''<figure id="S1.F1" class="ltx_figure">
    <img src="{source}" class="ltx_graphics" alt="Refer to caption">
    <figcaption class="ltx_caption ltx_centering">
    <span class="ltx_tag ltx_tag_figure"><span class="ltx_text">Figure 1</span>: </span>
    <span class="ltx_text">Original research figure.</span></figcaption></figure>'''
    # When
    selected = first_image(document.encode(), f"https://arxiv.org/html/{article}")
    # Then: normal document-relative resolution must not duplicate the article ID.
    assert selected == (expected, "Figure 1: Original research figure.")


def test_revision_prefixed_image_when_fetched_through_real_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the correctly resolved original image exists on the server.
    data = raster()
    document = b'''<figure id="S1.F1" class="ltx_figure">
    <img src="2408.00714v2/teaser_ai.png">
    <figcaption class="ltx_caption"><span class="ltx_tag">Figure 1: </span>
    Original research figure.</figcaption></figure>'''
    routes = {
        "/html/2408.00714": (200, {"Content-Type": "text/html"}, document),
        "/html/2408.00714v2/teaser_ai.png": (200, {"Content-Type": "image/png"}, data),
        "/html/2408.00714/2408.00714v2/teaser_ai.png": (404, {}, b"missing"),
    }
    paper = Paper("arxiv", "SAM 2", [], "", "https://arxiv.org/abs/2408.00714")
    with local_server(routes) as port:
        bind_local(monkeypatch, port)
        # When
        result = figures.fetch_first_figure(paper)
    # Then
    assert result is not None
    assert (result.data, result.source_url) == (
        data, "https://arxiv.org/html/2408.00714v2/teaser_ai.png",
    )


@pytest.mark.parametrize("identity", [
    ("https://doi.org/10.48550/arXiv.2408.00714", "https://arxiv.org/pdf/2408.00714v2",
     "https://arxiv.org/html/2408.00714v2"),
    ("https://arxiv.org/abs/2408.00714", "https://arxiv.org/pdf/2408.00714v2.pdf",
     "https://arxiv.org/html/2408.00714v2"),
    ("https://arxiv.org/abs/2408.00714v2", "https://arxiv.org/pdf/2408.00714",
     "https://arxiv.org/html/2408.00714v2"),
    ("https://arxiv.org/abs/2408.00714v2", "https://www.arxiv.org/pdf/2408.00714v2",
     "https://arxiv.org/html/2408.00714v2"),
    ("https://arxiv.org/abs/2408.00714v2", "https://127.0.0.1/private.pdf",
     "https://arxiv.org/html/2408.00714v2"),
    ("https://arxiv.org/abs/hep-th/9901001", "https://arxiv.org/pdf/hep-th/9901001v2",
     "https://arxiv.org/html/hep-th/9901001v2"),
])
def test_article_when_primary_and_pdf_identify_one_revision(
    monkeypatch: pytest.MonkeyPatch, identity: tuple[str, str, str],
) -> None:
    # Given: the transport accepts only the one reconciled article and its image.
    primary, pdf, expected = identity
    data = raster()
    urls: list[str] = []

    def fetch(url: str, deadline: float) -> Resource:
        assert 0 < deadline - monotonic() <= 15
        urls.append(url)
        if url == expected:
            return Resource(
                b'<figure><img src="/original.png"><figcaption>Figure 1: Original</figcaption></figure>',
                "text/html", url,
            )
        assert url == "https://arxiv.org/original.png"
        return Resource(data, "image/png", url)

    monkeypatch.setattr(figures, "fetch_resource", fetch)
    paper = Paper("openalex", "SAM 2", [], "", primary, pdf_url=pdf)
    # When
    result = figures.fetch_first_figure(paper)
    # Then
    assert result is not None and result.data == data
    assert urls == [expected, "https://arxiv.org/original.png"]


def test_relative_image_when_document_redirects_uses_final_document_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the canonical article identity permits the www hostname alias.
    data = raster()
    urls: list[str] = []

    def fetch(url: str, deadline: float) -> Resource:
        urls.append(url)
        if len(urls) == 1:
            return Resource(
                b'<figure><img src="2408.00714v2/teaser_ai.png"><figcaption>Figure 1: Original</figcaption></figure>',
                "text/html", "https://www.arxiv.org/html/2408.00714v2",
            )
        return Resource(data, "image/png", url)

    monkeypatch.setattr(figures, "fetch_resource", fetch)
    paper = Paper("arxiv", "SAM 2", [], "", "https://arxiv.org/abs/2408.00714v2")
    # When
    result = figures.fetch_first_figure(paper)
    # Then
    assert result is not None
    assert result.source_url == "https://www.arxiv.org/html/2408.00714v2/teaser_ai.png"
    assert urls == ["https://arxiv.org/html/2408.00714v2", result.source_url]


@pytest.mark.parametrize("primary,pdf", [
    ("https://arxiv.org/abs/2408.00714", "https://arxiv.org/pdf/2510.00001"),
    ("https://arxiv.org/abs/2408.00714v1", "https://arxiv.org/pdf/2408.00714v2"),
    ("https://www.biorxiv.org/content/10.1101/2025.01.01.123456v1",
     "https://arxiv.org/pdf/2408.00714"),
    ("https://doi.org/10.48550/arXiv.2408.00714", "https://127.0.0.1/private.pdf"),
    ("https://doi.org/10.48550/arXiv.2408.00714", "https://arxiv.org/pdf/2408.00714?token=x"),
    ("https://doi.org/10.48550/arXiv.2408.00714", "https://user:pass@arxiv.org/pdf/2408.00714"),
    ("https://doi.org/10.48550/arXiv.2408.00714", None),
])
def test_article_when_identities_conflict_or_pdf_is_unsafe_never_fetches(
    monkeypatch: pytest.MonkeyPatch, primary: str, pdf: str | None,
) -> None:
    # Given
    def forbidden(url: str, deadline: float) -> Resource:
        pytest.fail("untrusted or conflicting identity reached transport")

    monkeypatch.setattr(figures, "fetch_resource", forbidden)
    paper = Paper("openalex", "Title", [], "", primary, pdf_url=pdf)
    # When
    result = figures.fetch_first_figure(paper)
    # Then
    assert result is None
