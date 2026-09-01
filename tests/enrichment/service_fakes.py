"""State-recording fakes shared by enrichment service tests."""

from collections.abc import Callable
from threading import Lock
from typing import final

from zotero_arxiv_daily.enrichment.service import EnrichmentAdapters
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.retriever.crossref_client import JsonObject


type CrossrefResponder = Callable[[str], JsonObject]


@final
class RecordingCrossref:
    """Record DOI calls while delegating deterministic payload construction."""

    def __init__(self, responder: CrossrefResponder) -> None:
        self.calls: list[str] = []
        self._lock = Lock()
        self._responder = responder

    def get_work(self, doi: str) -> JsonObject:
        with self._lock:
            self.calls.append(doi)
        return self._responder(doi)


@final
class RecordingAdapter:
    """Record selected-provider calls and return a configurable abstract."""

    def __init__(self, result: str | None) -> None:
        self.calls: list[str] = []
        self.result = result
        self._lock = Lock()

    def fetch_abstract(self, doi: str) -> str | None:
        with self._lock:
            self.calls.append(doi)
        return self.result


@final
class AdapterProbe:
    """Expose four explicit adapters without a registry or fallback chain."""

    def __init__(self) -> None:
        self.pubmed = RecordingAdapter("pubmed abstract")
        self.ieee = RecordingAdapter("ieee abstract")
        self.elsevier = RecordingAdapter("elsevier abstract")
        self.springer = RecordingAdapter("springer abstract")

    def bundle(self) -> EnrichmentAdapters:
        return EnrichmentAdapters(
            pubmed=self.pubmed,
            ieee=self.ieee,
            elsevier=self.elsevier,
            springer=self.springer,
        )


def blank_published_paper() -> Paper:
    return Paper(
        source="test",
        title="A paper",
        authors=["Ada Lovelace"],
        abstract="",
        url="https://example.test/paper",
        doi="10.5555/example",
        is_preprint=False,
    )
