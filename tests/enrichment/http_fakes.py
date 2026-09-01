"""Typed HTTP fakes shared by enrichment adapter tests."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import final

import pytest
import requests

from zotero_arxiv_daily.retriever.crossref_client import JsonValue


@final
class Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: JsonValue = None,
        content: bytes = b"",
        headers: Mapping[str, str] | None = None,
        invalid_json_body: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = dict(headers or {})
        self._invalid_json_body = invalid_json_body

    def json(self) -> JsonValue:
        if self._invalid_json_body is not None:
            raise requests.JSONDecodeError(
                "invalid provider JSON",
                self._invalid_json_body,
                0,
            )
        return self._payload


@dataclass(frozen=True, slots=True)
class Call:
    url: str
    params: Mapping[str, str | int]
    headers: Mapping[str, str]
    timeout: float
    allow_redirects: bool


type Outcome = Callable[[], Response]


def install_get(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: Iterable[Outcome],
) -> list[Call]:
    outcome_iterator = iter(outcomes)
    calls: list[Call] = []

    def get(
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        calls.append(Call(url, dict(params), dict(headers), timeout, allow_redirects))
        return next(outcome_iterator)()

    monkeypatch.setattr(requests, "get", get)
    return calls
