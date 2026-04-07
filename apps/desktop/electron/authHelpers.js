function normalizeAuthRouteState(raw, version, now = Date.now()) {
  if (!raw || typeof raw !== "object") return null;
  if (Number(raw.version) !== Number(version)) return null;
  const mode = raw.mode === "compat" ? "compat" : "primary";
  const baseURL = String(raw.baseURL || "").trim();
  const expiresAt = Number(raw.expiresAt || 0);
  const updatedAt = Number(raw.updatedAt || 0);
  if (!baseURL || expiresAt <= now) return null;
  return {
    version: Number(version),
    mode,
    baseURL,
    expiresAt,
    updatedAt,
    lastErrorCode: raw.lastErrorCode ? String(raw.lastErrorCode) : undefined,
    lastErrorMessage: raw.lastErrorMessage ? String(raw.lastErrorMessage) : undefined,
  };
}

function getPreferredAuthBase({ fallbackEnabled, compatBase, primaryBase, readRouteState, now = Date.now() }) {
  if (!fallbackEnabled || !compatBase) return primaryBase;
  const state = typeof readRouteState === "function" ? readRouteState(now) : null;
  if (state?.mode === "compat" && state.baseURL) return state.baseURL;
  return primaryBase;
}

function isAuthFallbackCandidate(err, { fallbackEnabled, compatBase }) {
  if (!fallbackEnabled || !compatBase) return false;
  const text = `${String(err?.code || "")} ${String(err?.message || "")}`.toLowerCase();
  return (
    text.includes("econnreset") ||
    text.includes("err_connection_closed") ||
    text.includes("network changed") ||
    text.includes("timed out") ||
    text.includes("timeout") ||
    text.includes("tls") ||
    text.includes("ssl") ||
    text.includes("certificate") ||
    text.includes("handshake") ||
    text.includes("socket disconnected before secure tls connection was established")
  );
}

function isTrustedAuthCompatUrl(targetUrl, trustedHosts) {
  try {
    const parsed = new URL(String(targetUrl || ""));
    return parsed.protocol === "https:" && Array.isArray(trustedHosts) && trustedHosts.includes(parsed.hostname);
  } catch {
    return false;
  }
}

function buildFallbackRouteState({ compatBase, version, cacheMs, err, now = Date.now() }) {
  return {
    version: Number(version),
    mode: "compat",
    baseURL: String(compatBase || "").trim(),
    expiresAt: now + Math.max(Number(cacheMs) || 0, 1),
    updatedAt: now,
    lastErrorCode: err?.code ? String(err.code) : undefined,
    lastErrorMessage: err?.message ? String(err.message) : undefined,
  };
}

module.exports = {
  normalizeAuthRouteState,
  getPreferredAuthBase,
  isAuthFallbackCandidate,
  isTrustedAuthCompatUrl,
  buildFallbackRouteState,
};
