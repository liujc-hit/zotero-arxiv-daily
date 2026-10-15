"""Conservative article identity and explicitly labeled first-figure parsing."""

from dataclasses import dataclass
import re
from urllib.parse import urljoin

import lxml.etree as etree
from lxml import html

from .figure_transport import LIMIT, FigureFetchError, checked_url

_ARXIV = re.compile(r"/(?:abs|html|pdf)/((?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z][a-z.-]+/\d{2}(?:0[1-9]|1[0-2])\d{3})(?:v[1-9]\d*)?)(?:\.pdf)?")
_BIO = re.compile(r"/content/(10\.1101/(?:\d{4}\.\d{2}\.\d{2}\.)?\d+(?:v[1-9]\d*)?)(?:\.full(?:\.pdf)?)?")
_LABEL = re.compile(r"^(?:Figure|Fig\.?)\s*1(?=\s|[:.\u2014\u2013-]|$)(?!\.\d|\s*[a-z]\))", re.I)


@dataclass(frozen=True, slots=True)
class FigureImage:
    url: str
    caption: str


def article_url(url: str) -> str | None:
    """Derive HTML only from an allowlisted, syntactically valid article ID."""
    try:
        parts = checked_url(url)
    except (FigureFetchError, ValueError):
        return None
    host = (parts.hostname or "").removeprefix("www.")
    if host == "arxiv.org":
        found = _ARXIV.fullmatch(parts.path)
        return f"https://arxiv.org/html/{found[1]}" if found else None
    found = _BIO.fullmatch(parts.path)
    return f"https://www.{host}/content/{found[1]}.full" if found else None


def first_image(data: bytes, article: str) -> tuple[str, str] | None:
    """Require exactly one Figure 1 container with exactly one raster image."""
    if not data or len(data) > LIMIT:
        return None
    root = html.fromstring(data, parser=html.HTMLParser(no_network=True, recover=True))
    candidates: list[FigureImage] = []
    for node in root.iter():
        classes = set(node.get("class", "").split())
        if node.tag != "figure" and not classes.intersection({"ltx_figure", "fig"}):
            continue
        captions = [child for child in node.iterdescendants()
                    if child.tag == "figcaption" or set(child.get("class", "").split()).intersection({"ltx_caption", "fig-caption"})]
        if len(captions) != 1:
            continue
        caption = " ".join(captions[0].text_content().split())
        if not _LABEL.match(caption):
            continue
        images = list(node.iter("img"))
        if len(images) != 1 or any(child.tag == "figure" for child in node.iterdescendants()):
            return None
        if any(list(node.iter(tag)) for tag in ("svg", "object", "embed")):
            return None
        image = images[0]
        source = image.get("src", "").strip()
        if not source:
            choices = image.get("srcset", "").split(",")
            parsed = [choice.strip().split() for choice in choices]
            if not parsed or any(len(choice) != 2 or not re.fullmatch(r"\d+(?:\.\d+)?[wx]", choice[1]) for choice in parsed):
                return None
            source = parsed[0][0]
        base = article + "/" if "/html/" in article else article
        url = urljoin(base, source)
        try:
            parts = checked_url(url)
            parent = checked_url(article)
        except (FigureFetchError, ValueError):
            return None
        if (parts.hostname or "").removeprefix("www.") != (parent.hostname or "").removeprefix("www."):
            return None
        if not parts.path.lower().endswith((".png", ".jpg", ".jpeg")):
            return None
        candidates.append(FigureImage(url, caption))
    if len(candidates) != 1:
        return None
    return candidates[0].url, candidates[0].caption


PARSER_ERRORS = (etree.ParserError, etree.XMLSyntaxError)
