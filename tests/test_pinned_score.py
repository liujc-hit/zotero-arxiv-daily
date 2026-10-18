"""Pinning floor, stable ordinary-pool order, and final-selection regressions."""

import pytest
from omegaconf import DictConfig, OmegaConf

from tests.test_executor_pipeline import (
    RecordingReranker,
    StubRetriever,
    executor_config,
    paper,
    runtime_executor,
)
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.protocol import Paper


def test_base_config_pinning_floor_defaults_to_requested_value(config: DictConfig) -> None:
    # Given: the composed base configuration with no pinning-floor override.
    # When: reading the public executor setting.
    floor = config.executor.pinned_min_score

    # Then: the shipped default is independent of the optional ordinary floor.
    assert floor == 4.9


@pytest.mark.parametrize("score", [4.8, 4.9, 4.95, None, float("nan"), float("inf"), -float("inf")])
def test_only_known_finite_scores_at_floor_qualify_when_keyword_matches(score: float | None) -> None:
    # Given: one keyword match, including unknown and nonfinite score cases.
    executor = Executor.__new__(Executor)
    executor.config = executor_config(pin_keywords=["mamba"])
    candidate = paper("Mamba", score)
    candidate.pinned = True  # Reused state must not qualify an ineligible score.

    # When: selecting with the default independent pinning floor.
    pinned, remaining = executor._select_pinned([candidate])

    # Then: rejected pin candidates remain available to ordinary recommendation.
    qualifies = score in (4.9, 4.95)
    assert pinned == ([candidate] if qualifies else [])
    assert remaining == ([] if qualifies else [candidate])
    assert candidate.pinned is qualifies


@pytest.mark.parametrize("floor", [None, "invalid", True, False, float("nan"), float("inf"), "-inf", [], {}])
def test_invalid_config_uses_default_when_floor_cannot_be_finite_number(floor) -> None:
    # Given: invalid config must not disable the score gate.
    executor = Executor.__new__(Executor)
    executor.config = OmegaConf.create({"executor": {"pin_keywords": ["mamba"], "pinned_min_score": floor}})
    low, edge = paper("Mamba low", 4.8), paper("Mamba edge", 4.9)

    # When: splitting candidates at an invalid configured floor.
    pinned, remaining = executor._select_pinned([low, edge])

    # Then: the fallback remains 4.9 rather than unrestricted pinning.
    assert pinned == [edge]
    assert remaining == [low]


@pytest.mark.parametrize("floor", [6.0, "6.0", -1.0])
def test_finite_configured_floor_is_used_when_numeric_or_numeric_string(floor: float | str) -> None:
    # Given: finite numeric configuration, without unrequested range limits.
    executor = Executor.__new__(Executor)
    executor.config = executor_config(pin_keywords=["mamba"], pinned_min_score=floor)
    edge = paper("Mamba edge", float(floor))
    low = paper("Mamba low", float(floor) - 0.01)

    # When: selecting keyword matches around the configured boundary.
    pinned, remaining = executor._select_pinned([low, edge])

    # Then: the configured floor, not the default, determines eligibility.
    assert pinned == [edge]
    assert remaining == [low]


@pytest.mark.parametrize("scores", [(10.0, 9.0, 8.0), (5.0, 9.0, 8.0), (10.0, 6.0, 8.0)])
def test_overflow_keeps_original_reranker_order_when_normal_candidate_precedes_it(scores) -> None:
    # Given: score order or an MMR order that deliberately differs from it.
    executor = Executor.__new__(Executor)
    executor.config = executor_config(pin_keywords=["mamba"], max_pinned_num=1)
    first, normal, overflow = [paper(title, score) for title, score in zip(
        ("Mamba A", "Normal B", "Mamba C"), scores, strict=True
    )]

    # When: the second eligible match exceeds the cap.
    pinned, remaining = executor._select_pinned([first, normal, overflow])

    # Then: overflow neither jumps the normal queue nor leaks a renderer flag.
    assert pinned == [first]
    assert remaining == [normal, overflow]
    assert [candidate.pinned for candidate in [first, normal, overflow]] == [True, False, False]


@pytest.mark.parametrize("keywords,cap", [(["mamba"], 0), (["mamba"], -1), (None, 20), ([], 20)])
def test_stale_flags_reset_when_pinning_is_disabled_or_capped(keywords, cap: int) -> None:
    # Given: reused papers previously marked pinned, including a nonmatch.
    executor = Executor.__new__(Executor)
    executor.config = executor_config(pin_keywords=keywords, max_pinned_num=cap)
    candidates = [paper("Mamba", 8.0), paper("Normal", 9.0)]
    for candidate in candidates:
        candidate.pinned = True

    # When: no paper can be selected for the pinned section.
    pinned, remaining = executor._select_pinned(candidates)

    # Then: all papers retain their order and render as ordinary recommendations.
    assert pinned == []
    assert remaining == candidates
    assert not any(candidate.pinned for candidate in candidates)


def test_reselection_clears_flags_when_previously_pinned_score_drops() -> None:
    # Given: a paper pinned on an earlier selection, now below the floor.
    executor = Executor.__new__(Executor)
    executor.config = executor_config(pin_keywords=["mamba"])
    candidate = paper("Mamba", 8.0)
    executor._select_pinned([candidate])
    candidate.score = 4.8

    # When: selecting the reused paper again.
    pinned, remaining = executor._select_pinned([candidate])

    # Then: the stale pinned flag cannot bypass normal presentation limits.
    assert pinned == []
    assert remaining == [candidate]
    assert candidate.pinned is False


@pytest.mark.parametrize("ordinary_case", [(4.0, ["Mamba low", "Normal"]), (5.0, ["Normal"])])
def test_run_applies_ordinary_floor_later_when_keyword_match_is_below_pin_floor(
    runtime_executor: Executor, monkeypatch: pytest.MonkeyPatch, ordinary_case: tuple[float, list[str]],
) -> None:
    # Given: low keyword match comes first in reranker order, then a normal pick.
    ordinary_floor, expected_normal = ordinary_case
    candidates = [paper("Mamba low", 4.8), paper("Normal", 6.0), paper("Mamba eligible", 4.95)]
    runtime_executor.config = executor_config(pin_keywords=["mamba"], min_score=ordinary_floor, max_paper_num=2)
    setattr(runtime_executor, "retrievers", {"source": StubRetriever(lambda: candidates)})
    setattr(runtime_executor, "reranker", RecordingReranker())
    selected: list[Paper] = []
    monkeypatch.setattr(runtime_executor, "_enrich_papers", lambda papers: selected.extend(papers))

    # When: the real executor applies pinning, ordinary score filtering, then cap.
    runtime_executor.run()

    # Then: only true pins bypass the ordinary floor; low matches can still surface.
    assert [candidate.title for candidate in selected] == ["Mamba eligible", *expected_normal]
    assert [candidate.pinned for candidate in selected] == [True, *([False] * len(expected_normal))]


def test_run_normal_cap_does_not_promote_overflow_when_regular_pick_precedes_it(
    runtime_executor: Executor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a one-paper normal cap and one-paper pinned cap.
    candidates = [paper("Mamba A", 10.0), paper("Normal B", 9.0), paper("Mamba C", 8.0)]
    runtime_executor.config = executor_config(pin_keywords=["mamba"], max_pinned_num=1, max_paper_num=1)
    setattr(runtime_executor, "retrievers", {"source": StubRetriever(lambda: candidates)})
    setattr(runtime_executor, "reranker", RecordingReranker())
    selected: list[Paper] = []
    monkeypatch.setattr(runtime_executor, "_enrich_papers", lambda papers: selected.extend(papers))

    # When: selecting and sending the final recommendation list.
    runtime_executor.run()

    # Then: the overflow match is not promoted above B or flagged as pinned.
    assert selected == candidates[:2]
    assert [candidate.pinned for candidate in candidates] == [True, False, False]
