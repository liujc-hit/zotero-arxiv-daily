"""Best-effort original Figure 1 retrieval; no PDF or screenshot fallback."""

from __future__ import annotations

from http.client import HTTPException
from time import monotonic
from typing import TYPE_CHECKING

from loguru import logger

from .figure_parser import PARSER_ERRORS, article_url, first_image, select_article_url
from .figure_raster import verified_figure
from .figure_transport import FigureFetchError, checked_url, fetch_resource, remaining
from .paper_presentation import ResearchFigure

if TYPE_CHECKING:
    from .protocol import Paper

__all__ = ("fetch_first_figure",)


def fetch_first_figure(paper: Paper) -> ResearchFigure | None:
    """Return unchanged verified raster bytes, or None on optional-source failure.

    Coverage is intentionally limited to explicitly labeled arXiv HTML and
    bioRxiv/medRxiv HTML figures on the same source host. Missing, vector-only,
    separate-panel, unsupported, or ambiguous figures are omitted.
    """
    article = select_article_url(paper.url, paper.pdf_url)
    if article is None:
        return None
    deadline = monotonic() + 15.0
    try:
        document = fetch_resource(article, deadline)
        remaining(deadline)
        if document.media_type not in {"text/html", "application/xhtml+xml"}:
            return None
        if article_url(document.url) != article:
            return None
        selected = first_image(document.data, document.url)
        if selected is None:
            return None
        image_url, caption = selected
        resource = fetch_resource(image_url, deadline)
        # Redirects must stay within this paper's source family as well.
        if (checked_url(resource.url).hostname or "").removeprefix("www.") != (checked_url(article).hostname or "").removeprefix("www."):
            return None
        remaining(deadline)
        result = verified_figure(resource, caption)
        remaining(deadline)
        return result
    except (OSError, HTTPException, ValueError, FigureFetchError, *PARSER_ERRORS):
        logger.debug("Optional first-figure retrieval unavailable")
        return None
