"""Hydra contracts for disabled-by-default retrieval and ranking features."""

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Final, Never

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig, ListConfig, OmegaConf
import pytest

import zotero_arxiv_daily.enrichment.pipeline as pipeline_module
from zotero_arxiv_daily.enrichment.pipeline import build_pipeline_enrichers


_CONFIG_DIR: Final = str(Path(__file__).resolve().parent.parent / "config")
_PROVIDERS: Final = ("pubmed", "ieee", "elsevier", "springer")
_FEATURE_ENV_VARS: Final = (
    "CROSSREF_MAILTO",
    "ABSTRACT_ENRICHMENT_ENABLED",
    "PUBMED_ENABLED",
    "PUBMED_EMAIL",
    "PUBMED_ISSNS",
    "NIH_API",
    "IEEE_ENABLED",
    "IEEE_XPLORE_API",
    "ELSEVIER_ENABLED",
    "ELSEVIER_API",
    "SPRINGER_ENABLED",
    "SPRINGER_API",
    "VENUE_PRESTIGE_ENABLED",
    "PAPER_SOURCES",
)

type ConfigValue = (
    str | int | float | bool | None | list[ConfigValue] | dict[str, ConfigValue]
)
# Mirrors omegaconf.base.DictKeyType so to_container's result stays assignable
# without rebuilding the mapping (dict key types are invariant).
type SectionKey = str | bytes | int | float | bool | Enum
type ResolvedSection = dict[SectionKey, ConfigValue] | list[ConfigValue] | str | None


def _compose_config(
    monkeypatch: pytest.MonkeyPatch,
    config_name: str,
    env: Mapping[str, str] | None = None,
) -> DictConfig:
    configured_env = env or {}
    for name in _FEATURE_ENV_VARS:
        if name in configured_env:
            monkeypatch.setenv(name, configured_env[name])
        else:
            monkeypatch.delenv(name, raising=False)

    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=_CONFIG_DIR, version_base=None):
            return compose(config_name=config_name)
    finally:
        GlobalHydra.instance().clear()


def _forbidden_constructor(*args: Never, **kwargs: Never) -> Never:
    del args, kwargs
    pytest.fail("disabled feature constructed an optional runtime service")


def _resolved_section(config: DictConfig, key: str) -> dict[SectionKey, ConfigValue]:
    """Resolve one composed config subtree into a plain JSON-like mapping."""
    subtree: DictConfig | ListConfig | None = OmegaConf.select(config, key)
    section: ResolvedSection = OmegaConf.to_container(subtree, resolve=True)
    assert isinstance(section, dict)
    return section


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


def test_base_has_complete_neutral_feature_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the checked-in base configuration without user overrides.
    config = _compose_config(monkeypatch, "base")

    # When every new feature subtree is resolved.
    crossref = _resolved_section(config, "source.crossref")
    enrichment = _resolved_section(config, "enrichment")
    venue = _resolved_section(config, "reranker.venue_prestige")

    # Then defaults are complete, typed, and operationally neutral.
    assert crossref == {"mailto": None, "lookback_days": 1}
    assert type(crossref["lookback_days"]) is int
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
    assert venue == {"enabled": False, "weight": 0.1, "max_multiplier": 1.5}
    assert venue["enabled"] is False
    assert type(venue["weight"]) is float
    assert type(venue["max_multiplier"]) is float


def test_missing_custom_env_resolves_safe_typed_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every new custom environment variable is absent.
    config = _compose_config(monkeypatch, "default")

    # When the composed custom feature values are resolved.
    crossref = _resolved_section(config, "source.crossref")
    enrichment = _resolved_section(config, "enrichment")
    venue = _resolved_section(config, "reranker.venue_prestige")
    sources = _raw_sources(config)

    # Then all opt-ins fail closed and optional identity values remain empty.
    assert crossref == {"mailto": None, "lookback_days": 1}
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
    assert venue == {"enabled": False, "weight": 0.1, "max_multiplier": 1.5}
    assert venue["enabled"] is False
    assert type(sources) is list
    assert sources == ["arxiv", "openalex"]


def test_populated_custom_env_resolves_values_types_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given inert values for every new custom environment boundary.
    env = {
        "CROSSREF_MAILTO": "crossref-contact@example.test",
        "ABSTRACT_ENRICHMENT_ENABLED": "true",
        "PUBMED_ENABLED": "true",
        "PUBMED_EMAIL": "pubmed-contact@example.test",
        "PUBMED_ISSNS": '["0028-0836","0018-9219"]',
        "NIH_API": "inert-nih-marker",
        "IEEE_ENABLED": "true",
        "IEEE_XPLORE_API": "inert-ieee-marker",
        "ELSEVIER_ENABLED": "true",
        "ELSEVIER_API": "inert-elsevier-marker",
        "SPRINGER_ENABLED": "true",
        "SPRINGER_API": "inert-springer-marker",
        "VENUE_PRESTIGE_ENABLED": "true",
        "PAPER_SOURCES": '["crossref","openalex","arxiv"]',
    }
    config = _compose_config(monkeypatch, "default", env)

    # When environment-backed subtrees are fully resolved.
    crossref = _resolved_section(config, "source.crossref")
    enrichment = _resolved_section(config, "enrichment")
    venue = _resolved_section(config, "reranker.venue_prestige")
    sources = _raw_sources(config)

    # Then booleans, identities, decoded lists, and source order are preserved.
    assert crossref == {
        "mailto": "crossref-contact@example.test",
        "lookback_days": 1,
    }
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
    assert venue == {"enabled": True, "weight": 0.1, "max_multiplier": 1.5}
    assert venue["enabled"] is True
    assert type(sources) is list
    assert sources == ["crossref", "openalex", "arxiv"]
    assert all(isinstance(source, str) for source in sources)


def test_existing_default_fixture_builds_no_optional_services(
    config: DictConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the repository's existing composed fixture and no feature env opt-ins.
    for name in _FEATURE_ENV_VARS:
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
