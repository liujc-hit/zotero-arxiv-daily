"""Redacted typed failures shared by OpenAlex endpoint operations."""

from typing import ClassVar, Final


class OpenAlexClientError(RuntimeError):
    """Base class for redacted OpenAlex client failures."""


class _StaticOpenAlexError(OpenAlexClientError):
    message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.message)


class MissingOpenAlexCredentialsError(_StaticOpenAlexError):
    """Raised when neither keyed nor anonymous access was configured."""

    message: ClassVar[str] = "OpenAlex client has no configured request identity"


class OpenAlexCredentialsExhaustedError(_StaticOpenAlexError):
    """Raised after every configured request identity has been rate limited."""

    message: ClassVar[str] = "OpenAlex request identities are exhausted"


class OpenAlexCredentialConfigurationError(_StaticOpenAlexError):
    """Raised when configured OpenAlex keys are excessive or duplicated."""

    message: ClassVar[str] = "OpenAlex requires at most two distinct credentials"


class OpenAlexTransportError(_StaticOpenAlexError):
    """Raised when all transport attempts fail."""

    message: ClassVar[str] = "OpenAlex transport failed after three total attempts"


class OpenAlexServerError(_StaticOpenAlexError):
    """Raised when all attempts receive a server error."""

    message: ClassVar[str] = "OpenAlex server failed after three total attempts"


class OpenAlexInvalidJsonError(_StaticOpenAlexError):
    """Raised when a successful response does not contain JSON."""

    message: ClassVar[str] = "OpenAlex returned invalid JSON"


class OpenAlexSourcesTransportError(_StaticOpenAlexError):
    """Raised when the one OpenAlex Sources transport attempt fails."""

    message: ClassVar[str] = "OpenAlex Sources transport failed"


class OpenAlexSourcesInvalidJsonError(_StaticOpenAlexError):
    """Raised when a successful OpenAlex Sources response is not JSON."""

    message: ClassVar[str] = "OpenAlex Sources returned invalid JSON"


class OpenAlexUnsafeQueryParameterError(_StaticOpenAlexError):
    """Raised when a caller attempts query-string credential transport."""

    message: ClassVar[str] = "OpenAlex credentials are forbidden in query parameters"


class OpenAlexHttpStatusError(OpenAlexClientError):
    """Represent a non-retryable HTTP status without retaining a response."""

    status_code: int

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"OpenAlex returned HTTP status {status_code}")


class OpenAlexSourcesHttpStatusError(OpenAlexClientError):
    """Represent one failing Sources status without retaining its response."""

    status_code: int

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"OpenAlex Sources returned HTTP status {status_code}")


__all__: Final[tuple[str, ...]] = (
    "MissingOpenAlexCredentialsError",
    "OpenAlexClientError",
    "OpenAlexCredentialConfigurationError",
    "OpenAlexCredentialsExhaustedError",
    "OpenAlexHttpStatusError",
    "OpenAlexInvalidJsonError",
    "OpenAlexServerError",
    "OpenAlexSourcesHttpStatusError",
    "OpenAlexSourcesInvalidJsonError",
    "OpenAlexSourcesTransportError",
    "OpenAlexTransportError",
    "OpenAlexUnsafeQueryParameterError",
)
