"""IEEE Xplore one-attempt abstract adapter."""

from typing import Final, final

from ..identifiers import normalize_doi
from ..retriever.crossref_metadata import (
    clean_crossref_text as _clean_text,
    json_items as _items,
    json_mapping as _mapping,
)
from .pacing import ProviderTiming, StartPacer
from .settings import IeeeSettings
from .transport import (
    ProviderName,
    ProviderRequest,
    parse_json,
    request_once,
)

_IEEE_ENDPOINT: Final = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
_IEEE_USER_AGENT: Final = "zotero-arxiv-daily"


@final
class IEEEAdapter:
    """Retrieve one exact DOI from the IEEE Xplore API."""

    def __init__(
        self,
        settings: IeeeSettings,
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
                provider=ProviderName.IEEE,
                url=_IEEE_ENDPOINT,
                params={
                    "apikey": (self._settings.api_key or "").strip(),
                    "doi": doi,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": _IEEE_USER_AGENT,
                },
            ),
            self._pacer,
        )
        if response is None or (payload := parse_json(response, ProviderName.IEEE)) is None:
            return None
        for value in _items(payload.get("articles")):
            article = _mapping(value)
            article_doi = article.get("doi")
            normalized = normalize_doi(article_doi if isinstance(article_doi, str) else None)
            if normalized == target and (abstract := _clean_text(article.get("abstract"))):
                return abstract
        return None


__all__: Final[tuple[str, ...]] = ("IEEEAdapter",)
