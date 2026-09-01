"""Typed Crossref work parsing shared by retrieval and enrichment."""

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..identifiers import normalize_issn
from .crossref_client import JsonObject, JsonValue


_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"<[^>]*>")
_WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
_SPACE_BEFORE_PUNCTUATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\s+([,.;:!?])"
)
_WORK_TYPE_PREPRINT_STATUS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        "journal-article": False,
        "posted-content": True,
        "preprint": True,
        "proceedings-article": False,
    }
)


@dataclass(frozen=True, slots=True)
class CrossrefPublicationMetadata:
    abstract: str
    publisher: str | None
    issns: tuple[str, ...]
    is_preprint: bool | None


def json_mapping(value: JsonValue | None) -> Mapping[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def json_items(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def clean_crossref_text(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        return ""
    unescaped = html.unescape(value)
    normalized = _WHITESPACE_PATTERN.sub(" ", _TAG_PATTERN.sub(" ", unescaped)).strip()
    return _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", normalized)


def crossref_title(work: Mapping[str, JsonValue]) -> str:
    title_value = work.get("title")
    if isinstance(title_value, str):
        return clean_crossref_text(title_value)
    for value in json_items(title_value):
        if title := clean_crossref_text(value):
            return title
    return ""


def parse_crossref_publication_metadata(
    work: Mapping[str, JsonValue],
) -> CrossrefPublicationMetadata:
    normalized_issns: list[str] = []
    for value in json_items(work.get("ISSN")):
        if not isinstance(value, str):
            continue
        normalized = normalize_issn(value)
        if normalized is not None and normalized not in normalized_issns:
            normalized_issns.append(normalized)

    work_type = work.get("type")
    is_preprint = (
        _WORK_TYPE_PREPRINT_STATUS.get(work_type.strip().casefold())
        if isinstance(work_type, str)
        else None
    )
    return CrossrefPublicationMetadata(
        abstract=clean_crossref_text(work.get("abstract")),
        publisher=clean_crossref_text(work.get("publisher")) or None,
        issns=tuple(normalized_issns),
        is_preprint=is_preprint,
    )


def parse_crossref_work_payload(
    payload: JsonObject,
) -> CrossrefPublicationMetadata | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    return parse_crossref_publication_metadata(message)


__all__: Final[tuple[str, ...]] = (
    "CrossrefPublicationMetadata",
    "clean_crossref_text",
    "crossref_title",
    "json_items",
    "json_mapping",
    "parse_crossref_publication_metadata",
    "parse_crossref_work_payload",
)
