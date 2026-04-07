import axios from "axios";

export type AuthUser = {
  id: number;
  email: string;
  status: string;
  created_at?: string;
  last_login_at?: string;
};

export type AuthLicense = {
  status: string;
  active: boolean;
  license_type: string;
  product_edition?: string;
  start_at?: string;
  expire_at?: string;
};

export type AuthDevice = {
  id?: number;
  device_id: string;
  device_name?: string;
  platform?: string;
  active?: boolean;
  last_seen_at?: string;
  created_at?: string;
};

type AuthEnvelope = {
  user: AuthUser;
  license: AuthLicense;
  devices?: AuthDevice[];
  device_limit?: number;
};

export type AuthAdminUser = {
  id: number;
  email: string;
  status: string;
  created_at?: string;
  last_login_at?: string;
  license_type?: string;
  product_edition?: string;
  license_status?: string;
  expire_at?: string;
  active_device_count?: number;
};

export type AuthActivationCodeRecord = {
  id?: number;
  code: string;
  type: string;
  duration_days: number;
  status: string;
  product_edition?: string;
  used_by_user_id?: number | null;
  used_at?: string;
  created_at?: string;
};

export type AuthAdminDevice = {
  id?: number;
  user_id?: number;
  email: string;
  device_id: string;
  device_name?: string;
  platform?: string;
  active?: boolean;
  last_seen_at?: string;
  created_at?: string;
  license_status?: string;
  license_type?: string;
  product_edition?: string;
  expire_at?: string;
};

const DEFAULT_AUTH_API_BASE = "https://auth.miaoyichuhai.com";
const DEFAULT_COMPAT_AUTH_API_BASE = "https://8.149.245.13";
const LEGACY_COMPAT_AUTH_API_BASE = "http://auth.miaoyichuhai.com";
const AUTH_API_BASE_STORAGE_KEY = "ygf_auth_api_base";
const AUTH_ROUTE_STATE_STORAGE_KEY = "ygf_auth_route_state";
const AUTH_ROUTE_STATE_VERSION = 1;
const DEFAULT_AUTH_IP_FALLBACK_CACHE_MS = 5 * 60 * 1000;

type AuthRouteMode = "primary" | "compat";

type AuthRouteState = {
  version: number;
  mode: AuthRouteMode;
  baseURL: string;
  expiresAt: number;
  updatedAt: number;
  lastErrorCode?: string;
  lastErrorMessage?: string;
};

function readLocalStorage(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function removeLocalStorage(key: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore storage failures
  }
}

function writeLocalStorage(key: string, value: string) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore storage failures
  }
}

function parseBooleanEnv(value: unknown, fallback: boolean): boolean {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!normalized) return fallback;
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return fallback;
}

function parsePositiveIntEnv(value: unknown, fallback: number): number {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

const envAuthApiBase = String((import.meta as any)?.env?.VITE_AUTH_API_BASE || "").trim();
const compatAuthApiBase = String((import.meta as any)?.env?.VITE_AUTH_COMPAT_API_BASE || DEFAULT_COMPAT_AUTH_API_BASE).trim();
const authIpFallbackEnabled = parseBooleanEnv((import.meta as any)?.env?.VITE_AUTH_IP_FALLBACK_ENABLED, true);
const authIpFallbackCacheMs = parsePositiveIntEnv(
  (import.meta as any)?.env?.VITE_AUTH_IP_FALLBACK_CACHE_MS,
  DEFAULT_AUTH_IP_FALLBACK_CACHE_MS
);

const rawStoredAuthApiBase = readLocalStorage(AUTH_API_BASE_STORAGE_KEY);
const storedAuthApiBase = rawStoredAuthApiBase === LEGACY_COMPAT_AUTH_API_BASE ? compatAuthApiBase : rawStoredAuthApiBase;
const explicitStoredAuthApiBase =
  storedAuthApiBase && storedAuthApiBase !== compatAuthApiBase ? storedAuthApiBase : "";

function readStoredRouteState(now = Date.now()): AuthRouteState | null {
  const raw = readLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AuthRouteState>;
    if (Number(parsed?.version) !== AUTH_ROUTE_STATE_VERSION) {
      removeLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY);
      return null;
    }
    const mode = parsed?.mode === "compat" ? "compat" : "primary";
    const baseURL = String(parsed?.baseURL || "").trim();
    const expiresAt = Number(parsed?.expiresAt || 0);
    const updatedAt = Number(parsed?.updatedAt || 0);
    if (!baseURL || expiresAt <= now) {
      removeLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY);
      return null;
    }
    return {
      version: AUTH_ROUTE_STATE_VERSION,
      mode,
      baseURL,
      expiresAt,
      updatedAt,
      lastErrorCode: parsed?.lastErrorCode ? String(parsed.lastErrorCode) : undefined,
      lastErrorMessage: parsed?.lastErrorMessage ? String(parsed.lastErrorMessage) : undefined,
    };
  } catch {
    removeLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY);
    return null;
  }
}

function writeStoredRouteState(state: AuthRouteState) {
  writeLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY, JSON.stringify(state));
}

function clearStoredRouteState() {
  removeLocalStorage(AUTH_ROUTE_STATE_STORAGE_KEY);
}

if (!envAuthApiBase && storedAuthApiBase === compatAuthApiBase) {
  writeStoredRouteState({
    version: AUTH_ROUTE_STATE_VERSION,
    mode: "compat",
    baseURL: compatAuthApiBase,
    expiresAt: Date.now() + authIpFallbackCacheMs,
    updatedAt: Date.now(),
  });
  removeLocalStorage(AUTH_API_BASE_STORAGE_KEY);
}

const storedRouteState = readStoredRouteState();
const explicitAuthApiBaseOverride = envAuthApiBase || explicitStoredAuthApiBase;

function pickInitialAuthApiBase(): string {
  if (explicitAuthApiBaseOverride) return explicitAuthApiBaseOverride;
  if (authIpFallbackEnabled && storedRouteState?.mode === "compat" && storedRouteState.baseURL) {
    return storedRouteState.baseURL;
  }
  return DEFAULT_AUTH_API_BASE;
}

export const authApiBase =
  pickInitialAuthApiBase();

let runtimeAuthApiBase = authApiBase;

const authClient = axios.create({
  baseURL: runtimeAuthApiBase,
  timeout: 15000,
});

function setRuntimeAuthApiBase(nextBase: string) {
  const normalized = String(nextBase || "").trim();
  if (!normalized || normalized === runtimeAuthApiBase) return;
  runtimeAuthApiBase = normalized;
  authClient.defaults.baseURL = normalized;
}

function refreshRuntimeAuthApiBase(now = Date.now()) {
  if (explicitAuthApiBaseOverride) {
    setRuntimeAuthApiBase(explicitAuthApiBaseOverride);
    return;
  }
  const cachedState = readStoredRouteState(now);
  if (authIpFallbackEnabled && cachedState?.mode === "compat" && cachedState.baseURL) {
    setRuntimeAuthApiBase(cachedState.baseURL);
    return;
  }
  setRuntimeAuthApiBase(DEFAULT_AUTH_API_BASE);
}

export function getAuthApiBase(): string {
  refreshRuntimeAuthApiBase();
  return runtimeAuthApiBase;
}

export function getAuthRouteState(): {
  currentBaseURL: string;
  fallbackEnabled: boolean;
  cacheMs: number;
  mode: AuthRouteMode;
  expiresAt?: number;
} {
  const cachedState = readStoredRouteState();
  return {
    currentBaseURL: getAuthApiBase(),
    fallbackEnabled: authIpFallbackEnabled && !explicitAuthApiBaseOverride,
    cacheMs: authIpFallbackCacheMs,
    mode: cachedState?.mode === "compat" ? "compat" : "primary",
    expiresAt: cachedState?.expiresAt,
  };
}

function hasBridgeAuthRequest(): boolean {
  return typeof window !== "undefined" && typeof (window as any)?.bridge?.authRequest === "function";
}

async function bridgeAuthRequest<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  const response = await (window as any).bridge.authRequest({
    method,
    path,
    body,
    headers,
  });
  if (response?.ok) {
    return (response?.data ?? {}) as T;
  }
  throw { response: { status: Number(response?.status || 0), data: response?.data ?? response?.text ?? "" } };
}

async function sendAuthRequest<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  if (hasBridgeAuthRequest()) {
    return await bridgeAuthRequest<T>(method, path, body, headers);
  }
  refreshRuntimeAuthApiBase();
  const { data } = await withCompatFallback(() =>
    authClient.request<T>({
      url: path,
      method,
      data: body,
      headers,
    })
  );
  return data;
}

function isCompatFallbackCandidate(err: any): boolean {
  if (!authIpFallbackEnabled || !compatAuthApiBase) return false;
  if (runtimeAuthApiBase !== DEFAULT_AUTH_API_BASE) return false;
  if (explicitAuthApiBaseOverride) return false;
  if (err?.response) return false;
  const text = `${String(err?.code || "")} ${String(err?.message || "")}`.toLowerCase();
  return (
    text.includes("network error") ||
    text.includes("econnreset") ||
    text.includes("err_connection_closed") ||
    text.includes("socket disconnected before secure tls connection was established")
  );
}

async function withCompatFallback<T>(request: () => Promise<T>): Promise<T> {
  try {
    const result = await request();
    if (runtimeAuthApiBase === DEFAULT_AUTH_API_BASE) {
      clearStoredRouteState();
    }
    return result;
  } catch (err: any) {
    if (!isCompatFallbackCandidate(err)) throw err;
    const nextState: AuthRouteState = {
      version: AUTH_ROUTE_STATE_VERSION,
      mode: "compat",
      baseURL: compatAuthApiBase,
      expiresAt: Date.now() + authIpFallbackCacheMs,
      updatedAt: Date.now(),
      lastErrorCode: err?.code ? String(err.code) : undefined,
      lastErrorMessage: err?.message ? String(err.message) : undefined,
    };
    writeStoredRouteState(nextState);
    setRuntimeAuthApiBase(compatAuthApiBase);
    const result = await request();
    return result;
  }
}

export function getCurrentProductEdition(): "lite" | "quality" {
  try {
    const edition = String((window as any)?.bridge?.runtimeInfo?.productEdition || "").trim().toLowerCase();
    return edition === "quality" ? "quality" : "lite";
  } catch {
    return "lite";
  }
}

function extractApiError(err: any): string {
  const status = err?.response?.status;
  const data = err?.response?.data;
  let rawMessage = "";
  if (typeof data === "string" && data.trim()) {
    rawMessage = data.trim();
  } else if (data && typeof data === "object") {
    if (typeof data.error === "string" && data.error) rawMessage = data.error;
    else if (typeof data.message === "string" && data.message) rawMessage = data.message;
  }
  const normalized = String(rawMessage || "").trim().toLowerCase();
  if (normalized === "invalid email") return "请输入正确的邮箱地址。";
  if (normalized === "code is required") return "请输入验证码。";
  if (normalized === "invalid code") return "验证码错误或已失效，请重新获取。";
  if (normalized === "too many requests") return "请求过于频繁，请稍后再试。";
  if (normalized.startsWith("send mail failed:")) return "验证码发送失败，请稍后重试。";
  if (normalized === "activation code is required") return "请输入激活码。";
  if (normalized === "activation code not found") return "激活码不存在，请检查后重试。";
  if (normalized === "activation code already used or inactive") return "激活码已被使用或未生效。";
  if (normalized === "product edition is required") return "当前安装包版型信息缺失，请重新启动后再试。";
  if (normalized === "device limit reached") return "当前账号已达到设备数量上限，请先在后台解绑旧设备。";
  if (normalized.includes("activation code is only valid for") && normalized.includes("quality")) {
    return "该激活码仅适用于质量版，请更换对应安装包或激活码。";
  }
  if (normalized.includes("activation code is only valid for") && normalized.includes("lite")) {
    return "该激活码仅适用于轻量版，请更换对应安装包或激活码。";
  }
  if (normalized === "current license is still active; only same-edition renewal is allowed before expiry") {
    return "当前授权仍在有效期内，仅支持使用同版型激活码续期。";
  }
  if (status === 401) return "登录态已失效，请重新登录。";
  if (status === 403) return "当前请求未授权。";
  if (status === 404) return "认证服务不可用，请检查服务地址。";
  if (rawMessage) return rawMessage;
  return err?.message || "请求失败";
}

function authHeaders(token?: string) {
  return token ? { Authorization: `Bearer ${token}` } : undefined;
}

function adminHeaders(secret: string) {
  return { "X-Admin-Secret": String(secret || "").trim() };
}

export async function sendAuthEmailCode(email: string): Promise<{ ttl_seconds: number; dev_code?: string }> {
  try {
    const data = await sendAuthRequest<any>("POST", "/api/auth/email/send-code", { email });
    return {
      ttl_seconds: Number(data?.ttl_seconds || 0),
      dev_code: data?.dev_code ? String(data.dev_code) : undefined,
    };
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function loginWithEmailCode(payload: {
  email: string;
  code: string;
  device_id: string;
  device_aliases?: string[];
  device_name: string;
  platform: string;
  product_edition?: string;
}): Promise<{ token: string; user: AuthUser; license: AuthLicense; device_limit?: number }> {
  try {
    const data = await sendAuthRequest<any>("POST", "/api/auth/email/login", payload);
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getAuthMe(token: string): Promise<AuthEnvelope> {
  try {
    const data = await sendAuthRequest<AuthEnvelope>("GET", "/api/auth/me", undefined, authHeaders(token));
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function logoutAuth(token: string): Promise<void> {
  try {
    await sendAuthRequest("POST", "/api/auth/logout", {}, authHeaders(token));
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function redeemActivationCode(
  token: string,
  code: string,
  product_edition: string
): Promise<{ ok: boolean; license: AuthLicense }> {
  try {
    const data = await sendAuthRequest<any>("POST", "/api/license/redeem", { code, product_edition }, authHeaders(token));
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getCurrentLicense(token: string): Promise<{ license: AuthLicense; devices: AuthDevice[]; device_limit?: number }> {
  try {
    const data = await sendAuthRequest<any>("GET", "/api/license/current", undefined, authHeaders(token));
    return {
      license: data?.license,
      devices: Array.isArray(data?.devices) ? data.devices : [],
      device_limit: data?.device_limit,
    };
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function listAuthDevices(token: string): Promise<{ items: AuthDevice[]; device_limit?: number }> {
  try {
    const data = await sendAuthRequest<any>("GET", "/api/license/devices", undefined, authHeaders(token));
    return {
      items: Array.isArray(data?.items) ? data.items : [],
      device_limit: data?.device_limit,
    };
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function listAdminUsers(secret: string): Promise<AuthAdminUser[]> {
  try {
    const data = await sendAuthRequest<any>("GET", "/api/admin/users", undefined, adminHeaders(secret));
    return Array.isArray(data?.items) ? data.items : [];
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function listAdminActivationCodes(secret: string): Promise<AuthActivationCodeRecord[]> {
  try {
    const data = await sendAuthRequest<any>("GET", "/api/admin/activation-codes", undefined, adminHeaders(secret));
    return Array.isArray(data?.items) ? data.items : [];
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function listAdminDevices(secret: string): Promise<AuthAdminDevice[]> {
  try {
    const data = await sendAuthRequest<any>("GET", "/api/admin/devices", undefined, adminHeaders(secret));
    return Array.isArray(data?.items) ? data.items : [];
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function createAdminActivationCodes(
  secret: string,
  payload: { count: number; duration_days: number; type: string; product_edition: string }
): Promise<AuthActivationCodeRecord[]> {
  try {
    const data = await sendAuthRequest<any>("POST", "/api/admin/activation-codes", payload, adminHeaders(secret));
    return Array.isArray(data?.items) ? data.items : [];
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function freezeAdminLicense(secret: string, email: string, freeze = true): Promise<void> {
  try {
    await sendAuthRequest("POST", "/api/admin/licenses/freeze", { email, freeze }, adminHeaders(secret));
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function extendAdminLicense(secret: string, email: string, days: number): Promise<{ expire_at?: string }> {
  try {
    const data = await sendAuthRequest<any>("POST", "/api/admin/licenses/extend", { email, days }, adminHeaders(secret));
    return data || {};
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function unbindAdminDevice(secret: string, email: string, device_id: string): Promise<void> {
  try {
    await sendAuthRequest("POST", "/api/admin/devices/unbind", { email, device_id }, adminHeaders(secret));
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}
