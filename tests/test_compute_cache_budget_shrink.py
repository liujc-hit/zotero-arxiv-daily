"""Hit-only reopening must honor a smaller physical cache budget."""
import base64
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache
from zotero_arxiv_daily.compute_cache import ledger
from zotero_arxiv_daily.compute_cache.files import atomic_write


@pytest.fixture
def populated(tmp_path: Path) -> tuple[CacheSettings, tuple[str, ...]]:
    cfg = CacheSettings(enabled=True, key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
                        path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg, clock=lambda: 100.)
    keys = tuple(cache.make_key("vectors", i) for i in range(3))
    for i, key in enumerate(keys):
        cache.put_vector(key, np.random.default_rng(i).random(512))
    assert cache.flush()
    return cfg, keys


@pytest.mark.parametrize("touch_oldest", [False, True])
def test_shrink_when_reopened_with_only_hits_uses_read_lru(
    populated: tuple[CacheSettings, tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
    touch_oldest: bool,
) -> None:
    # Given a published cache larger than the new budget, with no pending puts.
    cfg, keys = populated
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    cfg = replace(cfg, budget_bytes=sum(map(len, before.values())) - 1)
    cache = ComputeCache(cfg, clock=lambda: 200.)
    touched = keys[0] if touch_oldest else keys[2]
    assert cache.get_vector(touched) is not None
    publications: list[str] = []

    def observed(path: Path, data: bytes) -> None:
        assert sum(p.stat().st_size for p in cfg.path.iterdir()) + len(data) <= cfg.budget_bytes
        publications.append(path.name)
        atomic_write(path, data)

    monkeypatch.setattr(ledger, "atomic_write", observed)
    # When the hit-only instance flushes under the reduced budget.
    changed = cache.flush()
    # Then one LRU shard is evicted, while unchanged encrypted survivors persist.
    assert changed is True
    assert cache.is_ready
    assert cache.stats().evictions == 1
    assert cache.stats().writes == 1
    assert publications == ["manifest.enc"]
    physical = sum(p.stat().st_size for p in cfg.path.iterdir())
    assert cache.stats().bytes_used == physical <= cfg.budget_bytes
    for path in cfg.path.glob("shard-*.enc"):
        assert path.read_bytes() == before[path.name]
    assert json.loads((cfg.path.parent / "cache-status.json").read_bytes()) == {
        "ready": True, "changed": True, "bytes": physical, "vector_hits": 1,
        "vector_misses": 0, "score_hits": 0, "score_misses": 0, "evictions": 1, "writes": 1,
    }
    snapshot = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    stats = cache.stats()
    assert cache.flush() is True  # The public result reports cumulative changes.
    assert cache.stats() == stats
    assert publications == ["manifest.enc"]
    assert {p.name: p.read_bytes() for p in cfg.path.iterdir()} == snapshot
    warm = ComputeCache(cfg, clock=lambda: 200.)
    evicted = keys[1] if touch_oldest else keys[0]
    for i, key in enumerate(keys):
        result = warm.get_vector(key)
        if key == evicted:
            assert result is None
        else:
            assert result is not None
            np.testing.assert_array_equal(result, np.random.default_rng(i).random(512))
    assert warm.flush() is False
    assert {p.name: p.read_bytes() for p in cfg.path.iterdir()} == snapshot
    assert json.loads((cfg.path.parent / "cache-status.json").read_bytes())["changed"] is False


@pytest.mark.parametrize("replacement_headroom", [False, True])
def test_flush_when_clean_hits_fit_budget_is_physical_noop(
    populated: tuple[CacheSettings, tuple[str, ...]], replacement_headroom: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given warm hits whose physical files fit, even without replacement headroom.
    cfg, keys = populated
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    if not replacement_headroom:
        cfg = replace(cfg, budget_bytes=sum(map(len, before.values())))
    cache = ComputeCache(cfg, clock=lambda: 200.)
    for key in reversed(keys):
        assert cache.get_vector(key) is not None

    def unexpected_write(path: Path, _data: bytes) -> None:
        pytest.fail(f"Clean flush attempted ciphertext replacement: {path.name}")

    monkeypatch.setattr(ledger, "atomic_write", unexpected_write)
    # When flushing without any substantive write or budget violation.
    changed = cache.flush()
    # Then ciphertext, readiness, and zero mutation counters are preserved.
    assert changed is False
    assert cache.is_ready
    assert cache.stats().writes == cache.stats().evictions == 0
    assert cache.stats().bytes_used == sum(map(len, before.values()))
    assert {p.name: p.read_bytes() for p in cfg.path.iterdir()} == before
    assert json.loads((cfg.path.parent / "cache-status.json").read_bytes()) == {
        "ready": True, "changed": False, "bytes": sum(map(len, before.values())),
        "vector_hits": len(keys), "vector_misses": 0, "score_hits": 0, "score_misses": 0,
        "evictions": 0, "writes": 0,
    }


@pytest.mark.parametrize("budget", [1, 100, 200, 400])
def test_shrink_when_budget_tiny_finishes_with_valid_empty_cache(
    populated: tuple[CacheSettings, tuple[str, ...]], budget: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a supported positive budget too small for any existing shard.
    cfg, keys = populated
    cfg = replace(cfg, budget_bytes=budget)
    cache = ComputeCache(cfg, clock=lambda: 200.)
    assert cache.get_vector(keys[0]) is not None
    publications: list[str] = []

    def observed(path: Path, data: bytes) -> None:
        assert sum(p.stat().st_size for p in cfg.path.iterdir()) + len(data) <= budget
        publications.append(path.name)
        atomic_write(path, data)

    monkeypatch.setattr(ledger, "atomic_write", observed)
    # When hit-only flush must reach the empty-cache terminal case.
    changed = cache.flush()
    # Then neither an oversized obsolete manifest nor dangling shard survives.
    assert changed is True
    assert cache.is_ready
    physical = sum(p.stat().st_size for p in cfg.path.iterdir())
    assert cache.stats().bytes_used == physical <= budget
    assert cache.stats().evictions == len(keys)
    assert cache.stats().writes == len(publications)
    assert set(p.name for p in cfg.path.iterdir()) <= {"manifest.enc"}
    assert json.loads((cfg.path.parent / "cache-status.json").read_bytes()) == {
        "ready": True, "changed": True, "bytes": physical, "vector_hits": 1,
        "vector_misses": 0, "score_hits": 0, "score_misses": 0,
        "evictions": len(keys), "writes": len(publications),
    }
    stats = cache.stats()
    assert cache.flush() is True
    assert cache.stats() == stats
    warm = ComputeCache(cfg, clock=lambda: 200.)
    assert warm.is_ready
    assert all(warm.get_vector(key) is None for key in keys)
    assert warm.flush() is False
    assert warm.stats().bytes_used == physical
