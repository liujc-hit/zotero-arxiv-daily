"""Corpus filtering must preserve selection semantics without disclosing library data."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

import pytest
from loguru import logger
from omegaconf import DictConfig, OmegaConf

from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.retriever.base import BaseRetriever
from zotero_arxiv_daily.sent_doi_state import DisabledSentDoiStateStore

if TYPE_CHECKING:
    from loguru import Message


INCLUDE: Final = "PRIVATE-PATH/研究/**"
IGNORE: Final = "PRIVATE-PATH/研究/PRIVATE-IGNORE/**"
USER_ID: Final = "PRIVATE-USER-ID-72941"
SECRET: Final = "PRIVATE-CREDENTIAL-71932"


@dataclass(frozen=True, slots=True)
class FilterCase:
    include: tuple[str, ...] | None
    ignore: tuple[str, ...] | None
    inputs: tuple[int, ...]
    expected: tuple[int, ...]


def _corpus() -> list[CorpusPaper]:
    return [
        CorpusPaper(
            title=f"PRIVATE-TITLE-{index}",
            abstract=f"PRIVATE-ABSTRACT-{index}",
            added_date=datetime(2026, 1, 1),
            paths=paths,
        )
        for index, paths in enumerate([
            ["PRIVATE-PATH/other", "PRIVATE-PATH/研究/first"],
            ["PRIVATE-PATH/研究/PRIVATE-IGNORE/second"],
            ["PRIVATE-PATH/unselected"],
            ["PRIVATE-PATH/研究/last"],
            [],
        ])
    ]


def _executor(case: FilterCase) -> Executor:
    executor = Executor.__new__(Executor)
    executor.include_path_patterns = list(case.include) if case.include else None
    executor.ignore_path_patterns = list(case.ignore) if case.ignore else None
    executor.config = OmegaConf.create({"zotero": {
        "user_id": USER_ID,
        "api_key": SECRET,
        "include_path": executor.include_path_patterns,
        "ignore_path": executor.ignore_path_patterns,
    }})
    executor.sent_doi_state_store = DisabledSentDoiStateStore()
    return executor


@pytest.fixture
def captured_logs() -> Iterator[list["Message"]]:
    messages: list[Message] = []
    sink = logger.add(messages.append, level="TRACE", format="{message} | {extra}")
    try:
        yield messages
    finally:
        logger.remove(sink)


def _assert_private_values_absent(messages: list["Message"]) -> None:
    private_values = [INCLUDE, IGNORE, USER_ID, SECRET, "PRIVATE-PATH"]
    for paper in _corpus():
        private_values.extend([paper.title, paper.abstract, *paper.paths])
    for message in messages:
        surfaces = (str(message), message.record["message"], repr(message.record["extra"]))
        for surface in surfaces:
            assert all(value not in surface for value in private_values)


@pytest.mark.parametrize("case", [
    pytest.param(FilterCase((INCLUDE,), None, (0, 1, 2, 3, 4), (0, 1, 3)), id="include-only"),
    pytest.param(FilterCase(None, (IGNORE,), (0, 1, 2, 3, 4), (0, 2, 3, 4)), id="ignore-only"),
    pytest.param(FilterCase((INCLUDE,), (IGNORE,), (0, 1, 2, 3, 4), (0, 3)), id="both"),
    pytest.param(FilterCase(None, None, (0, 1, 2, 3, 4), (0, 1, 2, 3, 4)), id="no-filters"),
    pytest.param(FilterCase((), (), (0, 1, 2, 3, 4), (0, 1, 2, 3, 4)), id="empty-rules"),
    pytest.param(FilterCase((INCLUDE,), (IGNORE,), (), ()), id="empty-input"),
    pytest.param(FilterCase((INCLUDE,), (IGNORE,), (1, 2, 4), ()), id="all-filtered"),
    pytest.param(FilterCase(("PRIVATE-PATH/unselected", INCLUDE), None,
                            (3, 2, 0, 1, 4), (3, 2, 0, 1)), id="include-or-root-unicode"),
])
def test_filter_preserves_selection_without_private_logs(
    case: FilterCase, captured_logs: list["Message"],
) -> None:
    # Given a private library with ordered, multi-collection and unfiled papers.
    papers = _corpus()
    corpus = [papers[index] for index in case.inputs]
    original = tuple(corpus)
    executor = _executor(case)

    # When the real filter applies the configured include/ignore rules.
    result = executor.filter_corpus(corpus)

    # Then selection preserves source identity/order and never mutates the input.
    assert tuple(map(id, result)) == tuple(id(papers[index]) for index in case.expected)
    assert tuple(map(id, corpus)) == tuple(map(id, original))
    _assert_private_values_absent(captured_logs)
    if case.include or case.ignore:
        assert len(captured_logs) == 1
        assert captured_logs[0].record["level"].name == "INFO"
        assert captured_logs[0].record["message"] == (
            f"Zotero corpus filter: input={len(case.inputs)} output={len(case.expected)} "
            f"removed={len(case.inputs) - len(case.expected)} "
            f"include_enabled={bool(case.include)} ignore_enabled={bool(case.ignore)}"
        )
        assert captured_logs[0].record["extra"] == {}
    else:
        assert result is corpus
        assert captured_logs == []


@pytest.mark.parametrize("empty_input", [False, True], ids=["all-filtered", "empty-fetch"])
def test_run_stops_privately_when_filtered_corpus_is_empty(
    empty_input: bool, captured_logs: list["Message"], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given synthetic settings and a local fetch that cannot produce selected papers.
    executor = _executor(FilterCase((INCLUDE,), (IGNORE,), (), ()))
    corpus = [] if empty_input else [_corpus()[1]]
    monkeypatch.setattr(executor, "fetch_zotero_corpus", lambda: corpus)
    calls: list[str] = []

    def retrieve(_retrievers: dict[str, BaseRetriever]) -> list[Paper]:
        calls.append("retrieve")
        return []

    def score(_papers: list[Paper]) -> tuple[list[Paper], list[Paper]]:
        calls.append("score")
        return [], []

    def mail(_config: DictConfig, _content: str) -> None:
        calls.append("mail")

    monkeypatch.setattr(
        "zotero_arxiv_daily.executor.retrieve_and_merge", retrieve,
    )
    monkeypatch.setattr(executor, "_select_pinned", score)
    monkeypatch.setattr("zotero_arxiv_daily.executor.send_email", mail)

    # When the real pipeline filters the corpus to empty.
    result = executor.run()

    # Then it returns before retrieval/scoring/mail, with a private-data-free error.
    assert result is None
    assert calls == []
    _assert_private_values_absent(captured_logs)
    assert len(captured_logs) == 2
    errors = [message for message in captured_logs if message.record["level"].name == "ERROR"]
    assert len(errors) == 1
    assert "No zotero papers found" in errors[0].record["message"]
    assert errors[0].record["extra"] == {}
