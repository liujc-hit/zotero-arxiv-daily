"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const persistSentDoiState = require("../../.github/scripts/persist-sent-doi-state.cjs");

const REPOSITORY = { owner: "paper-owner", repo: "paper-repo" };
const CONTEXT = { repo: REPOSITORY };
const STATE_BRANCH = "sent-doi-state-v1";
const STATE_PATH = ".state/sent-dois.fernet";

async function temporaryWorkspace(t) {
  const workspace = await fs.mkdtemp(path.join(os.tmpdir(), "sent-doi-persist-"));
  t.after(() => fs.rm(workspace, { recursive: true, force: true }));
  return workspace;
}

async function writeState(workspace, content) {
  const stateFile = path.join(workspace, STATE_PATH);
  await fs.mkdir(path.dirname(stateFile), { recursive: true });
  await fs.writeFile(stateFile, content);
}

function makeCore() {
  const failOnUse = () => assert.fail("state publisher must not log or set outputs");
  return {
    setOutput: failOnUse,
    debug: failOnUse, info: failOnUse, notice: failOnUse,
    warning: failOnUse, error: failOnUse,
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

function apiResponse(status, data = {}) { return { status, data }; }

function statusError(status) { return Object.assign(new Error("request failed"), { status }); }

function persistArgs(workspace, github, overrides = {}) {
  return {
    github,
    context: CONTEXT,
    core: makeCore(),
    env: {
      GITHUB_WORKSPACE: workspace,
      SENT_DOI_STATE_ENABLED: "true",
      SENT_DOI_STATE_BRANCH_EXISTS: "false",
      SENT_DOI_STATE_BLOB_SHA: "",
      ...overrides,
    },
  };
}

test("persist makes no API call when disabled", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const github = makeGithub();

  // When
  await persistSentDoiState(persistArgs(workspace, github, {
    SENT_DOI_STATE_ENABLED: "false",
    SENT_DOI_STATE_BRANCH_EXISTS: "",
  }));

  // Then
  assert.deepEqual(github.calls, []);
});

test("persist requires a workspace before evaluating the opt-in", async () => {
  // Given
  const github = makeGithub();

  // When / Then
  await assert.rejects(
    persistSentDoiState({
      github,
      context: CONTEXT,
      core: makeCore(),
      env: { SENT_DOI_STATE_ENABLED: "false" },
    }),
    /GitHub workspace is unavailable/,
  );
  assert.deepEqual(github.calls, []);
});

for (const [name, overrides, message] of [
  ["invalid opt-in", { SENT_DOI_STATE_ENABLED: "TRUE" }, /must be true or false/],
  ["missing branch result", { SENT_DOI_STATE_BRANCH_EXISTS: "" }, /probe result is unavailable/],
]) {
  test(`persist rejects ${name} before reading state`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    const github = makeGithub();

    // When / Then
    await assert.rejects(persistSentDoiState(persistArgs(workspace, github, overrides)), message);
    assert.deepEqual(github.calls, []);
  });
}

test("persist treats only absent-branch ENOENT as a no-op", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const github = makeGithub();

  // When
  await persistSentDoiState(persistArgs(workspace, github));

  // Then
  assert.deepEqual(github.calls, []);
});

const readFailureCases = [
  ["existing ENOENT", "true", null, /state was not saved/],
  ["non-ENOENT", "false", "not-directory", /state could not be read/],
  ["empty state", "false", "empty", /state was not saved/],
];

for (const [name, branchExists, setup, message] of readFailureCases) {
  test(`persist fails closed for ${name}`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    if (setup === "not-directory") {
      await fs.writeFile(path.join(workspace, ".state"), "file");
    } else if (setup === "empty") {
      await writeState(workspace, Buffer.alloc(0));
    }
    const github = makeGithub();

    // When / Then
    await assert.rejects(
      persistSentDoiState(persistArgs(workspace, github, {
        SENT_DOI_STATE_BRANCH_EXISTS: branchExists,
      })),
      message,
    );
    assert.deepEqual(github.calls, []);
  });
}

test("persist requires the previous blob SHA for an existing branch", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  await writeState(workspace, "ciphertext");
  const github = makeGithub();

  // When / Then
  await assert.rejects(
    persistSentDoiState(persistArgs(workspace, github, {
      SENT_DOI_STATE_BRANCH_EXISTS: "true",
      SENT_DOI_STATE_BLOB_SHA: " ",
    })),
    /Previous state blob SHA is unavailable/,
  );
  assert.deepEqual(github.calls, []);
});

test("persist updates existing state with an exact SHA-guarded Contents PUT", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const ciphertext = Buffer.from([0x00, 0x01, 0xff]);
  await writeState(workspace, ciphertext);
  const github = makeGithub(apiResponse(200));

  // When
  await persistSentDoiState(persistArgs(workspace, github, {
    SENT_DOI_STATE_BRANCH_EXISTS: "true",
    SENT_DOI_STATE_BLOB_SHA: "previous-blob",
  }));

  // Then
  assert.deepEqual(github.calls, [{
    route: "PUT /repos/{owner}/{repo}/contents/{path}",
    parameters: {
      ...REPOSITORY,
      path: STATE_PATH,
      message: "Update encrypted sent DOI state",
      content: ciphertext.toString("base64"),
      sha: "previous-blob",
      branch: STATE_BRANCH,
    },
  }]);
});

test("persist creates blob, tree, parentless commit, and final ref in exact order", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  const ciphertext = Buffer.from("new encrypted state");
  await writeState(workspace, ciphertext);
  const github = makeGithub(
    apiResponse(201, { sha: "new-blob" }),
    apiResponse(201, { sha: "new-tree" }),
    apiResponse(201, { sha: "new-commit" }),
    apiResponse(201),
  );

  // When
  await persistSentDoiState(persistArgs(workspace, github));

  // Then
  assert.deepEqual(github.calls, [
    {
      route: "POST /repos/{owner}/{repo}/git/blobs",
      parameters: {
        ...REPOSITORY,
        content: ciphertext.toString("base64"),
        encoding: "base64",
      },
    },
    {
      route: "POST /repos/{owner}/{repo}/git/trees",
      parameters: {
        ...REPOSITORY,
        tree: [{ path: STATE_PATH, mode: "100644", type: "blob", sha: "new-blob" }],
      },
    },
    {
      route: "POST /repos/{owner}/{repo}/git/commits",
      parameters: {
        ...REPOSITORY,
        message: "Initialize encrypted sent DOI state",
        tree: "new-tree",
        parents: [],
      },
    },
    {
      route: "POST /repos/{owner}/{repo}/git/refs",
      parameters: {
        ...REPOSITORY,
        ref: `refs/heads/${STATE_BRANCH}`,
        sha: "new-commit",
      },
    },
  ]);
  assert.equal(github.calls.at(-1).route, "POST /repos/{owner}/{repo}/git/refs");
});

for (const [name, badIndex] of [["blob", 0], ["tree", 1], ["commit", 2]]) {
  test(`persist terminates when the ${name} response has no SHA`, async (t) => {
    // Given
    const workspace = await temporaryWorkspace(t);
    await writeState(workspace, "ciphertext");
    const responses = [
      apiResponse(201, { sha: "new-blob" }),
      apiResponse(201, { sha: "new-tree" }),
      apiResponse(201, { sha: "new-commit" }),
      apiResponse(201),
    ];
    responses[badIndex] = apiResponse(201, { sha: " " });
    const github = makeGithub(...responses);

    // When / Then
    await assert.rejects(
      persistSentDoiState(persistArgs(workspace, github)),
      /response is missing an object SHA/,
    );
    assert.equal(github.calls.length, badIndex + 1);
  });
}

test("persist terminates immediately on an unexpected response status", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  await writeState(workspace, "ciphertext");
  const github = makeGithub(
    apiResponse(201, { sha: "new-blob" }),
    apiResponse(200, { sha: "new-tree" }),
    apiResponse(201, { sha: "must-not-run" }),
  );

  // When / Then
  await assert.rejects(
    persistSentDoiState(persistArgs(workspace, github)),
    /GitHub API request failed with HTTP status unknown/,
  );
  assert.deepEqual(
    github.calls.map(({ route }) => route),
    ["POST /repos/{owner}/{repo}/git/blobs", "POST /repos/{owner}/{repo}/git/trees"],
  );
});

test("persist does not retry a failed API request", async (t) => {
  // Given
  const workspace = await temporaryWorkspace(t);
  await writeState(workspace, "ciphertext");
  const github = makeGithub(statusError(503), apiResponse(201, { sha: "retry" }));

  // When / Then
  await assert.rejects(
    persistSentDoiState(persistArgs(workspace, github)),
    /GitHub API request failed with HTTP status 503/,
  );
  assert.equal(github.calls.length, 1);
});
