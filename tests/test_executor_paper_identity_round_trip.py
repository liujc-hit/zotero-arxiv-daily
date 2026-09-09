"""End-to-end Executor regression for encrypted paper identities."""

import json
from pathlib import Path
from typing import Final

from cryptography.fernet import Fernet
import pytest

from zotero_arxiv_daily.enrichment.pipeline import PipelineEnrichers
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.sent_doi_state import FernetSentDoiStateStore
from tests.test_executor_sent_doi_state import (
    RecordingEnricher,
    StubRetriever,
    make_runtime,
)


_ARXIV_IDENTITY: Final = "arxiv:2609.02003"


def _arxiv_paper(url: str) -> Paper:
    return Paper(
        source="fixture",
        title="Versioned arXiv paper",
        authors=[],
        abstract="Abstract.",
        url=url,
        full_text="Full text.",
        score=1.0,
    )


def test_run_filters_versioned_arxiv_url_across_real_fernet_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two scheduled runs share only an encrypted path and Fernet key.
    key = Fernet.generate_key()
    state_path = tmp_path / "sent-paper-identities.bin"
    first_runtime = make_runtime(monkeypatch)
    first_store = FernetSentDoiStateStore(state_path, key)
    first_runtime.executor.sent_doi_state_store = first_store
    first_paper = _arxiv_paper("https://arxiv.org/abs/2609.02003v1")
    setattr(
        first_runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [first_paper])},
    )

    # When: run one delivers the abs URL and run two retrieves its PDF v2 URL.
    first_runtime.executor.run()
    first_ciphertext = state_path.read_bytes()
    second_runtime = make_runtime(monkeypatch)
    second_store = FernetSentDoiStateStore(state_path, key)
    second_runtime.executor.sent_doi_state_store = second_store
    second_paper = _arxiv_paper("https://arxiv.org/pdf/2609.02003v2.pdf")
    setattr(
        second_runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [second_paper])},
    )
    second_enricher = RecordingEnricher(second_runtime.events)
    second_runtime.executor.pipeline_enrichers = PipelineEnrichers(
        abstract=second_enricher
    )
    second_runtime.executor.run()

    # Then: v2 state is retained and run two filters before enrichment or delivery.
    assert first_runtime.events.count("smtp") == 1
    assert first_store.load() == frozenset({_ARXIV_IDENTITY})
    assert json.loads(Fernet(key).decrypt(first_ciphertext)) == {
        "version": 2,
        "identities": [_ARXIV_IDENTITY],
    }
    assert second_enricher.candidates == []
    assert "pipeline" in second_runtime.events
    assert second_runtime.reranker.candidates == []
    assert "rerank" not in second_runtime.events
    assert "smtp" not in second_runtime.events
    assert state_path.read_bytes() == first_ciphertext
    assert second_store.load() == frozenset({_ARXIV_IDENTITY})
