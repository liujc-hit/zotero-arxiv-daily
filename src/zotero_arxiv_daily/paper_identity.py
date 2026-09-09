"""Derive stable, namespaced identities for paper records."""

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final, NewType, Protocol, TypeVar
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit

from .identifiers import normalize_doi


PaperIdentity = NewType("PaperIdentity", str)

_HTTP_SCHEMES: Final = frozenset({"http", "https"})
_DOI_RESOLVER_HOSTS: Final = frozenset({"doi.org", "dx.doi.org"})
_ARXIV_HOST: Final = "arxiv.org"
_ARXIV_MODERN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<date>[0-9]{4})\.(?P<sequence>[0-9]{4,5})"
)
_ARXIV_LEGACY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z]+(?:-[a-z]+)*(?:\.[A-Z]{2})?/"
    + r"(?P<date>[0-9]{4})(?P<sequence>[0-9]{3})"
)
_ARXIV_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"v[1-9]\d*$")
_INVALID_PERCENT_ESCAPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"%(?![0-9A-Fa-f]{2})"
)


@dataclass(frozen=True, slots=True)
class _HttpUrl:
    scheme: str
    hostname: str
    port: int | None
    path: str
    query: str


class _PaperInput(Protocol):
    """Structural identity-bearing subset of a paper record."""

    @property
    def doi(self) -> str | None: ...

    @property
    def url(self) -> str: ...

    @property
    def pdf_url(self) -> str | None: ...


_PaperT = TypeVar("_PaperT", bound=_PaperInput)


def _split_http_url(value: str | None) -> _HttpUrl | None:
    if value is None or not value:
        return None
    if any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    ):
        return None

    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    hostname = parts.hostname
    if scheme not in _HTTP_SCHEMES or hostname is None:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if "\\" in parts.netloc or parts.netloc.endswith(":"):
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return _HttpUrl(
        scheme=scheme,
        hostname=hostname,
        port=port,
        path=parts.path,
        query=parts.query,
    )


def _has_default_origin_port(url: _HttpUrl) -> bool:
    return (
        url.port is None
        or (url.scheme == "http" and url.port == 80)
        or (url.scheme == "https" and url.port == 443)
    )


def _has_valid_percent_triplets(value: str) -> bool:
    return _INVALID_PERCENT_ESCAPE_PATTERN.search(value) is None


def _doi_from_url(value: str) -> str | None:
    parts = _split_http_url(value)
    if (
        parts is None
        or parts.hostname.lower() not in _DOI_RESOLVER_HOSTS
        or not _has_default_origin_port(parts)
        or not _has_valid_percent_triplets(parts.path)
    ):
        return None
    try:
        decoded_path = unquote_to_bytes(parts.path).decode("utf-8")
    except UnicodeDecodeError:
        return None
    return normalize_doi(decoded_path.removeprefix("/"))


def _has_valid_arxiv_month(date: str) -> bool:
    return 1 <= int(date[2:]) <= 12


def _is_modern_arxiv_id(value: str) -> bool:
    matched = _ARXIV_MODERN_PATTERN.fullmatch(value)
    if matched is None:
        return False

    date_text = matched.group("date")
    sequence = matched.group("sequence")
    if not _has_valid_arxiv_month(date_text) or int(sequence) == 0:
        return False

    date = int(date_text)
    return (704 <= date <= 1412 and len(sequence) == 4) or (
        date >= 1501 and len(sequence) == 5
    )


def _is_legacy_arxiv_id(value: str) -> bool:
    matched = _ARXIV_LEGACY_PATTERN.fullmatch(value)
    if matched is None:
        return False

    date_text = matched.group("date")
    date = int(date_text)
    sequence = int(matched.group("sequence"))
    return (
        _has_valid_arxiv_month(date_text)
        and sequence > 0
        and (date >= 9107 or date <= 703)
    )


def _is_arxiv_id(value: str) -> bool:
    return _is_modern_arxiv_id(value) or _is_legacy_arxiv_id(value)


def _arxiv_from_url(value: str | None) -> str | None:
    parts = _split_http_url(value)
    if (
        parts is None
        or parts.hostname.lower() != _ARXIV_HOST
        or not _has_default_origin_port(parts)
    ):
        return None

    if parts.path.startswith("/abs/"):
        identifier = parts.path.removeprefix("/abs/")
    elif parts.path.startswith("/pdf/"):
        identifier = parts.path.removeprefix("/pdf/").removesuffix(".pdf")
    else:
        return None

    versionless = _ARXIV_VERSION_PATTERN.sub("", identifier)
    return versionless if _is_arxiv_id(versionless) else None


def _normalize_url(value: str) -> str | None:
    parts = _split_http_url(value)
    if parts is None or not all(
        _has_valid_percent_triplets(component)
        for component in (parts.path, parts.query)
    ):
        return None

    scheme = parts.scheme
    hostname = parts.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parts.port
    is_default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    authority = host if port is None or is_default_port else f"{host}:{port}"
    return urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))


def normalize_paper_identity(value: str | None) -> PaperIdentity | None:
    """Accept canonical namespaced identity text without rewriting it."""
    if value is None:
        return None

    if value.startswith("doi:"):
        payload = value.removeprefix("doi:")
        normalized_doi = normalize_doi(payload)
        return PaperIdentity(value) if normalized_doi == payload else None

    if value.startswith("arxiv:"):
        payload = value.removeprefix("arxiv:")
        return PaperIdentity(value) if _is_arxiv_id(payload) else None

    if value.startswith("url:"):
        payload = value.removeprefix("url:")
        normalized_url = _normalize_url(payload)
        if normalized_url != payload:
            return None
        if _doi_from_url(payload) is not None or _arxiv_from_url(payload) is not None:
            return None
        return PaperIdentity(value)

    return None


def paper_identity(paper: _PaperInput) -> PaperIdentity | None:
    """Return the highest-precedence canonical identity for a paper."""
    normalized_doi = normalize_doi(paper.doi)
    if normalized_doi is not None:
        return PaperIdentity(f"doi:{normalized_doi}")

    resolver_doi = _doi_from_url(paper.url)
    if resolver_doi is not None:
        return PaperIdentity(f"doi:{resolver_doi}")

    url_arxiv = _arxiv_from_url(paper.url)
    if url_arxiv is not None:
        return PaperIdentity(f"arxiv:{url_arxiv}")

    pdf_arxiv = _arxiv_from_url(paper.pdf_url)
    if pdf_arxiv is not None:
        return PaperIdentity(f"arxiv:{pdf_arxiv}")

    normalized_url = _normalize_url(paper.url)
    return (
        PaperIdentity(f"url:{normalized_url}")
        if normalized_url is not None
        else None
    )


def paper_identities(papers: Iterable[_PaperInput]) -> frozenset[PaperIdentity]:
    """Extract unique canonical identities, excluding unidentifiable papers."""
    return frozenset(
        identity
        for paper in papers
        if (identity := paper_identity(paper)) is not None
    )


def filter_sent_paper_candidates(
    papers: Iterable[_PaperT],
    sent_identities: Collection[str],
) -> list[_PaperT]:
    """Keep papers not represented by a canonical identity in sent state."""
    normalized_sent = frozenset(
        identity
        for value in sent_identities
        if (identity := normalize_paper_identity(value)) is not None
    )
    return [
        paper
        for paper in papers
        if (identity := paper_identity(paper)) is None
        or identity not in normalized_sent
    ]


__all__: Final[tuple[str, ...]] = (
    "PaperIdentity",
    "filter_sent_paper_candidates",
    "normalize_paper_identity",
    "paper_identities",
    "paper_identity",
)
