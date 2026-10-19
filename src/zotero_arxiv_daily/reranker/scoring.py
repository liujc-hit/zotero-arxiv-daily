"""Frozen numerical inputs and historical top-k aggregation."""

from dataclasses import dataclass

import numpy as np

from ..protocol import Paper
from .venue_citation_weighting import (
    VenueCitationWeighting,
    apply_venue_citation_weighting,
)


@dataclass(frozen=True, slots=True)
class ScoreContext:
    topk: int
    half_life: float | None
    recency_weights: np.ndarray | None
    venue: VenueCitationWeighting | None
    mmr_lambda: float | None
    mmr_target: int


def aggregate_scores(
    sim: np.ndarray, candidates: list[Paper], context: ScoreContext,
) -> np.ndarray:
    """Keep the existing top-k selection and floating-point operation order."""
    n_corpus = sim.shape[1]
    k = min(context.topk, n_corpus)
    if k >= n_corpus:
        topk_idx = np.tile(np.arange(n_corpus), (len(candidates), 1))
    else:
        topk_idx = np.argpartition(sim, -k, axis=1)[:, -k:]
    topk_sim = np.take_along_axis(sim, topk_idx, axis=1)
    if context.recency_weights is not None:
        rec_weights = context.recency_weights[topk_idx]
        denom = rec_weights.sum(axis=1)
        denom[denom == 0] = 1.0
        scores = (topk_sim * rec_weights).sum(axis=1) / denom
    else:
        scores = topk_sim.mean(axis=1)
    scores = scores * 10.0
    if context.venue is not None:
        scores = apply_venue_citation_weighting(scores, candidates, context.venue)
    return scores
