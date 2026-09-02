"""Redaction contracts for unresolved PubMed source configuration."""

from typing import final

import pytest
from omegaconf import DictConfig, open_dict

import zotero_arxiv_daily.retriever.pubmed_retriever as pubmed_module
from zotero_arxiv_daily.retriever.pubmed_retriever import (
    InvalidPubMedApiKeyError,
    InvalidPubMedContactEmailError,
    InvalidPubMedLookbackDaysError,
    InvalidPubMedQueryError,
    InvalidPubMedRequestRateError,
    PubMedRetriever,
)


@pytest.mark.parametrize(
    ("field", "env_var", "error_type", "message"),
    [
        (
            "query",
            "PUBMED_INTERP_QUERY_SECRET",
            InvalidPubMedQueryError,
            "source.pubmed.query must be a nonblank string",
        ),
        (
            "contact_email",
            "PUBMED_INTERP_EMAIL_SECRET",
            InvalidPubMedContactEmailError,
            "source.pubmed.contact_email must be a nonblank string",
        ),
        (
            "api_key",
            "NIH_INTERP_API_SECRET",
            InvalidPubMedApiKeyError,
            "source.pubmed.api_key must be a string or null",
        ),
        (
            "lookback_days",
            "PUBMED_INTERP_LOOKBACK_SECRET",
            InvalidPubMedLookbackDaysError,
            "source.pubmed.lookback_days must be a positive integer",
        ),
        (
            "request_rate",
            "PUBMED_INTERP_RATE_SECRET",
            InvalidPubMedRequestRateError,
            "source.pubmed.request_rate must be finite and positive",
        ),
    ],
)
def test_unresolved_env_interpolation_raises_redacted_field_error(
    config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    env_var: str,
    error_type: type[ValueError],
    message: str,
) -> None:
    # Given: one source field references a missing private environment value.
    with open_dict(config.source):
        config.source.pubmed = {
            "query": "robotics[Title]",
            "contact_email": "curator+pubmed@example.test",
            "api_key": "private-nih-key",
            "lookback_days": 3,
            "request_rate": 10.0,
        }
    monkeypatch.delenv(env_var, raising=False)
    config.source.pubmed[field] = f"${{oc.env:{env_var}}}"
    constructor_calls: list[bool] = []

    @final
    class _GuardedClient:
        def __init__(
            self,
            contact_email: str,
            api_key: str | None = None,
            request_rate: float = 10.0,
        ) -> None:
            del contact_email, api_key, request_rate
            constructor_calls.append(True)

    monkeypatch.setattr(pubmed_module, "PubMedClient", _GuardedClient)

    # When: the retriever resolves its strict source configuration.
    with pytest.raises(error_type) as caught:
        _ = PubMedRetriever(config)

    # Then: only the field-specific static error is observable.
    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert constructor_calls == []
    observable = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__cause__!r}"
    for forbidden in (
        env_var,
        "oc.env",
        "Environment variable",
        "\nKeyError(",
        "\nKeyError:",
        "InterpolationResolutionError",
        "full_key",
        "object_type",
        "raised while resolving",
    ):
        assert forbidden not in observable
