"""Atomic bilingual generation through the real transport and an offline SDK fake."""

import json

import pytest
from omegaconf import OmegaConf
from openai import APIConnectionError
import httpx
from loguru import logger

from tests.llm_fakes import llm_params, make_recording_client
from zotero_arxiv_daily.bilingual_generation import generate_bilingual_digest as generate
from zotero_arxiv_daily.llm import ApiMode
from zotero_arxiv_daily.paper_generation import GENERATION_FAILED_TLDR, NO_CONTENT_TLDR
from zotero_arxiv_daily.paper_presentation import BilingualText, PaperPresentation
from zotero_arxiv_daily.protocol import Paper


def payload() -> dict[str, str | list[str]]:
    return {
        "title_en": "Generated title", "title_zh": "中文标题",
        "tldr_en": "English summary", "tldr_zh": "中文总结",
        "affiliations": ["Example University", "Example University"],
    }


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
@pytest.mark.parametrize("language", ["English", "Chinese", "中文", "zh-CN"])
def test_complete_digest_when_request_succeeds(mode: ApiMode, language: str):
    # Given
    paper = Paper("arxiv", "Original title", ["Author Name"], "Original abstract", "url")
    client, calls = make_recording_client(json.dumps(payload()))
    # When
    result = generate(paper, client, OmegaConf.create(dict(llm_params(mode, language))))
    # Then
    assert result == PaperPresentation(
        title=BilingualText("Generated title", "中文标题"),
        tldr=BilingualText("English summary", "中文总结"),
    )
    assert paper.tldr == ("English summary" if language == "English" else "中文总结")
    assert paper.affiliations == ["Example University"]
    assert (paper.title, paper.abstract, paper.authors) == ("Original title", "Original abstract", ["Author Name"])
    assert paper.presentation is None
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [None, "", "not json", "{}", '[]', 'null'])
def test_fallback_when_response_unusable(bad: str | None):
    # Given
    paper = Paper("arxiv", "Title", [], "  Original abstract  ", "url")
    client, calls = make_recording_client(bad)
    # When
    result = generate(paper, client, llm_params("chat_completion", "Chinese"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert paper.abstract == "  Original abstract  "
    assert paper.affiliations is None
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("title_en", ""), ("title_zh", " "), ("tldr_en", 42),
    ("tldr_zh", "x" * 2001), ("title_en", "x" * 2049),
    ("tldr_zh", ""), ("affiliations", [42]), ("extra", "value"),
    ("affiliations", [" "]), ("affiliations", ["a" * 257]),
    ("affiliations", ["Institution"] * 101), ("tldr_en", True),
])
def test_fallback_when_schema_invalid(field: str, value: str | int | list[int]):
    # Given
    data = payload() | {field: value}
    paper = Paper("arxiv", "Title", [], "Abstract", "url")
    client, calls = make_recording_client(json.dumps(data))
    # When
    result = generate(paper, client, llm_params("response"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [TimeoutError("secret"), RuntimeError("secret")])
def test_fallback_when_sdk_raises(failure: Exception):
    # Given
    paper = Paper("arxiv", "Title", [], "", "url", full_text="Full text")
    client, calls = make_recording_client(failure=failure)
    # When
    result = generate(paper, client, llm_params("response"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert paper.affiliations is None
    assert len(calls) == 1


def test_skip_when_no_content():
    # Given
    paper = Paper("arxiv", "Title", [], " ", "url")
    client, calls = make_recording_client()
    # When
    result = generate(paper, client, llm_params("response"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == NO_CONTENT_TLDR
    assert calls == []


def test_title_and_tldr_when_only_fulltext_available():
    # Given
    paper = Paper("arxiv", "Title", [], "", "url", full_text="Paper body")
    data = payload()
    client, calls = make_recording_client(json.dumps(data))
    # When
    result = generate(paper, client, llm_params("response"))
    # Then
    assert result.abstract == BilingualText()
    assert paper.tldr == "English summary"
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
@pytest.mark.parametrize("example", [
    ("Un estudio sobre robots", "A study of robots", "一项关于机器人的研究"),
    ("An α-particle study at 5 μm", "A study of alpha particles at five micrometres", "五微米尺度的阿尔法粒子研究"),
    ("García and Müller study robots", "A robot study by García and Müller", "García 和 Müller 的机器人研究"),
    ("机器人研究的新方法 Robot-X", "A new method for robotics with Robot-X", "使用 Robot-X 的机器人研究新方法"),
], ids=["spanish-ascii", "scientific-unicode", "accented-proper-names", "mixed-han"])
def test_keeps_provider_translations_when_source_differs(mode: ApiMode, example: tuple[str, str, str]):
    # Given
    source, en, zh = example
    paper = Paper("arxiv", source, ["García", "Müller"], source, "url")
    data = payload() | {"title_en": en, "title_zh": zh}
    client, calls = make_recording_client(json.dumps(data))
    # When
    result = generate(paper, client, llm_params(mode))
    # Then
    assert result.title == BilingualText(en, zh)
    assert result.abstract == BilingualText()
    assert (paper.title, paper.abstract, paper.authors) == (source, source, ["García", "Müller"])
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
@pytest.mark.parametrize("source,translations", [
    ("  García studies α particles.\n", BilingualText("García studies α particles.", "García 研究阿尔法粒子。")),
    ("  机器人研究的新方法 Robot-X\n", BilingualText("A new robotics method with Robot-X", "机器人研究的新方法 Robot-X")),
])
def test_preserves_source_formatting_when_provider_text_matches(mode: ApiMode, source: str, translations: BilingualText):
    # Given
    paper = Paper("arxiv", source, [], source, "url")
    data = payload() | {
        "title_en": translations.en, "title_zh": translations.zh,
    }
    client, calls = make_recording_client(json.dumps(data))
    expected = BilingualText(
        source if translations.en == source.strip() else translations.en,
        source if translations.zh == source.strip() else translations.zh,
    )
    # When
    result = generate(paper, client, llm_params(mode))
    # Then
    assert result.title == expected
    assert result.abstract == BilingualText()
    assert (paper.title, paper.abstract) == (source, source)
    assert len(calls) == 1


def test_fallback_when_source_exceeds_input_budget():
    # Given
    paper = Paper("arxiv", "Title", [], "a" * 24001, "url")
    client, calls = make_recording_client(json.dumps(payload()))
    # When
    result = generate(paper, client, llm_params("response"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert paper.abstract == "a" * 24001
    assert calls == []


def test_complete_source_when_fulltext_exceeds_budget():
    # Given
    paper = Paper("arxiv", "Title", [], "Complete source abstract.", "url", full_text="Body " * 100000)
    client, calls = make_recording_client(json.dumps(payload()))
    # When
    result = generate(paper, client, llm_params("response"))
    # Then: inspect source data, not prompt prose.
    assert result.abstract == BilingualText()
    assert result.title.en == "Generated title"
    messages = calls[0]["input"]
    assert isinstance(messages, list)
    message = messages[1]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, str)
    assert paper.abstract in content
    assert paper.full_text is not None
    assert len(content) < len(paper.full_text)


def test_safe_fallback_when_api_connection_fails():
    # Given
    paper = Paper("arxiv", "Title", [], "Abstract", "url")
    failure = APIConnectionError(message="secret-key", request=httpx.Request("GET", "https://invalid.test"))
    client, calls = make_recording_client(failure=failure)
    logs: list[str] = []
    sink = logger.add(logs.append, format="{message} {extra}")
    # When
    try:
        result = generate(paper, client, llm_params("response"))
    finally:
        logger.remove(sink)
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert len(calls) == 1
    assert logs and all("secret-key" not in line for line in logs)


def test_fallback_when_response_exceeds_size_limit():
    # Given
    paper = Paper("arxiv", "Title", [], "Abstract", "url")
    client, calls = make_recording_client(" " * 100001)
    # When
    result = generate(paper, client, llm_params("chat_completion"))
    # Then
    assert result == PaperPresentation()
    assert paper.tldr == GENERATION_FAILED_TLDR
    assert len(calls) == 1
