"""Real HTML comments and processing instructions must not hide eligible figures."""

from typing import Final

import pytest

from zotero_arxiv_daily.figure_parser import first_image
from zotero_arxiv_daily.figures import fetch_first_figure
from zotero_arxiv_daily.protocol import Paper

from tests.test_figure_transport import bind_local, local_server
from tests.test_figures import raster

_ARTICLE: Final = "https://arxiv.org/html/2501.01234v1"
_CAPTION: Final = "Figure 1: Results"
_FIGURE: Final = (
    b'<figure><img src="f.png">'
    b'<figcaption>Figure 1: Results</figcaption></figure>'
)
_NODES: Final = (b"<!-- generated -->", b"<?latexml generated?>")


@pytest.mark.parametrize("marker", _NODES, ids=["comment", "pi"])
@pytest.mark.parametrize("template", [
    b"%s" + _FIGURE,
    _FIGURE + b"%s",
    _FIGURE.replace(b"<figure>", b"<figure>%s"),
    _FIGURE.replace(b"Results", b"Re%ssults"),
], ids=["before", "after", "inside-figure", "inside-caption"])
def test_first_image_when_non_element_nodes_are_present(
    template: bytes, marker: bytes,
) -> None:
    # Given
    body = b"<html><body>" + template % marker + b"</body></html>"
    # When
    result = first_image(body, _ARTICLE)
    # Then
    assert result == ("https://arxiv.org/html/f.png", _CAPTION)


@pytest.mark.parametrize("marker", _NODES, ids=["comment", "pi"])
def test_first_image_when_only_non_element_nodes_are_present(marker: bytes) -> None:
    # Given
    body = b"<html><body>" + marker + b"</body></html>"
    # When
    result = first_image(body, _ARTICLE)
    # Then
    assert result is None


@pytest.mark.parametrize("marker", _NODES, ids=["comment", "pi"])
def test_fetch_first_figure_when_http_html_contains_non_element_nodes(
    marker: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the connection seam changes; parsing and raster validation are real.
    data = raster()
    figure = _FIGURE.replace(b"<figure>", b"<figure>" + marker).replace(
        b"Results", b"Re" + marker + b"sults",
    )
    body = b"<html><body>" + marker + figure + marker + b"</body></html>"
    routes = {
        "/html/2501.01234v1": (200, {"Content-Type": "text/html"}, body),
        "/html/f.png": (200, {"Content-Type": "image/png"}, data),
    }
    paper = Paper("arxiv", "Title", [], "", "https://arxiv.org/abs/2501.01234v1")
    with local_server(routes) as port:
        bind_local(monkeypatch, port)
        # When
        result = fetch_first_figure(paper)
    # Then
    assert result is not None
    assert (result.data, result.media_type, result.caption, result.source_url) == (
        data, "image/png", _CAPTION, "https://arxiv.org/html/f.png",
    )
