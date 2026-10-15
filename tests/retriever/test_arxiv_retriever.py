"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser
import pytest
import trafilatura

from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout
import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    paper_ids = [e.id.removeprefix("oai:arXiv.org:") for e in new_entries]

    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in new_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return iter(fake_results)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    # convert_to_paper no longer downloads full text (it is fetched lazily after
    # reranking via ArxivRetriever.fetch_full_text), so no network mocking is
    # needed here.

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert set(p.title for p in papers) == set(e.title for e in new_entries)
    # Full text is intentionally left for lazy fetching.
    assert all(p.full_text is None for p in papers)
    assert all(p.is_preprint is True for p in papers)


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]


def test_tar_worker_returns_none_when_source_has_no_tex(monkeypatch):
    # Given a downloaded arXiv source payload that contains no usable TeX.
    monkeypatch.setattr(arxiv_retriever, "_download_file", lambda *_: None)
    monkeypatch.setattr(
        arxiv_retriever,
        "extract_tex_code_from_tar",
        lambda *_args, **_kwargs: None,
    )

    # When source extraction runs, then the expected miss is returned normally.
    result = arxiv_retriever._extract_text_from_tar_worker(
        "https://arxiv.org/src/2609.02003v1",
        "2609.02003v1",
        "PDF-only submission",
    )

    assert result is None


def test_html_worker_returns_none_when_rendering_is_not_found(monkeypatch):
    # Given an arXiv paper without an HTML rendering.
    response = SimpleNamespace(status=404, html=None)
    monkeypatch.setattr(
        trafilatura,
        "fetch_response",
        lambda _url, *, decode: response,
    )
    monkeypatch.setattr(trafilatura, "fetch_url", lambda _url: None)
    monkeypatch.setattr(
        trafilatura,
        "extract",
        lambda *_args, **_kwargs: pytest.fail("404 response must not be extracted"),
    )

    # When optional HTML extraction runs, then the miss is returned normally.
    result = arxiv_retriever._extract_text_from_html_worker(
        "https://arxiv.org/html/2609.02003v1"
    )

    assert result is None


def test_fetch_full_text_does_not_warn_when_pdf_fallback_succeeds(monkeypatch):
    # Given unavailable source and HTML stages followed by successful PDF text.
    stage_results = iter((None, "PDF text"))
    warnings: list[str] = []
    debug_messages: list[str] = []

    def unavailable_html(_url: str) -> None:
        raise ConnectionError("HTML unavailable")

    monkeypatch.setattr(
        arxiv_retriever,
        "_run_with_hard_timeout",
        lambda *_args, **_kwargs: next(stage_results),
    )
    monkeypatch.setattr(
        arxiv_retriever,
        "_extract_text_from_html_worker",
        unavailable_html,
    )
    monkeypatch.setattr(
        arxiv_retriever,
        "logger",
        SimpleNamespace(warning=warnings.append, debug=debug_messages.append),
    )

    # When full-text extraction falls through to PDF, then it is not a warning.
    result = arxiv_retriever.fetch_arxiv_full_text(
        "https://arxiv.org/abs/2609.02003v1",
        "https://arxiv.org/pdf/2609.02003v1",
        "PDF-only submission",
    )

    assert result == "PDF text"
    assert warnings == []


def test_clean_abstract():
    raw_with_prefix = (
        "arXiv:2609.22090v1 Announce Type: new \n"
        "Abstract: An LLM producing the response pattern associated with a human psychological effect..."
    )
    assert arxiv_retriever._clean_abstract(raw_with_prefix) == (
        "An LLM producing the response pattern associated with a human psychological effect..."
    )

    raw_plain = "Simple abstract without prefix."
    assert arxiv_retriever._clean_abstract(raw_plain) == "Simple abstract without prefix."

    raw_with_html_and_inner_abstract = (
        "<p>arXiv:2609.22090v1 Announce Type: new \n"
        "Abstract: In this work, we introduce Abstract: A Benchmark for evaluation.</p>"
    )
    assert arxiv_retriever._clean_abstract(raw_with_html_and_inner_abstract) == (
        "In this work, we introduce Abstract: A Benchmark for evaluation."
    )

    raw_with_inequalities = (
        "<p>arXiv:2609.22090v1 Announce Type: new \n"
        "Abstract: We prove that x < y > 0 and 0 < a < b under condition C.</p>"
    )
    assert arxiv_retriever._clean_abstract(raw_with_inequalities) == (
        "We prove that x < y > 0 and 0 < a < b under condition C."
    )

    raw_with_br = (
        "<p>arXiv:2609.22090v1 Announce Type: new<br>Abstract: This is the body after br tags.</p>"
    )
    assert arxiv_retriever._clean_abstract(raw_with_br) == "This is the body after br tags."


def test_parse_entry_time():
    from datetime import datetime, timezone
    import time
    st = time.strptime("2026-09-23 12:30:45", "%Y-%m-%d %H:%M:%S")
    dt = arxiv_retriever._parse_entry_time(st)
    assert dt == datetime(2026, 9, 23, 12, 30, 45, tzinfo=timezone.utc)
    assert arxiv_retriever._parse_entry_time(None) == datetime.min.replace(tzinfo=timezone.utc)


def test_extract_authors():
    entry_with_author = SimpleNamespace(author="Alice, Bob, Charlie")
    authors = arxiv_retriever._extract_authors(entry_with_author)
    assert [a.name for a in authors] == ["Alice", "Bob", "Charlie"]

    entry_with_authors_list = SimpleNamespace(
        author="",
        authors=[{"name": "David"}, {"name": "Eva"}]
    )
    authors_from_list = arxiv_retriever._extract_authors(entry_with_authors_list)
    assert [a.name for a in authors_from_list] == ["David", "Eva"]

    entry_empty = SimpleNamespace(author="")
    authors_empty = arxiv_retriever._extract_authors(entry_empty)
    assert [a.name for a in authors_empty] == ["Unknown"]


def test_entry_to_arxiv_result():
    import time
    st = time.strptime("2026-09-23 12:00:00", "%Y-%m-%d %H:%M:%S")
    entry = SimpleNamespace(
        id="oai:arXiv.org:2609.22090v1",
        title="  Recognition, Simulation, \n and Refusal  ",
        author="Joy Bose",
        summary="arXiv:2609.22090v1 Announce Type: new \nAbstract: Test summary.",
        link="https://arxiv.org/abs/2609.22090",
        tags=[{"term": "cs.AI"}, {"term": "cs.CL"}],
        published_parsed=st,
        updated_parsed=st,
        arxiv_comment="10 pages",
        arxiv_journal_ref="Nature",
        arxiv_doi="10.1234/test",
    )
    result = arxiv_retriever._entry_to_arxiv_result(entry)
    assert result.title == "Recognition, Simulation, and Refusal"
    assert [a.name for a in result.authors] == ["Joy Bose"]
    assert result.summary == "Test summary."
    assert result.entry_id == "https://arxiv.org/abs/2609.22090"
    assert result.pdf_url == "https://arxiv.org/pdf/2609.22090v1"
    assert result.source_url() == "https://arxiv.org/src/2609.22090v1"
    assert result.get_short_id() == "2609.22090"
    assert result.primary_category == "cs.AI"
    assert result.categories == ["cs.AI", "cs.CL"]
    assert result.comment == "10 pages"
    assert result.journal_ref == "Nature"
    assert result.doi == "10.1234/test"
    assert result.published.year == 2026
    assert result.published.hour == 12
    assert result.updated.year == 2026
    assert result.updated.hour == 12
    assert len(result.links) == 3

    # Verify authoritative arxiv_primary_category takes precedence over category list order
    entry_with_primary = SimpleNamespace(
        id="oai:arXiv.org:2609.22090v1",
        title="Title",
        author="Author",
        summary="Summary",
        link="https://arxiv.org/abs/2609.22090",
        tags=[{"term": "cs.AI"}, {"term": "cs.LG"}],
        arxiv_primary_category={"term": "cs.LG"},
    )
    result_with_primary = arxiv_retriever._entry_to_arxiv_result(entry_with_primary)
    assert result_with_primary.primary_category == "cs.LG"

    # Verify Atom's arxiv_journal_reference field is mapped to journal_ref
    entry_with_journal_ref = SimpleNamespace(
        id="oai:arXiv.org:2609.22090v1",
        title="Title",
        author="Author",
        summary="Summary",
        link="https://arxiv.org/abs/2609.22090",
        arxiv_journal_reference="Phys. Rev. Lett. 120, 012345 (2025)",
    )
    result_with_journal_ref = arxiv_retriever._entry_to_arxiv_result(entry_with_journal_ref)
    assert result_with_journal_ref.journal_ref == "Phys. Rev. Lett. 120, 012345 (2025)"


def test_retrieve_raw_papers_cross_list(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr(config.source.arxiv, "include_cross_list", True)
    retriever = ArxivRetriever(config)
    raw = retriever._retrieve_raw_papers()

    expected_len = len([
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") in {"new", "cross"}
    ])
    assert len(raw) == expected_len


def test_retrieve_raw_papers_debug_mode(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr(config.executor, "debug", True)
    monkeypatch.setattr(config.source.arxiv, "include_cross_list", True)
    retriever = ArxivRetriever(config)
    raw = retriever._retrieve_raw_papers()
    assert len(raw) <= 10


def test_retrieve_raw_papers_retries_on_http_failure(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr(arxiv_retriever, "sleep", lambda _: None)
    calls = []

    def mock_parse_flaky(url):
        calls.append(url)
        if len(calls) < 2:
            return SimpleNamespace(
                status=500,
                feed=SimpleNamespace(title="Server Error"),
                entries=[],
                bozo=False,
            )
        return mock_feedparser

    monkeypatch.setattr(arxiv_retriever.feedparser, "parse", mock_parse_flaky)
    retriever = ArxivRetriever(config)
    raw = retriever._retrieve_raw_papers()
    assert len(calls) == 2
    assert len(raw) > 0
