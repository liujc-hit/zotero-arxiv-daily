from dataclasses import replace
import json
from pathlib import Path
from collections.abc import Callable

from cryptography.fernet import Fernet
from loguru import logger
import numpy as np
from omegaconf import OmegaConf
import pytest

from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache, ScoreSnapshot, open_compute_cache
from zotero_arxiv_daily.compute_cache import ledger


def configured(path: Path) -> CacheSettings:
    return CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=path, model_revision="test")


def test_reservation_when_atomic_ciphertext_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a small ledger and an observer at the actual ciphertext write boundary.
    cfg = replace(configured(tmp_path / "cache"), budget_bytes=3500)
    original: Callable[[Path, bytes], None] = ledger.atomic_write
    peaks: list[int] = []

    def observed(path: Path, data: bytes) -> None:
        peak = sum(p.stat().st_size for p in cfg.path.iterdir()) + len(data)
        peaks.append(peak)
        assert peak <= cfg.budget_bytes
        original(path, data)

    monkeypatch.setattr(ledger, "atomic_write", observed)
    cache = ComputeCache(cfg)
    # When successive writes force bounded shard eviction and manifest replacement.
    for i in range(8):
        cache.put_vector(cache.make_key("vectors", i), np.random.default_rng(i).random(64))
    assert cache.flush()
    for i in range(8, 12):
        cache.put_vector(cache.make_key("vectors", i), np.random.default_rng(i).random(64))
    assert cache.flush()
    # Then even temporary replacement reservations stay within the limit.
    assert peaks
    assert cache.stats().evictions > 0
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in cfg.path.iterdir())


@pytest.mark.parametrize("snapshot", [ScoreSnapshot((1.,), (-1,)), ScoreSnapshot((1.,), (1,)),
                                     ScoreSnapshot((1., 2.), (0,)), ScoreSnapshot((float("nan"),), (0,)),
                                     ScoreSnapshot((float("inf"),), (0,))])
def test_skip_when_scores_invalid(tmp_path: Path, snapshot: ScoreSnapshot) -> None:
    # Given a non-finite score or incomplete/out-of-range order.
    cfg = configured(tmp_path / "cache")
    cache = ComputeCache(cfg)
    key = cache.make_key("scores", [])
    # When presented to the public write API.
    cache.put_scores(key, snapshot)
    # Then no invalid data file exists.
    assert cache.get_scores(key, len(snapshot.scores)) is None
    assert not cfg.path.exists()


@pytest.mark.parametrize("variant", ["nan", "inf", "matrix", "integer"])
def test_skip_when_vector_unsupported(tmp_path: Path, variant: str) -> None:
    # Given an input outside the finite float32/float64 one-dimensional contract.
    array = {"nan": np.array([float("nan")]), "inf": np.array([float("inf")]),
             "matrix": np.array([[1.]]), "integer": np.array([1], dtype="i8")}[variant]
    cfg = configured(tmp_path / "cache")
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", [])
    # When attempting to persist it.
    cache.put_vector(key, array)
    # Then there is neither a hit nor a plaintext/ciphertext data file.
    assert cache.get_vector(key) is None
    assert not cfg.path.exists()


def test_status_when_custom_config_invalid(tmp_path: Path) -> None:
    # Given stale custom metadata and invalid activation options.
    cfg = configured(tmp_path / "custom")
    status = tmp_path / "custom-status.json"
    status.write_text('{"ready":true,"changed":true}')
    config = OmegaConf.create({"compute_cache": {"enabled": True, "key": cfg.key,
                              "path": str(cfg.path), "budget_bytes": -1}})
    # When the factory rejects configuration.
    assert open_compute_cache(config) is None
    # Then the custom status is cleared without creating cache data.
    assert json.loads(status.read_bytes())["ready"] is False
    assert json.loads(status.read_bytes())["changed"] is False
    assert not cfg.path.exists()


def test_logging_when_failure_repeated_is_opaque(tmp_path: Path) -> None:
    # Given a cache whose private record will become corrupt.
    cfg = configured(tmp_path / "PRIVATE_PATH_SENTINEL")
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", {"abstract": "PRIVATE_CORPUS_SENTINEL"})
    cache.put_vector(key, np.array([1.]))
    next(cfg.path.glob("shard-*.enc")).write_bytes(b"bad")
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        # When repeated accesses encounter the same failed cache boundary.
        assert cache.get_vector(key) is None
        assert cache.get_vector(key) is None
        assert not cache.flush()
    finally:
        logger.remove(sink)
    # Then only one sanitized warning is emitted, without identifiers or secrets.
    assert len(messages) == 1
    assert cfg.key is not None
    assert all(value not in messages[0] for value in [cfg.key, key, "PRIVATE_PATH_SENTINEL", "PRIVATE_CORPUS_SENTINEL"])


def test_ttl_when_recently_touched_still_expires(tmp_path: Path) -> None:
    # Given a vector read successfully shortly before expiry.
    cfg = configured(tmp_path / "cache")
    cache = ComputeCache(cfg, clock=lambda: 100.)
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    recent = ComputeCache(cfg, clock=lambda: 100. + 6 * 86400)
    assert recent.get_vector(key) is not None
    recent.put_scores(recent.make_key("scores", []), ScoreSnapshot((), ()))
    assert recent.flush()
    # When the creation-based seven-day deadline is reached.
    expired = ComputeCache(cfg, clock=lambda: 100. + 7 * 86400)
    # Then the persisted recent read touch does not extend TTL.
    assert expired.get_vector(key) is None


def test_recovery_when_owned_encrypted_temporary_orphan(tmp_path: Path) -> None:
    # Given an interrupted write's encrypted owned file outside the manifest.
    cfg = replace(configured(tmp_path / "cache"), budget_bytes=3500)
    cache = ComputeCache(cfg)
    first = cache.make_key("vectors", 1)
    cache.put_vector(first, np.array([1.]))
    assert cache.flush()
    orphan = cfg.path / ("shard-" + "a" * 32 + ".enc")
    assert cfg.key is not None
    orphan.write_bytes(Fernet(cfg.key.encode()).encrypt(b"x" * 2000))
    cache = ComputeCache(cfg)
    next_key = cache.make_key("vectors", 2)
    # When a substantive write reserves its budget.
    cache.put_vector(next_key, np.array([2.]))
    # Then orphan ciphertext cannot permanently block the optional cache.
    assert cache.get_vector(next_key) is not None
    assert cache.get_vector(first) is not None
    assert cache.stats().bytes_used <= cfg.budget_bytes
