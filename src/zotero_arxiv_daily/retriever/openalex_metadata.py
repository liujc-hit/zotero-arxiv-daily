"""Parse publication metadata from typed OpenAlex work fields."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..identifiers import normalize_doi, normalize_issn
from .openalex_client import JsonValue


_WORK_TYPE_PREPRINT_STATUS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        "article": False,
        "conference-paper": False,
        "journal-article": False,
        "posted-content": True,
        "preprint": True,
        "proceedings-article": False,
    }
)


@dataclass(frozen=True, slots=True)
class OpenAlexPublicationMetadata:
    doi: str | None
    publisher: str | None
    issns: tuple[str, ...]
    is_preprint: bool | None


def parse_openalex_publication_metadata(
    work: Mapping[str, JsonValue], source: Mapping[str, JsonValue]
) -> OpenAlexPublicationMetadata:
    """Return normalized publication metadata from a typed OpenAlex work."""
    doi_value = work.get("doi")
    publisher_value = source.get("host_organization_name")
    issn_values = source.get("issn")
    raw_issns = issn_values if isinstance(issn_values, list) else []
    normalized_issns: list[str] = []
    for value in (*raw_issns, source.get("issn_l")):
        if not isinstance(value, str):
            continue
        normalized = normalize_issn(value)
        if normalized is not None and normalized not in normalized_issns:
            normalized_issns.append(normalized)

    is_preprint: bool | None = None
    for field in ("type", "type_crossref"):
        work_type = work.get(field)
        if isinstance(work_type, str):
            status = _WORK_TYPE_PREPRINT_STATUS.get(work_type.strip().casefold())
            if status is not None:
                is_preprint = status
                break

    return OpenAlexPublicationMetadata(
        doi=normalize_doi(doi_value if isinstance(doi_value, str) else None),
        publisher=(
            publisher_value.strip()
            if isinstance(publisher_value, str) and publisher_value.strip()
            else None
        ),
        issns=tuple(normalized_issns),
        is_preprint=is_preprint,
    )
