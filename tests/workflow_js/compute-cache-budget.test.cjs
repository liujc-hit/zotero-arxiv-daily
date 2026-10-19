"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const lifecycle = require("../../.github/scripts/compute-cache.cjs");
const { plan, reservation } = require("../../.github/scripts/compute-cache-policy.cjs");
const { fs, path, PREFIX, row, response, setup } = require("./compute-cache-fixtures.cjs");
const cfg = { budget: 8_000_000_000, ref: "refs/heads/main" };

test("8 GB decimal boundary and archive reservation require no large allocations", () => {
  assert.equal(reservation(10, 1), Math.ceil(10 * 1.05) + 64 * 1024 ** 2 + 1024);
  assert.equal(plan([row(1, 7_000_000_000)], cfg, 1_000_000_000).fits, true);
  assert.equal(plan([row(1, 7_000_000_001)], cfg, 1_000_000_000).fits, false);
  assert.equal(plan([], cfg, 8_000_000_001).oversized, true);
});

test("10 GB shared cap preserves 64 MiB headroom and never selects other caches", () => {
  const other = row(9, 9_000_000_000, { key: "unrelated" });
  const p = plan([row(1, 900_000_000), other], cfg, 100_000_000);
  assert.equal(p.fits, false);
  assert.equal(p.candidate.id, 1);
  assert.equal(p.cap, 1_000_000_000 - 64 * 1024 ** 2);
  assert.equal(plan([other], cfg, 1_000_000_000).oversized, true);
});

test("before-save scans actual ciphertext and allows a proven small snapshot", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  f.args.env.COMPUTE_CACHE_BUDGET_BYTES = "1000000000";
  f.replies.push(response([]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "true");
  assert.equal(f.outputs["save-key"], `${PREFIX}123-2`);
  assert.equal(f.calls.length, 1);
});

test("GC deletes exact oldest owned IDs across versions/rotated keys and relists each time", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  const oldest = row(2, 2_000_000_000, { key: "zad-compute-v1-linux-1111111111111111-2-1",
    last_accessed_at: "2025-01-01T00:00:00Z" });
  const keep = row(1, 6_000_000_000);
  const feature = row(3, 100, { ref: "refs/heads/feature" });
  const malformed = row(4, 100, { key: `${PREFIX}garbage` });
  f.replies.push(response([keep, oldest, feature, malformed]), { status: 204 }, response([keep, feature, malformed]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "true");
  assert.deepEqual(f.calls.map(c => c.parameters.cache_id).filter(Boolean), [2]);
  assert.equal(f.calls.length, 3);
  assert.equal(f.warnings.length, 1);
  assert.ok(!f.warnings[0].includes(PREFIX));
});

test("LRU ties break on creation time then numeric ID", () => {
  const rows = [row(4, 4_000_000_000), row(3, 4_000_000_000), row(2, 1, {
    created_at: "2026-02-01T00:00:00Z",
  })];
  assert.equal(plan(rows, cfg, 1).candidate.id, 3);
});

for (const code of [403, 429]) {
  test(`deletion HTTP ${code} fails closed without exposing API error`, async (t) => {
    const f = await setup(t, "before-save");
    await f.snapshot();
    f.replies.push(response([row(1, 8_000_000_000)]), Object.assign(new Error("sensitive request"), { status: code }));
    await lifecycle(f.args);
    assert.equal(f.outputs["save-allowed"], "false");
    assert.equal(f.warnings.length, 1);
    assert.ok(!f.warnings[0].includes("sensitive"));
  });
}

test("dry-run skips needed pruning and new save", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  f.args.env.COMPUTE_CACHE_DRY_RUN = "true";
  f.replies.push(response([row(1, 8_000_000_000)]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "false");
  assert.equal(f.calls.length, 1);
});

test("other cache exhaustion or oversized snapshot skips save without any deletion", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  f.replies.push(response([row(1, 1), row(2, 10_000_000_000, { key: "unrelated" })]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "false");
  assert.equal(f.calls.length, 1);
  f.args.env.COMPUTE_CACHE_BUDGET_BYTES = "10";
  f.replies.push(response([row(1, 1)]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "false");
  assert.equal(f.calls.filter(c => c.route.startsWith("DELETE")).length, 0);
});

for (const status of [null, { ready: false, changed: true, bytes: 10 },
  { ready: true, changed: false, bytes: 10 }, { ready: true, changed: true, bytes: 10, secret: "no" },
  { ready: true, changed: true, bytes: -1 }]) {
  test(`invalid or missing readiness status prevents all GC ${JSON.stringify(status)}`, async (t) => {
    const f = await setup(t, "before-save");
    if (status) await f.snapshot(status);
    await lifecycle(f.args);
    assert.equal(f.outputs["save-allowed"], "false");
    assert.equal(f.calls.length, 0);
  });
}

for (const kind of ["symlink", "directory", "plaintext", "status-link", "status-json", "large-manifest", "large-shard"]) {
  test(`unsafe snapshot ${kind} prevents save and GC`, async (t) => {
    const f = await setup(t, "before-save");
    await f.snapshot();
    const root = path.join(f.workspace, ".cache/zad-compute");
    if (kind === "symlink") await fs.symlink("manifest.enc", path.join(root, `shard-${"a".repeat(32)}.enc`));
    if (kind === "directory") await fs.mkdir(path.join(root, "nested"));
    if (kind === "plaintext") await fs.writeFile(path.join(root, "plain.txt"), "no");
    if (kind === "status-link") {
      await fs.rename(path.join(f.workspace, ".cache/zad-compute-status.json"), path.join(f.workspace, "status"));
      await fs.symlink("../status", path.join(f.workspace, ".cache/zad-compute-status.json"));
    }
    if (kind === "status-json") await fs.writeFile(path.join(f.workspace, ".cache/zad-compute-status.json"), "{");
    if (kind === "large-manifest") await fs.truncate(path.join(root, "manifest.enc"), 16 * 1024 ** 2 + 1);
    if (kind === "large-shard") {
      const shard = path.join(root, `shard-${"a".repeat(32)}.enc`);
      await fs.writeFile(shard, "x");
      await fs.truncate(shard, 16 * 1024 ** 2 + 1);
    }
    await lifecycle(f.args);
    assert.equal(f.outputs["save-allowed"], "false");
    assert.equal(f.calls.length, 0);
  });
}

test("after-save checks actual new inventory and prunes LRU without performing upload", async (t) => {
  const f = await setup(t, "after-save");
  const old = row(1, 4_000_000_000), uploaded = row(123, 5_000_000_000, {
    created_at: "2026-10-01T00:00:00Z", last_accessed_at: "2026-10-01T00:00:00Z",
  });
  f.replies.push(response([old, uploaded]), { status: 204 }, response([uploaded]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "false");
  assert.deepEqual(f.calls.map(c => c.parameters.cache_id).filter(Boolean), [1]);
});

test("stale inventory after deletion fails closed rather than trusting subtraction", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  const rows = [row(1, 8_000_000_000)];
  f.replies.push(response(rows), { status: 204 }, response(rows));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "false");
  assert.equal(f.calls.length, 3);
});

test("90 percent warning is inclusive for actual or projected owned usage", () => {
  assert.equal(plan([row(1, 7_199_999_999)], cfg, 0).warning, false);
  assert.equal(plan([row(1, 7_200_000_000)], cfg, 0).warning, true);
  assert.equal(plan([row(1, 7_000_000_000)], cfg, 199_999_999).warning, false);
  assert.equal(plan([row(1, 7_000_000_000)], cfg, 200_000_000).warning, true);
});

test("concurrent new unrelated usage is included after each deletion", async (t) => {
  const f = await setup(t, "before-save");
  await f.snapshot();
  const old = row(1, 2_000_000_000, { last_accessed_at: "2025-01-01T00:00:00Z" });
  const newer = row(2, 6_000_000_000);
  const other = row(3, 5_000_000_000, { key: "another-workflow" });
  f.replies.push(response([old, newer]), { status: 204 }, response([newer, other]),
    { status: 204 }, response([other]));
  await lifecycle(f.args);
  assert.equal(f.outputs["save-allowed"], "true");
  assert.deepEqual(f.calls.map(c => c.parameters.cache_id).filter(Boolean), [1, 2]);
});

test("after-save cleans all owned entries when other usage already exceeds global allowance", async (t) => {
  const f = await setup(t, "after-save");
  const other = row(2, 10_000_000_000, { key: "not-ours" });
  f.replies.push(response([row(1, 10), other]), { status: 204 }, response([other]));
  await lifecycle(f.args);
  assert.deepEqual(f.calls.map(c => c.parameters.cache_id).filter(Boolean), [1]);
  assert.equal(f.warnings.length, 1);
  assert.match(f.warnings[0], /capacity/);
});
