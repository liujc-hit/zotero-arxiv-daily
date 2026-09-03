"""Typed OpenAI-compatible fakes for LLM transport tests."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from threading import Lock
from typing import Final

from zotero_arxiv_daily.llm import ApiMode, LlmValue, SdkArgument

type RecordedRequest = dict[str, SdkArgument]

VALID_DIGEST: Final = json.dumps(
    {"tldr": "A valid digest.", "affiliations": ["Example University"]}
)


@dataclass(frozen=True, slots=True)
class _Function:
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class _ToolCall:
    function: _Function


@dataclass(frozen=True, slots=True)
class _Message:
    content: str | None
    tool_calls: tuple[_ToolCall, ...] | None = None


@dataclass(frozen=True, slots=True)
class _Choice:
    message: _Message


@dataclass(frozen=True, slots=True)
class _ChatCompletion:
    choices: tuple[_Choice, ...]


@dataclass(frozen=True, slots=True)
class _Response:
    output_text: str


@dataclass(frozen=True, slots=True)
class _CreateResource[ResponseT]:
    create: Callable[..., ResponseT]


@dataclass(frozen=True, slots=True)
class _Chat:
    completions: _CreateResource[_ChatCompletion]


@dataclass(frozen=True, slots=True)
class RecordingLlmClient:
    chat: _Chat
    responses: _CreateResource[_Response]


def make_recording_client(
    content: str | None = VALID_DIGEST,
    failure: Exception | None = None,
    *,
    tool_calls: tuple[tuple[str, str], ...] = (),
) -> tuple[RecordingLlmClient, list[RecordedRequest]]:
    """Create a client that records each SDK request before returning or failing."""
    calls: list[RecordedRequest] = []
    lock = Lock()
    chat_tool_calls = tuple(
        _ToolCall(_Function(name=name, arguments=arguments))
        for name, arguments in tool_calls
    )

    def record(request: RecordedRequest) -> None:
        with lock:
            calls.append(request)
        if failure is not None:
            raise failure

    def create_chat(**kwargs: SdkArgument) -> _ChatCompletion:
        record(kwargs)
        return _ChatCompletion(
            (_Choice(_Message(content, chat_tool_calls or None)),)
        )

    def create_response(**kwargs: SdkArgument) -> _Response:
        record(kwargs)
        return _Response(content or "")

    return (
        RecordingLlmClient(
            chat=_Chat(_CreateResource(create_chat)),
            responses=_CreateResource(create_response),
        ),
        calls,
    )


def llm_params(
    api_mode: ApiMode, language: str = "English"
) -> Mapping[str, LlmValue]:
    generation_kwargs: dict[str, LlmValue] = {
        "model": "gpt-4o-mini",
        "max_tokens": 1024,
    }
    return {
        "api_mode": api_mode,
        "language": language,
        "generation_kwargs": generation_kwargs,
    }
