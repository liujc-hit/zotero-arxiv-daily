"""Executor persistence contracts after candidate selection and SMTP."""

from omegaconf import DictConfig
import pytest

import zotero_arxiv_daily.executor as executor_module
from zotero_arxiv_daily.paper_identity import PaperIdentity, paper_identities
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.sent_doi_state import SentDoiStateWriteError
from tests.test_executor_sent_doi_state import (
    EXCLUDED_DOI,
    OLD_IDENTITY,
    PINNED_IDENTITY,
    TOP_DOI,
    TOP_IDENTITY,
    RuntimeHarness,
    SmtpFailure,
    StubRetriever,
    make_runtime,
    paper,
)


@pytest.fixture()
def runtime(monkeypatch: pytest.MonkeyPatch) -> RuntimeHarness:
    return make_runtime(monkeypatch)


def test_run_saves_only_old_union_exact_emailed_identities_after_smtp(
    runtime: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: pinned valid/invalid/missing papers, one top pick, and one excluded pick.
    runtime.store.loaded = frozenset({OLD_IDENTITY})
    top = paper("normal top", TOP_DOI, 9.0)
    excluded = paper("normal excluded", EXCLUDED_DOI, 8.0)
    pinned = paper("pin valid", "https://doi.org/10.1000/PINNED", 2.0)
    invalid = paper("pin invalid", "invalid", 1.0)
    missing = paper("pin missing", None, 0.5)
    runtime.executor.config.executor.pin_keywords = ["pin"]
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [top, excluded, pinned, invalid, missing])},
    )
    batches: list[list[Paper]] = []
    list_ids: list[int] = []

    def record_batch(papers: list[Paper], event: str) -> None:
        runtime.events.append(event)
        batches.append(list(papers))
        list_ids.append(id(papers))

    monkeypatch.setattr(
        runtime.executor,
        "_enrich_papers",
        lambda papers: record_batch(papers, "final"),
    )
    monkeypatch.setattr(
        executor_module,
        "render_email",
        lambda papers: record_batch(papers, "render") or "email",
    )

    def record_identities(papers: list[Paper]) -> frozenset[PaperIdentity]:
        record_batch(papers, "identify")
        return paper_identities(papers)

    monkeypatch.setattr(executor_module, "paper_identities", record_identities)
    monkeypatch.setattr(
        executor_module.logger,
        "info",
        lambda message: runtime.events.append("success")
        if message == "Email sent successfully"
        else None,
    )

    # When: SMTP succeeds and state is committed.
    runtime.executor.run()

    # Then: one selected list reaches all consumers and only its identities persist.
    emailed = [pinned, invalid, missing, top]
    assert batches == [emailed, emailed, emailed]
    assert len(set(list_ids)) == 1
    assert runtime.store.saved == [
        frozenset(
            {
                OLD_IDENTITY,
                PINNED_IDENTITY,
                TOP_IDENTITY,
                PaperIdentity("url:https://papers.example/pin-invalid"),
                PaperIdentity("url:https://papers.example/pin-missing"),
            }
        )
    ]
    assert runtime.events.index("smtp") < runtime.events.index("identify")
    assert runtime.events.index("identify") < runtime.events.index("save")
    assert runtime.events.index("save") < runtime.events.index("success")


def test_run_does_not_save_after_empty_corpus(runtime: RuntimeHarness) -> None:
    # Given: Zotero filtering produces no corpus.
    runtime.executor.fetch_zotero_corpus = lambda: []

    # When: Executor exits before retrieval.
    runtime.executor.run()

    # Then: neither SMTP nor state save runs.
    assert "smtp" not in runtime.events
    assert runtime.store.saved == []


def test_run_does_not_save_when_retrieval_is_empty(runtime: RuntimeHarness) -> None:
    # Given: a valid corpus and no configured candidates.

    # When: Executor exits at the no-new-papers branch.
    runtime.executor.run()

    # Then: neither SMTP nor state save runs.
    assert "smtp" not in runtime.events
    assert runtime.store.saved == []


def test_run_emails_invalid_and_missing_dois_without_saving_state(
    runtime: RuntimeHarness,
) -> None:
    # Given: the final email contains only DOI-less or invalid-DOI papers.
    invalid = paper("invalid", "not-a-doi", 2.0)
    missing = paper("missing", None, 1.0)
    invalid.url = "relative/invalid"
    missing.url = "ftp://papers.example/missing"
    runtime.executor.config.executor.max_paper_num = 2
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [invalid, missing])},
    )

    # When: the eligible papers are delivered successfully.
    runtime.executor.run()

    # Then: SMTP runs, but there is no usable identity snapshot to save.
    assert runtime.reranker.candidates == [invalid, missing]
    assert "smtp" in runtime.events
    assert runtime.store.saved == []


def test_run_does_not_save_when_no_candidate_survives_selection(
    runtime: RuntimeHarness,
) -> None:
    # Given: the only candidate falls below the relevance floor.
    runtime.executor.config.executor.min_score = 2.0
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [paper("below floor", TOP_DOI, 1.0)])},
    )

    # When: Executor exits after reranking.
    runtime.executor.run()

    # Then: neither SMTP nor state save runs.
    assert "smtp" not in runtime.events
    assert runtime.store.saved == []


def test_run_does_not_save_when_smtp_fails(
    runtime: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one selected DOI and an SMTP failure.
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [paper("selected", TOP_DOI)])},
    )

    def fail_smtp(config: DictConfig, content: str) -> None:
        del config, content
        runtime.events.append("smtp")
        raise SmtpFailure

    monkeypatch.setattr(executor_module, "send_email", fail_smtp)
    monkeypatch.setattr(
        executor_module,
        "paper_identities",
        lambda papers: pytest.fail("paper identities derived before SMTP returned"),
    )

    # When: delivery fails.
    with pytest.raises(SmtpFailure):
        runtime.executor.run()

    # Then: the exception propagates without marking any paper identity sent.
    assert runtime.events[-1] == "smtp"
    assert runtime.store.saved == []


def test_run_propagates_post_send_save_failure_without_success_log(
    runtime: RuntimeHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: SMTP succeeds, but committing the emailed identity fails.
    runtime.store.loaded = frozenset({OLD_IDENTITY})
    runtime.store.save_failure = SentDoiStateWriteError()
    setattr(
        runtime.executor,
        "retrievers",
        {"source": StubRetriever(lambda: [paper("selected", TOP_DOI)])},
    )
    monkeypatch.setattr(
        executor_module.logger,
        "info",
        lambda message: runtime.events.append("success")
        if message == "Email sent successfully"
        else None,
    )

    # When: post-delivery state persistence fails.
    with pytest.raises(SentDoiStateWriteError):
        runtime.executor.run()

    # Then: email preceded the failed exact-union save and success was not logged.
    assert runtime.store.saved == [frozenset({OLD_IDENTITY, TOP_IDENTITY})]
    assert runtime.events.index("smtp") < runtime.events.index("save")
    assert "success" not in runtime.events
