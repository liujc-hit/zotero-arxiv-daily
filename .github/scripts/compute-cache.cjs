"use strict";

const local = require("./compute-cache-local.cjs");
const policy = require("./compute-cache-policy.cjs");

// Contract: string outputs only; transport is owned by actions/cache restore/save.
// path is the whole encrypted directory, not a selective shard snapshot. The
// aggregate sibling status file is never part of that archive. Inventory is an
// observation, not a lock against concurrent writers or GitHub indexing latency.
module.exports = async function lifecycle({ github, context, core, env = process.env, diskSpace = local.diskSpace }) {
  const defaults = { enabled: "false", "restore-key": "", "restore-prefix": "",
    "save-key": "", "save-allowed": "false", path: local.CACHE_PATH };
  for (const [key, value] of Object.entries(defaults)) core.setOutput(key, value);
  const reasons = new Set();
  let deleted = 0, ownedCount = 0, stage = "configuration";
  try {
    if (!local.trusted(context, env)) return;
    const cfg = local.configuration(context, env);
    stage = "local-path";
    const paths = await local.localPaths(cfg.workspace);
    let reserved = 0;
    if (cfg.phase === "prepare") await local.clearStatus(paths);
    if (cfg.phase === "before-save") {
      stage = "snapshot";
      const snapshot = await local.snapshot(paths, cfg.budget);
      reserved = policy.reservation(snapshot.bytes, snapshot.count);
    }
    stage = "inventory";
    let rows = await policy.inventory(github, cfg.repo);
    core.setOutput("restore-prefix", cfg.prefix);
    core.setOutput("save-key", cfg.saveKey);
    if (cfg.phase === "prepare") {
      const selected = rows.filter(row => policy.owned(row, cfg) && row.key.startsWith(cfg.prefix))
        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)[0];
      stage = "disk";
      const available = await diskSpace(cfg.workspace);
      if (!Number.isSafeInteger(available) || available < 2 * (selected?.size_in_bytes ?? 0) + 2 * 1024 ** 3) {
        reasons.add("disk");
        return;
      }
      const state = policy.plan(rows, cfg, 0);
      ownedCount = state.count;
      if (state.warning) reasons.add("budget-warning");
      core.setOutput("restore-key", selected?.key ?? cfg.saveKey);
      core.setOutput("enabled", "true");
      return;
    }
    core.setOutput("enabled", "true");
    const removed = new Set();
    for (let iteration = 0; iteration <= 1000; iteration++) {
      const state = policy.plan(rows, cfg, reserved);
      ownedCount = state.count;
      if (state.warning) reasons.add("budget-warning");
      if (state.fits) {
        if (cfg.phase === "before-save") core.setOutput("save-allowed", "true");
        return;
      }
      if ((cfg.phase === "before-save" && state.oversized) || !state.candidate) {
        reasons.add("capacity");
        return;
      }
      if (cfg.dryRun) { reasons.add("dry-run"); return; }
      if (iteration === 1000 || removed.has(state.candidate.id)) {
        reasons.add("inventory-stale");
        return;
      }
      stage = "deletion";
      const result = await github.request("DELETE /repos/{owner}/{repo}/actions/caches/{cache_id}", {
        owner: cfg.repo.owner, repo: cfg.repo.repo, cache_id: state.candidate.id,
      });
      if (result?.status !== 204) throw new Error("deletion-invalid");
      removed.add(state.candidate.id);
      deleted++;
      reasons.add("pruned");
      stage = "inventory";
      rows = await policy.inventory(github, cfg.repo);
      // A stale list is not evidence that deletion freed capacity.
      if (rows.some(row => removed.has(row.id))) { reasons.add("inventory-stale"); return; }
    }
  } catch {
    // Never include request bodies, keys, paths, or exception messages in logs.
    reasons.add(stage);
  } finally {
    if (reasons.size) core.warning(`Compute cache: ${[...reasons].sort().join(",")}; owned=${ownedCount}; deleted=${deleted}`);
  }
};
