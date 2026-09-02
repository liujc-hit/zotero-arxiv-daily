"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

module.exports = async function persistSentDoiState({
  github,
  context,
  core,
  env = process.env,
}) {
  const stateBranch = "sent-doi-state-v1";
  const statePath = ".state/sent-dois.fernet";
  const stateEnabled = env.SENT_DOI_STATE_ENABLED ?? "false";
  const branchExists = env.SENT_DOI_STATE_BRANCH_EXISTS ?? "";
  const previousBlobSha = env.SENT_DOI_STATE_BLOB_SHA ?? "";
  const workspace = env.GITHUB_WORKSPACE;
  const { owner, repo } = context.repo;

  if (!workspace) {
    throw new Error("GitHub workspace is unavailable");
  }
  if (stateEnabled === "false") {
    return;
  }
  if (stateEnabled !== "true") {
    throw new Error("Sent DOI state opt-in must be true or false");
  }
  if (branchExists !== "true" && branchExists !== "false") {
    throw new Error("State branch probe result is unavailable");
  }

  const requestExact = async (route, expectedStatus, parameters) => {
    try {
      const response = await github.request(route, parameters);
      if (response.status !== expectedStatus) {
        throw new Error("GitHub API returned an unexpected status");
      }
      return response;
    } catch (error) {
      const status = error && typeof error === "object" && "status" in error
        ? error.status
        : undefined;
      throw new Error(
        `GitHub API request failed with HTTP status ${status ?? "unknown"}`,
      );
    }
  };
  const responseSha = (response) => {
    const sha = response.data.sha;
    if (typeof sha !== "string" || !sha.trim()) {
      throw new Error("GitHub API response is missing an object SHA");
    }
    return sha;
  };

  let ciphertext;
  try {
    ciphertext = await fs.readFile(path.join(workspace, statePath));
  } catch (error) {
    const code = error && typeof error === "object" && "code" in error
      ? error.code
      : undefined;
    if (code !== "ENOENT") {
      throw new Error("Encrypted sent DOI state could not be read");
    }
    if (branchExists === "false") {
      return;
    }
    throw new Error("Encrypted sent DOI state was not saved");
  }
  if (ciphertext.length === 0) {
    throw new Error("Encrypted sent DOI state was not saved");
  }
  const encodedContent = ciphertext.toString("base64");

  if (branchExists === "true") {
    if (!previousBlobSha.trim()) {
      throw new Error("Previous state blob SHA is unavailable");
    }
    await requestExact(
      "PUT /repos/{owner}/{repo}/contents/{path}",
      200,
      {
        owner,
        repo,
        path: statePath,
        message: "Update encrypted sent DOI state",
        content: encodedContent,
        sha: previousBlobSha,
        branch: stateBranch,
      },
    );
    return;
  }

  const blobResponse = await requestExact(
    "POST /repos/{owner}/{repo}/git/blobs",
    201,
    {
      owner,
      repo,
      content: encodedContent,
      encoding: "base64",
    },
  );
  const blobSha = responseSha(blobResponse);
  const treeResponse = await requestExact(
    "POST /repos/{owner}/{repo}/git/trees",
    201,
    {
      owner,
      repo,
      tree: [
        {
          path: statePath,
          mode: "100644",
          type: "blob",
          sha: blobSha,
        },
      ],
    },
  );
  const treeSha = responseSha(treeResponse);
  const commitResponse = await requestExact(
    "POST /repos/{owner}/{repo}/git/commits",
    201,
    {
      owner,
      repo,
      message: "Initialize encrypted sent DOI state",
      tree: treeSha,
      parents: [],
    },
  );
  const commitSha = responseSha(commitResponse);
  await requestExact(
    "POST /repos/{owner}/{repo}/git/refs",
    201,
    {
      owner,
      repo,
      ref: `refs/heads/${stateBranch}`,
      sha: commitSha,
    },
  );
};
