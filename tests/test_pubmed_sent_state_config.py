"""Focused tests for the PubMed source and sent-DOI state configurations.

Composes the PubMed source subtree and the ``sent_doi_state`` subtree from
the checked-in Hydra configs, then resolves the environment interpolations.
Verifies defaults, typed values, and environment overrides. Provider
enrichment and runtime contracts live in ``test_feature_config.py``.
"""

import pytest

from .feature_config_support import compose_config, resolved_section


def test_base_has_neutral_pubmed_source_and_disabled_sent_doi_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the checked-in base configuration without user overrides.
    config = compose_config(monkeypatch, "base")

    # When the PubMed source and sent-DOI state subtrees are resolved.
    pubmed_source = resolved_section(config, "source.pubmed")
    sent_state = resolved_section(config, "sent_doi_state")

    # Then defaults are complete, typed, and operationally neutral.
    assert pubmed_source == {
        "query": None,
        "contact_email": None,
        "api_key": None,
        "lookback_days": 3,
        "request_rate": 10.0,
    }
    assert type(pubmed_source["lookback_days"]) is int
    assert type(pubmed_source["request_rate"]) is float
    assert sent_state == {
        "enabled": False,
        "path": ".state/sent-dois.fernet",
        "key": None,
    }
    assert sent_state["enabled"] is False


def test_missing_custom_env_resolves_safe_typed_pubmed_and_sent_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every new custom environment variable is absent.
    config = compose_config(monkeypatch, "default")

    # When the PubMed source and sent-DOI state subtrees are resolved.
    pubmed_source = resolved_section(config, "source.pubmed")
    sent_state = resolved_section(config, "sent_doi_state")

    # Then opt-ins fail closed and optional identity values remain empty.
    assert pubmed_source["query"] is None
    assert pubmed_source["contact_email"] is None
    assert pubmed_source["api_key"] is None
    assert pubmed_source["lookback_days"] == 3
    assert type(pubmed_source["lookback_days"]) is int
    assert pubmed_source["request_rate"] == 10.0
    assert type(pubmed_source["request_rate"]) is float
    assert sent_state == {
        "enabled": False,
        "path": ".state/sent-dois.fernet",
        "key": None,
    }
    assert sent_state["enabled"] is False


def test_populated_custom_env_resolves_pubmed_and_sent_doi_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given inert values for the PubMed source and sent-DOI state boundaries.
    env = {
        "PUBMED_ENABLED": "true",
        "PUBMED_EMAIL": "pubmed-contact@example.test",
        "PUBMED_QUERY": "inert pubmed discovery query",
        "PUBMED_ISSNS": '["0028-0836","0018-9219"]',
        "NIH_API": "inert-nih-marker",
        "SENT_DOI_STATE_ENABLED": "true",
        "SENT_DOI_STATE_KEY": "inert-sent-doi-state-key",
    }
    config = compose_config(monkeypatch, "default", env)

    # When environment-backed subtrees are fully resolved.
    pubmed_source = resolved_section(config, "source.pubmed")
    sent_state = resolved_section(config, "sent_doi_state")

    # Then identities and the path are preserved while opt-ins flip on.
    assert pubmed_source == {
        "query": "inert pubmed discovery query",
        "contact_email": "pubmed-contact@example.test",
        "api_key": "inert-nih-marker",
        "lookback_days": 3,
        "request_rate": 10.0,
    }
    assert sent_state == {
        "enabled": True,
        "path": ".state/sent-dois.fernet",
        "key": "inert-sent-doi-state-key",
    }
