"""Runtime construction and pre-rerank ordering for optional enrichment."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Never, Protocol, TypeGuard

from loguru import logger
from omegaconf import DictConfig, ListConfig, OmegaConf
from omegaconf.errors import OmegaConfBaseException

from ..protocol import Paper
from ..reranker.venue_citation_weighting import resolve_venue_citation_weighting
from ..retriever.base import BaseRetriever
from ..retriever.openalex_client import OpenAlexClient, OpenAlexClientError
from ..retriever.openalex_retriever import OpenAlexRetriever
from .config import InvalidEnrichmentConfigurationError, resolve_enrichment_settings
from .service import AbstractEnricher
from .venue_citation import VenueCitationEnricher

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

_VENUE_IDENTITY_WARNING: Final = (
    "OpenAlex identity unavailable; venue citation enrichment disabled"
)
_VENUE_FAILURE_WARNING: Final = "OpenAlex venue citation enrichment failure"


class _PaperEnricher(Protocol):
    def enrich(self, papers: list[Paper]) -> None: ...


class _ConfigSelector(Protocol):
    def __call__(
        self,
        cfg: DictConfig,
        key: str,
        *,
        default: _ConfigValue = None,
        throw_on_resolution_failure: bool = True,
    ) -> _ConfigValue: ...


@dataclass(frozen=True, slots=True, repr=False)
class _OpenAlexIdentity:
    api_keys: tuple[str, ...] = field(repr=False)
    allow_anonymous: bool


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


def _read(section: DictConfig, key: str, default: _ConfigValue) -> _ConfigValue:
    if key not in section:
        return default
    try:
        return _select_strict(OmegaConf.select, section, key)
    except OmegaConfBaseException:
        _invalid()


def _is_config_sequence(value: _ConfigValue) -> TypeGuard[Sequence[_ConfigValue]]:
    return isinstance(value, (list, ListConfig))


def _source_section(config: DictConfig, name: str) -> DictConfig | None:
    source = _select_soft(OmegaConf.select, config, "source")
    if source is None:
        return None
    if not isinstance(source, DictConfig):
        _invalid()
    if name not in source:
        return None
    section = _read(source, name, None)
    if not isinstance(section, DictConfig):
        _invalid()
    return section


def _openalex_identity(config: DictConfig) -> _OpenAlexIdentity | None:
    section = _source_section(config, "openalex")
    if section is None:
        return None
    raw_keys = _read(section, "api_keys", None)
    if raw_keys is None:
        configured_keys: tuple[str, ...] = ()
    else:
        if not _is_config_sequence(raw_keys):
            _invalid()
        normalized_keys: list[str] = []
        for raw_key in raw_keys:
            if raw_key is None:
                continue
            if not isinstance(raw_key, str):
                _invalid()
            if key := raw_key.strip():
                normalized_keys.append(key)
        configured_keys = tuple(normalized_keys)
    if len(configured_keys) > 2 or len(set(configured_keys)) != len(configured_keys):
        _invalid()

    allow_anonymous = _read(section, "allow_anonymous", False)
    if not isinstance(allow_anonymous, bool):
        _invalid()
    if not configured_keys and not allow_anonymous:
        return None
    return _OpenAlexIdentity(configured_keys, allow_anonymous)


@dataclass(frozen=True, slots=True)
class PipelineEnrichers:
    """Hold independently optional enrichment stages in execution order."""

    abstract: _PaperEnricher | None = None
    venue_citation: _PaperEnricher | None = None

    def enrich_before_rerank(self, papers: list[Paper]) -> None:
        """Mutate the full candidate list in abstract-then-venue order."""
        if self.abstract is not None:
            self.abstract.enrich(papers)
        if self.venue_citation is None:
            return
        try:
            self.venue_citation.enrich(papers)
        except OpenAlexClientError:
            logger.warning(_VENUE_FAILURE_WARNING)


def build_pipeline_enrichers(
    config: DictConfig,
    retrievers: Mapping[str, BaseRetriever],
) -> PipelineEnrichers:
    """Build optional stages while reusing configured request clients."""
    settings = resolve_enrichment_settings(config)
    abstract: _PaperEnricher | None = None
    if settings is not None:
        abstract = AbstractEnricher(settings)

    venue_citation: _PaperEnricher | None = None
    weighting = resolve_venue_citation_weighting(config)
    if weighting is not None:
        openalex_retriever = retrievers.get("openalex")
        if isinstance(openalex_retriever, OpenAlexRetriever):
            openalex_client = openalex_retriever.client
        else:
            identity = _openalex_identity(config)
            openalex_client = (
                OpenAlexClient(
                    identity.api_keys,
                    anonymous_fallback=identity.allow_anonymous,
                )
                if identity is not None
                else None
            )
        if openalex_client is None:
            logger.warning(_VENUE_IDENTITY_WARNING)
        else:
            venue_citation = VenueCitationEnricher(openalex_client, enabled=True)

    return PipelineEnrichers(abstract, venue_citation)


__all__: Final[tuple[str, ...]] = (
    "PipelineEnrichers",
    "build_pipeline_enrichers",
)
