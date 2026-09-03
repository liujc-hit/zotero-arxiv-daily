"""Focused tests for one-call paper generation and final-paper enrichment."""

from collections.abc import Mapping
import json
from threading import Barrier, Lock
from typing import Literal

import pytest
import tiktoken

from tests import llm_params, make_recording_client
from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.final_enrichment import FullTextRetriever
from zotero_arxiv_daily.llm import ApiMode, LlmClient, LlmValue
from zotero_arxiv_daily.protocol import Paper


NO_AFFILIATIONS_DIGEST = json.dumps(
    {"tldr": "Abstract-only digest.", "affiliations": []}
)
NO_CONTENT_TLDR = (
    "Failed to generate TLDR. Neither full text nor abstract is provided"
)


MALFORMED_DIGESTS = (
    "not JSON",
    '```json\n{"tldr":"x","affiliations":[]}\n```',
    'prefix {"tldr":"x","affiliations":[]}',
    json.dumps(["not", "an", "object"]),
    json.dumps({"tldr": "missing affiliations"}),
    json.dumps({"tldr": "x", "affiliations": [], "extra": True}),
    json.dumps({"tldr": 42, "affiliations": []}),
    json.dumps({"tldr": "  ", "affiliations": []}),
    json.dumps({"tldr": "x", "affiliations": {"name": "University"}}),
    json.dumps({"tldr": "x", "affiliations": ["University", 42]}),
)


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
@pytest.mark.parametrize("content", MALFORMED_DIGESTS)
def test_malformed_digest_falls_back_after_exactly_one_call(
    api_mode: ApiMode, content: str
) -> None:
    # Given
    client, calls = make_recording_client(content)
    paper = make_sample_paper()

    # When
    result = paper.generate_tldr_and_affiliations(client, llm_params(api_mode))

    # Then
    assert result == (paper.abstract, None)
    assert paper.tldr == paper.abstract
    assert paper.affiliations is None
    assert len(calls) == 1


def test_minimax_m3_tool_digest_sets_both_fields_after_exactly_one_call() -> None:
    # Given
    digest = json.dumps(
        {
            "tldr": "  Tool-generated digest.  ",
            "affiliations": [" University B ", "University A", "University B"],
        }
    )
    client, calls = make_recording_client(
        content=None,
        tool_calls=(("paper_digest", digest),),
    )
    paper = make_sample_paper()
    params: dict[str, LlmValue] = {
        "api_mode": "chat_completion",
        "thinking": "disabled",
        "generation_kwargs": {
            "model": "MiniMax-M3",
            "max_tokens": 16384,
        },
    }

    # When
    result = paper.generate_tldr_and_affiliations(client, params)

    # Then
    assert result == (
        "Tool-generated digest.",
        ["University B", "University A"],
    )
    assert paper.tldr == "Tool-generated digest."
    assert paper.affiliations == ["University B", "University A"]
    assert len(calls) == 1


def test_minimax_m3_missing_digest_tool_falls_back_once_with_redacted_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from zotero_arxiv_daily import paper_generation

    logs: list[tuple[str, tuple[str, ...]]] = []

    def record_warning(message: str, *args: str) -> None:
        logs.append((message, args))

    monkeypatch.setattr(
        paper_generation.logger,
        "warning",
        record_warning,
    )
    client, calls = make_recording_client(
        content=json.dumps(
            {"tldr": "response-content-secret", "affiliations": []}
        ),
        tool_calls=(("other_tool", "tool-arguments-secret"),),
    )
    paper = make_sample_paper(
        source="safe-source",
        url="https://secret.invalid/paper-token",
        doi="10.secret/doi-token",
        full_text="full-content-secret",
    )
    params: dict[str, LlmValue] = {
        "api_mode": "chat_completion",
        "thinking": "disabled",
        "generation_kwargs": {
            "model": "MiniMax-M3",
            "max_tokens": 16384,
        },
    }

    # When
    result = paper.generate_tldr_and_affiliations(client, params)

    # Then
    rendered = " ".join(
        str(value) for message, args in logs for value in (message, *args)
    )
    assert result == (paper.abstract, None)
    assert paper.tldr == paper.abstract
    assert paper.affiliations is None
    assert len(calls) == 1
    assert "paper_digest" in rendered
    assert "safe-source" in rendered
    assert "JSONDecodeError" in rendered
    for secret in (
        "response-content-secret",
        "tool-arguments-secret",
        "paper-token",
        "doi-token",
        "full-content-secret",
    ):
        assert secret not in rendered


class SecretSdkError(RuntimeError):
    pass


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_request_failure_falls_back_once_without_logging_sensitive_content(
    monkeypatch: pytest.MonkeyPatch, api_mode: ApiMode
) -> None:
    # Given
    from zotero_arxiv_daily import paper_generation

    logs = []
    monkeypatch.setattr(
        paper_generation.logger,
        "warning",
        lambda message, *args: logs.append((message, args)),
    )
    client, calls = make_recording_client(
        failure=SecretSdkError("response-body-secret")
    )
    paper = make_sample_paper(
        source="safe-source",
        url="https://secret.invalid/paper-token",
        doi="10.secret/doi-token",
        full_text="full-content-secret",
    )

    # When
    result = paper.generate_tldr_and_affiliations(client, llm_params(api_mode))

    # Then
    rendered = " ".join(
        str(value) for message, args in logs for value in (message, *args)
    )
    assert result == (paper.abstract, None)
    assert len(calls) == 1
    assert "paper_digest" in rendered
    assert "safe-source" in rendered
    assert "SecretSdkError" in rendered
    for secret in (
        "response-body-secret",
        "paper-token",
        "doi-token",
        "full-content-secret",
    ):
        assert secret not in rendered


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_blank_abstract_and_full_text_skip_sdk_with_historical_tldr(
    api_mode: ApiMode,
) -> None:
    # Given
    client, calls = make_recording_client()
    paper = make_sample_paper(abstract=" \n", full_text="\t")

    # When
    result = paper.generate_tldr_and_affiliations(client, llm_params(api_mode))

    # Then
    assert result == (NO_CONTENT_TLDR, None)
    assert paper.tldr == NO_CONTENT_TLDR
    assert paper.affiliations is None
    assert calls == []


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_abstract_only_input_makes_one_combined_request(api_mode: ApiMode) -> None:
    # Given
    client, calls = make_recording_client(NO_AFFILIATIONS_DIGEST)
    paper = make_sample_paper(full_text=None)

    # When
    result = paper.generate_tldr_and_affiliations(client, llm_params(api_mode))

    # Then
    assert result == ("Abstract-only digest.", [])
    assert paper.affiliations == []
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("api_mode", "transport_field"),
    [("chat_completion", "messages"), ("response", "input")],
)
def test_complete_user_prompt_is_bounded_to_4000_tokens(
    api_mode: ApiMode, transport_field: Literal["messages", "input"]
) -> None:
    # Given
    sentinel = "FINAL_SENTINEL_MUST_BE_TRUNCATED"
    client, calls = make_recording_client()
    paper = make_sample_paper(
        title="TITLE_INPUT_MARKER",
        abstract="ABSTRACT_INPUT_MARKER",
        full_text="FULL_TEXT_START_MARKER " + ("longword " * 7000) + sentinel,
    )
    params = llm_params(api_mode, language="LANGUAGE_INPUT_MARKER")

    # When
    paper.generate_tldr_and_affiliations(client, params)

    # Then
    transport = calls[0][transport_field]
    assert isinstance(transport, list)
    user_messages = [
        message
        for message in transport
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    prompt = user_messages[0]["content"]
    assert isinstance(prompt, str)
    encoding = tiktoken.encoding_for_model("gpt-4o")
    assert len(encoding.encode(prompt)) <= 4000
    assert "TITLE_INPUT_MARKER" in prompt
    assert "ABSTRACT_INPUT_MARKER" in prompt
    assert "FULL_TEXT_START_MARKER" in prompt
    assert "LANGUAGE_INPUT_MARKER" in prompt
    assert sentinel not in prompt


def _enrich(
    papers: list[Paper],
    retrievers: Mapping[str, FullTextRetriever],
    client: LlmClient,
    workers: int,
    api_mode: ApiMode = "chat_completion",
) -> list[Paper]:
    from zotero_arxiv_daily.final_enrichment import enrich_final_papers

    return enrich_final_papers(
        papers, retrievers, client, llm_params(api_mode), workers
    )


@pytest.mark.parametrize(
    ("workers", "paper_count", "expected_workers"),
    [(-5, 2, 1), (99, 3, 3)],
)
def test_worker_count_is_clamped(
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
    paper_count: int,
    expected_workers: int,
) -> None:
    # Given
    from zotero_arxiv_daily import final_enrichment

    observed_workers = []

    class InlineExecutor:
        def __init__(self, max_workers):
            observed_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def map(self, function, items):
            return map(function, items)

    monkeypatch.setattr(final_enrichment, "ThreadPoolExecutor", InlineExecutor)
    client, _ = make_recording_client()
    papers = [make_sample_paper(title=f"Paper {index}") for index in range(paper_count)]

    # When
    result = _enrich(papers, {}, client, workers)

    # Then
    assert observed_workers == [expected_workers]
    assert result == papers


class OverlapRetriever:
    def __init__(
        self,
        barrier: Barrier | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.barrier = barrier
        self.failure = failure
        self.fetched: list[str] = []
        self.lock = Lock()

    def fetch_full_text(self, paper: Paper) -> str:
        with self.lock:
            self.fetched.append(paper.title)
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        if self.failure is not None:
            raise self.failure
        return f"Full text for {paper.title}"


def test_enrichment_overlaps_workers_and_preserves_input_order() -> None:
    # Given
    retriever = OverlapRetriever(barrier=Barrier(2))
    client, calls = make_recording_client()
    papers = [
        make_sample_paper(title="First", full_text=None),
        make_sample_paper(title="Second", full_text=None),
    ]

    # When
    result = _enrich(papers, {"arxiv": retriever}, client, workers=2)

    # Then
    assert [paper.title for paper in result] == ["First", "Second"]
    assert set(retriever.fetched) == {"First", "Second"}
    assert len(calls) == 2


def test_enrichment_fetches_only_missing_or_blank_full_text() -> None:
    # Given
    retriever = OverlapRetriever()
    client, _ = make_recording_client()
    papers = [
        make_sample_paper(title="Existing", full_text="Already present"),
        make_sample_paper(title="Blank", full_text="  "),
    ]

    # When
    _enrich(papers, {"arxiv": retriever}, client, workers=1)

    # Then
    assert retriever.fetched == ["Blank"]
    assert papers[0].full_text == "Already present"
    assert papers[1].full_text == "Full text for Blank"


class SecretRetrievalError(RuntimeError):
    pass


@pytest.mark.parametrize("api_mode", ["chat_completion", "response"])
def test_full_text_failure_is_redacted_and_generation_continues(
    monkeypatch: pytest.MonkeyPatch, api_mode: ApiMode
) -> None:
    # Given
    from zotero_arxiv_daily import final_enrichment

    logs = []
    monkeypatch.setattr(
        final_enrichment.logger,
        "warning",
        lambda message, *args: logs.append((message, args)),
    )
    retriever = OverlapRetriever(
        failure=SecretRetrievalError("retrieval-response-secret")
    )
    client, calls = make_recording_client()
    paper = make_sample_paper(
        source="safe-source",
        full_text=None,
        url="https://secret.invalid/url-token",
        doi="10.secret/doi-token",
    )

    # When
    result = _enrich([paper], {"safe-source": retriever}, client, workers=4, api_mode=api_mode)

    # Then
    rendered = " ".join(
        str(value) for message, args in logs for value in (message, *args)
    )
    assert result == [paper]
    assert paper.tldr == "A valid digest."
    assert paper.affiliations == ["Example University"]
    assert len(calls) == 1
    assert "safe-source" in rendered
    assert "SecretRetrievalError" in rendered
    for secret in ("retrieval-response-secret", "url-token", "doi-token"):
        assert secret not in rendered
