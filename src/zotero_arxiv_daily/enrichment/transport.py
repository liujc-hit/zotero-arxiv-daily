"""Redacted one-attempt HTTP boundary for enrichment providers."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

import requests
from loguru import logger

from .pacing import StartPacer


type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


HTTP_MULTIPLE_CHOICES: Final = 300
REQUEST_TIMEOUT_SECONDS: Final = 30.0


class ProviderName(StrEnum):
    IEEE = "IEEE"
    ELSEVIER = "Elsevier"
    SPRINGER = "Springer"
    PUBMED = "PubMed"


type HttpResponse = requests.Response
type ResponseObserver = Callable[[HttpResponse], None]


class _JsonResponse(Protocol):
    def json(self) -> JsonValue: ...


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    provider: ProviderName
    url: str = field(repr=False)
    params: Mapping[str, str | int] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)


def request_once(
    request: ProviderRequest,
    pacer: StartPacer,
    observe_response: ResponseObserver | None = None,
) -> HttpResponse | None:
    """Perform one fixed request without exposing request or failure details."""
    try:
        response: HttpResponse = pacer.call(
            lambda: requests.get(
                request.url,
                params=request.params,
                headers=request.headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        )
    except requests.RequestException:
        logger.warning("{} transport failure", request.provider.value)
        return None

    if observe_response is not None:
        observe_response(response)
    if response.status_code >= HTTP_MULTIPLE_CHOICES:
        logger.warning(
            "{} HTTP status {}",
            request.provider.value,
            response.status_code,
        )
        return None
    return response


def parse_json(response: _JsonResponse, provider: ProviderName) -> JsonObject | None:
    """Parse an object root once while keeping invalid payloads out of logs."""
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        logger.warning("{} invalid JSON", provider.value)
        return None
    if not isinstance(payload, dict):
        logger.warning("{} invalid JSON shape", provider.value)
        return None
    return payload


__all__: Final[tuple[str, ...]] = (
    "HttpResponse",
    "ProviderName",
    "ProviderRequest",
    "parse_json",
    "request_once",
)
