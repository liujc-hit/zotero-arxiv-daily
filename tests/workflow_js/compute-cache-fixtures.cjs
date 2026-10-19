"use strict";

const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");

const PREFIX = "zad-compute-v1-linux-07cd96ce2efc17f5-";
const CONTEXT = {
  repo: { owner: "paper-owner", repo: "paper-repo" },
  payload: { repository: { default_branch: "main", fork: false } },
  ref: "refs/heads/main", eventName: "schedule", runId: 123, runAttempt: 2,
};
function row(id, size, extra = {}) {
  return { id, key: `${PREFIX}${id}-1`, ref: CONTEXT.ref, size_in_bytes: size,
    created_at: "2026-01-01T00:00:00Z", last_accessed_at: "2026-01-02T00:00:00Z", ...extra };
}
function response(rows, total = rows.length) {
  return { status: 200, data: { total_count: total, actions_caches: rows } };
}
async function setup(t, phase = "prepare") {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "compute-cache-test-"));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  const outputs = {}, warnings = [], calls = [], replies = [];
  const args = {
    context: structuredClone(CONTEXT),
    env: { GITHUB_WORKSPACE: workspace, COMPUTE_CACHE_PHASE: phase,
      COMPUTE_CACHE_ENABLED: "true", COMPUTE_CACHE_MODEL_REVISION: "revision",
      COMPUTE_CACHE_KEY: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", RUNNER_OS: "Linux" },
    core: { setOutput: (key, value) => {
      assert.equal(typeof value, "string"); outputs[key] = value;
    }, warning: (message) => warnings.push(message) },
    github: { request: async (route, parameters) => {
      calls.push({ route, parameters });
      assert.ok(replies.length, "unexpected request");
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return reply;
    } },
    diskSpace: async () => 100_000_000_000,
  };
  async function snapshot(status = { ready: true, changed: true, bytes: 10 }) {
    await fs.mkdir(path.join(workspace, ".cache/zad-compute"), { recursive: true });
    await fs.writeFile(path.join(workspace, ".cache/zad-compute/manifest.enc"), "ciphertext");
    await fs.writeFile(path.join(workspace, ".cache/zad-compute-status.json"), JSON.stringify(status));
  }
  return { args, workspace, outputs, warnings, calls, replies, snapshot };
}
module.exports = { fs, path, PREFIX, CONTEXT, row, response, setup };
