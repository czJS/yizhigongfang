import { describe, it, expect } from "vitest";

const { normalizeOpenTargetPath, sanitizeDiagnosticsSummary, sanitizeDiagnosticsText } = require("./securityHelpers");

describe("securityHelpers", () => {
  it("rejects external URLs for openPath", () => {
    expect(normalizeOpenTargetPath("https://example.com")).toEqual({
      ok: false,
      error: "不支持打开外部链接",
    });
  });

  it("normalizes absolute local paths for openPath", () => {
    const result = normalizeOpenTargetPath("C:/demo/output");
    expect(result.ok).toBe(true);
    expect(String(result.path || "")).toContain("C:");
  });

  it("redacts sensitive tokens in diagnostics text", () => {
    const text = [
      "Authorization: Bearer abc123",
      "X-YGF-Token: local-token",
      'ygf_auth_token: "cloud-token"',
    ].join("\n");
    const sanitized = sanitizeDiagnosticsText(text);
    expect(sanitized).not.toContain("abc123");
    expect(sanitized).not.toContain("local-token");
    expect(sanitized).not.toContain("cloud-token");
    expect(sanitized).toContain("[REDACTED]");
  });

  it("redacts path-like fields in diagnostics summary", () => {
    const sanitized = sanitizeDiagnosticsSummary({
      configPath: "C:/app/resources/configs/defaults.yaml",
      runtimeStatus: {
        backendExe: "C:/app/resources/backend_server.exe",
        modelsPackZip: "C:/app/resources/models_pack.zip",
      },
    });
    expect(sanitized).toEqual({
      configPath: "defaults.yaml",
      runtimeStatus: {
        backendExe: "backend_server.exe",
        modelsPackZip: "models_pack.zip",
      },
    });
  });
});
