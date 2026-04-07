import { useCallback, useEffect, useMemo, useState } from "react";
import { message } from "antd";
import {
  authApiBase,
  getCurrentProductEdition,
  getAuthMe,
  getCurrentLicense,
  listAuthDevices,
  loginWithEmailCode,
  logoutAuth,
  redeemActivationCode,
  sendAuthEmailCode,
  type AuthDevice,
  type AuthLicense,
  type AuthUser,
} from "../authApi";

const AUTH_TOKEN_STORAGE_KEY = "ygf_auth_token";
const AUTH_EMAIL_STORAGE_KEY = "ygf_auth_email";

function defaultLicense(): AuthLicense {
  return {
    status: "none",
    active: false,
    license_type: "",
    start_at: "",
    expire_at: "",
  };
}

function normalizeLicenseByExpiry(raw: AuthLicense | null | undefined, now = Date.now()): AuthLicense {
  const license = raw ? { ...raw } : defaultLicense();
  const status = String(license.status || "").trim().toLowerCase();
  const expireAtMs = Date.parse(String(license.expire_at || ""));
  if (status === "active" && Number.isFinite(expireAtMs) && expireAtMs <= now) {
    return {
      ...license,
      status: "expired",
      active: false,
    };
  }
  return license;
}

function shouldClearSessionForAuthError(err: unknown): boolean {
  const message = String((err as any)?.message || err || "").trim();
  return message === "登录态已失效，请重新登录。" || message === "当前请求未授权。";
}

export function useCloudAuthGate() {
  const enabled = true;
  const sendCodeCooldownSecondsDefault = 30;
  const codeExpireDefaultMs = 5 * 60 * 1000;
  const clientProductEdition = getCurrentProductEdition();
  const [token, setToken] = useState(() => localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || "");
  const [email, setEmail] = useState(() => localStorage.getItem(AUTH_EMAIL_STORAGE_KEY) || "");
  const [code, setCode] = useState("");
  const [activationCode, setActivationCode] = useState("");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [license, setLicense] = useState<AuthLicense>(defaultLicense);
  const [devices, setDevices] = useState<AuthDevice[]>([]);
  const [deviceLimit, setDeviceLimit] = useState(2);
  const [deviceCode, setDeviceCode] = useState("UNKNOWN");
  const [deviceAliases, setDeviceAliases] = useState<string[]>([]);
  const [deviceName, setDeviceName] = useState("");
  const [platform, setPlatform] = useState("");
  const [authLoading, setAuthLoading] = useState(true);
  const [sendingCode, setSendingCode] = useState(false);
  const [sendCodeCooldownSeconds, setSendCodeCooldownSeconds] = useState(0);
  const [authCodeExpireAtMs, setAuthCodeExpireAtMs] = useState(0);
  const [loggingIn, setLoggingIn] = useState(false);
  const [redeemingCode, setRedeemingCode] = useState(false);
  const [authError, setAuthError] = useState("");
  const [devCodeHint, setDevCodeHint] = useState("");
  const [licenseInfoLoading, setLicenseInfoLoading] = useState(false);

  useEffect(() => {
    let active = true;
    const loadDeviceMeta = async () => {
      try {
        const info = (window as any)?.bridge?.runtimeInfo;
        const runtimePlatform = String(info?.platform || navigator.platform || "desktop");
        const friendlyName = `秒译出海-${runtimePlatform}`;
        if (active) {
          setPlatform(runtimePlatform);
          setDeviceName(friendlyName);
        }
        if ((window as any)?.bridge?.getDeviceIdentity) {
          const identity = await (window as any).bridge.getDeviceIdentity();
          if (active && identity?.deviceCode) setDeviceCode(String(identity.deviceCode));
          if (active && Array.isArray(identity?.aliases)) {
            setDeviceAliases(identity.aliases.map((item: unknown) => String(item || "").trim()).filter(Boolean));
          }
        } else if ((window as any)?.bridge?.getDeviceCode) {
          const code = await (window as any).bridge.getDeviceCode();
          if (active && code) setDeviceCode(String(code));
        }
      } catch {
        if (active) {
          setDeviceCode("UNKNOWN");
          setDeviceAliases([]);
        }
      }
    };
    loadDeviceMeta();
    return () => {
      active = false;
    };
  }, []);

  const refreshMe = useCallback(
    async (nextToken?: string) => {
      const currentToken = String(nextToken || token || "").trim();
      if (!currentToken) {
        setUser(null);
        setLicense(defaultLicense());
        setDevices([]);
        setAuthLoading(false);
        return;
      }
      setLicenseInfoLoading(true);
      try {
        const me = await getAuthMe(currentToken);
        setUser(me.user || null);
        setLicense(normalizeLicenseByExpiry(me.license || defaultLicense()));
        setDevices(Array.isArray(me.devices) ? me.devices : []);
        setDeviceLimit(Number(me.device_limit || 2));
        setAuthError("");
      } catch (err: any) {
        if (shouldClearSessionForAuthError(err)) {
          localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
          setToken("");
          setUser(null);
          setLicense(defaultLicense());
          setDevices([]);
        }
        setAuthError(String(err?.message || err || "登录态校验失败"));
      } finally {
        setAuthLoading(false);
        setLicenseInfoLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  useEffect(() => {
    if (sendCodeCooldownSeconds <= 0) return undefined;
    const timer = window.setTimeout(() => {
      setSendCodeCooldownSeconds((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [sendCodeCooldownSeconds]);

  useEffect(() => {
    if (!token) return undefined;
    const expireAtMs = Date.parse(String(license.expire_at || ""));
    if (!Number.isFinite(expireAtMs)) return undefined;
    const delay = expireAtMs - Date.now();
    if (delay <= 0) {
      setLicense((current) => normalizeLicenseByExpiry(current));
      void refreshMe();
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setLicense((current) => normalizeLicenseByExpiry(current));
      void refreshMe();
    }, Math.min(delay + 1000, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [license.expire_at, refreshMe, token]);

  useEffect(() => {
    if (!token) return undefined;
    const handleForegroundRefresh = () => {
      if (document.visibilityState === "hidden") return;
      setLicense((current) => normalizeLicenseByExpiry(current));
      void refreshMe();
    };
    window.addEventListener("focus", handleForegroundRefresh);
    document.addEventListener("visibilitychange", handleForegroundRefresh);
    return () => {
      window.removeEventListener("focus", handleForegroundRefresh);
      document.removeEventListener("visibilitychange", handleForegroundRefresh);
    };
  }, [refreshMe, token]);

  const handleSendCode = useCallback(async () => {
    if (sendingCode || sendCodeCooldownSeconds > 0) {
      return;
    }
    const normalized = String(email || "").trim().toLowerCase();
    if (!normalized) {
      setAuthError("请输入邮箱地址。");
      return;
    }
    setSendingCode(true);
    setAuthError("");
    setDevCodeHint("");
    try {
      const res = await sendAuthEmailCode(normalized);
      localStorage.setItem(AUTH_EMAIL_STORAGE_KEY, normalized);
      setEmail(normalized);
      if (res.dev_code) {
        setDevCodeHint(res.dev_code);
        setCode((prev) => prev || String(res.dev_code));
      }
      setSendCodeCooldownSeconds(sendCodeCooldownSecondsDefault);
      setAuthCodeExpireAtMs(Date.now() + Math.max(Number(res?.ttl_seconds || 0) * 1000, codeExpireDefaultMs));
      message.success("验证码已发送，请检查邮箱后继续。");
    } catch (err: any) {
      setAuthError(String(err?.message || err || "发送验证码失败"));
    } finally {
      setSendingCode(false);
    }
  }, [email, sendCodeCooldownSeconds, sendingCode]);

  const handleLogin = useCallback(async () => {
    const normalized = String(email || "").trim().toLowerCase();
    if (!normalized) {
      setAuthError("请输入邮箱地址。");
      return;
    }
    if (!code.trim()) {
      setAuthError("请输入验证码。");
      return;
    }
    if (authCodeExpireAtMs > 0 && Date.now() > authCodeExpireAtMs) {
      setCode("");
      setAuthError("验证码已过期，请重新发送。");
      return;
    }
    setLoggingIn(true);
    setAuthError("");
    let sessionToken = "";
    const activationInput = activationCode.trim();
    try {
      const res = await loginWithEmailCode({
        email: normalized,
        code: code.trim(),
        device_id: deviceCode || "UNKNOWN",
        device_aliases: deviceAliases,
        device_name: deviceName || "秒译出海桌面端",
        platform: platform || "desktop",
        product_edition: clientProductEdition,
      });
      sessionToken = res.token;
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, sessionToken);
      localStorage.setItem(AUTH_EMAIL_STORAGE_KEY, normalized);
      setToken(sessionToken);
      setUser(res.user || null);
      let nextLicense = res.license || defaultLicense();
      if (activationInput) {
        const redeemed = await redeemActivationCode(sessionToken, activationInput, clientProductEdition);
        nextLicense = redeemed.license || defaultLicense();
      }
      setLicense(nextLicense);
      setDeviceLimit(Number(res.device_limit || 2));
      setCode("");
      setActivationCode("");
      setAuthCodeExpireAtMs(0);
      await refreshMe(sessionToken);
      message.success(
        activationInput
          ? "登录成功，当前设备已激活。"
          : nextLicense.active
            ? "登录成功。"
            : "登录成功。当前账号尚未开通授权，新建任务前请输入激活码。"
      );
    } catch (err: any) {
      const loginSucceeded = !!sessionToken;
      if (loginSucceeded && activationInput) {
        await refreshMe(sessionToken);
        setActivationCode("");
        setAuthError(String(err?.message || err || "激活失败"));
        message.error(String(err?.message || err || "激活失败"));
      } else {
        if (activationInput) {
          localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
          setToken("");
          setUser(null);
          setLicense(defaultLicense());
        }
        setAuthError(String(err?.message || err || "登录失败"));
      }
    } finally {
      setLoggingIn(false);
    }
  }, [activationCode, authCodeExpireAtMs, clientProductEdition, code, deviceAliases, deviceCode, deviceName, email, platform, refreshMe]);

  const handleRedeemCode = useCallback(async () => {
    if (!token) {
      setAuthError("请先登录。");
      return false;
    }
    if (!activationCode.trim()) {
      setAuthError("请输入激活码。");
      return false;
    }
    setRedeemingCode(true);
    setAuthError("");
    try {
      const res = await redeemActivationCode(token, activationCode.trim(), clientProductEdition);
      setLicense(normalizeLicenseByExpiry(res.license || defaultLicense()));
      setActivationCode("");
      const current = await getCurrentLicense(token);
      const currentDevices = await listAuthDevices(token);
      setLicense(normalizeLicenseByExpiry(current.license || defaultLicense()));
      setDevices(currentDevices.items || []);
      setDeviceLimit(Number(current.device_limit || currentDevices.device_limit || 2));
      message.success("激活成功，当前设备已获得使用权限。");
      return true;
    } catch (err: any) {
      setAuthError(String(err?.message || err || "激活失败"));
      return false;
    } finally {
      setRedeemingCode(false);
    }
  }, [activationCode, clientProductEdition, token]);

  const handleLogout = useCallback(async () => {
    try {
      if (token) await logoutAuth(token);
    } catch {
      // ignore network logout errors
    } finally {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
      setToken("");
      setUser(null);
      setLicense(defaultLicense());
      setDevices([]);
      setCode("");
      setActivationCode("");
      setAuthError("");
    }
  }, [token]);

  const authStage = useMemo<"login" | "activate" | "ready">(() => {
    if (!user) return "login";
    if (!license.active && !["expired", "frozen"].includes(String(license.status || ""))) return "activate";
    return "ready";
  }, [license.active, license.status, user]);

  const isReadonlyMode = useMemo(() => {
    return !!user && ["expired", "frozen"].includes(String(license.status || ""));
  }, [license.status, user]);

  const isProductEditionMismatch = useMemo(() => {
    const boundEdition = String(license.product_edition || "").trim().toLowerCase();
    if (!user || !license.active || !boundEdition || boundEdition === "universal") return false;
    return boundEdition !== clientProductEdition;
  }, [clientProductEdition, license.active, license.product_edition, user]);

  const authStatusText = useMemo(() => {
    if (!user) return "请先完成邮箱登录";
    if (license.status === "none") return "当前账号尚未开通授权";
    if (license.status === "expired") return "授权已到期";
    if (license.status === "frozen") return "授权已冻结";
    if (isProductEditionMismatch) return "当前安装包与授权版本不匹配";
    if (license.active) return "授权有效";
    return "等待激活";
  }, [isProductEditionMismatch, license.active, license.status, user]);

  const taskCreationBlockedReason = useMemo(() => {
    if (user && license.status === "none") {
      return "当前账号尚未开通授权，可查看历史和账号信息，但不能新建或继续处理任务。请输入适用于当前安装包的激活码后开始使用。";
    }
    if (license.status === "expired") return "当前授权已到期，可查看历史与账号信息，但不能新建或继续处理任务。请输入新的激活码后恢复使用。";
    if (license.status === "frozen") return "当前授权已被冻结，可查看账号信息，但不能新建或继续处理任务。请联系管理员处理。";
    if (isProductEditionMismatch) {
      const boundEdition = license.product_edition === "quality" ? "质量版" : "轻量版";
      const currentEdition = clientProductEdition === "quality" ? "质量版" : "轻量版";
      return `当前账号授权属于${boundEdition}，不能在当前${currentEdition}安装包中继续处理任务。请使用对应安装包，或输入适用于当前安装包的新激活码。`;
    }
    return "";
  }, [clientProductEdition, isProductEditionMismatch, license.product_edition, license.status, user]);

  const canRenewInApp = useMemo(() => {
    return !!user && (license.status === "none" || license.status === "expired" || isProductEditionMismatch);
  }, [isProductEditionMismatch, license.status, user]);

  return {
    enabled,
    authApiBase,
    authLoading,
    authStage,
    showAuthGate: enabled && !user,
    user,
    license,
    isReadonlyMode,
    canRenewInApp,
    taskCreationBlocked: !!taskCreationBlockedReason,
    taskCreationBlockedReason,
    devices,
    deviceLimit,
    email,
    setEmail,
    code,
    setCode,
    activationCode,
    setActivationCode,
    deviceCode,
    deviceName,
    platform,
    authError,
    setAuthError,
    devCodeHint,
    sendingCode,
    sendCodeCooldownSeconds,
    loggingIn,
    redeemingCode,
    licenseInfoLoading,
    authStatusText,
    clientProductEdition,
    isProductEditionMismatch,
    handleSendCode,
    handleLogin,
    handleRedeemCode,
    handleLogout,
  };
}
