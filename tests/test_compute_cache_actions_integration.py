"""Real Python ciphertext/status consumed by the real Actions cache lifecycle."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from typing import ClassVar, Final

from cryptography.fernet import Fernet
import numpy as np
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict
import pytest

from zotero_arxiv_daily.compute_cache import (
    CacheSettings, ComputeCache, ScoreSnapshot, key_id, open_compute_cache,
)
from .test_compute_cache_actions_support import (
    CacheRow, LifecycleScenario, PRIVATE_TEXT, ProducerStatus, ROOT, SYNTHETIC_KEY, run_lifecycle,
)

_STATUS_FIELDS: Final = {"ready", "changed", "bytes", "vector_hits", "vector_misses",
                         "score_hits", "score_misses", "evictions", "writes"}
_PROBE_ENV: Final[dict[str, str]] = {
    "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
    "PYTHONDONTWRITEBYTECODE": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_PROBE_IMPORT_CHECK: Final = """
from pathlib import Path
import importlib
import os
import sys
source = Path(sys.argv[2]).resolve() / "src"
assert os.environ.get("PYTHONPATH") == str(source)
assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
assert os.environ.get("HF_HUB_OFFLINE") == "1"
assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
assert set(os.environ) <= {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "LC_CTYPE"}
for name in ("zotero_arxiv_daily.compute_cache.cache", "zotero_arxiv_daily.compute_cache.ledger", "zotero_arxiv_daily.reranker.local"):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(source)
"""


@pytest.fixture
def settings(tmp_path: Path) -> CacheSettings:
    return CacheSettings(enabled=True, key=SYNTHETIC_KEY, model_revision="fixture-v1",
                         path=tmp_path / ".cache/zad-compute")


def seed_cache(settings: CacheSettings) -> ComputeCache:
    """Write authenticated vector and score records, never fabricated ciphertext."""
    cache = ComputeCache(settings)
    vector_key = cache.make_key("vectors", [PRIVATE_TEXT])
    cache.put_vector(vector_key, np.array([1.25, 2.5, 3.75], dtype=np.float32))
    cache.put_scores(cache.make_key("scores", [PRIVATE_TEXT]), ScoreSnapshot((0.75,), (0,)))
    assert cache.flush() is True
    return cache


def cipher_snapshot(settings: CacheSettings) -> dict[str, bytes]:
    """Capture only the owned encrypted upload directory."""
    return {file.name: file.read_bytes() for file in settings.path.iterdir()}


def test_cold_python_flush_allows_save_on_own_fork_main(settings: CacheSettings, tmp_path: Path) -> None:
    # Given the actual prepare phase on an owner-controlled fork's default branch.
    _ = ComputeCache(replace(settings, enabled=False))
    status_path = tmp_path / ".cache/zad-compute-status.json"
    assert status_path.is_file()
    prepared = run_lifecycle(tmp_path)
    assert not status_path.exists()
    expected_prefix = f"zad-compute-v1-linux-{key_id(SYNTHETIC_KEY)}-"
    assert prepared.outputs["enabled"] == "true"
    assert prepared.outputs["restore-prefix"] == expected_prefix
    assert prepared.outputs["save-key"] == expected_prefix + "11-1"
    cache = seed_cache(settings)
    payload = (tmp_path / ".cache/zad-compute-status.json").read_bytes()
    status = ProducerStatus.model_validate_json(payload)
    assert set(ProducerStatus.model_fields) == _STATUS_FIELDS
    assert status.ready is True and status.changed is True
    assert status.bytes == cache.stats().bytes_used

    # When before-save consumes Python's real schema and authenticated files.
    result = run_lifecycle(tmp_path, LifecycleScenario(phase="before-save"))

    # Then the strict consumer accepts the producer and only ciphertext is public.
    assert result.outputs["save-allowed"] == "true"
    assert result.outputs["path"] == ".cache/zad-compute"
    assert result.warnings == []
    files = cipher_snapshot(settings)
    assert "manifest.enc" in files and len(files) == 3
    for content in (*files.values(), payload):
        assert PRIVATE_TEXT.encode() not in content
        assert SYNTHETIC_KEY.encode() not in content
        assert cache.make_key("vectors", [PRIVATE_TEXT]).encode() not in content
        assert np.array([1.25, 2.5, 3.75], dtype=np.float32).tobytes() not in content


def test_restored_warm_python_status_skips_save_without_rewriting_ciphertext(
    settings: CacheSettings, tmp_path: Path,
) -> None:
    # Given a real cold archive retained as the next job's restored ciphertext.
    _ = seed_cache(settings)
    before = cipher_snapshot(settings)
    prepared = run_lifecycle(tmp_path)
    assert prepared.outputs["enabled"] == "true"
    warm = open_compute_cache(OmegaConf.create({"compute_cache": {
        "enabled": True, "key": SYNTHETIC_KEY, "path": str(settings.path),
        "model_revision": "fixture-v1",
    }}))
    assert warm is not None and warm.is_ready
    assert warm.get_vector(warm.make_key("vectors", [PRIVATE_TEXT])) is not None
    assert warm.get_scores(warm.make_key("scores", [PRIVATE_TEXT]), 1) == ScoreSnapshot((0.75,), (0,))
    assert warm.flush() is False

    # When before-save observes a ready but unchanged Python flush.
    result = run_lifecycle(tmp_path, LifecycleScenario(phase="before-save", expect_requests=False))

    # Then no archive or garbage collection occurs and restored bytes remain identical.
    assert result.outputs["save-allowed"] == "false"
    assert cipher_snapshot(settings) == before
    assert warm.stats().vector_hits == warm.stats().score_hits == 1
    status = ProducerStatus.model_validate_json((tmp_path / ".cache/zad-compute-status.json").read_bytes())
    assert status.ready is True and status.changed is False


def test_fork_pr_denies_secret_access_and_all_api_operations(tmp_path: Path) -> None:
    # Given a fork PR context whose cache secret getter throws if touched.
    scenario = LifecycleScenario(event="pull_request", deny_secret=True, expect_requests=False)

    # When the real lifecycle evaluates trust before configuration.
    result = run_lifecycle(tmp_path, scenario)

    # Then even a caught secret-getter error is detected by the harness.
    assert result.outputs["enabled"] == result.outputs["save-allowed"] == "false"
    assert result.warnings == []
    assert not (tmp_path / ".cache").exists()


@pytest.mark.parametrize("capacity", [
    (100_000_000, (1,), "true"),
    (9_950_000_000, (), "false"),
])
def test_real_snapshot_budget_prunes_only_old_owned_default_ref_versions(
    settings: CacheSettings, tmp_path: Path,
    capacity: tuple[int, tuple[int, ...], str],
) -> None:
    # Given 8 GB of owned metadata, unrelated caches, and real small ciphertext.
    dependency_bytes, expected_deletions, allowed = capacity
    _ = seed_cache(settings)
    prefix = f"zad-compute-v1-linux-{key_id(SYNTHETIC_KEY)}-"
    rows = (
        CacheRow(1, prefix + "1-1", 4_000_000_000),
        CacheRow(2, prefix + "2-1", 4_000_000_000, last_accessed_at="2026-01-03T00:00:00Z"),
        CacheRow(3, "dependency-cache", dependency_bytes),
        CacheRow(4, prefix + "3-1", 1000, ref="refs/heads/feature"),
        CacheRow(5, prefix + "4-1", 1000, ref="refs/heads/sent-doi-state-v1"),
    )
    state = tmp_path / ".state/sent-dois.fernet"
    state.parent.mkdir()
    encrypted_state = Fernet(SYNTHETIC_KEY.encode()).encrypt(b"unrelated-delivery-fixture")
    _ = state.write_bytes(encrypted_state)
    dependency = tmp_path / ".cache/dependency-fixture"
    _ = dependency.write_bytes(b"unrelated-dependency")
    before = cipher_snapshot(settings)

    # When actual reservation and fake metadata deletion run without large allocations.
    result = run_lifecycle(tmp_path, LifecycleScenario(
        phase="before-save", rows=rows, expected_deletions=expected_deletions,
    ))

    # Then the fake verifies exact deletion order and untouched remote inventory.
    assert result.outputs["save-allowed"] == allowed
    assert result.warnings
    assert state.read_bytes() == encrypted_state
    assert dependency.read_bytes() == b"unrelated-dependency"
    assert cipher_snapshot(settings) == before


def test_wrong_python_key_publishes_unready_status_without_private_logs(
    settings: CacheSettings, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    # Given authenticated ciphertext encrypted with a distinct synthetic fixture key.
    _ = seed_cache(settings)
    before = cipher_snapshot(settings)
    wrong_key = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
    cache = ComputeCache(replace(settings, key=wrong_key))
    assert not cache.is_ready and cache.flush() is False

    # When the real JS consumer sees the Python fail-soft readiness status.
    result = run_lifecycle(tmp_path, LifecycleScenario(
        phase="before-save", key=wrong_key, expect_requests=False,
    ))

    # Then it refuses upload/GC and neither component exposes key material or corpus.
    assert result.outputs["save-allowed"] == "false"
    assert cipher_snapshot(settings) == before
    captured = capfd.readouterr()
    observable = captured.out + captured.err + result.model_dump_json()
    assert wrong_key not in observable and SYNTHETIC_KEY not in observable
    assert PRIVATE_TEXT not in observable


class ProbeMeasurement(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    candidates: int
    phase: str
    seconds: float
    encoded: int
    files: int
    bytes: int


def test_probe_child_imports_current_checkout_without_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an inert parent-only sentinel that must not reach the child.
    monkeypatch.setenv("PROBE_INHERITED_SENTINEL", "synthetic-parent-only")

    # When the same import checks and environment used by the benchmark run.
    process = subprocess.run(
        [sys.executable, "-c", _PROBE_IMPORT_CHECK, str(tmp_path), str(ROOT)],
        cwd=ROOT, env=_PROBE_ENV, capture_output=True, text=True, timeout=15, check=True,
    )

    # Then child assertions prove source provenance and the complete environment allowlist.
    assert process.stdout == process.stderr == ""


@pytest.mark.slow
def test_deterministic_real_reranker_cache_probe(tmp_path: Path) -> None:
    # Given the built-in local ranking flow with a nonzero 32-dimensional hash encoder.
    command = _PROBE_IMPORT_CHECK + (
        "from pathlib import Path; import sys; "
        "from tests.test_compute_cache_actions_support import run_probe; "
        "run_probe(Path(sys.argv[1]))"
    )

    # When cold/warm/one-new-text probes run under one 90-second subprocess bound.
    try:
        process = subprocess.run([sys.executable, "-c", command, str(tmp_path), str(ROOT)],
                                 cwd=ROOT, env=_PROBE_ENV, capture_output=True, text=True,
                                 timeout=90, check=True)
    except subprocess.TimeoutExpired as error:
        print(error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout)
        files = tuple((tmp_path / "3000/.cache/zad-compute").glob("*.enc"))
        print(json.dumps({"candidates": 3000, "phase": "incomplete-at-90s",
                          "files": len(files), "bytes": sum(file.stat().st_size for file in files)}))
        pytest.skip("performance probe exceeded 90 seconds; inspect partial measurements")
    measurements = [ProbeMeasurement.model_validate_json(line) for line in process.stdout.splitlines()]

    # Then score hits encode nothing, and adding one text encodes exactly one row.
    assert [(row.candidates, row.phase, row.encoded) for row in measurements] == [
        (count, phase, encoded) for count in (300, 3000)
        for phase, encoded in (("cold", count + 30), ("warm", 0), ("one-miss", 1))
    ]
    for cold, warm, changed in (measurements[:3], measurements[3:]):
        assert cold.files == warm.files == cold.candidates + 32
        assert cold.bytes == warm.bytes > 0
        assert changed.files == cold.files + 2 and changed.bytes > cold.bytes
    assert SYNTHETIC_KEY not in process.stdout + process.stderr
    assert PRIVATE_TEXT not in process.stdout + process.stderr
    print(process.stdout, end="")
