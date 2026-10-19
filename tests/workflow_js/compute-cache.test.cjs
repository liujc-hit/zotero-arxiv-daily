"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const lifecycle = require("../../.github/scripts/compute-cache.cjs");
const { fs, path, PREFIX, row, response, setup } = require("./compute-cache-fixtures.cjs");

test("cold miss emits fixed string outputs and Python HMAC namespace fixture", async (t) => {
  const f = await setup(t);
  f.replies.push(response([]));
  await lifecycle(f.args);
  assert.deepEqual(f.outputs, { enabled: "true", "restore-key": `${PREFIX}123-2`,
    "restore-prefix": PREFIX, "save-key": `${PREFIX}123-2`, "save-allowed": "false",
    path: ".cache/zad-compute" });
  assert.deepEqual(f.calls[0].parameters, { owner: "paper-owner", repo: "paper-repo", per_page: 100, page: 1 });
});

for (const variant of ["pr", "feature", "unknown", "push", "disabled"]) {
  test(`trust guard rejects ${variant} before reading secret or requesting`, async (t) => {
    const f = await setup(t);
    if (variant === "pr") f.args.context.eventName = "pull_request";
    if (variant === "feature") f.args.context.ref = "refs/heads/feature";
    if (variant === "unknown") f.args.context.repo = {};
    if (variant === "push") f.args.context.eventName = "push";
    if (variant === "disabled") f.args.env.COMPUTE_CACHE_ENABLED = "TRUE";
    Object.defineProperty(f.args.env, "COMPUTE_CACHE_KEY", { get() { throw new Error("secret read"); } });
    await lifecycle(f.args);
    assert.equal(f.outputs.enabled, "false");
    assert.equal(f.calls.length, 0);
    assert.equal(f.warnings.length, 0);
  });
}

for (const override of [
  { COMPUTE_CACHE_KEY: "A".repeat(42) + "B=" },
  { COMPUTE_CACHE_KEY: "A".repeat(43) },
  { COMPUTE_CACHE_KEY: "a secret not to log" },
  { COMPUTE_CACHE_MODEL_REVISION: " " }, { RUNNER_OS: "Windows" },
  { COMPUTE_CACHE_BUDGET_BYTES: "8000000001" }, { COMPUTE_CACHE_BUDGET_BYTES: "0" },
  { COMPUTE_CACHE_BUDGET_BYTES: "1e9" }, { GITHUB_RUN_ID: "../escape" },
  { COMPUTE_CACHE_PHASE: "unexpected" },
]) {
  test(`invalid configuration fails closed ${Object.keys(override)[0]} ${JSON.stringify(override).length}`, async (t) => {
    const f = await setup(t);
    Object.assign(f.args.env, override);
    await lifecycle(f.args);
    assert.equal(f.outputs.enabled, "false");
    assert.equal(f.calls.length, 0);
    assert.ok(!f.warnings.join().includes("secret"));
  });
}

test("restore selects creation recency, not access recency or different namespace/ref", async (t) => {
  const f = await setup(t);
  f.replies.push(response([
    row(1, 100, { last_accessed_at: "2026-09-01T00:00:00Z" }),
    row(2, 100, { created_at: "2026-02-01T00:00:00Z" }),
    row(3, 100, { key: "zad-compute-v1-linux-1111111111111111-3-1" }),
    row(4, 100, { ref: "refs/heads/feature", created_at: "2026-10-01T00:00:00Z" }),
  ]));
  await lifecycle(f.args);
  assert.equal(f.outputs["restore-key"], `${PREFIX}2-1`);
});

test("pagination includes all versions with duplicate keys and explicit repository scope", async (t) => {
  const f = await setup(t);
  const rows = Array.from({ length: 100 }, (_, i) => row(i + 1, 1));
  f.replies.push(response(rows, 101), response([row(101, 1, {
    key: rows[0].key, created_at: "2026-03-01T00:00:00Z",
  })], 101));
  f.args.env.GITHUB_REPOSITORY = "attacker/target";
  await lifecycle(f.args);
  assert.equal(f.outputs.enabled, "true");
  assert.equal(f.calls[1].parameters.page, 2);
  assert.equal(f.calls[1].parameters.owner, "paper-owner");
});

for (const reply of [response([], 1), { status: 206, data: {} }, response([row(1, -1)]),
  response([row(1, 1, { created_at: "invalid" })]), response([row(1, 1), row(1, 1)]),
  Object.assign(new Error("SECRET body"), { status: 429 })]) {
  test("malformed, partial, or failed inventory disables restore without leaking errors", async (t) => {
    const f = await setup(t);
    f.replies.push(reply);
    await lifecycle(f.args);
    assert.equal(f.outputs.enabled, "false");
    assert.equal(f.warnings.length, 1);
    assert.ok(!f.warnings[0].includes("SECRET"));
  });
}

test("disk estimate requires twice selected bytes plus 2 GiB, cold miss needs reserve only", async (t) => {
  const f = await setup(t);
  f.args.diskSpace = async () => 2 * 1024 ** 3 + 199;
  f.replies.push(response([row(1, 100)]));
  await lifecycle(f.args);
  assert.equal(f.outputs.enabled, "false");
  f.replies.push(response([]));
  await lifecycle(f.args);
  assert.equal(f.outputs.enabled, "true");
});

test("prepare removes only regular owned status, leaving unrelated files", async (t) => {
  const f = await setup(t);
  await f.snapshot();
  const unrelated = path.join(f.workspace, ".cache/other");
  await fs.writeFile(unrelated, "keep");
  f.replies.push(response([]));
  await lifecycle(f.args);
  await assert.rejects(fs.stat(path.join(f.workspace, ".cache/zad-compute-status.json")), { code: "ENOENT" });
  assert.equal(await fs.readFile(unrelated, "utf8"), "keep");
});

for (const target of [".cache", ".cache/zad-compute-status.json", ".cache/zad-compute"]) {
  test(`prepare refuses symlink ${target}`, async (t) => {
    const f = await setup(t);
    const outside = path.join(f.workspace, "unrelated");
    await fs.mkdir(outside);
    if (target !== ".cache") await fs.mkdir(path.join(f.workspace, ".cache"));
    await fs.symlink(outside, path.join(f.workspace, target));
    await lifecycle(f.args);
    assert.equal(f.outputs.enabled, "false");
    assert.equal(f.calls.length, 0);
    assert.ok((await fs.lstat(path.join(f.workspace, target))).isSymbolicLink());
  });
}

test("key rotation changes namespace without logging key or raw cache keys", async (t) => {
  const f = await setup(t);
  f.args.env.COMPUTE_CACHE_KEY = Buffer.alloc(32, 1).toString("base64url") + "=";
  f.replies.push(response([row(1, 7_000_000_000)]));
  await lifecycle(f.args);
  assert.notEqual(f.outputs["restore-prefix"], PREFIX);
  assert.ok(!f.warnings.join().includes(PREFIX));
  assert.ok(!f.warnings.join().includes(f.args.env.COMPUTE_CACHE_KEY));
});

test("failed later inventory page is not mistaken for complete inventory", async (t) => {
  const f = await setup(t);
  f.replies.push(response(Array.from({ length: 100 }, (_, i) => row(i + 1, 10)), 101),
    Object.assign(new Error("private response"), { status: 403 }));
  await lifecycle(f.args);
  assert.equal(f.outputs.enabled, "false");
  assert.equal(f.calls.length, 2);
  assert.equal(f.warnings.length, 1);
});

test("unreasonable inventory count fails closed before unbounded pagination", async (t) => {
  const f = await setup(t);
  f.replies.push(response([], 100_001));
  await lifecycle(f.args);
  assert.equal(f.outputs.enabled, "false");
  assert.equal(f.calls.length, 1);
});

test("workflow_dispatch uses validated env run identifiers and ignores arbitrary target configuration", async (t) => {
  const f = await setup(t);
  f.args.context.eventName = "workflow_dispatch";
  Object.assign(f.args.env, { GITHUB_RUN_ID: "456", GITHUB_RUN_ATTEMPT: "3",
    COMPUTE_CACHE_PATH: "/outside", COMPUTE_CACHE_PREFIX: "unrelated-" });
  f.replies.push(response([]));
  await lifecycle(f.args);
  assert.equal(f.outputs.path, ".cache/zad-compute");
  assert.equal(f.outputs["save-key"], `${PREFIX}456-3`);
});

test("production disk helper reads real filesystem availability", async (t) => {
  const f = await setup(t);
  const { diskSpace } = require("../../.github/scripts/compute-cache-local.cjs");
  const bytes = await diskSpace(f.workspace);
  assert.ok(Number.isSafeInteger(bytes) && bytes >= 0);
});
