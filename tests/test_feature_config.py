"""Provider enrichment and runtime contracts for disabled-by-default features."""

from typing import Never

from omegaconf import DictConfig, OmegaConf
import pytest

import zotero_arxiv_daily.enrichment.pipeline as pipeline_module
from zotero_arxiv_daily.enrichment.pipeline import build_pipeline_enrichers
from .feature_config_support import (
    ConfigValue,
    FEATURE_ENV_VARS,
    SectionKey,
    compose_config,
    resolved_section,
)


_PROVIDERS = ("pubmed", "ieee", "elsevier", "springer")


def _nested_section(
    parent: dict[SectionKey, ConfigValue],
    name: str,
) -> dict[str, ConfigValue]:
    """Narrow one resolved entry to its nested provider mapping."""
    value = parent[name]
    assert isinstance(value, dict)
    return value


def _raw_sources(config: DictConfig) -> list[ConfigValue]:
    """Read the raw decoded executor source list before any conversion."""
    sources: list[ConfigValue] = OmegaConf.select(config, "executor.source")
    return sources


def _forbidden_constructor(*args: Never, **kwargs: Never) -> Never:
    del args, kwargs
    pytest.fail("disabled feature constructed an optional runtime service")


def test_base_enrichment_defaults_are_disabled_with_typed_provider_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the checked-in base configuration without user overrides.
    config = compose_config(monkeypatch, "base")

    # When the enrichment subtree is resolved.
    enrichment = resolved_section(config, "enrichment")

    # Then defaults are complete, typed, and operationally neutral.
    assert enrichment == {
        "enabled": False,
        "workers": 3,
        "max_papers": 50,
        "pubmed": {
            "enabled": False,
            "contact_email": None,
            "api_key": None,
            "request_rate": 10.0,
            "issns": [],
        },
        "ieee": {
            "enabled": False,
            "api_key": None,
            "request_rate": 1.0,
            "issns": [],
            "doi_prefixes": ["10.1109"],
        },
        "elsevier": {
            "enabled": False,
            "api_key": None,
            "request_rate": 9.0,
            "issns": [],
            "doi_prefixes": ["10.1016"],
        },
        "springer": {
            "enabled": False,
            "api_key": None,
            "request_rate": 1.0,
            "issns": [],
            "doi_prefixes": ["10.1007", "10.1038", "10.1057", "10.1186"],
        },
    }
    assert enrichment["enabled"] is False
    assert type(enrichment["workers"]) is int
    assert type(enrichment["max_papers"]) is int
    for provider in _PROVIDERS:
        section = _nested_section(enrichment, provider)
        assert section["enabled"] is False
        assert type(section["request_rate"]) is float
        assert type(section["issns"]) is list


def test_missing_custom_env_resolves_enrichment_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every new custom environment variable is absent.
    config = compose_config(monkeypatch, "default")

    # When the enrichment subtree is resolved.
    enrichment = resolved_section(config, "enrichment")
    sources = _raw_sources(config)

    # Then all opt-ins fail closed and optional identity values remain empty.
    assert enrichment["enabled"] is False
    assert (
        [_nested_section(enrichment, name)["enabled"] for name in _PROVIDERS]
        == [False] * 4
    )
    assert _nested_section(enrichment, "pubmed")["contact_email"] is None
    assert (
        [_nested_section(enrichment, name)["api_key"] for name in _PROVIDERS]
        == [None] * 4
    )
    assert (
        [_nested_section(enrichment, name)["issns"] for name in _PROVIDERS]
        == [[], [], [], []]
    )
    assert type(sources) is list
    assert sources == ["arxiv", "openalex"]


def test_populated_custom_env_resolves_enrichment_identities_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given inert values for every new custom environment boundary.
    env = {
        "CROSSREF_MAILTO": "crossref-contact@example.test",
        "ABSTRACT_ENRICHMENT_ENABLED": "true",
        "PUBMED_ENABLED": "true",
        "PUBMED_EMAIL": "pubmed-contact@example.test",
        "PUBMED_QUERY": "inert pubmed discovery query",
        "PUBMED_ISSNS": '["0028-0836","0018-9219"]',
        "NIH_API": "inert-nih-marker",
        "IEEE_ENABLED": "true",
        "IEEE_XPLORE_API": "inert-ieee-marker",
        "ELSEVIER_ENABLED": "true",
        "ELSEVIER_API": "inert-elsevier-marker",
        "SPRINGER_ENABLED": "true",
        "SPRINGER_API": "inert-springer-marker",
        "VENUE_PRESTIGE_ENABLED": "true",
        "SENT_DOI_STATE_ENABLED": "true",
        "SENT_DOI_STATE_KEY": "inert-sent-doi-state-key",
        "PAPER_SOURCES": '["crossref","openalex","arxiv"]',
    }
    config = compose_config(monkeypatch, "default", env)

    # When the enrichment subtree is resolved.
    enrichment = resolved_section(config, "enrichment")
    sources = _raw_sources(config)

    # Then booleans, identities, decoded lists, and source order are preserved.
    assert enrichment["enabled"] is True
    assert (
        [_nested_section(enrichment, name)["enabled"] for name in _PROVIDERS]
        == [True] * 4
    )
    assert _nested_section(enrichment, "pubmed") == {
        "enabled": True,
        "contact_email": "pubmed-contact@example.test",
        "api_key": "inert-nih-marker",
        "request_rate": 10.0,
        "issns": ["0028-0836", "0018-9219"],
    }
    assert [
        _nested_section(enrichment, name)["api_key"]
        for name in ("ieee", "elsevier", "springer")
    ] == [
        "inert-ieee-marker",
        "inert-elsevier-marker",
        "inert-springer-marker",
    ]
    assert type(sources) is list
    assert sources == ["crossref", "openalex", "arxiv"]
    assert all(isinstance(source, str) for source in sources)


def test_existing_default_fixture_builds_no_optional_services(
    config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the repository's existing composed fixture and no feature env opt-ins.
    for name in FEATURE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for constructor_name in (
        "CrossrefClient",
        "OpenAlexClient",
        "AbstractEnricher",
        "VenueCitationEnricher",
    ):
        monkeypatch.setattr(
            pipeline_module,
            constructor_name,
            _forbidden_constructor,
        )

    # When runtime enrichers are built from that default fixture.
    enrichers = build_pipeline_enrichers(config, {})

    # Then composition remains usable and disabled features allocate nothing.
    assert list(_raw_sources(config)) == ["arxiv"]
    assert enrichers.abstract is None
    assert enrichers.venue_citation is None
