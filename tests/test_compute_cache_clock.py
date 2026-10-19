"""Bounded wall-clock skew must not poison unrelated private cache records."""
import json
from pathlib import Path
import subprocess
import sys
from typing import Literal

from cryptography.fernet import Fernet
import numpy as np
import pytest

from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache, ScoreSnapshot
from zotero_arxiv_daily.compute_cache.codec import decrypt, encrypt
from zotero_arxiv_daily.compute_cache.models import Manifest


def settings(path: Path) -> CacheSettings:
    return CacheSettings(enabled=True, key=Fernet.generate_key().decode(), path=path, model_revision="test")


@pytest.mark.parametrize("backstep", [2., 2.37, 300.])
def test_hits_when_warm_clock_steps_back(tmp_path: Path, backstep: float) -> None:
    # Given vector/score records created before a small wall-clock adjustment.
    cfg = settings(tmp_path / "cache")
    cold = ComputeCache(cfg, clock=lambda: 1000.)
    vector_key = cold.make_key("vectors", [])
    score_key = cold.make_key("scores", [])
    vector = np.array([0., -0., 1.125], dtype=">f4")
    snapshot = ScoreSnapshot((.2, .8), (1, 0))
    cold.put_vector(vector_key, vector)
    cold.put_scores(score_key, snapshot)
    assert cold.flush()
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    # When a new instance reads with an earlier, still valid clock.
    warm = ComputeCache(cfg, clock=lambda: 1000. - backstep)
    result = warm.get_vector(vector_key)
    # Then both record kinds survive without cipher writes or sticky bypass.
    assert result is not None
    assert result.dtype.str == vector.dtype.str
    assert result.tobytes() == vector.tobytes()
    assert warm.get_scores(score_key, 2) == snapshot
    assert warm.is_ready
    assert warm.flush() is False
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    assert ComputeCache(cfg, clock=lambda: 1000. + 7 * 86400 - 1).get_vector(vector_key) is not None
    assert ComputeCache(cfg, clock=lambda: 1000. + 7 * 86400).get_vector(vector_key) is None


@pytest.mark.parametrize("backstep", [1.19, 2.])
def test_hits_when_clock_steps_back_mid_cold_batch(tmp_path: Path, backstep: float) -> None:
    # Given a single batch spanning a small backward clock adjustment.
    cfg = settings(tmp_path / "cache")
    now = [1000.]
    cache = ComputeCache(cfg, clock=lambda: now[0])
    for i in range(4):
        if i == 2:
            now[0] -= backstep
        cache.put_vector(cache.make_key("vectors", i), np.array([float(i)]))
        cache.put_scores(cache.make_key("scores", i), ScoreSnapshot((float(i),), (0,)))
    assert cache.flush()
    # When all records are read at the adjusted wall clock.
    warm = ComputeCache(cfg, clock=lambda: now[0])
    results = [warm.get_vector(warm.make_key("vectors", i)) for i in range(4)]
    # Then old and new timestamps coexist without invalidating the cache.
    assert all(result is not None for result in results)
    for i in range(4):
        assert warm.get_scores(warm.make_key("scores", i), 1) == ScoreSnapshot((float(i),), (0,))
    assert warm.is_ready


@pytest.mark.parametrize("damage", ["created", "touched", "chronology"])
def test_miss_when_one_timestamp_invalid_preserves_other_entries(tmp_path: Path, damage: Literal["created", "touched", "chronology"]) -> None:
    # Given one valid record and one authenticated but temporally invalid entry.
    cfg = settings(tmp_path / "cache")
    cold = ComputeCache(cfg, clock=lambda: 1000.)
    good = cold.make_key("vectors", "good")
    bad = cold.make_key("vectors", "bad")
    cold.put_vector(good, np.array([1.]))
    cold.put_vector(bad, np.array([2.]))
    assert cold.flush()
    assert cfg.key is not None
    cipher = Fernet(cfg.key.encode())
    path = cfg.path / "manifest.enc"
    manifest = Manifest.model_validate_json(decrypt(cipher, path.read_bytes()))
    stamps = {"created": (1301., 1301.), "touched": (1000., 1301.), "chronology": (1000., 999.)}
    created, touched = stamps[damage]
    entry = manifest.entries[bad]
    manifest.entries[bad] = entry.model_copy(update={"created": created, "touched": touched})
    path.write_bytes(encrypt(cipher, manifest.model_dump_json().encode()))
    shard = cfg.path / entry.name
    shard.write_bytes(b"x" * shard.stat().st_size)
    # When the bad entry is checked before its intentionally corrupt shard.
    warm = ComputeCache(cfg, clock=lambda: 1000.)
    assert warm.get_vector(bad) is None
    # Then timestamp rejection is entry-local and happens before decryption.
    assert warm.is_ready
    result = warm.get_vector(good)
    assert result is not None
    assert result.tolist() == [1.]
    assert warm.flush() is False


@pytest.mark.parametrize("touch_ahead", [2., 300.])
def test_touch_when_clock_backsteps_stays_monotone_without_sliding_ttl(tmp_path: Path, touch_ahead: float) -> None:
    # Given creation in the past and an accepted future last-read timestamp.
    cfg = settings(tmp_path / "cache")
    cold = ComputeCache(cfg, clock=lambda: 900.)
    key = cold.make_key("vectors", [])
    cold.put_vector(key, np.array([1.]))
    assert cold.flush()
    touched = ComputeCache(cfg, clock=lambda: 1000. + touch_ahead)
    assert touched.get_vector(key) is not None
    touched.put_scores(touched.make_key("scores", []), ScoreSnapshot((), ()))
    assert touched.flush()
    # When a backward-clock read is persisted along with a substantive write.
    warm = ComputeCache(cfg, clock=lambda: 1000.)
    assert warm.get_vector(key) is not None
    warm.put_scores(warm.make_key("scores", "next"), ScoreSnapshot((), ()))
    assert warm.flush()
    # Then creation is fixed and last-read time never regresses.
    assert cfg.key is not None
    manifest = Manifest.model_validate_json(decrypt(Fernet(cfg.key.encode()), (cfg.path / "manifest.enc").read_bytes()))
    assert manifest.entries[key].created == 900.
    assert manifest.entries[key].touched == 1000. + touch_ahead
    before_expiry = ComputeCache(cfg, clock=lambda: 900. + 7 * 86400 - 1)
    assert before_expiry.get_vector(key) is not None
    expired = ComputeCache(cfg, clock=lambda: 900. + 7 * 86400)
    assert expired.get_vector(key) is None
    assert expired.is_ready


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1.])
def test_bypass_when_active_clock_becomes_invalid(tmp_path: Path, invalid: float) -> None:
    # Given an available cache and an injectable clock that subsequently breaks.
    cfg = settings(tmp_path / "cache")
    now = [1000.]
    cache = ComputeCache(cfg, clock=lambda: now[0])
    key = cache.make_key("vectors", [])
    cache.put_vector(key, np.array([1.]))
    assert cache.flush()
    now[0] = invalid
    # When any entry is requested with a nonfinite or negative current time.
    assert cache.get_vector(key) is None
    # Then invalid clocks still cause global safe bypass.
    assert not cache.is_ready
    now[0] = 1000.
    assert cache.get_vector(key) is None


def test_hits_when_new_process_backsteps_with_mixed_timestamp_validity(tmp_path: Path) -> None:
    # Given private vectors/scores, including two entries beyond the skew allowance.
    cfg = settings(tmp_path / "cache")
    cold = ComputeCache(cfg, clock=lambda: 1000.)
    for i in range(2):
        cold.put_vector(cold.make_key("vectors", i), np.array([0., -0., 1.125], dtype=">f4"))
        cold.put_scores(cold.make_key("scores", i), ScoreSnapshot((.2, .8), (1, 0)))
    assert cold.flush()
    assert cfg.key is not None
    cipher = Fernet(cfg.key.encode())
    path = cfg.path / "manifest.enc"
    manifest = Manifest.model_validate_json(decrypt(cipher, path.read_bytes()))
    for kind in ("vectors", "scores"):
        key = cold.make_key(kind, 1)
        manifest.entries[key] = manifest.entries[key].model_copy(update={"created": 1400., "touched": 1400.})
    path.write_bytes(encrypt(cipher, manifest.model_dump_json().encode()))
    before = {p.name: p.read_bytes() for p in cfg.path.iterdir()}
    script = '''
import json, sys
from pathlib import Path
import numpy as np
import zotero_arxiv_daily.compute_cache as compute_cache
from zotero_arxiv_daily.compute_cache import CacheSettings, ComputeCache, ScoreSnapshot
config = json.loads(sys.stdin.read())
assert Path(compute_cache.__file__).resolve() == Path(config["source"]) / "zotero_arxiv_daily/compute_cache/__init__.py"
cache = ComputeCache(CacheSettings(enabled=True, key=config["key"], path=Path(config["path"]), model_revision="test"), clock=lambda: 998.)
assert cache.get_vector(cache.make_key("vectors", 1)) is None
assert cache.get_scores(cache.make_key("scores", 1), 2) is None
vector = cache.get_vector(cache.make_key("vectors", 0))
assert vector is not None
assert vector.dtype.str == ">f4"
assert vector.tobytes() == np.array([0., -0., 1.125], dtype=">f4").tobytes()
assert cache.get_scores(cache.make_key("scores", 0), 2) == ScoreSnapshot((.2, .8), (1, 0))
assert cache.is_ready
assert cache.flush() is False
print(json.dumps({"vector_hits": cache.stats().vector_hits, "vector_misses": cache.stats().vector_misses, "score_hits": cache.stats().score_hits, "score_misses": cache.stats().score_misses}))
'''
    # When a new process uses the same synthetic key at an earlier clock.
    source = Path(__file__).resolve().parents[1] / "src"
    child_env = {"PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1",
                 "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps({"key": cfg.key, "path": str(cfg.path), "source": str(source)}),
                            text=True, capture_output=True, check=True, env=child_env)
    # Then expected misses do not poison valid hits or mutate persisted ciphertext.
    assert json.loads(result.stdout) == {"vector_hits": 1, "vector_misses": 1, "score_hits": 1, "score_misses": 1}
    assert before == {p.name: p.read_bytes() for p in cfg.path.iterdir()}


def test_hits_when_default_real_clock_reopens_repeatedly(tmp_path: Path) -> None:
    # Given ten independent synthetic cold caches using the actual default clock.
    hits = 0
    for run in range(10):
        cfg = settings(tmp_path / f"round-{run}")
        cold = ComputeCache(cfg)
        for i in range(100):
            cold.put_vector(cold.make_key("vectors", i), np.full(32, i, dtype=np.float32))
        assert cold.flush()
        # When each batch is reopened five times without any fake/system clock changes.
        for _ in range(5):
            warm = ComputeCache(cfg)
            for i in range(100):
                result = warm.get_vector(warm.make_key("vectors", i))
                assert result is not None, {"round": run, "index": i, "ready": warm.is_ready}
                assert result.tobytes() == np.full(32, i, dtype=np.float32).tobytes()
            assert warm.stats().vector_misses == 0
            assert warm.stats().writes == 0
            assert warm.flush() is False
            hits += warm.stats().vector_hits
    # Then all 5,000 real-clock warm requests hit exact stored values.
    assert hits == 5000
    print("real-clock QA: rounds=10 vectors=100 warm_reopens=5 hits=5000 misses=0")
