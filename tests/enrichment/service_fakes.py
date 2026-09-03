"""State-recording fakes shared by enrichment service tests."""

from collections.abc import Callable
from threading import Barrier, Lock
from typing import final

from zotero_arxiv_daily.enrichment.service import EnrichmentAdapters
from zotero_arxiv_daily.protocol import Paper


type CallObserver = Callable[[str], None]


@final
class RecordingAdapter:
    """Record selected-provider calls and return a configurable abstract."""

    def __init__(
        self,
        result: str | None,
        observer: CallObserver | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.result = result
        self.error: RuntimeError | None = None
        self._lock = Lock()
        self._observer = observer

    def fetch_abstract(self, doi: str) -> str | None:
        with self._lock:
            self.calls.append(doi)
        if self._observer is not None:
            self._observer(doi)
        if self.error is not None:
            raise self.error
        return self.result


@final
class ConcurrentAdapter:
    """Synchronize calls to prove papers share one concurrently used adapter."""

    def __init__(self, parties: int) -> None:
        self.calls: list[str] = []
        self.max_active = 0
        self._active = 0
        self._barrier = Barrier(parties)
        self._lock = Lock()

    def fetch_abstract(self, doi: str) -> str:
        with self._lock:
            self.calls.append(doi)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            _ = self._barrier.wait(timeout=5)
            return "concurrent abstract"
        finally:
            with self._lock:
                self._active -= 1


@final
class AdapterProbe:
    """Expose four adapters and their cross-provider attempt order."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._attempt_lock = Lock()
        self.pubmed = RecordingAdapter(
            "pubmed abstract",
            lambda _doi: self._record_attempt("pubmed"),
        )
        self.ieee = RecordingAdapter(
            "ieee abstract",
            lambda _doi: self._record_attempt("ieee"),
        )
        self.elsevier = RecordingAdapter(
            "elsevier abstract",
            lambda _doi: self._record_attempt("elsevier"),
        )
        self.springer = RecordingAdapter(
            "springer abstract",
            lambda _doi: self._record_attempt("springer"),
        )

    def _record_attempt(self, provider: str) -> None:
        with self._attempt_lock:
            self.attempts.append(provider)

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
