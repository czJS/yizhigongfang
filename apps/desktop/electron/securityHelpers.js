const path = require("path");

function normalizeOpenTargetPath(targetPath) {
  const raw = String(targetPath || "").trim();
  if (!raw) {
    return { ok: false, error: "路径为空" };
  }
  if (/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(raw)) {
    return { ok: false, error: "不支持打开外部链接" };
  }
  const resolved = path.resolve(raw);
  if (!path.isAbsolute(resolved)) {
    return { ok: false, error: "仅支持打开本地绝对路径" };
  }
  return { ok: true, path: resolved };
}

function sanitizeDiagnosticsText(text) {
  let sanitized = String(text || "");
  const replacements = [
    [/(Authorization\s*:\s*Bearer\s+)[^\s\r\n]+/gi, "$1[REDACTED]"],
    [/(X-YGF-Token\s*:\s*)[^\s\r\n]+/gi, "$1[REDACTED]"],
    [/(X-YGF-Cloud-Token\s*:\s*)[^\s\r\n]+/gi, "$1[REDACTED]"],
    [/(X-Admin-Secret\s*:\s*)[^\s\r\n]+/gi, "$1[REDACTED]"],
    [/(ygf_auth_token["']?\s*[:=]\s*["'])[^"'\r\n]+/gi, "$1[REDACTED]"],
    [/(YGF_API_TOKEN["']?\s*[:=]\s*["'])[^"'\r\n]+/gi, "$1[REDACTED]"],
    [/(token["']?\s*:\s*["'])[A-Za-z0-9._\-]+/gi, "$1[REDACTED]"],
  ];
  for (const [pattern, replacement] of replacements) {
    sanitized = sanitized.replace(pattern, replacement);
  }
  return sanitized;
}

function sanitizePathLikeValue(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/\\/g, "/");
  const base = normalized.split("/").filter(Boolean).pop() || "";
  return base || "[REDACTED_PATH]";
}

function sanitizeDiagnosticsSummary(value) {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeDiagnosticsSummary(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (item == null) {
      result[key] = item;
      continue;
    }
    if (typeof item === "string" && /(path|root|dir|exe|zip)$/i.test(key)) {
      result[key] = sanitizePathLikeValue(item);
      continue;
    }
    result[key] = sanitizeDiagnosticsSummary(item);
  }
  return result;
}

module.exports = {
  normalizeOpenTargetPath,
  sanitizeDiagnosticsText,
  sanitizeDiagnosticsSummary,
};
