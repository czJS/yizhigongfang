// @vitest-environment jsdom

import { renderHook, act, waitFor } from "@testing-library/react";
import { message } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getAuthMe,
  getCurrentProductEdition,
  getCurrentLicense,
  listAuthDevices,
  loginWithEmailCode,
  logoutAuth,
  redeemActivationCode,
  sendAuthEmailCode,
} from "../authApi";
import { useCloudAuthGate } from "./useCloudAuthGate";

vi.mock("../authApi", () => ({
  authApiBase: "http://auth.test",
  getAuthMe: vi.fn(),
  getCurrentProductEdition: vi.fn(() => "lite"),
  getCurrentLicense: vi.fn(),
  listAuthDevices: vi.fn(),
  loginWithEmailCode: vi.fn(),
  logoutAuth: vi.fn(),
  redeemActivationCode: vi.fn(),
  sendAuthEmailCode: vi.fn(),
}));

const mockedGetAuthMe = vi.mocked(getAuthMe);
const mockedGetCurrentProductEdition = vi.mocked(getCurrentProductEdition);
const mockedGetCurrentLicense = vi.mocked(getCurrentLicense);
const mockedListAuthDevices = vi.mocked(listAuthDevices);
const mockedLoginWithEmailCode = vi.mocked(loginWithEmailCode);
const mockedLogoutAuth = vi.mocked(logoutAuth);
const mockedRedeemActivationCode = vi.mocked(redeemActivationCode);
const mockedSendAuthEmailCode = vi.mocked(sendAuthEmailCode);

describe("useCloudAuthGate", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.useRealTimers();
    mockedGetCurrentProductEdition.mockReturnValue("lite");
    (window as any).bridge = {
      runtimeInfo: { platform: "macOS", productEdition: "lite" },
      getDeviceIdentity: vi.fn().mockResolvedValue({
        deviceCode: "DEV-STABLE-001",
        aliases: ["DEV-STABLE-001", "AA-BB-CC-DD-EE-FF"],
      }),
      getDeviceCode: vi.fn().mockResolvedValue("device-001"),
    };
    vi.spyOn(message, "success").mockImplementation(() => undefined as any);
  });

  it("treats expired licenses as readonly and allows in-app renewal", async () => {
    localStorage.setItem("ygf_auth_token", "tok-expired");
    mockedGetAuthMe.mockResolvedValueOnce({
      user: { id: 1, email: "expired@example.com", status: "active" },
      license: {
        status: "expired",
        active: false,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    expect(result.current.authStage).toBe("ready");
    expect(result.current.showAuthGate).toBe(false);
    expect(result.current.isReadonlyMode).toBe(true);
    expect(result.current.canRenewInApp).toBe(true);
    expect(result.current.taskCreationBlocked).toBe(true);
    expect(result.current.taskCreationBlockedReason).toContain("当前授权已到期");
  });

  it("keeps session on transient auth refresh failures", async () => {
    localStorage.setItem("ygf_auth_token", "tok-network");
    mockedGetAuthMe.mockRejectedValueOnce(new Error("网络异常，请稍后重试"));

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    expect(localStorage.getItem("ygf_auth_token")).toBe("tok-network");
    expect(result.current.authError).toBe("网络异常，请稍后重试");
  });

  it("clears session only when auth refresh confirms token invalid", async () => {
    localStorage.setItem("ygf_auth_token", "tok-invalid");
    mockedGetAuthMe.mockRejectedValueOnce(new Error("登录态已失效，请重新登录。"));

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    expect(localStorage.getItem("ygf_auth_token")).toBeNull();
    expect(result.current.user).toBeNull();
    expect(result.current.authError).toBe("登录态已失效，请重新登录。");
  });

  it("stores token and refreshes me on successful login and redeem", async () => {
    mockedLoginWithEmailCode.mockResolvedValueOnce({
      token: "tok-active",
      user: { id: 1, email: "user@example.com", status: "active" },
      license: { status: "none", active: false, license_type: "", start_at: "", expire_at: "" },
      device_limit: 2,
    });
    mockedRedeemActivationCode.mockResolvedValueOnce({
      ok: true,
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
    });
    mockedGetAuthMe.mockResolvedValue({
      user: { id: 1, email: "user@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [{ device_id: "device-001", device_name: "秒译出海-macOS", platform: "macOS" }],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    act(() => {
      result.current.setEmail("user@example.com");
      result.current.setCode("123456");
      result.current.setActivationCode("ABCD-EFGH-IJKL-MNOP");
    });

    await act(async () => {
      await result.current.handleLogin();
    });

    expect(localStorage.getItem("ygf_auth_token")).toBe("tok-active");
    expect(localStorage.getItem("ygf_auth_email")).toBe("user@example.com");
    expect(result.current.license.status).toBe("active");
    expect(result.current.user?.email).toBe("user@example.com");
    expect(mockedLoginWithEmailCode).toHaveBeenCalledTimes(1);
    expect(mockedLoginWithEmailCode.mock.calls[0]?.[0]).toMatchObject({
      device_id: "DEV-STABLE-001",
      device_aliases: ["DEV-STABLE-001", "AA-BB-CC-DD-EE-FF"],
    });
    expect(mockedRedeemActivationCode).toHaveBeenCalledWith("tok-active", "ABCD-EFGH-IJKL-MNOP", "lite");
  });

  it("allows already-licensed users to log in without entering an activation code", async () => {
    mockedLoginWithEmailCode.mockResolvedValueOnce({
      token: "tok-existing",
      user: { id: 7, email: "licensed@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      device_limit: 2,
    });
    mockedGetAuthMe.mockResolvedValue({
      user: { id: 7, email: "licensed@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [{ device_id: "device-001", device_name: "秒译出海-macOS", platform: "macOS" }],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());
    await waitFor(() => expect(result.current.authLoading).toBe(false));

    act(() => {
      result.current.setEmail("licensed@example.com");
      result.current.setCode("123456");
      result.current.setActivationCode("");
    });

    await act(async () => {
      await result.current.handleLogin();
    });

    expect(result.current.user?.email).toBe("licensed@example.com");
    expect(result.current.license.status).toBe("active");
    expect(result.current.showAuthGate).toBe(false);
    expect(result.current.taskCreationBlocked).toBe(false);
    expect(mockedRedeemActivationCode).not.toHaveBeenCalled();
  });

  it("keeps login session when redeem fails after login", async () => {
    const errorSpy = vi.spyOn(message, "error").mockImplementation(() => undefined as any);
    mockedLoginWithEmailCode.mockResolvedValueOnce({
      token: "tok-fail",
      user: { id: 2, email: "fail@example.com", status: "active" },
      license: { status: "none", active: false, license_type: "", start_at: "", expire_at: "" },
      device_limit: 2,
    });
    mockedRedeemActivationCode.mockRejectedValueOnce(new Error("激活码已被使用或未生效。"));
    mockedGetAuthMe.mockResolvedValue({
      user: { id: 2, email: "fail@example.com", status: "active" },
      license: { status: "none", active: false, license_type: "", start_at: "", expire_at: "" },
      devices: [{ device_id: "device-001", device_name: "秒译出海-macOS", platform: "macOS" }],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    act(() => {
      result.current.setEmail("fail@example.com");
      result.current.setCode("123456");
      result.current.setActivationCode("FAIL-CODE-0001");
    });

    await act(async () => {
      await result.current.handleLogin();
    });

    expect(localStorage.getItem("ygf_auth_token")).toBe("tok-fail");
    expect(result.current.user?.email).toBe("fail@example.com");
    expect(result.current.license.status).toBe("none");
    expect(result.current.activationCode).toBe("");
    expect(result.current.showAuthGate).toBe(false);
    expect(result.current.taskCreationBlocked).toBe(true);
    expect(mockedLogoutAuth).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledWith("激活码已被使用或未生效。");
  });

  it("lets unlicensed users log in but blocks new tasks until they redeem", async () => {
    mockedLoginWithEmailCode.mockResolvedValueOnce({
      token: "tok-none",
      user: { id: 8, email: "new@example.com", status: "active" },
      license: { status: "none", active: false, license_type: "", start_at: "", expire_at: "" },
      device_limit: 2,
    });
    mockedGetAuthMe.mockResolvedValue({
      user: { id: 8, email: "new@example.com", status: "active" },
      license: { status: "none", active: false, license_type: "", start_at: "", expire_at: "" },
      devices: [{ device_id: "device-001", device_name: "秒译出海-macOS", platform: "macOS" }],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());
    await waitFor(() => expect(result.current.authLoading).toBe(false));

    act(() => {
      result.current.setEmail("new@example.com");
      result.current.setCode("123456");
      result.current.setActivationCode("");
    });

    await act(async () => {
      await result.current.handleLogin();
    });

    expect(result.current.user?.email).toBe("new@example.com");
    expect(result.current.license.status).toBe("none");
    expect(result.current.showAuthGate).toBe(false);
    expect(result.current.taskCreationBlocked).toBe(true);
    expect(result.current.canRenewInApp).toBe(true);
    expect(result.current.taskCreationBlockedReason).toContain("尚未开通授权");
  });

  it("redeems a renewal code in place for expired users", async () => {
    localStorage.setItem("ygf_auth_token", "tok-renew");
    mockedGetAuthMe.mockResolvedValueOnce({
      user: { id: 3, email: "renew@example.com", status: "active" },
      license: {
        status: "expired",
        active: false,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [{ device_id: "device-001", device_name: "Mac", platform: "macOS" }],
      device_limit: 2,
    });
    mockedRedeemActivationCode.mockResolvedValueOnce({
      ok: true,
      license: {
        status: "active",
        active: true,
        license_type: "renewal",
        start_at: "2026-05-02T00:00:00Z",
        expire_at: "2026-06-01T00:00:00Z",
      },
    });
    mockedGetCurrentLicense.mockResolvedValueOnce({
      license: {
        status: "active",
        active: true,
        license_type: "renewal",
        start_at: "2026-05-02T00:00:00Z",
        expire_at: "2026-06-01T00:00:00Z",
      },
      devices: [{ device_id: "device-001", device_name: "Mac", platform: "macOS" }],
      device_limit: 2,
    });
    mockedListAuthDevices.mockResolvedValueOnce({
      items: [{ device_id: "device-001", device_name: "Mac", platform: "macOS" }],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.license.status).toBe("expired"));

    act(() => {
      result.current.setActivationCode("RENEW-0001");
    });

    await act(async () => {
      const ok = await result.current.handleRedeemCode();
      expect(ok).toBe(true);
    });

    expect(result.current.license.status).toBe("active");
    expect(result.current.canRenewInApp).toBe(false);
    expect(result.current.taskCreationBlocked).toBe(false);
  });

  it("surfaces dev code hints when auth service returns them", async () => {
    mockedSendAuthEmailCode.mockResolvedValueOnce({ ttl_seconds: 300, dev_code: "654321" });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    act(() => {
      result.current.setEmail("dev@example.com");
    });

    await act(async () => {
      await result.current.handleSendCode();
    });

    expect(result.current.devCodeHint).toBe("654321");
    expect(result.current.code).toBe("654321");
    expect(localStorage.getItem("ygf_auth_email")).toBe("dev@example.com");
  });

  it("starts a 30 second cooldown after sending auth code", async () => {
    vi.useFakeTimers();
    mockedSendAuthEmailCode.mockResolvedValueOnce({ ttl_seconds: 300, dev_code: "" });

    const { result } = renderHook(() => useCloudAuthGate());

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      result.current.setEmail("cooldown@example.com");
    });

    await act(async () => {
      await result.current.handleSendCode();
    });

    expect(result.current.sendCodeCooldownSeconds).toBe(30);
    expect(mockedSendAuthEmailCode).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.handleSendCode();
    });
    expect(mockedSendAuthEmailCode).toHaveBeenCalledTimes(1);

    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
    }
    expect(result.current.sendCodeCooldownSeconds).toBe(25);

    for (let i = 0; i < 25; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
    }
    expect(result.current.sendCodeCooldownSeconds).toBe(0);
  });

  it("blocks login when auth code has expired locally", async () => {
    vi.useFakeTimers();
    const baseNow = Date.parse("2026-04-07T12:00:00Z");
    vi.setSystemTime(baseNow);
    mockedSendAuthEmailCode.mockResolvedValueOnce({ ttl_seconds: 300, dev_code: "" });

    const { result } = renderHook(() => useCloudAuthGate());

    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      result.current.setEmail("expire@example.com");
    });

    await act(async () => {
      await result.current.handleSendCode();
    });

    act(() => {
      result.current.setCode("123456");
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(301_000);
      await result.current.handleLogin();
    });

    expect(mockedLoginWithEmailCode).not.toHaveBeenCalled();
    expect(result.current.code).toBe("");
    expect(result.current.authError).toBe("验证码已过期，请重新发送。");
  });

  it("blocks task creation when license product does not match current installer", async () => {
    localStorage.setItem("ygf_auth_token", "tok-quality");
    mockedGetCurrentProductEdition.mockReturnValue("lite");
    mockedGetAuthMe.mockResolvedValueOnce({
      user: { id: 9, email: "quality@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        product_edition: "quality",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    expect(result.current.taskCreationBlocked).toBe(true);
    expect(result.current.canRenewInApp).toBe(true);
    expect(result.current.authStatusText).toContain("不匹配");
    expect(result.current.taskCreationBlockedReason).toContain("质量版");
  });

  it("shows frozen accounts as readonly and blocks in-app renewal", async () => {
    localStorage.setItem("ygf_auth_token", "tok-frozen");
    mockedGetAuthMe.mockResolvedValueOnce({
      user: { id: 10, email: "frozen@example.com", status: "active" },
      license: {
        status: "frozen",
        active: false,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [],
      device_limit: 2,
    });

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    expect(result.current.isReadonlyMode).toBe(true);
    expect(result.current.canRenewInApp).toBe(false);
    expect(result.current.taskCreationBlocked).toBe(true);
    expect(result.current.authStatusText).toContain("冻结");
    expect(result.current.taskCreationBlockedReason).toContain("请联系管理员");
  });

  it("marks an already-past active license as expired on refresh", async () => {
    vi.useFakeTimers();
    localStorage.setItem("ygf_auth_token", "tok-active-expired");
    mockedGetAuthMe.mockResolvedValue({
      user: { id: 12, email: "late@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-04-07T00:00:00Z",
      },
      devices: [],
      device_limit: 2,
    });

    const now = Date.parse("2026-04-07T12:00:00Z");
    vi.setSystemTime(now);

    const { result } = renderHook(() => useCloudAuthGate());

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current.license.status).toBe("expired");
    expect(result.current.taskCreationBlocked).toBe(true);
    expect(result.current.authStatusText).toContain("到期");
  });

  it("refreshes auth status after license expiry timeout", async () => {
    vi.useFakeTimers();
    const baseNow = Date.parse("2026-04-07T12:00:00Z");
    vi.setSystemTime(baseNow);
    localStorage.setItem("ygf_auth_token", "tok-timeout-expire");
    mockedGetAuthMe
      .mockResolvedValueOnce({
        user: { id: 13, email: "timer@example.com", status: "active" },
        license: {
          status: "active",
          active: true,
          license_type: "monthly",
          start_at: "2026-04-01T00:00:00Z",
          expire_at: new Date(baseNow + 2_000).toISOString(),
        },
        devices: [],
        device_limit: 2,
      })
      .mockResolvedValue({
        user: { id: 13, email: "timer@example.com", status: "active" },
        license: {
          status: "expired",
          active: false,
          license_type: "monthly",
          start_at: "2026-04-01T00:00:00Z",
          expire_at: new Date(baseNow + 2_000).toISOString(),
        },
        devices: [],
        device_limit: 2,
      });

    const { result } = renderHook(() => useCloudAuthGate());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.license.status).toBe("active");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_500);
    });

    expect(result.current.license.status).toBe("expired");
    expect(mockedGetAuthMe).toHaveBeenCalledTimes(2);
    expect(result.current.taskCreationBlocked).toBe(true);
  });

  it("refreshes auth status when window regains focus", async () => {
    const now = Date.parse("2026-04-07T12:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(now);
    localStorage.setItem("ygf_auth_token", "tok-focus");
    mockedGetAuthMe
      .mockResolvedValueOnce({
        user: { id: 14, email: "focus@example.com", status: "active" },
        license: {
          status: "active",
          active: true,
          license_type: "monthly",
          start_at: "2026-04-01T00:00:00Z",
          expire_at: "2026-05-01T00:00:00Z",
        },
        devices: [],
        device_limit: 2,
      })
      .mockResolvedValueOnce({
        user: { id: 14, email: "focus@example.com", status: "active" },
        license: {
          status: "expired",
          active: false,
          license_type: "monthly",
          start_at: "2026-04-01T00:00:00Z",
          expire_at: "2026-05-01T00:00:00Z",
        },
        devices: [],
        device_limit: 2,
      });

    const { result } = renderHook(() => useCloudAuthGate());

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.license.status).toBe("active");

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });

    expect(result.current.license.status).toBe("expired");
    expect(mockedGetAuthMe.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("clears local session state after explicit logout", async () => {
    localStorage.setItem("ygf_auth_token", "tok-logout");
    localStorage.setItem("ygf_auth_email", "logout@example.com");
    mockedGetAuthMe.mockResolvedValueOnce({
      user: { id: 11, email: "logout@example.com", status: "active" },
      license: {
        status: "active",
        active: true,
        license_type: "monthly",
        start_at: "2026-04-01T00:00:00Z",
        expire_at: "2026-05-01T00:00:00Z",
      },
      devices: [{ device_id: "device-001", device_name: "秒译出海-macOS", platform: "macOS" }],
      device_limit: 2,
    });
    mockedLogoutAuth.mockResolvedValueOnce(undefined);

    const { result } = renderHook(() => useCloudAuthGate());

    await waitFor(() => expect(result.current.authLoading).toBe(false));

    await act(async () => {
      await result.current.handleLogout();
    });

    expect(mockedLogoutAuth).toHaveBeenCalledWith("tok-logout");
    expect(localStorage.getItem("ygf_auth_token")).toBeNull();
    expect(result.current.user).toBeNull();
    expect(result.current.showAuthGate).toBe(true);
    expect(result.current.taskCreationBlocked).toBe(false);
  });
});
