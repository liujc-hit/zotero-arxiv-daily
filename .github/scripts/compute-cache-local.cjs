"use strict";

const fs = require("node:fs/promises");
const { constants } = require("node:fs");
const path = require("node:path");
const { createHmac } = require("node:crypto");

const CACHE_PATH = ".cache/zad-compute";
const STATUS_PATH = ".cache/zad-compute-status.json";
const MAX_BUDGET = 8_000_000_000;
const COUNTERS = ["bytes", "vector_hits", "vector_misses", "score_hits", "score_misses", "evictions", "writes"];
const fail = () => { throw new Error("local-invalid"); };

function trusted(context, env) {
  const repository = context?.payload?.repository;
  const { owner, repo } = context?.repo ?? {};
  const name = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;
  return env.COMPUTE_CACHE_ENABLED === "true"
    && typeof owner === "string" && name.test(owner)
    && typeof repo === "string" && name.test(repo)
    && (!repository?.full_name || repository.full_name.toLowerCase() === `${owner}/${repo}`.toLowerCase())
    && typeof repository?.default_branch === "string" && repository.default_branch.trim() !== ""
    && context.ref === `refs/heads/${repository.default_branch}`
    && ["workflow_dispatch", "schedule"].includes(context.eventName);
}

function configuration(context, env) {
  if (!["prepare", "before-save", "after-save"].includes(env.COMPUTE_CACHE_PHASE)) fail();
  if (env.RUNNER_OS !== "Linux") fail();
  if (typeof env.COMPUTE_CACHE_MODEL_REVISION !== "string" || !env.COMPUTE_CACHE_MODEL_REVISION.trim()) fail();
  const encoded = env.COMPUTE_CACHE_KEY;
  if (typeof encoded !== "string" || !/^[A-Za-z0-9_-]{43}=$/.test(encoded)) fail();
  const raw = Buffer.from(encoded, "base64url");
  if (raw.length !== 32 || `${raw.toString("base64url")}=` !== encoded) fail();
  const keyId = createHmac("sha256", raw).update("zad-compute-cache-v1", "ascii").digest("hex").slice(0, 16);
  const budgetText = env.COMPUTE_CACHE_BUDGET_BYTES ?? String(MAX_BUDGET);
  const budget = Number(budgetText);
  if (!/^[1-9][0-9]*$/.test(budgetText) || !Number.isSafeInteger(budget) || budget > MAX_BUDGET) fail();
  const run = String(env.GITHUB_RUN_ID ?? context.runId ?? "");
  const attempt = String(env.GITHUB_RUN_ATTEMPT ?? context.runAttempt ?? "");
  if (!/^[1-9][0-9]*$/.test(run) || !/^[1-9][0-9]*$/.test(attempt)) fail();
  if (env.COMPUTE_CACHE_DRY_RUN !== undefined && !["true", "false"].includes(env.COMPUTE_CACHE_DRY_RUN)) fail();
  if (typeof env.GITHUB_WORKSPACE !== "string" || !path.isAbsolute(env.GITHUB_WORKSPACE)) fail();
  const prefix = `zad-compute-v1-linux-${keyId}-`;
  return { budget, prefix, saveKey: `${prefix}${run}-${attempt}`, ref: context.ref,
    workspace: env.GITHUB_WORKSPACE, phase: env.COMPUTE_CACHE_PHASE,
    dryRun: env.COMPUTE_CACHE_DRY_RUN === "true", repo: { ...context.repo } };
}

async function optionalStat(file) {
  try { return await fs.lstat(file); } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function localPaths(workspace) {
  const root = path.join(workspace, CACHE_PATH);
  const status = path.join(workspace, STATUS_PATH);
  for (const directory of [workspace, path.join(workspace, ".cache"), root]) {
    const stat = await optionalStat(directory);
    if ((!stat && directory === workspace) || (stat && !stat.isDirectory())) fail();
  }
  const stat = await optionalStat(status);
  if (stat && !stat.isFile()) fail();
  return { root, status, statusExists: Boolean(stat) };
}

async function clearStatus(paths) {
  if (paths.statusExists) {
    const stat = await fs.lstat(paths.status);
    if (!stat.isFile()) fail();
    await fs.unlink(paths.status);
  }
}

async function snapshot(paths, budget) {
  const handle = await fs.open(paths.status, constants.O_RDONLY | constants.O_NOFOLLOW);
  let status;
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size > 64 * 1024) fail();
    status = JSON.parse(await handle.readFile("utf8"));
  } finally { await handle.close(); }
  if (!status || Array.isArray(status) || typeof status !== "object"
    || Object.keys(status).some(key => !["ready", "changed", ...COUNTERS].includes(key))
    || status.ready !== true || status.changed !== true
    || !Number.isSafeInteger(status.bytes) || status.bytes < 0
    || COUNTERS.some(key => key in status && (!Number.isSafeInteger(status[key]) || status[key] < 0))) fail();
  const files = await fs.readdir(paths.root);
  if (!files.includes("manifest.enc")) fail();
  let bytes = 0;
  for (const file of files) {
    if (!/^(manifest|shard-[0-9a-f]{32})\.enc$/.test(file)) fail();
    const stat = await fs.lstat(path.join(paths.root, file));
    if (!stat.isFile() || stat.nlink !== 1 || stat.size <= 0 || stat.size > 16 * 1024 ** 2) fail();
    bytes += stat.size;
    if (!Number.isSafeInteger(bytes) || bytes > budget) fail();
  }
  return { bytes, count: files.length };
}

async function diskSpace(workspace) {
  const stat = await fs.statfs(workspace, { bigint: true });
  const bytes = stat.bavail * stat.bsize;
  return Number(bytes > BigInt(Number.MAX_SAFE_INTEGER) ? BigInt(Number.MAX_SAFE_INTEGER) : bytes);
}

module.exports = { CACHE_PATH, trusted, configuration, localPaths, clearStatus, snapshot, diskSpace };
