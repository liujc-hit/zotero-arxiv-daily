"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const lifecycle = require("../../.github/scripts/compute-cache.cjs");
const { row, response, setup } = require("./compute-cache-fixtures.cjs");

function ownFork(context) {
  context.repo = { owner: "liujc-hit", repo: "zotero-arxiv-daily" };
  Object.assign(context.payload.repository, { fork: true,
    full_name: "liujc-hit/zotero-arxiv-daily", name: "zotero-arxiv-daily",
    owner: { login: "liujc-hit" } });
}

for (const event of ["schedule", "workflow_dispatch"]) {
  for (const phase of ["prepare", "before-save", "after-save"]) {
    test(`owner fork main ${event} allows ${phase} with scoped fake API requests`, async (t) => {
      const f = await setup(t, phase);
      ownFork(f.args.context);
      f.args.context.eventName = event;
      if (phase === "prepare") f.replies.push(response([]));
      else {
        if (phase === "before-save") await f.snapshot({ ready: true, changed: true, bytes: 10,
          vector_hits: 1, vector_misses: 2, score_hits: 3, score_misses: 4, evictions: 0, writes: 1 });
        f.replies.push(response([row(1, 8_000_000_001)]), { status: 204 }, response([]));
      }
      await lifecycle(f.args);
      assert.equal(f.outputs.enabled, "true");
      assert.equal(f.outputs["save-allowed"], phase === "before-save" ? "true" : "false");
      assert.equal(f.outputs.path, ".cache/zad-compute");
      assert.equal(f.calls.length, phase === "prepare" ? 1 : 3);
      for (const { route, parameters } of f.calls) {
        assert.equal(parameters.owner, "liujc-hit");
        assert.equal(parameters.repo, "zotero-arxiv-daily");
        if (route.startsWith("DELETE")) assert.equal(parameters.cache_id, 1);
        else assert.equal(route, "GET /repos/{owner}/{repo}/actions/caches");
      }
      assert.ok(!f.warnings.join().includes(f.args.env.COMPUTE_CACHE_KEY));
      assert.ok(!f.warnings.join().includes(f.args.env.COMPUTE_CACHE_MODEL_REVISION));
    });
  }
}

for (const variant of ["pr", "pr-target", "feature-dispatch", "feature-schedule", "ref-spoof",
  "unknown-repo", "missing-default", "repository-mismatch"]) {
  for (const phase of ["prepare", "before-save", "after-save"]) {
    test(`fork ${variant} blocks ${phase} before secret access and all API operations`, async (t) => {
      const f = await setup(t, phase);
      ownFork(f.args.context);
      const context = f.args.context;
      if (variant === "pr") context.eventName = "pull_request";
      if (variant === "pr-target") context.eventName = "pull_request_target";
      if (variant === "feature-dispatch") {
        context.eventName = "workflow_dispatch";
        context.ref = "refs/heads/feature";
      }
      if (variant === "feature-schedule") context.ref = "refs/heads/feature";
      if (variant === "ref-spoof") context.ref = "refs/pull/1/merge";
      if (variant === "unknown-repo") context.repo = {};
      if (variant === "missing-default") delete context.payload.repository.default_branch;
      if (variant === "repository-mismatch") context.payload.repository.full_name = "external/zotero-arxiv-daily";
      let secretReads = 0;
      Object.defineProperty(f.args.env, "COMPUTE_CACHE_KEY", { get() {
        secretReads++;
        throw new Error("untrusted secret access");
      } });
      await lifecycle(f.args);
      assert.equal(f.outputs.enabled, "false");
      assert.equal(f.outputs["save-allowed"], "false");
      assert.equal(secretReads, 0);
      assert.deepEqual(f.calls, []);
      assert.deepEqual(f.warnings, []);
    });
  }
}
