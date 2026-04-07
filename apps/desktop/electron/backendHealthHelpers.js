function parseBackendProcessList(raw) {
  return String(raw || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^(\d+)\s+(.+)$/);
      if (!match) return null;
      return {
        pid: Number(match[1]),
        command: String(match[2] || "").trim(),
      };
    })
    .filter((item) => item && Number.isFinite(item.pid) && item.pid > 0);
}

function parseListeningPorts(raw) {
  const ports = [];
  for (const line of String(raw || "").split(/\r?\n/)) {
    const match = line.match(/:(\d+)\s+\(LISTEN\)\s*$/);
    if (!match) continue;
    const port = Number(match[1]);
    if (Number.isFinite(port) && port > 0 && !ports.includes(port)) ports.push(port);
  }
  return ports;
}

function parseDockerContainerList(raw) {
  return String(raw || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name = "", status = "", image = "", ports = ""] = line.split("\t");
      return {
        name: String(name || "").trim(),
        status: String(status || "").trim(),
        image: String(image || "").trim(),
        ports: String(ports || "").trim(),
      };
    })
    .filter((item) => item.name);
}

function summarizeDockerBackendContainers({ dockerPsText, preferredPort = 5175, expectedNames = ["yizhi-backend-1"] }) {
  const expected = new Set((Array.isArray(expectedNames) ? expectedNames : []).map((item) => String(item || "").trim()).filter(Boolean));
  const items = parseDockerContainerList(dockerPsText).filter((item) => /(^|\/)yizhi-backend/i.test(item.name) || /^yzh-backend:/i.test(item.image));
  const preferredPortPattern = new RegExp(`(^|[^\\d])${Number(preferredPort || 5175)}->${Number(preferredPort || 5175)}/tcp`);
  const publishedPreferred = items.filter((item) => /^up\b/i.test(item.status) && preferredPortPattern.test(item.ports));
  const stale = items.filter((item) => !expected.has(item.name));
  return {
    items,
    publishedPreferred,
    stale,
    hasPublishedPreferredConflict: publishedPreferred.some((item) => !expected.has(item.name)),
    hasStaleContainers: stale.length > 0,
  };
}

function summarizeBackendListeners({ processListText, listenByPid, preferredPort = 5175, nearbyWindow = 10 }) {
  const items = parseBackendProcessList(processListText)
    .map((proc) => ({
      ...proc,
      ports: parseListeningPorts(listenByPid?.[proc.pid] || ""),
    }))
    .filter((proc) => proc.ports.length > 0);

  const nearbyListeners = items.filter((proc) => proc.ports.some((port) => port >= preferredPort && port < preferredPort + nearbyWindow));
  const preferredListeners = items.filter((proc) => proc.ports.includes(preferredPort));

  return {
    items,
    nearbyListeners,
    preferredListeners,
    hasNearbyConflicts: nearbyListeners.length > 0,
    hasPreferredPortConflict: preferredListeners.length > 0,
  };
}

module.exports = {
  parseBackendProcessList,
  parseListeningPorts,
  parseDockerContainerList,
  summarizeDockerBackendContainers,
  summarizeBackendListeners,
};
