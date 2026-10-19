"""Operation-count regression coverage for the cold encrypted write path."""
from pathlib import Path
import json
import sys
from time import perf_counter
from typing import Literal, assert_never

from cryptography.fernet import Fernet
import numpy as np
import pytest

from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache
from zotero_arxiv_daily.compute_cache import bounds, ledger
from zotero_arxiv_daily.compute_cache.codec import decrypt
from zotero_arxiv_daily.compute_cache.models import Manifest


@pytest.mark.parametrize("count", [100, 300])
def test_batch_when_cold_writes_bound_full_index_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    # Given real disk operations with counters, not fake successful writes.
    calls = {"inventory": 0, "manifest": 0, "orphans": 0}
    original_inventory = ledger.inventory
    original_manifest = ledger.Ledger._manifest_bytes
    original_orphans = ledger.Ledger._collect_orphans

    def inventory(path: Path) -> dict[str, int]:
        calls["inventory"] += 1
        return original_inventory(path)

    def manifest(store: ledger.Ledger) -> bytes:
        calls["manifest"] += 1
        return original_manifest(store)

    def orphans(store: ledger.Ledger) -> None:
        calls["orphans"] += 1
        original_orphans(store)

    monkeypatch.setattr(ledger, "inventory", inventory)
    monkeypatch.setattr(ledger.Ledger, "_manifest_bytes", manifest)
    monkeypatch.setattr(ledger.Ledger, "_collect_orphans", orphans)
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg, clock=lambda: 100.)
    # When an entire cold batch is written and explicitly flushed.
    for i in range(count):
        cache.put_vector(cache.make_key("vectors", i), np.full(32, i, dtype=np.float32))
    assert cache.flush() is True
    # Then full-index work is batch-bounded and every value survives reopening.
    assert calls["inventory"] <= 3, calls
    assert calls["manifest"] == 1, calls
    assert calls["orphans"] <= 3, calls
    assert cache.stats().writes == count + 1
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    warm = ComputeCache(cfg, clock=lambda: 100.)
    for i in range(count):
        result = warm.get_vector(warm.make_key("vectors", i))
        assert result is not None
        np.testing.assert_array_equal(result, np.full(32, i, dtype=np.float32))
    assert warm.flush() is False
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}


@pytest.mark.parametrize("cap", ["entries", "plain", "cipher"])
def test_eviction_when_pending_index_reaches_scaled_format_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: str) -> None:
    # Given independently scaled writer limits without large disk allocations.
    limits = {"entries": ("MAX_ENTRIES", 3), "plain": ("MAX_PLAIN", bounds.manifest_plain_bound(3)),
              "cipher": ("MAX_CIPHER", bounds.manifest_cipher_bound(3))}
    name, value = limits[cap]
    monkeypatch.setattr(bounds, name, value)
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg)
    # When more than three private records are admitted and published.
    for i in range(10):
        cache.put_vector(cache.make_key("vectors", i), np.array([float(i)]))
    assert cache.flush()
    # Then LRU eviction, not disabling or an unreadable manifest, bounds the index.
    assert cache.is_ready
    assert cache.stats().evictions == 7
    warm = ComputeCache(cfg)
    assert warm.get_vector(warm.make_key("vectors", 0)) is None
    assert warm.get_vector(warm.make_key("vectors", 9)) is not None


@pytest.mark.parametrize("now", [0., 1e-300, sys.float_info.max])
def test_reservation_when_timestamps_extreme_covers_encoded_index(tmp_path: Path, now: float) -> None:
    # Given the worst timestamp magnitudes allowed by the finite float boundary.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg, clock=lambda: now)
    for i in range(8):
        cache.put_vector(cache.make_key("vectors", i), np.array([float(i)]))
    # When the real encrypted/compressed manifest is published.
    assert cache.flush()
    # Then fixed-format reservations dominate actual plain and cipher sizes.
    data = (cfg.path / "manifest.enc").read_bytes()
    assert cfg.key is not None
    plain = decrypt(Fernet(cfg.key.encode()), data)
    parsed = Manifest.model_validate_json(plain)
    assert len(plain) <= bounds.manifest_plain_bound(len(parsed.entries))
    assert len(data) <= bounds.manifest_cipher_bound(len(parsed.entries))


@pytest.mark.parametrize("attack", ["unknown", "symlink", "owned-extra", "missing"])
def test_flush_when_directory_changes_after_initial_inventory_fails_closed(tmp_path: Path, attack: Literal["unknown", "symlink", "owned-extra", "missing"]) -> None:
    # Given a consistent published index, followed by another pending batch.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg)
    cache.put_vector(cache.make_key("vectors", 0), np.array([0.]))
    assert cache.flush()
    previous = (cfg.path / "manifest.enc").read_bytes()
    cache.put_vector(cache.make_key("vectors", 1), np.array([1.]))
    external = tmp_path / "private"
    external.write_bytes(b"DO_NOT_CHANGE")
    match attack:
        case "symlink":
            target = next(cfg.path.glob("shard-*.enc"))
            target.unlink()
            target.symlink_to(external)
        case "missing":
            next(cfg.path.glob("shard-*.enc")).unlink()
        case "unknown" | "owned-extra":
            name = ".env" if attack == "unknown" else "shard-" + "f" * 32 + ".enc"
            (cfg.path / name).write_bytes(b"DO_NOT_CHANGE")
        case _:
            assert_never(attack)
    # When flush rechecks ownership and regular files before publishing.
    assert cache.flush() is False
    # Then unsafe state cannot authorize upload or touch external/private files.
    assert not cache.is_ready
    assert json.loads((tmp_path / "cache-status.json").read_bytes())["ready"] is False
    assert (cfg.path / "manifest.enc").read_bytes() == previous
    assert external.read_bytes() == b"DO_NOT_CHANGE"


def test_status_when_new_batch_pending_blocks_upload_until_flush(tmp_path: Path) -> None:
    # Given a successful first batch whose public status authorizes publication.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg)
    cache.put_vector(cache.make_key("vectors", 0), np.array([0.]))
    assert cache.flush()
    # When another batch changes shards without publishing its index yet.
    cache.put_vector(cache.make_key("vectors", 1), np.array([1.]))
    # Then stale success metadata cannot authorize archiving an unfinished batch.
    status = json.loads((tmp_path / "cache-status.json").read_bytes())
    assert status["ready"] is False
    assert cache.is_ready
    assert cache.flush()
    assert json.loads((tmp_path / "cache-status.json").read_bytes())["ready"] is True


def test_reuse_when_pending_replacement_returns_to_original_content(tmp_path: Path) -> None:
    # Given immutable files from successive replacements under a fixed creation clock.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg, clock=lambda: 100.)
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([0.]))
    original = next(cfg.path.glob("shard-*.enc"))
    ciphertext = original.read_bytes()
    cache.put_vector(key, np.array([1.]))
    # When the original file becomes live again before orphan collection.
    cache.put_vector(key, np.array([0.]))
    assert cache.flush()
    # Then cleanup preserves the reused ciphertext and a valid durable reference.
    assert original.read_bytes() == ciphertext
    warm = ComputeCache(cfg, clock=lambda: 100.)
    result = warm.get_vector(key)
    assert result is not None
    assert result.tolist() == [0.]


def test_flush_when_warm_root_disappears_fails_closed(tmp_path: Path) -> None:
    # Given a warm instance whose owned cache directory subsequently moves away.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg)
    cache.put_vector(cache.make_key("vectors", 0), np.array([0.]))
    assert cache.flush()
    warm = ComputeCache(cfg)
    cfg.path.rename(tmp_path / "moved")
    # When a no-write flush checks the expected snapshot.
    assert warm.flush() is False
    # Then absence is not mistaken for unchanged, upload-ready state.
    assert not warm.is_ready


@pytest.mark.parametrize("count", [300, 3000])
@pytest.mark.parametrize("dimensions", [32, 128])
def test_local_batch_when_scaled_vectors_reuse_exact_keys_and_bits(tmp_path: Path, count: int, dimensions: int) -> None:
    # Given deterministic local float32 vectors, without an encoder/API or array cache.
    cfg = CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=tmp_path / "cache", model_revision="test")
    cache = ComputeCache(cfg, clock=lambda: 100.)
    generator = np.random.default_rng(42)
    started = perf_counter()
    # When a cold batch is published and the same inputs are reopened warm.
    for i in range(count):
        cache.put_vector(cache.make_key("vectors", {"i": i, "dimensions": dimensions}),
                         generator.random(dimensions, dtype=np.float32))
    assert cache.flush()
    cold_seconds = perf_counter() - started
    started = perf_counter()
    warm = ComputeCache(cfg, clock=lambda: 100.)
    generator = np.random.default_rng(42)
    for i in range(count):
        result = warm.get_vector(warm.make_key("vectors", {"i": i, "dimensions": dimensions}))
        assert result is not None, {"index": i, "ready": warm.is_ready, "stats": warm.stats()}
        assert result.tobytes() == generator.random(dimensions, dtype=np.float32).tobytes()
    assert warm.flush() is False
    warm_seconds = perf_counter() - started
    # Then physical cipher writes are linear; timings are evidence, not flaky gates.
    assert cache.stats().writes == count + 1
    assert warm.stats().vector_hits == count
    assert warm.stats().writes == 0
    print(f"cache benchmark count={count} dimensions={dimensions} cold={cold_seconds:.3f}s warm={warm_seconds:.3f}s writes={cache.stats().writes}")
