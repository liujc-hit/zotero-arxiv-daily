"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

module.exports = async function loadSentDoiState({
  github,
  context,
  core,
  env = process.env,
}) {
  const stateBranch = "sent-doi-state-v1";
  const statePath = ".state/sent-dois.fernet";
  const stateEnabled = env.SENT_DOI_STATE_ENABLED ?? "false";
  const stateKeyPresent = env.SENT_DOI_STATE_KEY_PRESENT ?? "false";
  const workspace = env.GITHUB_WORKSPACE;
  const { owner, repo } = context.repo;

  if (!workspace) {
    throw new Error("GitHub workspace is unavailable");
  }
  if (stateEnabled === "false") {
    core.setOutput("branch-exists", "false");
    core.setOutput("blob-sha", "");
    return;
  }
  if (stateEnabled !== "true") {
    throw new Error("Sent DOI state opt-in must be true or false");
  }
  if (stateKeyPresent !== "true") {
    throw new Error("Sent DOI state key is required when persistence is enabled");
  }

  const stateFile = path.join(workspace, statePath);
  await fs.mkdir(path.dirname(stateFile), { recursive: true });
  await fs.rm(stateFile, { force: true });

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

  let branchResponse;
  try {
    branchResponse = await github.request(
      "GET /repos/{owner}/{repo}/git/ref/heads/{branch}",
      { owner, repo, branch: stateBranch },
    );
  } catch (error) {
    const status = error && typeof error === "object" && "status" in error
      ? error.status
      : undefined;
    if (status === 404) {
      core.setOutput("branch-exists", "false");
      core.setOutput("blob-sha", "");
      return;
    }
    throw new Error(
      `State branch probe failed with HTTP status ${status ?? "unknown"}`,
    );
  }
  if (
    branchResponse.status !== 200
    || branchResponse.data.ref !== `refs/heads/${stateBranch}`
  ) {
    throw new Error("State branch probe returned an unexpected response");
  }

  const fileResponse = await requestExact(
    "GET /repos/{owner}/{repo}/contents/{path}",
    200,
    {
      owner,
      repo,
      path: statePath,
      ref: stateBranch,
      headers: { accept: "application/vnd.github.object+json" },
    },
  );
  if (
    Array.isArray(fileResponse.data)
    || fileResponse.data.type !== "file"
    || typeof fileResponse.data.sha !== "string"
    || !fileResponse.data.sha.trim()
    || !Number.isSafeInteger(fileResponse.data.size)
    || fileResponse.data.size <= 0
  ) {
    throw new Error("State branch does not contain a valid encrypted state file");
  }

  const fileSha = fileResponse.data.sha;
  const fileSize = fileResponse.data.size;
  const blobResponse = await requestExact(
    "GET /repos/{owner}/{repo}/git/blobs/{file_sha}",
    200,
    { owner, repo, file_sha: fileSha },
  );
  if (
    blobResponse.data.sha !== fileSha
    || blobResponse.data.size !== fileSize
    || blobResponse.data.encoding !== "base64"
    || typeof blobResponse.data.content !== "string"
  ) {
    throw new Error("Git blob does not match encrypted state metadata");
  }

  const encodedContent = blobResponse.data.content.replace(/[\r\n]/g, "");
  if (
    encodedContent.length % 4 !== 0
    || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encodedContent)
  ) {
    throw new Error("Encrypted state content is not valid base64");
  }
  const decoded = Buffer.from(encodedContent, "base64");
  if (decoded.toString("base64") !== encodedContent) {
    throw new Error("Encrypted state content is not canonical base64");
  }
  if (decoded.length !== fileSize) {
    throw new Error("Encrypted state size does not match metadata");
  }
  await fs.writeFile(stateFile, decoded, { flag: "wx", mode: 0o600 });
  core.setOutput("branch-exists", "true");
  core.setOutput("blob-sha", fileSha);
};
