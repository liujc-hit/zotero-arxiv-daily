"""Config resolution and score adjustment for venue citation proxies."""

from dataclasses import dataclass
from math import isfinite, log1p
from typing import Final

import numpy as np
from omegaconf import DictConfig, OmegaConf

from ..protocol import Paper


@dataclass(frozen=True, slots=True)
class VenueCitationWeighting:
    weight: float
    max_multiplier: float


def resolve_venue_citation_weighting(
    config: DictConfig,
) -> VenueCitationWeighting | None:
    """Return settings only when every opt-in field is present and valid."""
    venue_config = OmegaConf.select(
        config,
        "reranker.venue_prestige",
        default=None,
        throw_on_resolution_failure=False,
    )
    if not isinstance(venue_config, DictConfig):
        return None
    enabled = OmegaConf.select(
        venue_config,
        "enabled",
        default=None,
        throw_on_resolution_failure=False,
    )
    if enabled is not True:
        return None
    weight = OmegaConf.select(
        venue_config,
        "weight",
        default=None,
        throw_on_resolution_failure=False,
    )
    max_multiplier = OmegaConf.select(
        venue_config,
        "max_multiplier",
        default=None,
        throw_on_resolution_failure=False,
    )
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or isinstance(max_multiplier, bool)
        or not isinstance(max_multiplier, (int, float))
    ):
        return None
    parsed_weight = float(weight)
    parsed_max_multiplier = float(max_multiplier)
    if (
        not isfinite(parsed_weight)
        or parsed_weight < 0.0
        or not isfinite(parsed_max_multiplier)
        or parsed_max_multiplier < 1.0
    ):
        return None
    return VenueCitationWeighting(parsed_weight, parsed_max_multiplier)


def apply_venue_citation_weighting(
    scores: np.ndarray,
    candidates: list[Paper],
    weighting: VenueCitationWeighting,
) -> np.ndarray:
    """Multiply historical scores by bounded proxy factors, then clip them."""
    multipliers: list[float] = []
    for candidate in candidates:
        proxy = candidate.venue_citation_proxy
        multiplier = 1.0
        if (
            proxy is not None
            and not isinstance(proxy, bool)
            and isfinite(proxy)
            and proxy >= 0.0
        ):
            multiplier = min(
                1.0 + weighting.weight * log1p(proxy),
                weighting.max_multiplier,
            )
        multipliers.append(multiplier)
    return np.clip(scores * np.asarray(multipliers), 0.0, 10.0)


__all__: Final[tuple[str, ...]] = (
    "VenueCitationWeighting",
    "apply_venue_citation_weighting",
    "resolve_venue_citation_weighting",
)
