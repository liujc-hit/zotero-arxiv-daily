"""Production-visible fallback diagnostics must contain only bounded metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
from threading import Barrier
from typing import TYPE_CHECKING

import httpx
from loguru import logger
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from pydantic_core import PydanticCustomError
import pytest

from tests.llm_fakes import _Message, llm_params, make_recording_client
from tests.test_bilingual_generation import payload
from zotero_arxiv_daily.bilingual_generation import InvalidBilingualDigestError, generate_bilingual_digest
from zotero_arxiv_daily.llm import ApiMode, LlmValue, SdkArgument, _Response as LlmResponse, _request_llm
from zotero_arxiv_daily.paper_presentation import PaperPresentation
from zotero_arxiv_daily.protocol import Paper

if TYPE_CHECKING:
    from loguru import Message

SECRET = "PRIVATE_SENTINEL_7e92"
PRODUCTION_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"


@pytest.fixture
def logs() -> Iterator[list[Message]]:
    messages: list[Message] = []
    sink = logger.add(messages.append, format=PRODUCTION_FORMAT, level="INFO")
    try:
        yield messages
    finally:
        logger.remove(sink)


def paper() -> Paper:
    return Paper("arxiv", SECRET, [], SECRET, f"https://invalid.test/{SECRET}")


def fields(messages: list[Message]) -> dict[str, str]:
    assert len(messages) == 1
    message = messages[0]
    assert SECRET not in str(message)
    assert SECRET not in repr(message.record["extra"])
    assert message.record["exception"] is None
    assert len(str(message).splitlines()) == 1
    parsed = dict(item.split("=", 1) for item in message.record["message"].split() if "=" in item)
    assert set(parsed) <= {"category", "reason", "exception_type", "finish_reason", "http_status", "validation_field", "validation_type"}
    assert str(message).count("validation_field=") <= 1
    return parsed


@pytest.mark.parametrize("failure,reason,status", [
    (TimeoutError(SECRET), "timeout", "-"),
    (APITimeoutError(request=httpx.Request("POST", f"https://invalid.test/{SECRET}")), "timeout", "-"),
    (APIConnectionError(message=SECRET, request=httpx.Request("POST", f"https://invalid.test/{SECRET}")), "transport", "-"),
    *[(APIStatusError(SECRET, response=httpx.Response(code, request=httpx.Request("POST", f"https://invalid.test/{SECRET}")), body={SECRET: SECRET}), "http_error", str(code)) for code in (400, 429)],
    (type(SECRET, (RuntimeError,), {})(SECRET), "other", "-"),
])
def test_safe_warning_when_request_fails(logs: list[Message], failure: Exception, reason: str, status: str) -> None:
    # Given
    client, calls = make_recording_client(failure=failure)
    original = paper()
    # When
    result = generate_bilingual_digest(original, client, llm_params("response"))
    # Then
    visible = fields(logs)
    assert visible["reason"] == reason
    assert visible["http_status"] == status
    assert visible["finish_reason"] == "unknown"
    assert visible["exception_type"] == ("unknown" if reason == "other" else type(failure).__name__)
    assert result == PaperPresentation() and original.tldr == SECRET
    assert original.affiliations is None and len(calls) == 1


@pytest.mark.parametrize("body,reason,field,kind", [
    ("{}", "schema_validation", "title_en", "missing"),
    ("{" + SECRET, "invalid_json", "-", "json_invalid"),
    (json.dumps(payload() | {"title_en": ""}), "schema_validation", "title_en", "string_too_short"),
    (json.dumps({k: v for k, v in payload().items() if k != "abstract_en"}), "schema_validation", "abstract_en", "missing"),
    (json.dumps(payload() | {SECRET: SECRET}), "schema_validation", "other", "extra_forbidden"),
    (json.dumps(payload() | {"abstract_zh": ""}), "schema_validation", "-", "-"),
    (" " * 100001, "bounds", "-", "-"),
])
def test_safe_warning_when_payload_fails(logs: list[Message], body: str, reason: str, field: str, kind: str) -> None:
    # Given
    client, calls = make_recording_client(body)
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("chat_completion"))
    # Then
    visible = fields(logs)
    assert (visible["reason"], visible.get("validation_field", "-"), visible.get("validation_type", "-")) == (reason, field, kind)
    assert result == PaperPresentation() and len(calls) == 1


def test_missing_tool_when_valid_content_is_ignored(logs: list[Message]) -> None:
    # Given
    client, calls = make_recording_client(json.dumps(payload()))
    params: dict[str, LlmValue] = {"generation_kwargs": {"model": "MiniMax-M3"}}
    # When
    result = generate_bilingual_digest(paper(), client, params)
    # Then
    assert fields(logs)["reason"] == "missing_tool"
    assert result == PaperPresentation() and len(calls) == 1


def test_bounds_when_source_is_too_long(logs: list[Message]) -> None:
    # Given
    original = paper()
    original.abstract = SECRET * 24000
    client, calls = make_recording_client()
    # When
    result = generate_bilingual_digest(original, client, llm_params("response"))
    # Then
    assert fields(logs)["reason"] == "bounds"
    assert result == PaperPresentation() and calls == []


@dataclass(frozen=True, slots=True)
class Choice:
    message: _Message
    finish_reason: LlmValue


@dataclass(frozen=True, slots=True)
class Completion:
    choices: tuple[Choice, ...]


@dataclass(frozen=True, slots=True)
class IncompleteDetails:
    reason: LlmValue


@dataclass(frozen=True, slots=True)
class Response:
    output_text: str
    incomplete_details: IncompleteDetails | None = None


@dataclass(frozen=True, slots=True)
class Resource[ResponseT]:
    create: Callable[..., ResponseT]


@dataclass(frozen=True, slots=True)
class Chat:
    completions: Resource[Completion]


def resource[ResponseT](response: ResponseT) -> Resource[ResponseT]:
    def create(**_kwargs: SdkArgument) -> ResponseT:
        return response

    return Resource(create)


@dataclass(frozen=True, slots=True)
class Client:
    chat: Chat = Chat(resource(Completion(())))
    responses: Resource[LlmResponse] = resource(Response(""))


@pytest.mark.parametrize("finish,expected", [("length", "length"), ("stop", "stop"), ("tool_calls", "tool_calls"), ("content_filter", "content_filter"), (SECRET, "unknown"), ({SECRET: SECRET}, "unknown")])
def test_finish_reason_when_chat_payload_is_invalid(logs: list[Message], finish: LlmValue, expected: str) -> None:
    # Given
    response = Completion((Choice(_Message("{" + SECRET), finish),))
    client = Client(chat=Chat(resource(response)))
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("chat_completion"))
    # Then
    assert fields(logs)["finish_reason"] == expected
    assert result == PaperPresentation()


@pytest.mark.parametrize("reason,expected", [("max_output_tokens", "length"), ("content_filter", "content_filter"), (SECRET, "unknown"), ([], "unknown")])
def test_finish_reason_when_response_is_incomplete(logs: list[Message], reason: LlmValue, expected: str) -> None:
    # Given
    response = Response("{" + SECRET, IncompleteDetails(reason))
    client = Client(responses=resource(response))
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    assert fields(logs)["finish_reason"] == expected
    assert result == PaperPresentation()


def test_other_when_choices_are_empty(logs: list[Message]) -> None:
    # Given
    client = Client()
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("chat_completion"))
    # Then
    assert fields(logs)["reason"] == "other"
    assert result == PaperPresentation()


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
def test_no_warning_when_success_or_legacy_transport(logs: list[Message], mode: ApiMode) -> None:
    # Given
    client, calls = make_recording_client(json.dumps(payload()))
    params = llm_params(mode)
    saved = deepcopy(params)
    # When
    result = generate_bilingual_digest(paper(), client, params)
    # Then
    assert result.title.en == "Generated title" and logs == []
    assert params == saved and len(calls) == 1


@pytest.mark.parametrize("mode", ["chat_completion", "response"])
def test_legacy_request_when_diagnostics_are_omitted(logs: list[Message], mode: ApiMode) -> None:
    # Given
    client, calls = make_recording_client(SECRET)
    params = llm_params(mode)
    # When
    result = _request_llm(client, params, [], structured_output="paper_digest")
    # Then
    assert result == SECRET and len(calls) == 1 and logs == []


def test_metadata_isolated_when_requests_overlap(logs: list[Message]) -> None:
    # Given
    barrier = Barrier(2)

    def run(finish: str) -> PaperPresentation:
        response = Completion((Choice(_Message("{"), finish),))

        def create(**_kwargs: SdkArgument) -> Completion:
            _ = barrier.wait(timeout=5)
            return response

        client = Client(chat=Chat(Resource(create)))
        return generate_bilingual_digest(paper(), client, llm_params("chat_completion"))

    # When
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("length", "stop")))
    # Then
    assert results == [PaperPresentation(), PaperPresentation()]
    assert len(logs) == 2
    assert {fields([message])["finish_reason"] for message in logs} == {"length", "stop"}


@pytest.mark.parametrize("status", [True, 99, 600])
def test_unknown_status_when_http_metadata_is_invalid(logs: list[Message], status: int) -> None:
    # Given
    failure = APIStatusError(SECRET, response=httpx.Response(400, request=httpx.Request("POST", "https://invalid.test")), body={SECRET: SECRET})
    failure.status_code = status
    client, calls = make_recording_client(failure=failure)
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    assert fields(logs)["http_status"] == "-"
    assert result == PaperPresentation() and len(calls) == 1


def test_other_labels_when_validation_metadata_is_untrusted(logs: list[Message]) -> None:
    # Given
    failure = ValidationError.from_exception_data(SECRET, [{
        "type": PydanticCustomError(SECRET, SECRET), "loc": (SECRET,), "input": SECRET,
    }])
    client, calls = make_recording_client(failure=failure)
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    visible = fields(logs)
    assert (visible["validation_field"], visible["validation_type"]) == ("other", "other")
    assert result == PaperPresentation() and len(calls) == 1


def test_other_when_local_error_has_unknown_code(logs: list[Message]) -> None:
    # Given
    client, calls = make_recording_client(failure=InvalidBilingualDigestError(SECRET))
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    assert fields(logs)["reason"] == "other"
    assert result == PaperPresentation() and len(calls) == 1


def test_bounds_when_source_exceeds_token_budget(logs: list[Message], monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setattr("zotero_arxiv_daily.bilingual_generation._MAX_PROMPT_TOKENS", 0)
    client, calls = make_recording_client()
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    assert fields(logs)["reason"] == "bounds"
    assert result == PaperPresentation() and calls == []


class BrokenMetadataResponse:
    output_text: str = json.dumps(payload())

    @property
    def incomplete_details(self) -> IncompleteDetails:
        raise RuntimeError(SECRET)


def test_success_when_optional_metadata_accessor_fails(logs: list[Message]) -> None:
    # Given
    client = Client(responses=resource(BrokenMetadataResponse()))
    # When
    result = generate_bilingual_digest(paper(), client, llm_params("response"))
    # Then
    assert result.title.en == "Generated title" and logs == []
