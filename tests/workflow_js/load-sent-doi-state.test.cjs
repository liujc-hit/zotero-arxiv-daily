"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const loadSentDoiState = require("../../.github/scripts/load-sent-doi-state.cjs");

const CONTEXT = { repo: { owner: "paper-owner", repo: "paper-repo" } };
const STATE_BRANCH = "sent-doi-state-v1";
const STATE_PATH = ".state/sent-dois.fernet";

async function temporaryWorkspace(t) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "sent-doi-load-"));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  return workspace;
}

function makeCore() {
  const outputs = [];
  const failOnLog = () => assert.fail("state loader must not log");
  return {
    outputs,
    setOutput(name, value) {
      outputs.push([name, value]);
    },
    debug: failOnLog,
    info: failOnLog,
    notice: failOnLog,
    warning: failOnLog,
    error: failOnLog,
  };
}

function makeGithub(...responses) {
  const calls = [];
  return {
    calls,
    async request(route, parameters) {
      calls.push({ route, parameters });
      assert.notEqual(responses.length, 0, `unexpected request: ${route}`);
      const response = responses.shift();
      if (response instanceof Error) {
        throw response;
      }
      return response;
    },
  };
}

function apiResponse(status, data) {
  return { status, data };
}

function statusError(status) {
  return Object.assign(new Error("request failed"), { status });
}

function loadArgs(workspace, github, core, overrides = {}) {
  return {
    github,
    context: CONTEXT,
    core,
    env: {
      GITHUB_WORKSPACE: workspace,
      SENT_DOI_STATE_ENABLED: "true",
      SENT_DOI_STATE_KEY_PRESENT: "true",
      ...overrides,
    },
  };
}

test("load reports an empty state without API calls when disabled", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const github = makeGithub();
  const core = makeCore();

  // When
  await loadSentDoiState(loadArgs(workspace, github, core, {
    SENT_DOI_STATE_ENABLED: "false",
    SENT_DOI_STATE_KEY_PRESENT: "false",
  }));

  // Then
  assert.deepEqual(github.calls, []);
  assert.deepEqual(core.outputs, [["branch-exists", "false"], ["blob-sha", ""]]);
});

test("load requires a workspace before evaluating the opt-in", async () => {
  // Given
  const github = makeGithub();
  const core = makeCore();

  // When / Then
  await assert.rejects(
    loadSentDoiState({
      github,
      context: CONTEXT,
      core,
      env: { SENT_DOI_STATE_ENABLED: "false" },
    }),
    /GitHub workspace is unavailable/,
  );
  assert.deepEqual(github.calls, []);
});

for (const [name, overrides, message] of [
  ["invalid opt-in", { SENT_DOI_STATE_ENABLED: "TRUE" }, /must be true or false/],
  ["missing key", { SENT_DOI_STATE_KEY_PRESENT: "false" }, /state key is required/],
]) {
  test(`load rejects ${name} before API access`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    const github = makeGithub();

    // When / Then
    await assert.rejects(loadSentDoiState(loadArgs(workspace, github, makeCore(), overrides)), message);
    assert.deepEqual(github.calls, []);
  });
}

test("load treats only a branch-probe 404 as first-run state", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const github = makeGithub(statusError(404));
  const core = makeCore();

  // When
  await loadSentDoiState(loadArgs(workspace, github, core));

  // Then
  assert.equal(github.calls.length, 1);
  assert.deepEqual(core.outputs, [["branch-exists", "false"], ["blob-sha", ""]]);
});

test("load fails closed when Contents is missing on an existing branch", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const github = makeGithub(
    apiResponse(200, { ref: `refs/heads/${STATE_BRANCH}` }),
    statusError(404),
  );

  // When / Then
  await assert.rejects(
    loadSentDoiState(loadArgs(workspace, github, makeCore())),
    /GitHub API request failed with HTTP status 404/,
  );
  assert.equal(github.calls.length, 2);
});

for (const [name, branchResponse, message] of [
  ["non-404 probe failure", statusError(403), /probe failed with HTTP status 403/],
  ["unexpected probe status", apiResponse(201, { ref: `refs/heads/${STATE_BRANCH}` }), /unexpected response/],
  ["wrong branch ref", apiResponse(200, { ref: "refs/heads/main" }), /unexpected response/],
]) {
  test(`load terminates on ${name}`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    const github = makeGithub(branchResponse);

    // When / Then
    await assert.rejects(loadSentDoiState(loadArgs(workspace, github, makeCore())), message);
    assert.equal(github.calls.length, 1);
  });
}

const validMetadata = { type: "file", sha: "blob-sha", size: 5 };
const validBlob = { sha: "blob-sha", size: 5, encoding: "base64", content: "c3RhdGU=" };
const invalidCases = [
  ["Contents array", [], validBlob, /valid encrypted state file/, 2],
  ["metadata SHA", { ...validMetadata, sha: " " }, validBlob, /valid encrypted state file/, 2],
  ["metadata size", { ...validMetadata, size: 0 }, validBlob, /valid encrypted state file/, 2],
  ["blob SHA", validMetadata, { ...validBlob, sha: "other" }, /does not match/, 3],
  ["blob size", validMetadata, { ...validBlob, size: 4 }, /does not match/, 3],
  ["blob encoding", validMetadata, { ...validBlob, encoding: "utf-8" }, /does not match/, 3],
  ["blob content", validMetadata, { ...validBlob, content: null }, /does not match/, 3],
  ["base64 alphabet", validMetadata, { ...validBlob, content: "%%%%" }, /not valid base64/, 3],
  ["canonical base64", { ...validMetadata, size: 1 }, { ...validBlob, size: 1, content: "AB==" }, /not canonical base64/, 3],
  ["decoded size", { ...validMetadata, size: 6 }, { ...validBlob, size: 6 }, /size does not match/, 3],
];

for (const [name, metadata, blob, message, expectedCalls] of invalidCases) {
  test(`load rejects invalid ${name}`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    const github = makeGithub(
      apiResponse(200, { ref: `refs/heads/${STATE_BRANCH}` }),
      apiResponse(200, metadata),
      apiResponse(200, blob),
    );

    // When / Then
    await assert.rejects(loadSentDoiState(loadArgs(workspace, github, makeCore())), message);
    assert.equal(github.calls.length, expectedCalls);
  });
}

test("load requests object metadata then Git Blobs for state larger than 1 MB and writes privately", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const stateFile = path.join(workspace, STATE_PATH);
  const ciphertext = Buffer.alloc(1_048_577, 0x61);
  await fs.mkdir(path.dirname(stateFile), { recursive: true });
  await fs.writeFile(stateFile, "stale");
  const github = makeGithub(
    apiResponse(200, { ref: `refs/heads/${STATE_BRANCH}` }),
    apiResponse(200, { type: "file", sha: "large-blob", size: ciphertext.length }),
    apiResponse(200, {
      sha: "large-blob",
      size: ciphertext.length,
      encoding: "base64",
      content: ciphertext.toString("base64"),
    }),
  );
  const core = makeCore();
  const realWriteFile = fs.writeFile.bind(fs);
  let writeOptions;
  t.mock.method(fs, "writeFile", async (file, data, options) => {
    writeOptions = options;
    return realWriteFile(file, data, options);
  });

  // When
  await loadSentDoiState(loadArgs(workspace, github, core));

  // Then
  assert.deepEqual(github.calls, [
    {
      route: "GET /repos/{owner}/{repo}/git/ref/heads/{branch}",
      parameters: { owner: "paper-owner", repo: "paper-repo", branch: STATE_BRANCH },
    },
    {
      route: "GET /repos/{owner}/{repo}/contents/{path}",
      parameters: {
        owner: "paper-owner",
        repo: "paper-repo",
        path: STATE_PATH,
        ref: STATE_BRANCH,
        headers: { accept: "application/vnd.github.object+json" },
      },
    },
    {
      route: "GET /repos/{owner}/{repo}/git/blobs/{file_sha}",
      parameters: { owner: "paper-owner", repo: "paper-repo", file_sha: "large-blob" },
    },
  ]);
  assert.deepEqual(writeOptions, { flag: "wx", mode: 0o600 });
  assert.ok((await fs.readFile(stateFile)).equals(ciphertext));
  assert.equal((await fs.stat(stateFile)).mode & 0o777, 0o600);
  assert.deepEqual(core.outputs, [["branch-exists", "true"], ["blob-sha", "large-blob"]]);
});
