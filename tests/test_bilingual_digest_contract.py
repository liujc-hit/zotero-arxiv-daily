"""Five-field output keeps original abstracts exclusively as source context."""

import json

import pytest

from tests.llm_fakes import make_recording_client
from zotero_arxiv_daily.bilingual_generation import generate_bilingual_digest
from zotero_arxiv_daily.llm import ApiMode, LlmValue
from zotero_arxiv_daily.paper_generation import GENERATION_FAILED_TLDR
from zotero_arxiv_daily.paper_presentation import BilingualText, PaperPresentation
from zotero_arxiv_daily.protocol import Paper


def five_fields() -> dict[str, str | list[str]]:
    return {
        "title_en": "English title", "title_zh": "中文标题",
        "tldr_en": "English summary", "tldr_zh": "中文总结", "affiliations": [],
    }


@pytest.mark.parametrize("route", [
    ("chat_completion", "gpt-4o-mini", False),
    ("response", "gpt-4o-mini", False),
    ("chat_completion", "MiniMax-M3", False),
    ("chat_completion", "MiniMax-M3", True),
])
def test_five_field_digest_keeps_source_abstract_only_as_input(route: tuple[ApiMode, str, bool]) -> None:
    # Given
    mode, model, tool = route
    body = json.dumps(five_fields())
    client, calls = make_recording_client(body, tool_calls=(("bilingual_paper_digest", body),) if tool else ())
    original = Paper("arxiv", "Original title", [], "Complete source abstract.", "url")
    config: dict[str, LlmValue] = {"api_mode": mode, "generation_kwargs": {"model": model}}
    # When
    result = generate_bilingual_digest(original, client, config)
    # Then
    assert result == PaperPresentation(
        title=BilingualText("English title", "中文标题"),
        tldr=BilingualText("English summary", "中文总结"),
    )
    assert result.abstract == BilingualText()
    assert (original.title, original.abstract) == ("Original title", "Complete source abstract.")
    assert len(calls) == 1
    messages = calls[0].get("messages", calls[0].get("input"))
    assert isinstance(messages, list)
    source = messages[1]
    assert isinstance(source, dict)
    content = source["content"]
    assert isinstance(content, str) and original.abstract in content


@pytest.mark.parametrize("route", [
    ("chat_completion", "gpt-4o-mini", False),
    ("response", "gpt-4o-mini", False),
    ("chat_completion", "MiniMax-M3", False),
    ("chat_completion", "MiniMax-M3", True),
])
def test_old_seven_field_response_is_rejected_without_abstract_fallback(route: tuple[ApiMode, str, bool]) -> None:
    # Given
    mode, model, tool = route
    body = json.dumps(five_fields() | {"abstract_en": "English abstract", "abstract_zh": "中文摘要"})
    client, calls = make_recording_client(body, tool_calls=(("bilingual_paper_digest", body),) if tool else ())
    original = Paper("arxiv", "Original title", [], "Source must never become TLDR.", "url")
    config: dict[str, LlmValue] = {"api_mode": mode, "generation_kwargs": {"model": model}}
    # When
    result = generate_bilingual_digest(original, client, config)
    # Then
    assert result == PaperPresentation()
    assert original.tldr == GENERATION_FAILED_TLDR and original.affiliations is None
    assert (original.title, original.abstract) == ("Original title", "Source must never become TLDR.")
    assert len(calls) == 1
