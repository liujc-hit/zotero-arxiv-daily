"""IEEE Xplore one-attempt abstract adapter with a run-scoped quota breaker."""

from threading import Event, Lock
from typing import Final, final

from loguru import logger

from ..identifiers import normalize_doi
from ..retriever.crossref_metadata import (
    clean_crossref_text as _clean_text,
    json_items as _items,
    json_mapping as _mapping,
)
from .pacing import ProviderTiming, StartPacer
from .settings import IeeeSettings
from .transport import (
    HttpResponse,
    ProviderName,
    ProviderRequest,
    parse_json,
    request_once,
)

_IEEE_ENDPOINT: Final = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
_IEEE_USER_AGENT: Final = "zotero-arxiv-daily"
_IEEE_QUOTA_EXHAUSTED: Final = 418
_IEEE_TRIP_THRESHOLD: Final = 5


@final
class IEEEAdapter:
    """Retrieve one exact DOI from the IEEE Xplore API, stopping on quota 418."""

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
        self._quota_available = Event()
        self._quota_available.set()
        self._request_lock = Lock()
        self._consecutive_rejections = 0

    def _observe_quota(self, response: HttpResponse) -> None:
        if response.status_code != _IEEE_QUOTA_EXHAUSTED:
            self._consecutive_rejections = 0
            return
        self._consecutive_rejections += 1
        if self._consecutive_rejections >= _IEEE_TRIP_THRESHOLD:
            self._quota_available.clear()
            logger.warning(
                "IEEE quota exhausted after {} consecutive rejections; disabled for this run",
                _IEEE_TRIP_THRESHOLD,
            )

    def fetch_abstract(self, doi: str) -> str | None:
        if not self._settings.available:
            return None
        target = normalize_doi(doi)
        if target is None:
            return None
        with self._request_lock:
            if not self._quota_available.is_set():
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
                self._observe_quota,
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
