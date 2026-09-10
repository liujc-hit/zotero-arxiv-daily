"""Concrete one-attempt abstract adapters for routed publication verticals."""

import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from threading import Event, Lock
from typing import Final, final
from urllib.parse import quote

from loguru import logger

from ..identifiers import normalize_doi
from ..retriever.crossref_metadata import (
    clean_crossref_text as _clean_text,
    json_items as _items,
    json_mapping as _mapping,
)
from .ieee import IEEEAdapter
from .pacing import ProviderTiming, StartPacer
from .settings import (
    ElsevierSettings,
    PubMedSettings,
    SpringerSettings,
)
from .transport import (
    HttpResponse,
    ProviderName,
    ProviderRequest,
    parse_json,
    request_once,
)


_ELSEVIER_ENDPOINT: Final = "https://api.elsevier.com/content/abstract/doi"
_SPRINGER_ENDPOINT: Final = "https://api.springernature.com/meta/v2/json"
_PUBMED_ESEARCH_ENDPOINT: Final = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)
_PUBMED_EFETCH_ENDPOINT: Final = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)
_PUBMED_TOOL: Final = "zotero-arxiv-daily"
_ELSEVIER_REMAINING_HEADER: Final = "x-ratelimit-remaining"


def _elsevier_quota_exhausted(headers: Mapping[str, str]) -> bool:
    return any(
        name.casefold() == _ELSEVIER_REMAINING_HEADER and value.strip() == "0"
        for name, value in headers.items()
    )


@final
class ElsevierAdapter:
    """Retrieve one DOI from Elsevier and stop when run quota reaches zero."""

    def __init__(
        self,
        settings: ElsevierSettings,
        *,
        timing: ProviderTiming | None = None,
    ) -> None:
        self._settings = settings
        self._pacer = StartPacer(
            settings.effective_request_rate,
            timing or ProviderTiming(),
        )
        self._quota_available = Event()
        self._quota_available.set()
        self._request_lock = Lock()

    def _observe_quota(self, response: HttpResponse) -> None:
        if _elsevier_quota_exhausted(response.headers):
            self._quota_available.clear()

    def fetch_abstract(self, doi: str) -> str | None:
        if not self._settings.available:
            return None
        with self._request_lock:
            if not self._quota_available.is_set():
                return None
            response = request_once(
                ProviderRequest(
                    provider=ProviderName.ELSEVIER,
                    url=f"{_ELSEVIER_ENDPOINT}/{quote(doi, safe='')}",
                    params={},
                    headers={
                        "Accept": "application/json",
                        "X-ELS-APIKey": (self._settings.api_key or "").strip(),
                    },
                ),
                self._pacer,
                self._observe_quota,
            )
        if response is None or (payload := parse_json(response, ProviderName.ELSEVIER)) is None:
            return None
        retrieval = _mapping(payload.get("abstracts-retrieval-response"))
        coredata = _mapping(retrieval.get("coredata"))
        return _clean_text(coredata.get("dc:description")) or None


@final
class SpringerAdapter:
    """Retrieve one article abstract from the Springer Nature Meta API."""

    def __init__(
        self,
        settings: SpringerSettings,
        *,
        timing: ProviderTiming | None = None,
    ) -> None:
        self._settings = settings
        self._pacer = StartPacer(
            settings.effective_request_rate,
            timing or ProviderTiming(),
        )

    def fetch_abstract(self, doi: str) -> str | None:
        if not self._settings.available:
            return None
        target = normalize_doi(doi)
        if target is None:
            return None
        response = request_once(
            ProviderRequest(
                provider=ProviderName.SPRINGER,
                url=_SPRINGER_ENDPOINT,
                params={
                    "q": f"doi:{doi}",
                    "api_key": (self._settings.api_key or "").strip(),
                    "p": 1,
                    "s": 1,
                },
                headers={"Accept": "application/json"},
            ),
            self._pacer,
        )
        if response is None or (payload := parse_json(response, ProviderName.SPRINGER)) is None:
            return None
        records = _items(payload.get("records"))
        if not records:
            return None
        record = _mapping(records[0])
        if _clean_text(record.get("contentType")).casefold() != "article":
            return None
        record_doi = record.get("doi")
        if normalize_doi(record_doi if isinstance(record_doi, str) else None) != target:
            return None
        return _clean_text(record.get("abstract")) or None


@final
class PubMedAdapter:
    """Resolve a DOI inside PubMed, then fetch at most one matching PMID."""

    def __init__(
        self,
        settings: PubMedSettings,
        *,
        timing: ProviderTiming | None = None,
    ) -> None:
        self._settings = settings
        self._pacer = StartPacer(
            settings.effective_request_rate,
            timing or ProviderTiming(),
        )

    def fetch_abstract(self, doi: str) -> str | None:
        if not self._settings.available:
            return None
        identity: dict[str, str] = {
            "tool": _PUBMED_TOOL,
            "email": (self._settings.contact_email or "").strip(),
        }
        api_key = (self._settings.api_key or "").strip()
        if api_key:
            identity["api_key"] = api_key
        search_response = request_once(
            ProviderRequest(
                provider=ProviderName.PUBMED,
                url=_PUBMED_ESEARCH_ENDPOINT,
                params={
                    "db": "pubmed",
                    "term": f"{doi}[doi]",
                    "retmode": "json",
                    "retmax": 1,
                    **identity,
                },
                headers={"Accept": "application/json"},
            ),
            self._pacer,
        )
        if search_response is None:
            return None
        payload = parse_json(search_response, ProviderName.PUBMED)
        if payload is None:
            return None
        ids = _items(_mapping(payload.get("esearchresult")).get("idlist"))
        pmid = ids[0].strip() if ids and isinstance(ids[0], str) else ""
        if not pmid:
            return None
        fetch_response = request_once(
            ProviderRequest(
                provider=ProviderName.PUBMED,
                url=_PUBMED_EFETCH_ENDPOINT,
                params={
                    "db": "pubmed",
                    "id": pmid,
                    "retmode": "xml",
                    **identity,
                },
                headers={"Accept": "application/xml"},
            ),
            self._pacer,
        )
        if fetch_response is None:
            return None
        try:
            root = ElementTree.fromstring(fetch_response.content)
        except ElementTree.ParseError:
            logger.warning("PubMed invalid XML")
            return None
        sections = (
            _clean_text("".join(node.itertext()))
            for node in root.findall(".//AbstractText")
        )
        return " ".join(section for section in sections if section) or None


__all__: Final[tuple[str, ...]] = (
    "ElsevierAdapter", "IEEEAdapter", "PubMedAdapter", "SpringerAdapter"
)
