"""Immutable settings for bounded abstract enrichment."""

from dataclasses import dataclass, field
from typing import Final


_PUBMED_ANONYMOUS_RATE: Final = 3.0
_PUBMED_KEYED_RATE: Final = 10.0
_ELSEVIER_RATE: Final = 9.0


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


@dataclass(frozen=True, slots=True)
class PubMedSettings:
    enabled: bool = False
    contact_email: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)
    request_rate: float = _PUBMED_KEYED_RATE
    issns: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.enabled and _has_text(self.contact_email) and self.request_rate > 0

    @property
    def effective_request_rate(self) -> float:
        limit = _PUBMED_KEYED_RATE if _has_text(self.api_key) else _PUBMED_ANONYMOUS_RATE
        return max(0.0, min(self.request_rate, limit))


@dataclass(frozen=True, slots=True)
class IeeeSettings:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    request_rate: float = 1.0
    issns: tuple[str, ...] = ()
    doi_prefixes: tuple[str, ...] = ("10.1109",)

    @property
    def available(self) -> bool:
        return self.enabled and _has_text(self.api_key) and self.request_rate > 0

    @property
    def effective_request_rate(self) -> float:
        return max(0.0, self.request_rate)


@dataclass(frozen=True, slots=True)
class ElsevierSettings:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    request_rate: float = _ELSEVIER_RATE
    issns: tuple[str, ...] = ()
    doi_prefixes: tuple[str, ...] = ("10.1016",)

    @property
    def available(self) -> bool:
        return self.enabled and _has_text(self.api_key) and self.request_rate > 0

    @property
    def effective_request_rate(self) -> float:
        return max(0.0, min(self.request_rate, _ELSEVIER_RATE))


@dataclass(frozen=True, slots=True)
class SpringerSettings:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    request_rate: float = 1.0
    issns: tuple[str, ...] = ()
    doi_prefixes: tuple[str, ...] = (
        "10.1007",
        "10.1038",
        "10.1057",
        "10.1186",
    )

    @property
    def available(self) -> bool:
        return self.enabled and _has_text(self.api_key) and self.request_rate > 0

    @property
    def effective_request_rate(self) -> float:
        return max(0.0, self.request_rate)


@dataclass(frozen=True, slots=True)
class EnrichmentSettings:
    pubmed: PubMedSettings = field(default_factory=PubMedSettings)
    ieee: IeeeSettings = field(default_factory=IeeeSettings)
    elsevier: ElsevierSettings = field(default_factory=ElsevierSettings)
    springer: SpringerSettings = field(default_factory=SpringerSettings)
    workers: int = 3
    max_papers: int = 50


__all__: Final[tuple[str, ...]] = (
    "ElsevierSettings",
    "EnrichmentSettings",
    "IeeeSettings",
    "PubMedSettings",
    "SpringerSettings",
)
