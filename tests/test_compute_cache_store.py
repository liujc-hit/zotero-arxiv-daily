from dataclasses import replace
import base64
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
import numpy as np
from omegaconf import OmegaConf
import pytest

from zotero_arxiv_daily.compute_cache import (
    CacheSettings, ComputeCache, ScoreSnapshot, key_id, open_compute_cache,
)


def settings(path: Path, **overrides: int) -> CacheSettings:
    return replace(CacheSettings(enabled=True, key=Fernet.generate_key().decode(),
                                 path=path, model_revision="test-revision"), **overrides)


@pytest.mark.parametrize("dtype", ["<f4", ">f4", "<f8", ">f8"])
def test_roundtrip_when_reopened(tmp_path: Path, dtype: str) -> None:
    # Given a private input and an exact binary vector.
    cfg = settings(tmp_path / "cache")
    assert cfg.key is not None
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", {"title": "PRIVATE_SENTINEL", "x": [1, None]})
    vector = np.array([0.0, -0.0, 1.123456789, -8.5], dtype=dtype)
    cache.put_vector(key, vector)
    cache.put_scores(cache.make_key("scores", []), ScoreSnapshot((.1, .9), (1, 0)))
    assert cache.flush()
    # When a new instance reads the persisted vector.
    warm = ComputeCache(cfg)
    result = warm.get_vector(key)
    # Then numeric bits, byte order, privacy, and warm status are preserved.
    assert result is not None
    assert result.dtype.str == vector.dtype.str
    assert result.tobytes() == vector.tobytes()
    result[0] = 9
    second = warm.get_vector(key)
    assert second is not None
    assert second[0] == 0
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    assert warm.flush() is False
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    status = json.loads((tmp_path / "cache-status.json").read_bytes())
    assert status["changed"] is False
    assert status["vector_hits"] == 2
    assert cfg.key not in repr(cfg)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert b"PRIVATE_SENTINEL" not in p.read_bytes()
            assert cfg.key.encode() not in p.read_bytes()


def test_hmac_when_instances_have_distinct_keys(tmp_path: Path) -> None:
    # Given a known secret and canonical Unicode JSON.
    cfg = settings(tmp_path / "cache")
    assert cfg.key is not None
    raw = base64.urlsafe_b64decode(cfg.key)
    cache = ComputeCache(cfg)
    # When constructing the instance-bound key.
    actual = cache.make_key("vectors", {"z": 2, "a": "文"})
    # Then it matches the cross-language contract, not another instance.
    expected = hmac.new(raw, b'zad-compute-cache-v1\0vectors\0' +
                        '{"a":"文","z":2}'.encode(), hashlib.sha256).hexdigest()
    assert actual == expected
    assert key_id(cfg.key) == hmac.new(raw, b"zad-compute-cache-v1", hashlib.sha256).hexdigest()[:16]
    assert ComputeCache(settings(tmp_path / "other")).make_key("vectors", {"z": 2, "a": "文"}) != actual


def test_ttl_when_hit_does_not_extend_creation(tmp_path: Path) -> None:
    # Given a cache with an injected creation clock.
    cfg = settings(tmp_path / "cache")
    cache = ComputeCache(cfg, clock=lambda: 100.0)
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.0]))
    assert cache.flush()
    # When reading at the TTL boundary.
    warm = ComputeCache(cfg, clock=lambda: 100.0 + 7 * 86400)
    # Then the entry is expired.
    assert warm.get_vector(key) is None


def test_optional_when_config_missing_or_invalid(tmp_path: Path) -> None:
    # Given disabled, missing, or invalid optional configuration.
    for cfg in [None, OmegaConf.create({}), OmegaConf.create({"compute_cache": {"enabled": True, "key": "bad"}})]:
        # When resolving the optional store.
        cache = open_compute_cache(cfg)
        # Then the caller can bypass it.
        assert cache is None


def test_scores_when_invalid_permutation(tmp_path: Path) -> None:
    # Given a malformed order.
    cache = ComputeCache(settings(tmp_path / "cache"))
    key = cache.make_key("scores", [])
    # When attempting to cache it.
    cache.put_scores(key, ScoreSnapshot((1., 2.), (0, 0)))
    # Then no invalid snapshot is returned.
    assert cache.get_scores(key, 2) is None


def test_public_api_when_separate_local_processes(tmp_path: Path) -> None:
    # Given two isolated processes using only the public cache API and stdin secrets.
    secret = Fernet.generate_key().decode()
    script = '''
import json, sys
from pathlib import Path
import numpy as np
from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache
settings = json.loads(sys.stdin.read())
cache = ComputeCache(CacheSettings(enabled=True, key=settings["key"], path=Path(settings["path"]), model_revision="test"))
key = cache.make_key("vectors", {"private": "PROCESS_SENTINEL"})
result = cache.get_vector(key)
if result is None:
    cache.put_vector(key, np.array([1., 2.], dtype=np.float32))
else:
    assert result.tolist() == [1., 2.]
assert cache.flush() is (result is None)
print(json.dumps({"hit": result is not None, "writes": cache.stats().writes}))
'''
    payload = json.dumps({"key": secret, "path": str(tmp_path / "cache")})
    # When a cold process writes and a warm process reopens the encrypted state.
    cold = subprocess.run([sys.executable, "-c", script], input=payload, text=True, capture_output=True, check=True)
    warm = subprocess.run([sys.executable, "-c", script], input=payload, text=True, capture_output=True, check=True)
    # Then the persisted result is reused without cipher writes or exposed input.
    assert json.loads(cold.stdout) == {"hit": False, "writes": 2}
    assert json.loads(warm.stdout) == {"hit": True, "writes": 0}
    assert "PROCESS_SENTINEL" not in warm.stdout + warm.stderr


def test_activation_when_config_valid(tmp_path: Path) -> None:
    # Given an explicit complete OmegaConf cache section.
    cfg = settings(tmp_path / "cache")
    config = OmegaConf.create({"compute_cache": {"enabled": True, "key": cfg.key,
                               "path": str(cfg.path), "model_revision": "test"}})
    # When resolving configuration.
    cache = open_compute_cache(config)
    # Then activation is lazy with respect to the encrypted cache directory.
    assert cache is not None
    assert cache.flush() is False
    assert cache.is_ready
    assert not cfg.path.exists()
    assert cache.settings.budget_bytes == 8_000_000_000


def test_public_status_when_flushed_matches_strict_allowlist(tmp_path: Path) -> None:
    # Given private cached input and owned ciphertext files.
    cfg = settings(tmp_path / "cache")
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", {"title": "STATUS_PRIVATE_SENTINEL"})
    cache.put_vector(key, np.array([1., 2.], dtype=np.float32))
    assert cache.flush() is True
    # When publishing public metadata.
    assert cache.flush()
    # Then only the cross-language aggregate schema is serialized.
    payload = (tmp_path / "cache-status.json").read_bytes()
    status = json.loads(payload)
    assert set(status) == {"ready", "changed", "bytes", "vector_hits", "vector_misses",
                           "score_hits", "score_misses", "evictions", "writes"}
    assert status["bytes"] == cache.stats().bytes_used == sum(p.stat().st_size for p in cfg.path.iterdir())
    assert status["ready"] is True
    assert status["changed"] is True
    assert b"STATUS_PRIVATE_SENTINEL" not in payload
    assert key.encode() not in payload
    assert cfg.key is not None
    assert cfg.key.encode() not in payload


def test_activation_when_warm_config_has_no_new_ciphertext(tmp_path: Path) -> None:
    # Given valid persisted state and an explicit reopening configuration.
    cfg = settings(tmp_path / "cache")
    cold = ComputeCache(cfg)
    key = cold.make_key("vectors", [])
    cold.put_vector(key, np.array([1.]))
    assert cold.flush()
    config = OmegaConf.create({"compute_cache": {"enabled": True, "key": cfg.key,
                               "path": str(cfg.path), "model_revision": "test"}})
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    # When opening through the public factory and hitting existing state.
    warm = open_compute_cache(config)
    # Then availability remains independent of cumulative ciphertext changes.
    assert warm is not None
    assert warm.is_ready
    assert warm.get_vector(key) is not None
    assert warm.flush() is False
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    status = json.loads((tmp_path / "cache-status.json").read_bytes())
    assert status["ready"] is True
    assert status["changed"] is False


@pytest.mark.parametrize("budget", [8_000_000_001, True, False, 0, -1])
def test_bypass_when_budget_outside_integer_8gb_contract(tmp_path: Path, budget: int) -> None:
    # Given unsupported direct-constructor and OmegaConf budget values.
    cfg = settings(tmp_path / "direct", budget_bytes=budget)
    direct = ComputeCache(cfg)
    config = OmegaConf.create({"compute_cache": {"enabled": True, "key": cfg.key,
                               "path": str(tmp_path / "configured"), "model_revision": "test",
                               "budget_bytes": budget}})
    # When activation is attempted at both boundaries.
    opened = open_compute_cache(config)
    # Then both fail soft without creating ciphertext or reporting readiness.
    assert direct.flush() is False
    assert opened is None
    assert not direct.is_ready
    assert not cfg.path.exists()
    assert not (tmp_path / "configured").exists()
    assert json.loads((tmp_path / "direct-status.json").read_bytes())["ready"] is False
    assert json.loads((tmp_path / "configured-status.json").read_bytes())["ready"] is False
