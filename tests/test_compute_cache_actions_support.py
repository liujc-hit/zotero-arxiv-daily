"""Isolated process adapters for real compute-cache integration tests."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from time import perf_counter
from typing import ClassVar, Final, Literal, assert_never, override

import numpy as np
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict

from zotero_arxiv_daily.compute_cache import FloatArray
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.reranker.local import LocalReranker

ROOT: Final = Path(__file__).resolve().parent.parent
SYNTHETIC_KEY: Final = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
PRIVATE_TEXT: Final = "PRIVATE_INTEGRATION_TITLE_ABSTRACT_SENTINEL"

_NODE: Final = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const lifecycle = require(input.module);
const scenario = input.scenario;
const original = scenario.rows;
let rows = structuredClone(original), calls = 0, secretReads = 0;
const deleted = [], outputs = {}, warnings = [];
const env = new Proxy(process.env, { get(target, key) {
  if (key === 'COMPUTE_CACHE_KEY' && scenario.deny_secret) {
    secretReads++; throw new Error('forbidden-secret-access');
  }
  return target[key];
}});
const context = { repo: {owner: 'fixture-owner', repo: 'fixture-fork'},
  payload: {repository: {default_branch: 'main', fork: true,
    full_name: 'fixture-owner/fixture-fork'}},
  ref: scenario.event === 'pull_request' ? 'refs/pull/5/merge' : 'refs/heads/main',
  eventName: scenario.event, runId: 11, runAttempt: 1 };
const github = {request: async (route, parameters) => {
  calls++;
  assert.equal(parameters.owner, context.repo.owner);
  assert.equal(parameters.repo, context.repo.repo);
  if (route === 'GET /repos/{owner}/{repo}/actions/caches') {
    const offset = (parameters.page - 1) * parameters.per_page;
    return {status: 200, data: {total_count: rows.length,
      actions_caches: rows.slice(offset, offset + parameters.per_page)}};
  }
  assert.equal(route, 'DELETE /repos/{owner}/{repo}/actions/caches/{cache_id}');
  assert.equal(parameters.cache_id, scenario.expected_deletions[deleted.length]);
  assert.ok(rows.some(row => row.id === parameters.cache_id));
  deleted.push(parameters.cache_id);
  rows = rows.filter(row => row.id !== parameters.cache_id);
  return {status: 204};
}};
const core = {setOutput: (key, value) => {
  assert.equal(typeof value, 'string'); outputs[key] = value;
}, warning: message => warnings.push(message)};
(async () => {
  await lifecycle({github, context, core, env, diskSpace: async () => 100_000_000_000});
  assert.equal(secretReads, 0);
  assert.equal(calls > 0, scenario.expect_requests);
  assert.deepEqual(deleted, scenario.expected_deletions);
  assert.deepEqual(rows, original.filter(row => !deleted.includes(row.id)));
  process.stdout.write(JSON.stringify({outputs, warnings}));
})().catch(() => { process.stderr.write('integration-harness-contract-failed'); process.exitCode = 1; });
"""


@dataclass(frozen=True, slots=True)
class CacheRow:
    id: int
    key: str
    size_in_bytes: int
    ref: str = "refs/heads/main"
    created_at: str = "2026-01-01T00:00:00Z"
    last_accessed_at: str = "2026-01-02T00:00:00Z"


@dataclass(frozen=True, slots=True)
class LifecycleScenario:
    phase: Literal["prepare", "before-save", "after-save"] = "prepare"
    rows: tuple[CacheRow, ...] = ()
    expected_deletions: tuple[int, ...] = ()
    event: Literal["workflow_dispatch", "pull_request"] = "workflow_dispatch"
    expect_requests: bool = True
    deny_secret: bool = False
    key: str = field(default=SYNTHETIC_KEY, repr=False)


class LifecycleResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    outputs: dict[str, str]
    warnings: list[str]


class ProducerStatus(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    ready: bool
    changed: bool
    bytes: int
    vector_hits: int
    vector_misses: int
    score_hits: int
    score_misses: int
    evictions: int
    writes: int


_DEFAULT_SCENARIO: Final = LifecycleScenario()


def run_lifecycle(workspace: Path, scenario: LifecycleScenario = _DEFAULT_SCENARIO) -> LifecycleResult:
    """Run the real CommonJS helper with no inherited credentials or live transport."""
    node = shutil.which("node")
    assert node is not None
    payload = asdict(scenario)
    del payload["key"]
    process = subprocess.run(
        [node, "-e", _NODE], input=json.dumps({
            "module": str(ROOT / ".github/scripts/compute-cache.cjs"), "scenario": payload,
        }), text=True, capture_output=True, timeout=15, check=True,
        env={"GITHUB_WORKSPACE": str(workspace), "RUNNER_OS": "Linux",
             "GITHUB_RUN_ID": "11", "GITHUB_RUN_ATTEMPT": "1",
             "COMPUTE_CACHE_PHASE": scenario.phase, "COMPUTE_CACHE_ENABLED": "true",
             "COMPUTE_CACHE_MODEL_REVISION": "fixture-v1", "COMPUTE_CACHE_KEY": scenario.key,
             "COMPUTE_CACHE_BUDGET_BYTES": "8000000000"},
    )
    assert process.stderr == ""
    assert scenario.key not in process.stdout
    assert PRIVATE_TEXT not in process.stdout
    return LifecycleResult.model_validate_json(process.stdout)


class DeterministicLocal(LocalReranker):
    """Explicitly trusted local fixture; mutable counter measures encoded rows only."""

    encoded: int = 0

    @override
    def _cache_backend(self) -> Literal["local"]:
        return "local"

    @override
    def get_embeddings(self, texts: list[str]) -> FloatArray:
        self.encoded += len(texts)
        return np.stack([
            np.frombuffer(hashlib.sha256(text.encode()).digest(), dtype=np.uint8).astype(np.float32) + 1
            for text in texts
        ])


def run_probe(workspace: Path) -> None:
    """Emit public measurements after each phase; caller bounds total process time."""
    for count in (300, 3000):
        path = workspace / str(count) / ".cache/zad-compute"
        config = OmegaConf.create({
            "compute_cache": {"enabled": True, "key": SYNTHETIC_KEY, "path": str(path),
                              "model_revision": "fixture-v1", "budget_bytes": 300 * 1024**2},
            "reranker": {"topk": 10, "local": {"model": "deterministic-fixture", "encode_kwargs": {}}},
            "executor": {"max_paper_num": 100, "debug": True},
        })
        corpus = [CorpusPaper(f"corpus-{index}", "fixture", datetime(2026, 1, 1), []) for index in range(30)]
        expected: list[tuple[str, float | None]] = []
        phases: tuple[tuple[Literal["cold", "warm", "one-miss"], int], ...] = (
            ("cold", count), ("warm", count), ("one-miss", count + 1),
        )
        for phase, size in phases:
            ranker = DeterministicLocal(config)
            papers = [Paper("fixture", f"candidate-{index}", [], "fixture", f"https://example.test/{index}") for index in range(size)]
            started = perf_counter()
            ranked = ranker.rerank(papers, corpus)
            seconds = perf_counter() - started
            actual = [(paper.title, paper.score) for paper in ranked]
            match phase:
                case "cold":
                    expected = actual
                case "warm":
                    assert actual == expected
                case "one-miss":
                    assert len(actual) == count + 1
                case unreachable:
                    assert_never(unreachable)
            files = tuple(path.glob("*.enc"))
            print(json.dumps({"candidates": count, "phase": phase, "seconds": seconds,
                              "encoded": ranker.encoded, "files": len(files),
                              "bytes": sum(file.stat().st_size for file in files)}), flush=True)
