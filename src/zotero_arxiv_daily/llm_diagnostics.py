"""Allowlisted metadata for one LLM request; never retain provider payloads."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from .llm_schemas import BilingualPaperDigest, LlmValue

type FallbackReason = Literal[
    "timeout", "transport", "http_error", "missing_tool", "invalid_json",
    "schema_validation", "bounds", "other",
]

_FINISH_REASONS: Final = frozenset({
    "stop", "length", "tool_calls", "content_filter", "function_call",
})
_EXCEPTION_TYPES: Final = frozenset({
    "TimeoutError", "APITimeoutError", "APIConnectionError", "APIStatusError",
    "BadRequestError", "AuthenticationError", "PermissionDeniedError",
    "NotFoundError", "ConflictError", "UnprocessableEntityError",
    "RateLimitError", "InternalServerError", "APIResponseValidationError",
    "OpenAIError", "ValidationError", "InvalidBilingualDigestError",
    "InvalidLlmMappingError", "UnsupportedApiModeError", "UnsupportedThinkingError",
    "ThinkingCannotBeDisabledError", "ValueError", "RuntimeError", "TypeError", "IndexError",
})
_VALIDATION_TYPES: Final = frozenset({
    "json_invalid", "model_type", "missing", "extra_forbidden", "string_type",
    "string_too_short", "string_too_long", "list_type", "too_long",
})


@dataclass(slots=True)
class RequestDiagnostics:
    """Request-local accumulator, mutated only to retain normalized metadata."""

    finish_reason: str = "unknown"
    missing_tool: bool = False

    def observe_finish(self, value: LlmValue) -> None:
        self.finish_reason = value if type(value) is str and value in _FINISH_REASONS else "unknown"

    def observe_incomplete(self, value: LlmValue) -> None:
        if type(value) is str and value == "max_output_tokens":
            self.finish_reason = "length"
        else:
            self.observe_finish(value)

    def capture(self, read: Callable[[], LlmValue], *, chat: bool) -> None:
        """Optional SDK metadata must never change payload selection or exceptions."""
        try:
            if chat:
                self.observe_finish(read())
            else:
                self.observe_incomplete(read())
        except Exception:  # noqa: BROAD_EXCEPT_OK - Untrusted optional SDK accessor boundary; no payload retained.
            self.finish_reason = "unknown"


@dataclass(frozen=True, slots=True)
class FallbackDiagnostic:
    reason: FallbackReason
    exception_type: str
    finish_reason: str
    http_status: int | Literal["-"] = "-"
    validation_field: str = "-"
    validation_type: str = "-"

    @property
    def validation_suffix(self) -> str:
        if self.validation_type == "-":
            return ""
        return f" validation_field={self.validation_field} validation_type={self.validation_type}"


def summarize_fallback(
    error: Exception, request: RequestDiagnostics, local_reason: str | None = None,
) -> FallbackDiagnostic:
    """Project a caught error into fixed labels, excluding messages and input values."""
    name = type(error).__name__
    exception_type = name if name in _EXCEPTION_TYPES else "unknown"
    reason: FallbackReason = "other"
    http_status: int | Literal["-"] = "-"
    field, kind = "-", "-"
    if local_reason in {"source_length", "source_token_budget", "response_length"}:
        reason = "bounds"
    elif local_reason in {"incomplete_abstract", "invented_abstract"}:
        reason = "schema_validation"
    else:
        match error:
            case APITimeoutError() | TimeoutError():
                reason = "timeout"
            case APIConnectionError():
                reason = "transport"
            case APIStatusError():
                reason = "http_error"
                status = error.status_code
                if type(status) is int and 100 <= status <= 599:
                    http_status = status
            case ValidationError():
                issue = next(iter(error.errors(include_input=False, include_context=False, include_url=False)), None)
                if issue is not None:
                    raw_kind = issue["type"]
                    kind = raw_kind if raw_kind in _VALIDATION_TYPES else "other"
                    location = issue["loc"]
                    if location:
                        first = location[0]
                        field = first if isinstance(first, str) and first in BilingualPaperDigest.model_fields else "other"
                reason = "missing_tool" if request.missing_tool else (
                    "invalid_json" if kind == "json_invalid" else "schema_validation"
                )
            case Exception():
                reason = "other"
    return FallbackDiagnostic(reason, exception_type, request.finish_reason, http_status, field, kind)
