"""Shared Hydra helpers for the feature configuration tests.

Composes the checked-in ``config/base.yaml`` and ``config/custom.yaml``
trees, scopes environment variables the new feature boundaries read, and
resolves one subtree into a plain JSON-like mapping. Provider enrichment
and runtime helpers stay in ``test_feature_config.py``.
"""

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import DictConfig, ListConfig, OmegaConf
import pytest


_CONFIG_DIR: Final = str(Path(__file__).resolve().parent.parent / "config")

FEATURE_ENV_VARS: Final = (
    "CROSSREF_MAILTO",
    "ABSTRACT_ENRICHMENT_ENABLED",
    "PUBMED_ENABLED",
    "PUBMED_EMAIL",
    "PUBMED_QUERY",
    "PUBMED_ISSNS",
    "NIH_API",
    "IEEE_ENABLED",
    "IEEE_XPLORE_API",
    "ELSEVIER_ENABLED",
    "ELSEVIER_API",
    "SPRINGER_ENABLED",
    "SPRINGER_API",
    "VENUE_PRESTIGE_ENABLED",
    "SENT_DOI_STATE_ENABLED",
    "SENT_DOI_STATE_KEY",
    "PAPER_SOURCES",
)

type ConfigValue = (
    str | int | float | bool | None | list[ConfigValue] | dict[str, ConfigValue]
)
type SelectedConfigValue = ConfigValue | DictConfig | ListConfig
# Mirrors omegaconf.base.DictKeyType so to_container's result stays assignable
# without rebuilding the mapping (dict key types are invariant).
type SectionKey = str | bytes | int | float | bool | Enum
type ResolvedSection = dict[SectionKey, ConfigValue] | list[ConfigValue] | str | None


class ConfigSelector(Protocol):
    def __call__(
        self,
        cfg: DictConfig,
        key: str,
        *,
        default: SelectedConfigValue = None,
        throw_on_resolution_failure: bool = True,
    ) -> SelectedConfigValue: ...


def compose_config(
    monkeypatch: pytest.MonkeyPatch,
    config_name: str,
    env: Mapping[str, str] | None = None,
) -> DictConfig:
    configured_env = env or {}
    for name in FEATURE_ENV_VARS:
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


def select_config_value(config: DictConfig, key: str) -> SelectedConfigValue:
    selector: ConfigSelector = OmegaConf.select
    selected = selector(config, key)
    return selected


def resolved_section(config: DictConfig, key: str) -> dict[SectionKey, ConfigValue]:
    """Resolve one composed config subtree into a plain JSON-like mapping."""
    subtree = select_config_value(config, key)
    assert isinstance(subtree, (DictConfig, ListConfig))
    section: ResolvedSection = OmegaConf.to_container(subtree, resolve=True)
    assert isinstance(section, dict)
    return section
