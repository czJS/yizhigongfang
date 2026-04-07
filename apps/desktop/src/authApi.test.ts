// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

const authClient = {
  defaults: { baseURL: "" },
  request: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
};

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => authClient),
  },
}));

describe("authApi", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useRealTimers();
    authClient.defaults.baseURL = "";
    authClient.request.mockReset();
    authClient.get.mockReset();
    authClient.post.mockReset();
    localStorage.clear();
    (window as any).bridge = { runtimeInfo: { productEdition: "quality" } };
  });

  it("prefers localStorage auth base override", async () => {
    localStorage.setItem("ygf_auth_api_base", "https://auth.example.com");

    const mod = await import("./authApi");

    expect(mod.authApiBase).toBe("https://auth.example.com");
  });

  it("falls back to built-in auth base", async () => {
    const mod = await import("./authApi");

    expect(mod.authApiBase).toBe("https://auth.miaoyichuhai.com");
  });

  it("falls back to compatibility HTTP base after TLS-like network error", async () => {
    authClient.request
      .mockRejectedValueOnce({ code: "ECONNRESET", message: "Client network socket disconnected before secure TLS connection was established" })
      .mockResolvedValueOnce({
        data: { ttl_seconds: 300, dev_code: "654321" },
      });
    const mod = await import("./authApi");

    await expect(mod.sendAuthEmailCode("user@example.com")).resolves.toEqual({
      ttl_seconds: 300,
      dev_code: "654321",
    });
    expect(authClient.request).toHaveBeenCalledTimes(2);
    expect(mod.getAuthApiBase()).toBe("https://8.149.245.13");
    expect(authClient.defaults.baseURL).toBe("https://8.149.245.13");
    expect(mod.getAuthRouteState()).toMatchObject({
      currentBaseURL: "https://8.149.245.13",
      mode: "compat",
      fallbackEnabled: true,
    });
    expect(JSON.parse(localStorage.getItem("ygf_auth_route_state") || "{}")).toMatchObject({
      mode: "compat",
      baseURL: "https://8.149.245.13",
    });
  });

  it("uses cached compatibility route during fallback cache window", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-03T12:00:00.000Z"));
    localStorage.setItem(
      "ygf_auth_route_state",
      JSON.stringify({
        version: 1,
        mode: "compat",
        baseURL: "https://8.149.245.13",
        expiresAt: Date.now() + 60_000,
        updatedAt: Date.now(),
      })
    );

    const mod = await import("./authApi");

    expect(mod.getAuthApiBase()).toBe("https://8.149.245.13");
    expect(mod.getAuthRouteState()).toMatchObject({
      currentBaseURL: "https://8.149.245.13",
      mode: "compat",
    });
  });

  it("returns to domain-first after fallback cache expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-03T12:00:00.000Z"));
    localStorage.setItem(
      "ygf_auth_route_state",
      JSON.stringify({
        version: 1,
        mode: "compat",
        baseURL: "https://8.149.245.13",
        expiresAt: Date.now() - 1,
        updatedAt: Date.now() - 60_000,
      })
    );

    const mod = await import("./authApi");

    expect(mod.getAuthApiBase()).toBe("https://auth.miaoyichuhai.com");
    expect(mod.getAuthRouteState()).toMatchObject({
      currentBaseURL: "https://auth.miaoyichuhai.com",
      mode: "primary",
    });
    expect(localStorage.getItem("ygf_auth_route_state")).toBeNull();
  });

  it("unwraps send code payload", async () => {
    authClient.request.mockResolvedValueOnce({
      data: { ttl_seconds: 300, dev_code: "123456" },
    });
    const mod = await import("./authApi");

    await expect(mod.sendAuthEmailCode("user@example.com")).resolves.toEqual({
      ttl_seconds: 300,
      dev_code: "123456",
    });
    expect(authClient.request).toHaveBeenCalledWith({
      url: "/api/auth/email/send-code",
      method: "POST",
      data: { email: "user@example.com" },
      headers: undefined,
    });
  });

  it("maps auth client errors to friendly messages", async () => {
    authClient.request.mockRejectedValueOnce({ response: { status: 403 } });
    const mod = await import("./authApi");

    await expect(mod.freezeAdminLicense("bad-secret", "user@example.com", true)).rejects.toThrow("当前请求未授权。");
  });

  it("maps auth validation errors to chinese messages", async () => {
    authClient.request
      .mockRejectedValueOnce({ response: { status: 400, data: { error: "invalid code" } } })
      .mockRejectedValueOnce({ response: { status: 400, data: { error: "activation code already used or inactive" } } })
      .mockRejectedValueOnce({ response: { status: 500, data: { error: "send mail failed: smtp offline" } } });
    const mod = await import("./authApi");

    await expect(
      mod.loginWithEmailCode({
        email: "user@example.com",
        code: "123456",
        device_id: "device-001",
        device_name: "秒译出海-macOS",
        platform: "macOS",
        product_edition: "lite",
      }),
    ).rejects.toThrow("验证码错误或已失效，请重新获取。");

    await expect(mod.redeemActivationCode("tok", "BAD-CODE", "lite")).rejects.toThrow("激活码已被使用或未生效。");
    await expect(mod.sendAuthEmailCode("user@example.com")).rejects.toThrow("验证码发送失败，请稍后重试。");
  });

  it("prefers Electron auth bridge when available", async () => {
    (window as any).bridge = {
      runtimeInfo: { productEdition: "quality" },
      authRequest: vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        data: { ttl_seconds: 300, dev_code: "222333" },
      }),
    };
    const mod = await import("./authApi");

    await expect(mod.sendAuthEmailCode("bridge@example.com")).resolves.toEqual({
      ttl_seconds: 300,
      dev_code: "222333",
    });
    expect((window as any).bridge.authRequest).toHaveBeenCalledWith({
      method: "POST",
      path: "/api/auth/email/send-code",
      body: { email: "bridge@example.com" },
      headers: undefined,
    });
    expect(authClient.request).not.toHaveBeenCalled();
  });

  it("reads current product edition from runtime info", async () => {
    const mod = await import("./authApi");

    expect(mod.getCurrentProductEdition()).toBe("quality");
  });
});
