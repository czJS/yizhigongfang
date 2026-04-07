import { describe, expect, it } from "vitest";
import authHelpers from "./authHelpers";

const {
  normalizeAuthRouteState,
  getPreferredAuthBase,
  isAuthFallbackCandidate,
  isTrustedAuthCompatUrl,
  buildFallbackRouteState,
} = authHelpers;

describe("authHelpers", () => {
  it("accepts a valid compat route state within ttl", () => {
    const now = Date.parse("2026-04-07T12:00:00.000Z");
    const state = normalizeAuthRouteState(
      {
        version: 1,
        mode: "compat",
        baseURL: "https://8.149.245.13",
        expiresAt: now + 60_000,
        updatedAt: now,
      },
      1,
      now,
    );

    expect(state).toMatchObject({
      mode: "compat",
      baseURL: "https://8.149.245.13",
    });
  });

  it("rejects expired or mismatched route state", () => {
    const now = Date.parse("2026-04-07T12:00:00.000Z");

    expect(
      normalizeAuthRouteState(
        {
          version: 2,
          mode: "compat",
          baseURL: "https://8.149.245.13",
          expiresAt: now + 60_000,
          updatedAt: now,
        },
        1,
        now,
      ),
    ).toBeNull();

    expect(
      normalizeAuthRouteState(
        {
          version: 1,
          mode: "compat",
          baseURL: "https://8.149.245.13",
          expiresAt: now - 1,
          updatedAt: now,
        },
        1,
        now,
      ),
    ).toBeNull();
  });

  it("prefers compat base only when cached state is still valid", () => {
    const compat = "https://8.149.245.13";
    const primary = "https://auth.miaoyichuhai.com";
    const now = Date.parse("2026-04-07T12:00:00.000Z");

    const compatBase = getPreferredAuthBase({
      fallbackEnabled: true,
      compatBase: compat,
      primaryBase: primary,
      readRouteState: () => ({ mode: "compat", baseURL: compat }),
      now,
    });

    const primaryBase = getPreferredAuthBase({
      fallbackEnabled: true,
      compatBase: compat,
      primaryBase: primary,
      readRouteState: () => null,
      now,
    });

    expect(compatBase).toBe(compat);
    expect(primaryBase).toBe(primary);
  });

  it("treats TLS and certificate failures as fallback candidates", () => {
    expect(
      isAuthFallbackCandidate(
        { code: "ECONNRESET", message: "Client network socket disconnected before secure TLS connection was established" },
        { fallbackEnabled: true, compatBase: "https://8.149.245.13" },
      ),
    ).toBe(true);

    expect(
      isAuthFallbackCandidate(
        { code: "EOTHER", message: "certificate verify failed" },
        { fallbackEnabled: true, compatBase: "https://8.149.245.13" },
      ),
    ).toBe(true);

    expect(
      isAuthFallbackCandidate(
        { code: "EOTHER", message: "business validation failed" },
        { fallbackEnabled: true, compatBase: "https://8.149.245.13" },
      ),
    ).toBe(false);
  });

  it("builds compat route state with cache and error details", () => {
    const now = Date.parse("2026-04-07T12:00:00.000Z");
    const state = buildFallbackRouteState({
      compatBase: "https://8.149.245.13",
      version: 1,
      cacheMs: 300_000,
      err: { code: "ECONNRESET", message: "tls handshake failed" },
      now,
    });

    expect(state).toMatchObject({
      version: 1,
      mode: "compat",
      baseURL: "https://8.149.245.13",
      updatedAt: now,
      lastErrorCode: "ECONNRESET",
      lastErrorMessage: "tls handshake failed",
    });
    expect(state.expiresAt).toBe(now + 300_000);
  });

  it("only trusts https urls on trusted compat hosts", () => {
    expect(isTrustedAuthCompatUrl("https://8.149.245.13/api/health", ["8.149.245.13"])).toBe(true);
    expect(isTrustedAuthCompatUrl("http://8.149.245.13/api/health", ["8.149.245.13"])).toBe(false);
    expect(isTrustedAuthCompatUrl("https://example.com/api/health", ["8.149.245.13"])).toBe(false);
  });
});
