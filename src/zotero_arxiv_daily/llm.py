"""OpenAI-compatible LLM transport and structured-output configuration."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Final, Literal, Protocol, assert_never

from omegaconf import DictConfig, OmegaConf

type LlmValue = (
    str | int | float | bool | None | list["LlmValue"] | dict[str, "LlmValue"]
)
type ApiMode = Literal["chat_completion", "response"]
type StructuredOutput = Literal["paper_digest"]
type SdkArgument = LlmValue | list[dict[str, str]]


class _FunctionCall(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def arguments(self) -> str: ...


class _ChatToolCall(Protocol):
    @property
    def function(self) -> _FunctionCall: ...


class _ChatMessage(Protocol):
    @property
    def content(self) -> str | None: ...

    @property
    def tool_calls(self) -> Sequence[_ChatToolCall] | None: ...


class _ChatChoice(Protocol):
    @property
    def message(self) -> _ChatMessage: ...


class _ChatCompletion(Protocol):
    @property
    def choices(self) -> Sequence[_ChatChoice]: ...


class _Response(Protocol):
    @property
    def output_text(self) -> str: ...


class _ChatCompletions(Protocol):
    @property
    def create(self) -> Callable[..., _ChatCompletion]: ...


class _Chat(Protocol):
    @property
    def completions(self) -> _ChatCompletions: ...


class _Responses(Protocol):
    @property
    def create(self) -> Callable[..., _Response]: ...


class LlmClient(Protocol):
    """OpenAI-compatible client surfaces consumed by the LLM transport."""

    @property
    def chat(self) -> _Chat: ...

    @property
    def responses(self) -> _Responses: ...

PAPER_DIGEST_JSON_SCHEMA: Final[dict[str, LlmValue]] = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string"},
        "affiliations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tldr", "affiliations"],
    "additionalProperties": False,
}

_MINIMAX_M2_MODELS: Final[frozenset[str]] = frozenset(
    {
        "MiniMax-M2",
        "MiniMax-M2.1",
        "MiniMax-M2.1-highspeed",
        "MiniMax-M2.5",
        "MiniMax-M2.5-highspeed",
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    }
)


class InvalidLlmMappingError(TypeError):
    def __init__(self, path: str) -> None:
        self.path: str = path
        super().__init__(f"{path} must be a mapping")


class UnsupportedThinkingError(ValueError):
    def __init__(self) -> None:
        super().__init__("Unsupported llm.thinking; expected null or 'disabled'.")


class ThinkingCannotBeDisabledError(ValueError):
    def __init__(self, model: str) -> None:
        self.model: str = model
        super().__init__(f"{model} cannot disable thinking; set llm.thinking to null.")


class UnsupportedApiModeError(ValueError):
    def __init__(self, api_mode: LlmValue) -> None:
        self.api_mode: LlmValue = api_mode
        super().__init__(f"Unsupported llm.api_mode: {api_mode}. Expected 'chat_completion' or 'response'.")


def _call_sdk[ResponseT](
    create: Callable[..., ResponseT], **kwargs: SdkArgument
) -> ResponseT:
    return create(**kwargs)


def _resolve_generation_kwargs(
    llm_params: Mapping[str, LlmValue] | DictConfig,
) -> dict[str, LlmValue]:
    generation_source = llm_params.get("generation_kwargs", {})
    if not isinstance(generation_source, (dict, DictConfig)):
        raise InvalidLlmMappingError("llm.generation_kwargs")
    generation_config = OmegaConf.create(generation_source)
    resolved_kwargs = OmegaConf.to_container(generation_config, resolve=True)
    if not isinstance(resolved_kwargs, dict):
        raise InvalidLlmMappingError("llm.generation_kwargs")

    generation_kwargs: dict[str, LlmValue] = {}
    for key, value in resolved_kwargs.items():
        if not isinstance(key, str):
            raise InvalidLlmMappingError("llm.generation_kwargs")
        generation_kwargs[key] = value
    return generation_kwargs


def _strict_paper_digest_config() -> dict[str, LlmValue]:
    return {
        "name": "paper_digest",
        "strict": True,
        "schema": deepcopy(PAPER_DIGEST_JSON_SCHEMA),
    }


def _request_llm(
    openai_client: LlmClient,
    llm_params: Mapping[str, LlmValue] | DictConfig,
    messages: list[dict[str, str]],
    *,
    structured_output: StructuredOutput | None = None,
) -> str:
    """Issue one SDK request without mutating the supplied LLM configuration."""
    api_mode_value = llm_params.get("api_mode", "chat_completion")
    if api_mode_value not in ("chat_completion", "response"):
        raise UnsupportedApiModeError(api_mode_value)
    api_mode: ApiMode = api_mode_value
    generation_kwargs = _resolve_generation_kwargs(llm_params)

    thinking = llm_params.get("thinking")
    if thinking is not None and thinking != "disabled":
        raise UnsupportedThinkingError()
    model_value = generation_kwargs.get("model")
    model = model_value if isinstance(model_value, str) else None
    if thinking == "disabled" and model in _MINIMAX_M2_MODELS:
        raise ThinkingCannotBeDisabledError(model)
    uses_minimax_m3_chat_tool = (
        api_mode == "chat_completion"
        and model == "MiniMax-M3"
        and structured_output == "paper_digest"
    )

    match api_mode:
        case "chat_completion":
            if thinking == "disabled" and model == "MiniMax-M3":
                extra_body = generation_kwargs.get("extra_body", {})
                if not isinstance(extra_body, dict):
                    raise InvalidLlmMappingError("llm.generation_kwargs.extra_body")
                thinking_body = extra_body.get("thinking", {})
                if not isinstance(thinking_body, dict):
                    raise InvalidLlmMappingError(
                        "llm.generation_kwargs.extra_body.thinking"
                    )
                generation_kwargs["extra_body"] = {
                    **extra_body,
                    "thinking": {**thinking_body, "type": "disabled"},
                }
            match structured_output:
                case None:
                    pass
                case "paper_digest":
                    if uses_minimax_m3_chat_tool:
                        _ = generation_kwargs.pop("response_format", None)
                        _ = generation_kwargs.pop("tool_choice", None)
                        paper_digest_tool: dict[str, LlmValue] = {
                            "type": "function",
                            "function": {
                                "name": "paper_digest",
                                "description": "Return the requested paper digest.",
                                "parameters": deepcopy(PAPER_DIGEST_JSON_SCHEMA),
                            },
                        }
                        generation_kwargs["tools"] = [paper_digest_tool]
                    else:
                        generation_kwargs["response_format"] = {
                            "type": "json_schema",
                            "json_schema": _strict_paper_digest_config(),
                        }
                case unreachable:
                    assert_never(unreachable)
            chat_response = _call_sdk(
                openai_client.chat.completions.create,
                messages=messages,
                **generation_kwargs,
            )
            message = chat_response.choices[0].message
            if uses_minimax_m3_chat_tool:
                for tool_call in message.tool_calls or ():
                    if tool_call.function.name == "paper_digest":
                        return tool_call.function.arguments
                return ""
            return message.content or ""

        case "response":
            if thinking == "disabled" and model == "MiniMax-M3":
                reasoning = generation_kwargs.get("reasoning", {})
                if not isinstance(reasoning, dict):
                    raise InvalidLlmMappingError("llm.generation_kwargs.reasoning")
                generation_kwargs["reasoning"] = {**reasoning, "effort": "none"}
            max_tokens = generation_kwargs.pop("max_tokens", None)
            if max_tokens is not None and "max_output_tokens" not in generation_kwargs:
                generation_kwargs["max_output_tokens"] = max_tokens
            match structured_output:
                case None:
                    pass
                case "paper_digest":
                    text_options = generation_kwargs.get("text", {})
                    if text_options is None:
                        text_options = {}
                    if not isinstance(text_options, dict):
                        raise InvalidLlmMappingError("llm.generation_kwargs.text")
                    generation_kwargs["text"] = {
                        **text_options,
                        "format": {
                            "type": "json_schema",
                            **_strict_paper_digest_config(),
                        },
                    }
                case unreachable:
                    assert_never(unreachable)
            api_response = _call_sdk(
                openai_client.responses.create,
                input=messages,
                **generation_kwargs,
            )
            return api_response.output_text

        case unreachable:
            assert_never(unreachable)
