"use strict";

const GLOBAL_CAP = 10_000_000_000;
const HEADROOM = 64 * 1024 ** 2;
const MAX_PAGES = 1000;
const OWNED = /^zad-compute-v1-linux-[0-9a-f]{16}-[1-9][0-9]*-[1-9][0-9]*$/;
const fail = () => { throw new Error("inventory-invalid"); };

function validDate(value) {
  return typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 19) === value.slice(0, 19);
}

function validRow(row) {
  return row && Number.isSafeInteger(row.id) && row.id > 0
    && typeof row.key === "string" && row.key.length > 0 && row.key.length <= 512 && !/[\x00-\x1f\x7f]/.test(row.key)
    && typeof row.ref === "string" && /^refs\/(heads|pull|tags)\/\S+$/.test(row.ref)
    && Number.isSafeInteger(row.size_in_bytes) && row.size_in_bytes >= 0
    && validDate(row.created_at) && validDate(row.last_accessed_at);
}

async function inventory(github, repo) {
  const rows = [], ids = new Set();
  let total;
  for (let page = 1; page <= MAX_PAGES; page++) {
    const response = await github.request("GET /repos/{owner}/{repo}/actions/caches", {
      owner: repo.owner, repo: repo.repo, per_page: 100, page,
    });
    if (response?.status !== 200) fail();
    const data = response.data;
    if (!data || !Number.isSafeInteger(data.total_count) || data.total_count < 0
      || data.total_count > MAX_PAGES * 100 || !Array.isArray(data.actions_caches)) fail();
    if (total === undefined) total = data.total_count;
    if (data.total_count !== total || data.actions_caches.length !== Math.min(100, total - rows.length)) fail();
    for (const row of data.actions_caches) {
      if (!validRow(row) || ids.has(row.id)) fail();
      ids.add(row.id);
      rows.push(row);
    }
    if (rows.length === total) {
      if (!Number.isSafeInteger(rows.reduce((sum, row) => sum + row.size_in_bytes, 0))) fail();
      return rows;
    }
  }
  fail();
}

function owned(row, cfg) { return row.ref === cfg.ref && OWNED.test(row.key); }
// Each file needs a tar header and up to one block of padding, in addition to
// the fixed reserve for directory headers and transport/compression overhead.
function reservation(bytes, count) { return Math.ceil(bytes * 1.05) + HEADROOM + count * 1024; }
function lru(a, b) {
  return Date.parse(a.last_accessed_at) - Date.parse(b.last_accessed_at)
    || Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id;
}

function plan(rows, cfg, reserved) {
  const ours = rows.filter(row => owned(row, cfg));
  const ownBytes = ours.reduce((sum, row) => sum + row.size_in_bytes, 0);
  const otherBytes = rows.filter(row => !owned(row, cfg)).reduce((sum, row) => sum + row.size_in_bytes, 0);
  const cap = Math.min(cfg.budget, GLOBAL_CAP - otherBytes - HEADROOM);
  return { ownBytes, otherBytes, cap, count: ours.length,
    warning: ownBytes * 10 >= cfg.budget * 9 || (ownBytes + reserved) * 10 >= cfg.budget * 9,
    fits: ownBytes + reserved <= cap,
    oversized: reserved > cap,
    candidate: ours.sort(lru)[0] };
}

module.exports = { inventory, owned, reservation, plan };
