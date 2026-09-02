"""Strict, redacted parsing of batched PubMed article metadata."""

import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Final

from ..identifiers import normalize_doi, normalize_issn


_PREPRINT_MESH_UI: Final = "D000076942"


class PubMedMetadataError(ValueError):
    """Base class for PubMed metadata that cannot form a domain record."""


class _StaticPubMedMetadataError(PubMedMetadataError):
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)


class PubMedInvalidXmlError(_StaticPubMedMetadataError):
    """Raised when a response is malformed or is not a PubmedArticleSet."""

    message: ClassVar[str] = "PubMed returned invalid XML"


class PubMedMissingPmidError(_StaticPubMedMetadataError):
    """Raised when an article has no nonblank PMID."""

    message: ClassVar[str] = "PubMed article is missing a PMID"


class PubMedMissingTitleError(_StaticPubMedMetadataError):
    """Raised when an article has no nonblank title."""

    message: ClassVar[str] = "PubMed article is missing a title"


@dataclass(frozen=True, slots=True)
class PubMedRecord:
    """Normalized publication metadata from one supported PubMed record."""

    pmid: str
    doi: str | None
    title: str
    authors: tuple[str, ...]
    abstract: str
    journal: str | None
    issns: tuple[str, ...]
    is_preprint: bool | None


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _article_id_doi(identifiers: list[ElementTree.Element]) -> str | None:
    for identifier in identifiers:
        if identifier.get("IdType", "").strip().casefold() != "doi":
            continue
        if normalized := normalize_doi(_text(identifier)):
            return normalized
    return None


def _elocation_doi(identifiers: list[ElementTree.Element]) -> str | None:
    for identifier in identifiers:
        if identifier.get("EIdType", "").strip().casefold() != "doi":
            continue
        validity = identifier.get("ValidYN")
        if validity is not None and validity.strip().casefold() != "y":
            continue
        if normalized := normalize_doi(_text(identifier)):
            return normalized
    return None


def _doi(article: ElementTree.Element) -> str | None:
    return _article_id_doi(
        article.findall("./PubmedData/ArticleIdList/ArticleId")
    ) or _elocation_doi(
        article.findall("./MedlineCitation/Article/ELocationID")
    )


def _book_doi(article: ElementTree.Element) -> str | None:
    return _article_id_doi(
        article.findall("./BookDocument/ArticleIdList/ArticleId")
    ) or _elocation_doi(
        article.findall("./BookDocument/Book/ELocationID")
    )


def _author_names(author_list: ElementTree.Element | None) -> tuple[str, ...]:
    if author_list is None:
        return ()

    names: list[str] = []
    for author in author_list.findall("./Author"):
        collective_name = _text(author.find("./CollectiveName"))
        if collective_name:
            names.append(collective_name)
            continue

        given_name = _text(author.find("./ForeName")) or _text(
            author.find("./Initials")
        )
        personal_name = " ".join(
            part
            for part in (
                given_name,
                _text(author.find("./LastName")),
                _text(author.find("./Suffix")),
            )
            if part
        )
        if personal_name:
            names.append(personal_name)
    return tuple(names)


def _authors(article: ElementTree.Element) -> tuple[str, ...]:
    return _author_names(
        article.find("./MedlineCitation/Article/AuthorList")
    )


def _book_authors(article: ElementTree.Element) -> tuple[str, ...]:
    author_list = article.find("./BookDocument/AuthorList")
    if author_list is None:
        author_list = article.find("./BookDocument/Book/AuthorList")
    return _author_names(author_list)


def _abstract(article: ElementTree.Element) -> str:
    sections = (
        _text(section)
        for section in article.findall(
            "./MedlineCitation/Article/Abstract/AbstractText"
        )
    )
    return " ".join(section for section in sections if section)


def _book_abstract(article: ElementTree.Element) -> str:
    sections = (
        _text(section)
        for section in article.findall("./BookDocument/Abstract/AbstractText")
    )
    return " ".join(section for section in sections if section)


def _journal(article: ElementTree.Element) -> str | None:
    journal_title = _text(article.find("./MedlineCitation/Article/Journal/Title"))
    if journal_title:
        return journal_title
    medline_title = _text(
        article.find("./MedlineCitation/MedlineJournalInfo/MedlineTA")
    )
    return medline_title or None


def _book_journal(article: ElementTree.Element) -> str | None:
    book_title = _text(article.find("./BookDocument/Book/BookTitle"))
    return book_title or None


def _issns(article: ElementTree.Element) -> tuple[str, ...]:
    identifiers = (
        *article.findall("./MedlineCitation/Article/Journal/ISSN"),
        *article.findall("./MedlineCitation/MedlineJournalInfo/ISSNLinking"),
    )
    normalized_issns: list[str] = []
    for identifier in identifiers:
        normalized = normalize_issn(_text(identifier))
        if normalized is not None and normalized not in normalized_issns:
            normalized_issns.append(normalized)
    return tuple(normalized_issns)


def _is_preprint(publication_types: list[ElementTree.Element]) -> bool | None:
    if not publication_types:
        return None
    return any(
        publication_type.get("UI") == _PREPRINT_MESH_UI
        for publication_type in publication_types
    )


def _parse_article(article: ElementTree.Element) -> PubMedRecord:
    pmid = _text(article.find("./MedlineCitation/PMID"))
    if not pmid:
        raise PubMedMissingPmidError from None

    title = _text(article.find("./MedlineCitation/Article/ArticleTitle"))
    if not title:
        raise PubMedMissingTitleError from None

    return PubMedRecord(
        pmid=pmid,
        doi=_doi(article),
        title=title,
        authors=_authors(article),
        abstract=_abstract(article),
        journal=_journal(article),
        issns=_issns(article),
        is_preprint=_is_preprint(
            article.findall(
                "./MedlineCitation/Article/PublicationTypeList/PublicationType"
            )
        ),
    )


def _parse_book_article(article: ElementTree.Element) -> PubMedRecord:
    pmid = _text(article.find("./BookDocument/PMID"))
    if not pmid:
        raise PubMedMissingPmidError from None

    title = _text(article.find("./BookDocument/ArticleTitle")) or _text(
        article.find("./BookDocument/Book/BookTitle")
    )
    if not title:
        raise PubMedMissingTitleError from None

    return PubMedRecord(
        pmid=pmid,
        doi=_book_doi(article),
        title=title,
        authors=_book_authors(article),
        abstract=_book_abstract(article),
        journal=_book_journal(article),
        issns=(),
        is_preprint=_is_preprint(
            article.findall("./BookDocument/PublicationType")
        ),
    )


def parse_pubmed_article_set(payload: bytes) -> tuple[PubMedRecord, ...]:
    """Parse one PubmedArticleSet response without retaining unsafe input."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        raise PubMedInvalidXmlError from None

    if root.tag != "PubmedArticleSet":
        raise PubMedInvalidXmlError from None

    parsers: dict[
        str,
        Callable[[ElementTree.Element], PubMedRecord],
    ] = {
        "PubmedArticle": _parse_article,
        "PubmedBookArticle": _parse_book_article,
    }
    records: list[PubMedRecord] = []
    for publication in root:
        parser = parsers.get(publication.tag)
        if parser is not None:
            records.append(parser(publication))
    return tuple(records)


__all__: Final[tuple[str, ...]] = (
    "PubMedInvalidXmlError",
    "PubMedMetadataError",
    "PubMedMissingPmidError",
    "PubMedMissingTitleError",
    "PubMedRecord",
    "parse_pubmed_article_set",
)
