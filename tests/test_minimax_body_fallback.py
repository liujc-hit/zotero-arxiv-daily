"""MiniMax compatibility uses one response, tool first, without JSON repair."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import TYPE_CHECKING

import httpx
from openai import APIStatusError
import pytest

from tests.llm_fakes import _Message, make_recording_client
from tests.test_bilingual_generation import payload
from tests.test_llm_diagnostics import (
    SECRET, Chat, Choice, Client, Completion, Resource, fields, logs as logs, paper,
)
from zotero_arxiv_daily.bilingual_generation import generate_bilingual_digest
from zotero_arxiv_daily.llm import LlmValue, SdkArgument, StructuredOutput, _request_llm
from zotero_arxiv_daily.paper_presentation import PaperPresentation

if TYPE_CHECKING:
    from loguru import Message


def params() -> dict[str, LlmValue]:
    return {"api_mode": "chat_completion", "generation_kwargs": {"model": "MiniMax-M3"}}


@pytest.mark.parametrize("tools", [(), (("other_tool", "{}"),)])
@pytest.mark.parametrize("whitespace", [False, True])
def test_accepts_complete_body_when_matching_tool_absent(logs: list[Message], tools: tuple[tuple[str, str], ...], whitespace: bool) -> None:
    # Given
    body = json.dumps(payload())
    content = f" \n{body}\t " if whitespace else body
    client, calls = make_recording_client(content, tool_calls=tools)
    original = paper()
    # When
    result = generate_bilingual_digest(original, client, params())
    # Then
    assert result.title.en == "Generated title"
    assert original.tldr == "English summary" and original.affiliations == ["Example University"]
    assert len(calls) == 1 and logs == []


@pytest.mark.parametrize("output", ["paper_digest", "bilingual_paper_digest"])
@pytest.mark.parametrize("choice", ["auto", "none", "required", {"type": "function", "function": {"name": "bilingual_paper_digest"}}])
def test_preserves_explicit_tool_choice(logs: list[Message], output: StructuredOutput, choice: LlmValue) -> None:
    # Given
    config = params()
    config["generation_kwargs"] = {"model": "MiniMax-M3", "tool_choice": choice, "response_format": {"type": "json_object"}}
    saved = deepcopy(config)
    client, calls = make_recording_client("complete body")
    # When
    result = _request_llm(client, config, [], structured_output=output)
    # Then
    assert calls[0]["tool_choice"] == choice and "response_format" not in calls[0]
    assert config == saved and len(calls) == 1
    assert result == "complete body" and logs == []


@pytest.mark.parametrize("output", ["paper_digest", "bilingual_paper_digest"])
def test_omits_tool_choice_when_unset(logs: list[Message], output: StructuredOutput) -> None:
    # Given
    client, calls = make_recording_client("body")
    config = params()
    saved = deepcopy(config)
    # When
    result = _request_llm(client, config, [], structured_output=output)
    # Then
    assert result == "body" and len(calls) == 1
    assert "tool_choice" not in calls[0] and config == saved and logs == []


@pytest.mark.parametrize("output", ["paper_digest", "bilingual_paper_digest"])
@pytest.mark.parametrize("arguments", ["", "{", "{}", " \n{\"selected\":true}\t "])
def test_matching_tool_arguments_win_even_when_invalid(output: StructuredOutput, arguments: str) -> None:
    # Given
    client, calls = make_recording_client(json.dumps(payload()), tool_calls=((output, arguments), (output, "second")))
    # When
    result = _request_llm(client, params(), [], structured_output=output)
    # Then
    assert result == arguments and len(calls) == 1


def test_matching_valid_tool_wins_over_different_valid_body(logs: list[Message]) -> None:
    # Given
    tool = json.dumps(payload() | {"title_en": "Tool title"})
    client, calls = make_recording_client(json.dumps(payload()), tool_calls=(("bilingual_paper_digest", tool),))
    # When
    result = generate_bilingual_digest(paper(), client, params())
    # Then
    assert result.title.en == "Tool title" and len(calls) == 1 and logs == []


@pytest.mark.parametrize("output", ["paper_digest", "bilingual_paper_digest"])
@pytest.mark.parametrize("body", [" \n{\"untouched\":true}\t ", "```json\n{}\n```"])
def test_raw_body_is_unchanged_without_matching_tool(output: StructuredOutput, body: str) -> None:
    # Given
    client, calls = make_recording_client(body)
    # When
    result = _request_llm(client, params(), [], structured_output=output)
    # Then
    assert result == body and len(calls) == 1


@pytest.mark.parametrize("arguments,reason", [("", "invalid_json"), ("{", "invalid_json"), ("{}", "schema_validation")])
def test_fallback_when_matching_tool_is_bad_despite_valid_body(logs: list[Message], arguments: str, reason: str) -> None:
    # Given
    client, calls = make_recording_client(json.dumps(payload()), tool_calls=(("bilingual_paper_digest", arguments),))
    # When
    result = generate_bilingual_digest(paper(), client, params())
    # Then
    assert result == PaperPresentation() and len(calls) == 1
    assert fields(logs)["reason"] == reason


@pytest.mark.parametrize("case", [
    (None, "missing_tool"), ("", "missing_tool"), (" \t\n", "missing_tool"),
    ("```json\n" + json.dumps(payload()) + "\n```", "invalid_json"),
    ("Explanation: " + json.dumps(payload()), "invalid_json"),
    (json.dumps(payload()) + " trailing", "invalid_json"),
    (json.dumps(payload()) + json.dumps(payload()), "invalid_json"),
    (json.dumps(payload())[:-1], "invalid_json"),
    ("[]", "schema_validation"), ("null", "schema_validation"), ("{}", "schema_validation"),
    (json.dumps(payload() | {"title_en": 42}), "schema_validation"),
    (json.dumps(payload() | {"title_en": ""}), "schema_validation"),
    (json.dumps(payload() | {"title_en": "x" * 2049}), "schema_validation"),
    (json.dumps(payload() | {"abstract_zh": ""}), "schema_validation"),
    (json.dumps(payload() | {"unrequested": "value"}), "schema_validation"),
    (json.dumps(payload() | {SECRET: SECRET}), "schema_validation"),
])
def test_rejects_entire_unusable_body_without_repair(logs: list[Message], case: tuple[str | None, str]) -> None:
    # Given
    body, reason = case
    client, calls = make_recording_client(body)
    original = paper()
    # When
    result = generate_bilingual_digest(original, client, params())
    # Then
    assert result == PaperPresentation() and len(calls) == 1
    assert original.tldr == original.abstract and original.affiliations is None
    assert fields(logs)["reason"] == reason


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_finish_reason_retained_when_body_is_invalid(logs: list[Message], finish: str) -> None:
    # Given
    calls: list[int] = []

    def create(**_kwargs: SdkArgument) -> Completion:
        calls.append(1)
        return Completion((Choice(_Message("{"), finish),))

    client = Client(chat=Chat(Resource(create)))
    # When
    result = generate_bilingual_digest(paper(), client, params())
    # Then
    assert result == PaperPresentation() and len(calls) == 1
    visible = fields(logs)
    assert (visible["reason"], visible["finish_reason"]) == ("invalid_json", finish)


def test_no_retry_when_provider_rejects_explicit_tool_choice(logs: list[Message]) -> None:
    # Given
    config = params()
    config["generation_kwargs"] = {"model": "MiniMax-M3", "tool_choice": "required"}
    saved = deepcopy(config)
    failure = APIStatusError(SECRET, response=httpx.Response(400, request=httpx.Request("POST", f"https://invalid.test/{SECRET}")), body={SECRET: SECRET})
    client, calls = make_recording_client(failure=failure)
    # When
    result = generate_bilingual_digest(paper(), client, config)
    # Then
    assert result == PaperPresentation() and len(calls) == 1
    assert calls[0]["tool_choice"] == "required" and config == saved
    visible = fields(logs)
    assert (visible["reason"], visible["http_status"]) == ("http_error", "400")


@pytest.mark.parametrize("body,expected", [
    ('{"tldr":"Body summary","affiliations":[]}', "Body summary"),
    ('```json\n{"tldr":"Body summary","affiliations":[]}\n```', SECRET),
    ('{"tldr":"Body summary","affiliations":[]}{}', SECRET),
    ('{"tldr":"Body summary","affiliations":[],"extra":true}', SECRET),
    ('{"tldr":42,"affiliations":[]}', SECRET),
])
def test_legacy_digest_still_validates_entire_body(logs: list[Message], body: str, expected: str) -> None:
    # Given
    original = paper()
    client, calls = make_recording_client(body)
    # When
    summary, affiliations = original.generate_tldr_and_affiliations(client, params())
    # Then
    assert summary == expected and len(calls) == 1
    assert affiliations == ([] if expected == "Body summary" else None)
    assert len(logs) == (0 if expected == "Body summary" else 1)
