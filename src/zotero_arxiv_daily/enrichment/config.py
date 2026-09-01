"""Strict opt-in parsing for runtime enrichment settings."""

import re
from collections.abc import Sequence
from math import isfinite
from typing import ClassVar, Final, Never, Protocol, TypeGuard

from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from ..identifiers import normalize_issn
from .settings import (
    ElsevierSettings,
    EnrichmentSettings,
    IeeeSettings,
    PubMedSettings,
    SpringerSettings,
)

type _ConfigValue = (
    str
    | int
    | float
    | bool
    | None
    | list[_ConfigValue]
    | dict[str, _ConfigValue]
    | DictConfig
    | ListConfig
)

_DEFAULTS: Final = EnrichmentSettings()
_TOP_LEVEL_FIELDS: Final = frozenset(
    {"enabled", "workers", "max_papers", "pubmed", "ieee", "elsevier", "springer"}
)
_PUBMED_FIELDS: Final = frozenset(
    {"enabled", "contact_email", "api_key", "request_rate", "issns"}
)
_VENDOR_FIELDS: Final = frozenset(
    {"enabled", "api_key", "request_rate", "issns", "doi_prefixes"}
)
_DOI_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}")


class InvalidEnrichmentConfigurationError(ValueError):
    """Report malformed enabled settings without retaining supplied values."""

    message: ClassVar[str] = "enabled enrichment configuration is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class _ConfigSelector(Protocol):
    def __call__(
        self,
        cfg: DictConfig,
        key: str,
        *,
        default: _ConfigValue = None,
        throw_on_resolution_failure: bool = True,
    ) -> _ConfigValue: ...


def _invalid() -> Never:
    raise InvalidEnrichmentConfigurationError from None


def _select_strict(
    selector: _ConfigSelector,
    section: DictConfig,
    key: str,
) -> _ConfigValue:
    return selector(section, key, throw_on_resolution_failure=True)


def _select_soft(
    selector: _ConfigSelector,
    section: DictConfig,
    key: str,
) -> _ConfigValue:
    return selector(
        section,
        key,
        default=None,
        throw_on_resolution_failure=False,
    )


def _read(
    section: DictConfig | None,
    key: str,
    default: _ConfigValue,
) -> _ConfigValue:
    if section is None or key not in section:
        return default
    try:
        return _select_strict(OmegaConf.select, section, key)
    except OmegaConfBaseException:
        _invalid()


def _section(
    parent: DictConfig,
    key: str,
    allowed_fields: frozenset[str],
) -> DictConfig | None:
    if key not in parent:
        return None
    value = _read(parent, key, None)
    if not isinstance(value, DictConfig):
        _invalid()
    if any(not isinstance(name, str) or name not in allowed_fields for name in value):
        _invalid()
    return value


def _boolean(section: DictConfig | None, key: str, default: bool) -> bool:
    value = _read(section, key, default)
    if not isinstance(value, bool):
        _invalid()
    return value


def _positive_integer(section: DictConfig, key: str, default: int) -> int:
    value = _read(section, key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid()
    return value


def _optional_text(section: DictConfig | None, key: str) -> str | None:
    value = _read(section, key, None)
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid()
    return value.strip() or None


def _is_config_sequence(value: _ConfigValue) -> TypeGuard[Sequence[_ConfigValue]]:
    return isinstance(value, (list, ListConfig))


def _positive_rate(section: DictConfig | None, default: float) -> float:
    value = _read(section, "request_rate", default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid()
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0.0:
        _invalid()
    return parsed


def _issns(
    section: DictConfig | None,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if section is None or "issns" not in section:
        return default
    value = _read(section, "issns", None)
    if not _is_config_sequence(value):
        _invalid()
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or (issn := normalize_issn(item)) is None:
            _invalid()
        normalized.append(issn)
    return tuple(normalized)


def _doi_prefixes(
    section: DictConfig | None,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if section is None or "doi_prefixes" not in section:
        return default
    value = _read(section, "doi_prefixes", None)
    if not _is_config_sequence(value):
        _invalid()
    prefixes: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _invalid()
        prefix = item.strip().casefold().rstrip("/")
        if _DOI_PREFIX_PATTERN.fullmatch(prefix) is None:
            _invalid()
        prefixes.append(prefix)
    return tuple(prefixes)


def resolve_enrichment_settings(config: DictConfig) -> EnrichmentSettings | None:
    """Return immutable settings only for the exact top-level boolean opt-in."""
    enrichment = _select_soft(OmegaConf.select, config, "enrichment")
    if not isinstance(enrichment, DictConfig):
        return None
    enabled = _select_soft(OmegaConf.select, enrichment, "enabled")
    if enabled is not True:
        return None
    if any(
        not isinstance(name, str) or name not in _TOP_LEVEL_FIELDS
        for name in enrichment
    ):
        _invalid()

    pubmed_config = _section(enrichment, "pubmed", _PUBMED_FIELDS)
    ieee_config = _section(enrichment, "ieee", _VENDOR_FIELDS)
    elsevier_config = _section(enrichment, "elsevier", _VENDOR_FIELDS)
    springer_config = _section(enrichment, "springer", _VENDOR_FIELDS)

    pubmed_enabled = _boolean(pubmed_config, "enabled", False)
    pubmed_contact = _optional_text(pubmed_config, "contact_email")
    if pubmed_enabled and pubmed_contact is None:
        _invalid()
    pubmed = PubMedSettings(
        enabled=pubmed_enabled,
        contact_email=pubmed_contact,
        api_key=_optional_text(pubmed_config, "api_key"),
        request_rate=_positive_rate(pubmed_config, _DEFAULTS.pubmed.request_rate),
        issns=_issns(pubmed_config, _DEFAULTS.pubmed.issns),
    )

    ieee_enabled = _boolean(ieee_config, "enabled", False)
    ieee_key = _optional_text(ieee_config, "api_key")
    if ieee_enabled and ieee_key is None:
        _invalid()
    ieee = IeeeSettings(
        enabled=ieee_enabled,
        api_key=ieee_key,
        request_rate=_positive_rate(ieee_config, _DEFAULTS.ieee.request_rate),
        issns=_issns(ieee_config, _DEFAULTS.ieee.issns),
        doi_prefixes=_doi_prefixes(ieee_config, _DEFAULTS.ieee.doi_prefixes),
    )

    elsevier_enabled = _boolean(elsevier_config, "enabled", False)
    elsevier_key = _optional_text(elsevier_config, "api_key")
    if elsevier_enabled and elsevier_key is None:
        _invalid()
    elsevier = ElsevierSettings(
        enabled=elsevier_enabled,
        api_key=elsevier_key,
        request_rate=_positive_rate(elsevier_config, _DEFAULTS.elsevier.request_rate),
        issns=_issns(elsevier_config, _DEFAULTS.elsevier.issns),
        doi_prefixes=_doi_prefixes(
            elsevier_config,
            _DEFAULTS.elsevier.doi_prefixes,
        ),
    )

    springer_enabled = _boolean(springer_config, "enabled", False)
    springer_key = _optional_text(springer_config, "api_key")
    if springer_enabled and springer_key is None:
        _invalid()
    springer = SpringerSettings(
        enabled=springer_enabled,
        api_key=springer_key,
        request_rate=_positive_rate(springer_config, _DEFAULTS.springer.request_rate),
        issns=_issns(springer_config, _DEFAULTS.springer.issns),
        doi_prefixes=_doi_prefixes(
            springer_config,
            _DEFAULTS.springer.doi_prefixes,
        ),
    )

    return EnrichmentSettings(
        pubmed=pubmed,
        ieee=ieee,
        elsevier=elsevier,
        springer=springer,
        workers=_positive_integer(enrichment, "workers", _DEFAULTS.workers),
        max_papers=_positive_integer(
            enrichment,
            "max_papers",
            _DEFAULTS.max_papers,
        ),
    )


__all__: Final[tuple[str, ...]] = (
    "InvalidEnrichmentConfigurationError",
    "resolve_enrichment_settings",
)
