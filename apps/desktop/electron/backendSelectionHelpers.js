const path = require("path");

function getReportedRoot(runtime) {
  return runtime && typeof runtime.repo_root === "string" ? String(runtime.repo_root) : "";
}

function getConfigPath(runtime) {
  return runtime && typeof runtime.config_path === "string" ? String(runtime.config_path) : "";
}

function getConfigBasename(runtime) {
  const cfgPath = getConfigPath(runtime).trim();
  if (!cfgPath) return "";
  return path.basename(cfgPath).toLowerCase();
}

function isRepoBackendRuntime(runtime, expectedRoot) {
  const reportedRoot = getReportedRoot(runtime);
  if (!reportedRoot || !expectedRoot) return false;
  return path.resolve(reportedRoot) === path.resolve(expectedRoot);
}

function isDockerRepoBackendRuntime(runtime, expectedConfigBasenames = []) {
  const reportedRoot = getReportedRoot(runtime);
  const cfgPath = getConfigPath(runtime);
  if (!/\/configs\/(?:quality|defaults)\.ya?ml$/i.test(cfgPath) || (reportedRoot && reportedRoot !== "/app")) {
    return false;
  }

  const allowed = Array.isArray(expectedConfigBasenames)
    ? expectedConfigBasenames.map((item) => String(item || "").trim().toLowerCase()).filter(Boolean)
    : [];
  if (allowed.length === 0) return true;
  return allowed.includes(getConfigBasename(runtime));
}

function chooseDevBackendPort({
  preferredPort,
  isDev,
  expectedRoot,
  expectedDockerConfigBasenames = [],
  preferredPortOpen,
  preferredRuntime,
  preferredRuntimeKnown = true,
  nearbyPorts = [],
}) {
  const preferred = Number(preferredPort || 5175);
  if (!isDev) return { port: preferred, reason: "non-dev" };
  if (!preferredPortOpen) return { port: preferred, reason: "preferred-free" };
  if (isRepoBackendRuntime(preferredRuntime, expectedRoot)) {
    return { port: preferred, reason: "preferred-repo" };
  }
  if (isDockerRepoBackendRuntime(preferredRuntime, expectedDockerConfigBasenames)) {
    return { port: preferred, reason: "preferred-docker-repo" };
  }

  let firstFree = 0;
  for (const entry of nearbyPorts) {
    const port = Number(entry?.port || 0);
    if (!port || port <= preferred) continue;
    if (!entry?.open) {
      if (!firstFree) firstFree = port;
      continue;
    }
    if (isRepoBackendRuntime(entry?.runtime, expectedRoot)) {
      return { port, reason: "nearby-repo" };
    }
  }

  if (firstFree) {
    return {
      port: firstFree,
      reason: preferredRuntimeKnown ? "preferred-other-backend-first-free" : "preferred-unknown-first-free",
    };
  }

  return {
    port: preferred + 1,
    reason: preferredRuntimeKnown ? "fallback-next" : "fallback-next-unknown",
  };
}

module.exports = {
  isRepoBackendRuntime,
  isDockerRepoBackendRuntime,
  chooseDevBackendPort,
};
