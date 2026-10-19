from dataclasses import replace
import json
from pathlib import Path
import zlib
from typing import Literal, assert_never

from cryptography.fernet import Fernet
import numpy as np
from omegaconf import OmegaConf
import pytest

from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache, ScoreSnapshot, open_compute_cache
from zotero_arxiv_daily.compute_cache.codec import MAX_PLAIN, InvalidCache, decrypt, encrypt
from zotero_arxiv_daily.compute_cache.ledger import MAX_CLOCK_SKEW_SECONDS


def configured(path: Path) -> CacheSettings:
    return CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=path,
                         model_revision="revision")


@pytest.mark.parametrize("attack", ["wrong-key", "corrupt", "missing", "symlink", "permissions"])
def test_miss_when_storage_untrusted(tmp_path: Path, attack: Literal["wrong-key", "corrupt", "missing", "symlink", "permissions"]) -> None:
    # Given a populated encrypted cache.
    cfg = configured(tmp_path / "cache")
    assert cfg.key is not None
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    shard = next(cfg.path.glob("shard-*.enc"))
    target = tmp_path / "untouched"
    target.write_bytes(b"PRIVATE_EXTERNAL")
    # When local cache state becomes unavailable or hostile.
    match attack:
        case "wrong-key":
            cfg = replace(cfg, key=Fernet.generate_key().decode())
        case "corrupt":
            shard.write_bytes(b"tamper")
        case "missing":
            shard.unlink()
        case "symlink":
            shard.unlink()
            shard.symlink_to(target)
        case "permissions":
            shard.chmod(0)
        case _:
            assert_never(attack)
    warm = ComputeCache(cfg)
    # Then the workflow gets a miss without external file changes.
    assert warm.get_vector(key) is None
    assert target.read_bytes() == b"PRIVATE_EXTERNAL"
    assert not warm.flush()


def test_decode_when_authenticated_compression_bomb() -> None:
    # Given an authenticated payload whose expansion exceeds the hard ceiling.
    cipher = Fernet(Fernet.generate_key())
    payload = cipher.encrypt(zlib.compress(b"x" * (MAX_PLAIN + 1)))
    # When decoding.
    with pytest.raises(InvalidCache):
        decrypt(cipher, payload)
    # Then decompression never exposes an unbounded result.


@pytest.mark.parametrize("mutation", ["path", "unknown", "future", "dtype", "count", "version"])
def test_miss_when_authenticated_schema_invalid(tmp_path: Path, mutation: Literal["path", "unknown", "future", "dtype", "count", "version"]) -> None:
    # Given legitimate encrypted state and possession of a test-only key.
    cfg = configured(tmp_path / "cache")
    assert cfg.key is not None
    cache = ComputeCache(cfg, clock=lambda: 100.)
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    cipher = Fernet(cfg.key.encode())
    manifest_path = cfg.path / "manifest.enc"
    manifest = json.loads(decrypt(cipher, manifest_path.read_bytes()))
    shard = cfg.path / manifest["entries"][key]["name"]
    record = json.loads(decrypt(cipher, shard.read_bytes()))
    # When authenticated JSON violates its structural boundary.
    match mutation:
        case "path":
            manifest["entries"][key]["name"] = "../outside.enc"
        case "unknown":
            record["pickle"] = "malicious"
        case "future":
            manifest["entries"][key]["created"] = 100. + MAX_CLOCK_SKEW_SECONDS + 1
            manifest["entries"][key]["touched"] = 100. + MAX_CLOCK_SKEW_SECONDS + 1
        case "dtype":
            record["dtype"] = "|O"
        case "count":
            record["count"] = 1_000_001
        case "version":
            record["version"] = 99
        case _:
            assert_never(mutation)
    manifest_path.write_bytes(encrypt(cipher, json.dumps(manifest).encode()))
    shard.write_bytes(encrypt(cipher, json.dumps(record).encode()))
    warm = ComputeCache(cfg, clock=lambda: 100.)
    # Then no hostile record becomes a numeric allocation or cache hit.
    assert warm.get_vector(key) is None


def test_bypass_when_root_symlink_or_unknown_file(tmp_path: Path) -> None:
    # Given unrelated data in a proposed cache root.
    root = tmp_path / "real"
    root.mkdir()
    private = root / ".env"
    private.write_bytes(b"UNTOUCHED")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    # When activating either unsafe root.
    for path in [root, link]:
        cache = ComputeCache(configured(path))
        # Then activation refuses cache writes and preserves unrelated data.
        assert not cache.flush()
    assert list(root.iterdir()) == [private]
    assert private.read_bytes() == b"UNTOUCHED"


def test_small_budget_when_read_touch_changes_eviction(tmp_path: Path) -> None:
    # Given two shards and enough reserved space for one replacement manifest.
    cfg = replace(configured(tmp_path / "cache"), budget_bytes=3500)
    now = [100.]
    cache = ComputeCache(cfg, clock=lambda: now[0])
    keys = [cache.make_key("vectors", i) for i in range(3)]
    vectors = [np.random.default_rng(i).random(64) for i in range(3)]
    cache.put_vector(keys[0], vectors[0])
    now[0] += 1
    cache.put_vector(keys[1], vectors[1])
    now[0] += 1
    assert cache.get_vector(keys[0]) is not None
    now[0] += 1
    # When a new shard forces eviction.
    cache.put_vector(keys[2], vectors[2])
    # Then the read-touched first shard wins over the second.
    assert cache.get_vector(keys[0]) is not None
    assert cache.get_vector(keys[1]) is None
    assert cache.get_vector(keys[2]) is not None
    assert cache.stats().evictions == 1
    assert cache.stats().bytes_used == sum(p.stat().st_size for p in cfg.path.iterdir()) <= cfg.budget_bytes
    assert cache.flush()
    warm = ComputeCache(cfg, clock=lambda: now[0])
    assert warm.get_vector(keys[0]) is not None


def test_skip_when_record_larger_than_shard_or_budget(tmp_path: Path) -> None:
    # Given a tiny ciphertext budget and a larger vector.
    cfg = replace(configured(tmp_path / "cache"), budget_bytes=500, shard_bytes=400)
    cache = ComputeCache(cfg)
    key = cache.make_key("vectors", [])
    # When attempting an oversized write.
    cache.put_vector(key, np.random.default_rng(1).random(1000))
    # Then no shard is cached or allocated beyond the budget.
    assert cache.get_vector(key) is None
    assert cache.stats().bytes_used <= 500


def test_scores_when_cold_warm_and_count_mismatch(tmp_path: Path) -> None:
    # Given a valid complete score permutation.
    cfg = configured(tmp_path / "cache")
    cache = ComputeCache(cfg)
    key = cache.make_key("scores", [])
    snapshot = ScoreSnapshot((.5, .2, .9), (2, 0, 1))
    cache.put_scores(key, snapshot)
    assert cache.flush()
    # When reopened with matching and differing candidate counts.
    warm = ComputeCache(cfg)
    # Then only the full validated permutation is returned.
    assert warm.get_scores(key, 3) == snapshot
    assert warm.get_scores(key, 2) is None
    assert warm.stats().score_hits == 1
    assert warm.stats().score_misses == 1


def test_reuse_when_same_record_written_again(tmp_path: Path) -> None:
    # Given an immutable existing record.
    cfg = configured(tmp_path / "cache")
    now = [100.]
    cache = ComputeCache(cfg, clock=lambda: now[0])
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    now[0] += 1
    # When writing the same value under the same private key.
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    # Then immutable data is reused without ciphertext churn or TTL renewal.
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}


def test_status_when_config_missing_clears_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given stale public metadata from an earlier process.
    monkeypatch.chdir(tmp_path)
    status = tmp_path / ".cache/zad-compute-status.json"
    status.parent.mkdir()
    status.write_text('{"ready":true,"changed":true}')
    # When optional configuration is absent.
    assert open_compute_cache(None) is None
    # Then the workflow cannot upload based on stale success.
    assert json.loads(status.read_bytes())["ready"] is False
    assert json.loads(status.read_bytes())["changed"] is False
