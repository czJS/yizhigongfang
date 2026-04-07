// Minimal Electron entry for Vite dev + packaged build
const { app, BrowserWindow, dialog, ipcMain, shell, protocol, Menu, session, net: electronNet } = require("electron");
const { spawn, spawnSync, execSync } = require("child_process");
const http = require("http");
const nodeNet = require("net");
const os = require("os");
const crypto = require("crypto");

function getMacAddresses() {
  try {
    const nets = os.networkInterfaces() || {};
    const macs = [];
    for (const name of Object.keys(nets)) {
      for (const info of nets[name] || []) {
        if (!info || info.internal) continue;
        const mac = String(info.mac || "").toUpperCase().replace(/:/g, "-");
        if (!mac || mac === "00-00-00-00-00-00") continue;
        if (!macs.includes(mac)) macs.push(mac);
      }
    }
    return macs;
  } catch {
    return [];
  }
}
function getDeviceIdentityStorePath() {
  try {
    return path.join(app.getPath("userData"), "device_identity.json");
  } catch {
    return "";
  }
}

function readPersistedDeviceIdentity() {
  try {
    const p = getDeviceIdentityStorePath();
    if (!p || !fssync.existsSync(p)) return null;
    const parsed = JSON.parse(fssync.readFileSync(p, "utf-8"));
    const deviceCode = String(parsed?.deviceCode || "").trim();
    if (!deviceCode) return null;
    return { deviceCode };
  } catch {
    return null;
  }
}

function writePersistedDeviceIdentity(identity) {
  try {
    const p = getDeviceIdentityStorePath();
    if (!p) return;
    fssync.mkdirSync(path.dirname(p), { recursive: true });
    fssync.writeFileSync(p, JSON.stringify({ version: 1, deviceCode: String(identity?.deviceCode || "") }, null, 2), "utf-8");
  } catch {
    // ignore persistence failures
  }
}

function buildStableDeviceCodeFromMacs(macs) {
  const list = Array.isArray(macs) ? [...macs].map((item) => String(item || "").trim()).filter(Boolean).sort() : [];
  if (list.length === 0) return "";
  const digest = crypto.createHash("sha256").update(list.join("|")).digest("hex").slice(0, 32).toUpperCase();
  return `DEV-${digest}`;
}

function getDeviceIdentity() {
  const macs = getMacAddresses();
  const persisted = readPersistedDeviceIdentity();
  let deviceCode = persisted?.deviceCode || "";
  const legacyCompatibleCode = String(macs[0] || "").trim();
  if (deviceCode && /^DEV-[A-F0-9]{16,}$/i.test(deviceCode) && legacyCompatibleCode) {
    deviceCode = legacyCompatibleCode;
    writePersistedDeviceIdentity({ deviceCode });
  }
  if (!deviceCode) {
    deviceCode = legacyCompatibleCode || buildStableDeviceCodeFromMacs(macs) || `DEV-${crypto.randomBytes(16).toString("hex").toUpperCase()}`;
    writePersistedDeviceIdentity({ deviceCode });
  }
  const aliases = [deviceCode, ...macs.map((item) => String(item || "").trim()).filter(Boolean)];
  return {
    deviceCode,
    aliases: Array.from(new Set(aliases)),
  };
}
const fs = require("fs/promises");
const fssync = require("fs");
const path = require("path");
const { normalizeOpenTargetPath, sanitizeDiagnosticsSummary, sanitizeDiagnosticsText } = require("./securityHelpers");
const {
  normalizeAuthRouteState,
  getPreferredAuthBase: pickPreferredAuthBase,
  isAuthFallbackCandidate: shouldFallbackAuthRequest,
  isTrustedAuthCompatUrl,
  buildFallbackRouteState,
} = require("./authHelpers");
const { chooseDevBackendPort } = require("./backendSelectionHelpers");

const e2eSmokeEnabled = String(process.env.YGF_E2E_SMOKE || "").trim() === "1";
const e2eSmokeFile = String(process.env.YGF_E2E_SMOKE_FILE || "").trim();

process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";

const isDev = !!process.env.VITE_DEV_SERVER_URL;
const AUTH_COMPAT_HOST = "8.149.245.13";
const AUTH_PRIMARY_BASE = String(process.env.YGF_AUTH_API_BASE || process.env.VITE_AUTH_API_BASE || "https://auth.miaoyichuhai.com").trim();
const AUTH_COMPAT_API_BASE = String(process.env.YGF_AUTH_COMPAT_API_BASE || process.env.VITE_AUTH_COMPAT_API_BASE || `https://${AUTH_COMPAT_HOST}`).trim();
const AUTH_ROUTE_STATE_VERSION = 1;
const AUTH_IP_FALLBACK_CACHE_MS = Number.parseInt(
  String(process.env.YGF_AUTH_IP_FALLBACK_CACHE_MS || process.env.VITE_AUTH_IP_FALLBACK_CACHE_MS || `${5 * 60 * 1000}`),
  10
);
const AUTH_IP_FALLBACK_ENABLED = (() => {
  const normalized = String(
    process.env.YGF_AUTH_IP_FALLBACK_ENABLED || process.env.VITE_AUTH_IP_FALLBACK_ENABLED || "1"
  )
    .trim()
    .toLowerCase();
  if (!normalized) return true;
  return !["0", "false", "off", "no"].includes(normalized);
})();
const AUTH_TRUSTED_COMPAT_HOSTS = (() => {
  const hosts = [];
  for (const value of [AUTH_COMPAT_HOST, AUTH_COMPAT_API_BASE]) {
    const raw = String(value || "").trim();
    if (!raw) continue;
    try {
      const parsed = raw.includes("://") ? new URL(raw) : new URL(`https://${raw}`);
      if (parsed.hostname && !hosts.includes(parsed.hostname)) hosts.push(parsed.hostname);
    } catch {}
  }
  return hosts;
})();
let authRouteStateCache = null;

// Prevent "Error: write EPIPE" from crashing the main process.
// This can happen on macOS when stdout/stderr is a closed pipe (e.g., launched from Finder or parent process detached).
function swallowEpipe(stream, name) {
  try {
    if (!stream || typeof stream.on !== "function") return;
    stream.on("error", (err) => {
      if (err && err.code === "EPIPE") return;
      try {
        logMain(`[fatal] ${name} error: ${err?.stack || err?.message || String(err)}`);
      } catch {}
      // Re-throw non-EPIPE errors to keep them visible during development.
      throw err;
    });
  } catch {
    // ignore
  }
}
swallowEpipe(process.stdout, "stdout");
swallowEpipe(process.stderr, "stderr");

// Optional local API token (used when backend enables YGF_API_TOKEN).
// We generate one per app launch to mitigate cross-app/webpage calls to the local backend.
if (!process.env.YGF_API_TOKEN) {
  try {
    process.env.YGF_API_TOKEN = crypto.randomBytes(16).toString("hex");
  } catch {
    process.env.YGF_API_TOKEN = `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  }
}

function pickUserDataBaseDir() {
  // Allow explicit override.
  const override = process.env.YGF_USER_DATA;
  if (override) return String(override);

  // Prefer install directory (same drive the user chose to install to).
  // This keeps large models/logs off C: when users install to another drive.
  if (process.platform === "win32") {
    try {
      const exeDir = path.dirname(app.getPath("exe"));
      if (exeDir && fssync.existsSync(exeDir)) {
        return path.join(exeDir, "user_data");
      }
    } catch {}
  }

  // Prefer D:\ on Windows when available (C: might be low on space).
  if (process.platform === "win32") {
    const d = "D:\\dubbing-gui";
    try {
      if (fssync.existsSync("D:\\")) return d;
    } catch {}
  }

  // Fallback: default Electron userData under roaming profile.
  return "";
}

function ensureUserDataPath() {
  try {
    // Keep userData consistent between dev and packaged builds.
    // Prefer non-C drive paths when possible (common in this project).
    const base = pickUserDataBaseDir();
    const target = base ? base : path.join(app.getPath("appData"), "dubbing-gui");
    // Ensure directory exists to avoid silent fallback.
    fssync.mkdirSync(target, { recursive: true });
    app.setPath("userData", target);
  } catch {
    // ignore
  }
}

// Must run before app is ready to take effect.
ensureUserDataPath();

function getMainLogPath() {
  try {
    const dir = path.join(app.getPath("userData"), "logs");
    fssync.mkdirSync(dir, { recursive: true });
    return path.join(dir, "main_process.log");
  } catch {
    try {
      return path.join(process.resourcesPath, "main_process.log");
    } catch {
      return "";
    }
  }
}

function getAuthRouteStatePath() {
  try {
    return path.join(app.getPath("userData"), "auth_route_state.json");
  } catch {
    return "";
  }
}

function readAuthRouteState(now = Date.now()) {
  const cached = normalizeAuthRouteState(authRouteStateCache, AUTH_ROUTE_STATE_VERSION, now);
  if (cached) {
    authRouteStateCache = cached;
    return cached;
  }
  authRouteStateCache = null;
  try {
    const p = getAuthRouteStatePath();
    if (!p || !fssync.existsSync(p)) return null;
    const parsed = JSON.parse(fssync.readFileSync(p, "utf-8"));
    const normalized = normalizeAuthRouteState(parsed, AUTH_ROUTE_STATE_VERSION, now);
    if (!normalized) {
      try {
        fssync.unlinkSync(p);
      } catch {}
      return null;
    }
    authRouteStateCache = normalized;
    return normalized;
  } catch {
    return null;
  }
}

function writeAuthRouteState(state) {
  const normalized = normalizeAuthRouteState(state, AUTH_ROUTE_STATE_VERSION, 0);
  if (!normalized) return;
  authRouteStateCache = normalized;
  try {
    const p = getAuthRouteStatePath();
    if (!p) return;
    fssync.mkdirSync(path.dirname(p), { recursive: true });
    fssync.writeFileSync(p, JSON.stringify(normalized, null, 2), "utf-8");
  } catch {
    // ignore
  }
}

function clearAuthRouteState() {
  authRouteStateCache = null;
  try {
    const p = getAuthRouteStatePath();
    if (p && fssync.existsSync(p)) {
      fssync.unlinkSync(p);
    }
  } catch {
    // ignore
  }
}

function getPreferredAuthBase(now = Date.now()) {
  return pickPreferredAuthBase({
    fallbackEnabled: AUTH_IP_FALLBACK_ENABLED,
    compatBase: AUTH_COMPAT_API_BASE,
    primaryBase: AUTH_PRIMARY_BASE,
    readRouteState: readAuthRouteState,
    now,
  });
}

function isAuthFallbackCandidate(err) {
  return shouldFallbackAuthRequest(err, {
    fallbackEnabled: AUTH_IP_FALLBACK_ENABLED,
    compatBase: AUTH_COMPAT_API_BASE,
  });
}

function buildAuthTargetUrl(baseURL, targetPath) {
  return new URL(String(targetPath || ""), String(baseURL || "").endsWith("/") ? String(baseURL) : `${String(baseURL)}/`).toString();
}

function logMain(msg) {
  try {
    const p = getMainLogPath();
    if (!p) return;
    const line = `[${new Date().toISOString()}] ${String(msg || "")}\n`;
    fssync.appendFileSync(p, line, { encoding: "utf-8" });
  } catch {
    // ignore
  }
}

logMain(`startup isDev=${isDev}`);
try {
  logMain(`exe=${app.getPath("exe")}`);
  logMain(`resourcesPath=${process.resourcesPath}`);
  logMain(`userData=${app.getPath("userData")}`);
} catch {
  // ignore
}

// Allow loading local media safely from renderer (avoids file:// blocked under http dev server).
// Some Electron builds/environments may not expose `protocol` early; don't crash dev startup.
try {
  if (protocol && typeof protocol.registerSchemesAsPrivileged === "function") {
    protocol.registerSchemesAsPrivileged([
      {
        scheme: "localfile",
        privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
      },
    ]);
  } else {
    logMain("protocol.registerSchemesAsPrivileged not available; skip scheme registration");
  }
} catch (e) {
  logMain(`protocol.registerSchemesAsPrivileged failed: ${String(e)}`);
}

function safeResolveUnder(baseDir, relativePath) {
  const base = path.resolve(String(baseDir || ""));
  const target = path.resolve(base, String(relativePath || ""));
  // Ensure target is inside base
  if (target === base) return target;
  if (!target.startsWith(base + path.sep)) {
    throw new Error("非法路径：目标不在输出目录下");
  }
  return target;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForRendererE2EBridge(win, timeoutMs = 15000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const ready = await win.webContents.executeJavaScript(
        "Boolean(window.__ygfE2E && typeof window.__ygfE2E.uploadLocalVideo === 'function' && typeof window.__ygfE2E.getWizardState === 'function')",
        true,
      );
      if (ready) return true;
    } catch {}
    // eslint-disable-next-line no-await-in-loop
    await sleep(250);
  }
  return false;
}

async function runElectronUploadSmoke(win) {
  if (!e2eSmokeEnabled) return;
  try {
    if (!e2eSmokeFile) throw new Error("YGF_E2E_SMOKE_FILE is required");
    const ready = await waitForRendererE2EBridge(win);
    if (!ready) throw new Error("renderer E2E bridge not ready");

    const uploadResult = await win.webContents.executeJavaScript(
      `window.__ygfE2E.uploadLocalVideo(${JSON.stringify(e2eSmokeFile)})`,
      true,
    );

    const startedAt = Date.now();
    let wizardState = null;
    while (Date.now() - startedAt < 15000) {
      // eslint-disable-next-line no-await-in-loop
      wizardState = await win.webContents.executeJavaScript("window.__ygfE2E.getWizardState()", true);
      if (Number(wizardState?.taskCount || 0) > 0) break;
      // eslint-disable-next-line no-await-in-loop
      await sleep(250);
    }
    if (!wizardState || Number(wizardState.taskCount || 0) <= 0) {
      throw new Error("wizard task list did not update after upload");
    }

    console.log(
      `YGF_E2E_SMOKE_RESULT=${JSON.stringify({
        ok: true,
        uploadedName: uploadResult?.name || path.basename(e2eSmokeFile),
        taskCount: Number(wizardState.taskCount || 0),
        taskNames: Array.isArray(wizardState.taskNames) ? wizardState.taskNames : [],
      })}`
    );
    setTimeout(() => {
      app.quit();
    }, 300);
  } catch (err) {
    console.log(
      `YGF_E2E_SMOKE_RESULT=${JSON.stringify({
        ok: false,
        error: err?.message || String(err),
      })}`
    );
    setTimeout(() => {
      app.exit(1);
    }, 300);
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    icon: path.join(__dirname, "icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
  const devOrigin = (() => {
    try {
      return new URL(devUrl).origin;
    } catch {
      return "http://127.0.0.1:5173";
    }
  })();

  function isAllowedMainFrameUrl(targetUrl) {
    const s = String(targetUrl || "");
    if (!s) return false;
    if (s === "about:blank") return true;
    if (s.startsWith("devtools://")) return true;
    if (s.startsWith("localfile://")) return true;
    if (s.startsWith("data:")) return true;
    if (isDev) return s.startsWith(devOrigin);
    try {
      const distIndex = path.join(__dirname, "../dist/index.html");
      const fileUrl = new URL(`file://${distIndex}`).href;
      return s.startsWith("file://") || s === fileUrl;
    } catch {
      return s.startsWith("file://");
    }
  }

  win.webContents.on("will-navigate", (e, targetUrl) => {
    if (isAllowedMainFrameUrl(targetUrl)) return;
    e.preventDefault();
    logMain(`blocked unexpected main-frame navigation: ${String(targetUrl || "")}`);
    try {
      if (isDev) win.loadURL(devUrl);
    } catch {}
  });
  win.webContents.on("did-navigate", (_e, targetUrl) => {
    logMain(`did-navigate: ${String(targetUrl || "")}`);
  });
  win.webContents.on("did-finish-load", () => {
    try {
      logMain(`did-finish-load: ${win.webContents.getURL()}`);
    } catch {}
    if (e2eSmokeEnabled) {
      runElectronUploadSmoke(win).catch(() => {});
    }
  });

  if (isDev) {
    // In dev, Electron might start before Vite is ready, causing a blank window.
    // Auto-retry loading the dev server when connection is refused.
    let attempts = 0;
    const maxAttempts = 80; // ~20s with backoff
    win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
      try {
        logMain(`did-fail-load: code=${code} desc=${String(desc || "")} url=${String(url || "")} main=${!!isMainFrame}`);
        if (!isMainFrame) return;
        const d = String(desc || "");
        const isConnRefused = d.includes("ERR_CONNECTION_REFUSED") || code === -102;
        if (!isConnRefused) return;
        if (attempts >= maxAttempts) return;
        attempts += 1;
        const delayMs = Math.min(2000, 200 + attempts * 100);
        setTimeout(() => {
          try {
            win.loadURL(devUrl);
          } catch {}
        }, delayMs);
      } catch {
        // ignore
      }
    });
    win.loadURL(devUrl);
    win.webContents.openDevTools({ mode: "detach" });
  } else {
    const indexHtml = path.join(__dirname, "../dist/index.html");
    win.loadFile(indexHtml);
    win.setMenuBarVisibility(false);
  }
  return win;
}

function normalizeAuthProxyPath(rawPath) {
  const normalized = String(rawPath || "").trim();
  if (!normalized.startsWith("/api/")) {
    throw new Error("非法鉴权请求路径");
  }
  return normalized;
}

function coerceAuthProxyBody(body) {
  if (body === undefined || body === null) return "";
  if (typeof body === "string") return body;
  return JSON.stringify(body);
}

function issueAuthProxyRequest({ baseURL, method, targetPath, headers, bodyText }) {
  const url = buildAuthTargetUrl(baseURL, targetPath);
  return new Promise((resolve, reject) => {
    const req = electronNet.request({
      method,
      url,
      redirect: "follow",
    });

    // Chromium/Electron rejects manually setting Host and returns ERR_INVALID_ARGUMENT.
    req.setHeader("Accept", "application/json");
    for (const [key, value] of Object.entries(headers || {})) {
      if (value === undefined || value === null) continue;
      req.setHeader(key, String(value));
    }
    if (bodyText) {
      if (!Object.keys(headers || {}).some((key) => String(key).toLowerCase() === "content-type")) {
        req.setHeader("Content-Type", "application/json");
      }
      req.write(bodyText);
    }

    req.on("response", (res) => {
      const chunks = [];
      res.on("data", (chunk) => {
        chunks.push(Buffer.from(chunk));
      });
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf-8");
        let data = undefined;
        try {
          data = text ? JSON.parse(text) : undefined;
        } catch {
          data = undefined;
        }
        resolve({
          ok: (res.statusCode || 0) >= 200 && (res.statusCode || 0) < 300,
          status: res.statusCode || 0,
          text,
          data,
          meta: { baseURL, url },
        });
      });
    });
    req.on("error", (err) => {
      try {
        err.authBaseURL = baseURL;
        err.authTargetURL = url;
      } catch {}
      reject(err);
    });
    req.end();
  });
}

async function proxyAuthRequest(payload) {
  const method = String(payload?.method || "GET").trim().toUpperCase();
  const targetPath = normalizeAuthProxyPath(payload?.path);
  const headers = payload?.headers && typeof payload.headers === "object" ? payload.headers : {};
  const bodyText = coerceAuthProxyBody(payload?.body);
  const primaryBase = getPreferredAuthBase();

  try {
    const response = await issueAuthProxyRequest({
      baseURL: primaryBase,
      method,
      targetPath,
      headers,
      bodyText,
    });
    if (primaryBase === AUTH_PRIMARY_BASE) {
      clearAuthRouteState();
    }
    return {
      ...response,
      route: {
        baseURL: primaryBase,
        mode: primaryBase === AUTH_PRIMARY_BASE ? "primary" : "compat",
        fallbackTriggered: false,
      },
    };
  } catch (err) {
    const canFallback = primaryBase === AUTH_PRIMARY_BASE && isAuthFallbackCandidate(err);
    if (!canFallback) throw err;

    const nextState = buildFallbackRouteState({
      compatBase: AUTH_COMPAT_API_BASE,
      version: AUTH_ROUTE_STATE_VERSION,
      cacheMs: AUTH_IP_FALLBACK_CACHE_MS,
      err,
      now: Date.now(),
    });
    writeAuthRouteState(nextState);
    logMain(
      `auth primary failed, fallback to compat: code=${String(err?.code || "")} message=${String(err?.message || "")} expiresAt=${new Date(nextState.expiresAt).toISOString()}`
    );
    const response = await issueAuthProxyRequest({
      baseURL: AUTH_COMPAT_API_BASE,
      method,
      targetPath,
      headers,
      bodyText,
    });
    return {
      ...response,
      route: {
        baseURL: AUTH_COMPAT_API_BASE,
        mode: "compat",
        fallbackTriggered: true,
        expiresAt: nextState.expiresAt,
      },
    };
  }
}

function getModelsRoot() {
  return path.join(app.getPath("userData"), "models");
}

function getOllamaRoot() {
  if (process.platform !== "win32") {
    const exe = getOllamaExecutable();
    if (exe) return path.dirname(exe);
  }
  // Prefer user-imported ollama pack (scheme A).
  const userDir = path.join(app.getPath("userData"), "ollama");
  try {
    if (fssync.existsSync(path.join(userDir, "ollama.exe"))) return userDir;
  } catch {}

  // Fallback: bundled resources (older builds / dev convenience).
  const bundled = path.join(process.resourcesPath, "ollama");
  try {
    if (fssync.existsSync(path.join(bundled, "ollama.exe"))) return bundled;
  } catch {}

  // Default destination for import.
  return userDir;
}

function getOllamaModelsRoot() {
  if (process.platform !== "win32") {
    const envRoot = String(process.env.OLLAMA_MODELS || "").trim();
    if (envRoot) return envRoot;
    return path.join(os.homedir(), ".ollama", "models");
  }
  return path.join(getModelsRoot(), "ollama_models");
}

function getOllamaExecutable() {
  const envBin = String(process.env.YGF_OLLAMA_BIN || process.env.OLLAMA_BIN || "").trim();
  if (envBin && pathExists(envBin)) return envBin;

  if (process.platform === "win32") {
    const root = getOllamaRoot();
    const exe = path.join(root, "ollama.exe");
    return pathExists(exe) ? exe : "";
  }

  const candidates = [
    "/Applications/Ollama.app/Contents/MacOS/Ollama",
    "/Applications/Ollama.app/Contents/MacOS/ollama",
    "/opt/homebrew/bin/ollama",
    "/usr/local/bin/ollama",
  ];
  for (const p of candidates) {
    if (pathExists(p)) return p;
  }
  try {
    const probe = spawnSync("bash", ["-lc", "command -v ollama"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 1500,
    });
    const resolved = String(probe?.stdout || "").trim();
    if (resolved && pathExists(resolved)) return resolved;
  } catch {}
  return "";
}

function getPackagedConfigPath() {
  // backend/app.py supports CONFIG_PATH; point it to a real file on disk (NOT inside app.asar).
  const base = process.resourcesPath;
  const candidates = [
    // v2 preferred
    path.join(base, "configs", "quality.yaml"),
    path.join(base, "configs", "defaults.yaml"),
    // legacy fallback
    path.join(base, "config", "quality.yaml"),
    path.join(base, "config", "defaults.yaml"),
  ];
  for (const p of candidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {
      // ignore
    }
  }
  return "";
}

function getProductEdition() {
  const explicit = String(process.env.YGF_PRODUCT_EDITION || process.env.VITE_PRODUCT_EDITION || "").trim().toLowerCase();
  if (explicit === "quality") return "quality";
  if (explicit === "lite") return "lite";
  if (!isDev) {
    const cfg = path.basename(getPackagedConfigPath()).toLowerCase();
    if (cfg === "quality.yaml" || cfg === "quality.yml") return "quality";
  }
  return "lite";
}

function getQualityRuntimeManifestPath() {
  const candidates = [
    path.join(process.resourcesPath, "assets", "runtime", "quality_windows_manifest.json"),
    path.join(getRepoRoot(), "assets", "runtime", "quality_windows_manifest.json"),
  ];
  for (const p of candidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {}
  }
  return candidates[0];
}

function pathExists(p) {
  try {
    return !!p && fssync.existsSync(p);
  } catch {
    return false;
  }
}

function readJsonFile(p) {
  try {
    return JSON.parse(fssync.readFileSync(p, "utf-8"));
  } catch {
    return null;
  }
}

function loadQualityRuntimeManifest() {
  const manifestPath = getQualityRuntimeManifestPath();
  const manifest = readJsonFile(manifestPath);
  return { manifestPath, manifest };
}

function parseConfiguredQualityModelIds(configPath) {
  const ids = [];
  const seen = new Set();
  try {
    const raw = fssync.readFileSync(configPath, "utf-8");
    const llmModelMatch = raw.match(/^\s*llm_model:\s*([^\n#]+?)\s*$/m);
    const phraseModelMatch = raw.match(/^\s*zh_phrase_llm_model:\s*([^\n#]+?)\s*$/m);
    const llmModel = String(llmModelMatch?.[1] || "").trim().replace(/^['"]|['"]$/g, "");
    const phraseModel = String(phraseModelMatch?.[1] || "").trim().replace(/^['"]|['"]$/g, "");
    for (const value of [llmModel, phraseModel || llmModel]) {
      if (!value || seen.has(value)) continue;
      seen.add(value);
      ids.push(value);
    }
  } catch {}
  return ids;
}

async function checkManifestEntries(baseDir, items) {
  const missing = [];
  const present = [];
  for (const item of items || []) {
    const relativePath = String(item?.relativePath || "");
    const targetPath = path.join(baseDir, relativePath);
    try {
      const st = await fs.stat(targetPath);
      const isDir = String(item?.type || "file") === "dir";
      const minBytes = Number(item?.minBytes || 0);
      const isExpectedType = isDir ? st.isDirectory() : st.isFile();
      const hasExpectedSize = isDir || minBytes <= 0 || st.size >= minBytes;
      const record = {
        key: String(item?.key || ""),
        label: String(item?.label || item?.key || relativePath || "unknown"),
        path: targetPath,
        exists: isExpectedType && hasExpectedSize,
        size: st.size || 0,
        minBytes,
      };
      if (record.exists) present.push(record);
      else missing.push(record);
    } catch {
      missing.push({
        key: String(item?.key || ""),
        label: String(item?.label || item?.key || relativePath || "unknown"),
        path: targetPath,
        exists: false,
        size: 0,
        minBytes: Number(item?.minBytes || 0),
      });
    }
  }
  return { missing, present };
}

function isPackagedWindowsProduct() {
  return !isDev && process.platform === "win32";
}

function shouldUseLocalWindowsProductFlow() {
  return isPackagedWindowsProduct();
}

async function checkModels(root) {
  const r = root || getModelsRoot();
  const { manifestPath, manifest } = loadQualityRuntimeManifest();
  const layouts = manifest?.quality?.modelLayouts || {};
  const v2 = Array.isArray(layouts?.v2) ? layouts.v2 : [];
  const legacy = Array.isArray(layouts?.legacy) ? layouts.legacy : [];
  const checkedV2 = await checkManifestEntries(r, v2);
  if (checkedV2.missing.length === 0) return { ready: true, root: r, layout: "v2", missing: [], manifestPath };

  const checkedLegacy = await checkManifestEntries(r, legacy);
  if (checkedLegacy.missing.length === 0) return { ready: true, root: r, layout: "legacy", missing: [], manifestPath };

  return { ready: false, root: r, layout: "v2", missing: checkedV2.missing, manifestPath };
}

function getCandidateZips() {
  const exeDir = path.dirname(app.getPath("exe"));
  const resourcesDir = process.resourcesPath;
  const appDir = path.dirname(resourcesDir);
  const candidates = [
    path.join(exeDir, "models_pack.zip"),
    path.join(appDir, "models_pack.zip"),
    path.join(resourcesDir, "models_pack.zip"),
    path.join(app.getPath("userData"), "models_pack.zip"),
    path.join(app.getPath("documents"), "models_pack.zip"),
    path.join(app.getPath("downloads"), "models_pack.zip"),
    path.join(app.getPath("desktop"), "models_pack.zip"),
  ];
  const exeDrive = path.parse(exeDir).root.toLowerCase();
  const sameDrive = [];
  const otherDrive = [];
  for (const p of candidates) {
    const drive = path.parse(p).root.toLowerCase();
    if (drive === exeDrive) sameDrive.push(p);
    else otherDrive.push(p);
  }
  return [...new Set([...sameDrive, ...otherDrive])];
}

function formatZipHint(zipPath) {
  if (!zipPath) return "";
  const exeDir = path.dirname(app.getPath("exe"));
  const resourcesDir = process.resourcesPath;
  const userData = app.getPath("userData");
  const tryRel = (base, label) => {
    try {
      const rel = path.relative(base, zipPath);
      if (rel && !rel.startsWith("..") && !path.isAbsolute(rel)) {
        return `${label}\\${rel}`;
      }
    } catch {}
    return "";
  };
  return (
    tryRel(exeDir, "应用目录") ||
    tryRel(resourcesDir, "资源目录") ||
    tryRel(userData, "用户数据目录") ||
    zipPath
  );
}

function findModelsPackZip() {
  const candidates = getCandidateZips();
  for (const p of candidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {
      continue;
    }
  }
  return "";
}

function getCandidateOllamaZips() {
  const exeDir = path.dirname(app.getPath("exe"));
  const resourcesDir = process.resourcesPath;
  const appDir = path.dirname(resourcesDir);
  const candidates = [
    path.join(exeDir, "ollama_pack.zip"),
    path.join(appDir, "ollama_pack.zip"),
    path.join(resourcesDir, "ollama_pack.zip"),
    path.join(app.getPath("userData"), "ollama_pack.zip"),
    path.join(app.getPath("documents"), "ollama_pack.zip"),
    path.join(app.getPath("downloads"), "ollama_pack.zip"),
    path.join(app.getPath("desktop"), "ollama_pack.zip"),
  ];
  const exeDrive = path.parse(exeDir).root.toLowerCase();
  const sameDrive = [];
  const otherDrive = [];
  for (const p of candidates) {
    const drive = path.parse(p).root.toLowerCase();
    if (drive === exeDrive) sameDrive.push(p);
    else otherDrive.push(p);
  }
  return [...new Set([...sameDrive, ...otherDrive])];
}

function findOllamaPackZip() {
  const candidates = getCandidateOllamaZips();
  for (const p of candidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {
      continue;
    }
  }
  return "";
}

async function findOllamaBaseDir(root, maxDepth = 10) {
  try {
    const items = await fs.readdir(root, { withFileTypes: true });
    const names = items.map((d) => d.name);
    if (names.includes("ollama.exe")) return root;
    if (names.includes("ollama")) return path.join(root, "ollama");
    if (maxDepth <= 0) return "";
    const dirs = items.filter((d) => d.isDirectory()).map((d) => d.name);
    if (dirs.length === 1) {
      return await findOllamaBaseDir(path.join(root, dirs[0]), maxDepth - 1);
    }
    return "";
  } catch {
    return "";
  }
}

async function normalizeExtractedOllama(root) {
  const base = await findOllamaBaseDir(root);
  if (!base || base === root) return false;
  try {
    const items = await fs.readdir(base, { withFileTypes: true });
    for (const it of items) {
      const src = path.join(base, it.name);
      const dst = path.join(root, it.name);
      try {
        if (fssync.existsSync(dst)) {
          await fs.rm(dst, { recursive: true, force: true });
        }
        await fs.rename(src, dst);
      } catch {
        // ignore
      }
    }
  } catch {
    // ignore
  }
  return true;
}

function extractZip(zipPath, destDir) {
  return new Promise((resolve, reject) => {
    const tryFallbackPowershell = () => {
      const ps = spawn(
        "powershell",
        [
          "-NoProfile",
          "-Command",
          `Expand-Archive -Path '${zipPath}' -DestinationPath '${destDir}' -Force`,
        ],
        { windowsHide: true }
      );
      ps.on("error", reject);
      ps.on("exit", (code) => (code === 0 ? resolve(true) : reject(new Error(`Expand-Archive exit ${code}`))));
    };

    // Prefer 7-Zip when available (more reliable for Zip64/large archives).
    const candidates = [
      process.env.YGF_7Z,
      "D:\\7-Zip\\7z.exe",
      "C:\\Program Files\\7-Zip\\7z.exe",
      "C:\\Program Files (x86)\\7-Zip\\7z.exe",
      "7z",
    ].filter(Boolean);

    const sevenZip = candidates.find((p) => {
      try {
        return p === "7z" ? true : fssync.existsSync(p);
      } catch {
        return false;
      }
    });

    if (!sevenZip) {
      tryFallbackPowershell();
      return;
    }

    const args = ["x", "-y", "-aoa", `-o${destDir}`, zipPath];
    const p = spawn(sevenZip, args, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let tail = "";
    const add = (d) => {
      try {
        tail += String(d || "");
        if (tail.length > 6000) tail = tail.slice(-6000);
      } catch {}
    };
    p.stdout?.on("data", add);
    p.stderr?.on("data", add);
    p.on("error", () => tryFallbackPowershell());
    p.on("exit", (code) => {
      if (code === 0) resolve(true);
      else reject(new Error(`7z extract failed (exit ${code})\n${tail}`));
    });
  });
}

async function findModelsBaseDir(root, maxDepth = 10) {
  // If already in expected layout, return root.
  try {
    const items = await fs.readdir(root, { withFileTypes: true });
    const names = items.filter((d) => d.isDirectory()).map((d) => d.name);
    if (
      names.includes("quality_asr_whisperx") ||
      names.includes("quality_tts_coqui") ||
      names.includes("whisperx") ||
      names.includes("tts") ||
      names.includes("ollama_models")
    )
      return root;
    if (names.includes("models_pack")) return path.join(root, "models_pack");
    if (maxDepth <= 0) return "";
    const dirs = items.filter((d) => d.isDirectory()).map((d) => d.name);
    if (dirs.length === 1) {
      return await findModelsBaseDir(path.join(root, dirs[0]), maxDepth - 1);
    }
    return "";
  } catch {
    return "";
  }
}

async function promoteDirContents(fromDir, toDir) {
  const wanted = ["quality_asr_whisperx", "quality_tts_coqui", "common_cache_hf", "whisperx", "tts", "ollama_models"];
  for (const name of wanted) {
    const src = path.join(fromDir, name);
    const dst = path.join(toDir, name);
    try {
      if (!fssync.existsSync(src)) continue;
      // Remove existing destination to allow overwrite.
      if (fssync.existsSync(dst)) {
        await fs.rm(dst, { recursive: true, force: true });
      }
      await fs.rename(src, dst);
    } catch {
      // ignore and continue
    }
  }
}

async function normalizeExtractedModels(root) {
  // Detect nested models_pack created by zips that contain full repo paths.
  const base = await findModelsBaseDir(root);
  if (!base || base === root) return false;
  await promoteDirContents(base, root);
  return true;
}

async function ensureModels() {
  const root = getModelsRoot();
  process.env.YGF_MODELS_ROOT = root;
  const checked = await checkModels(root);
  if (checked.ready) return { ready: true, root };
  const zip = findModelsPackZip();
  if (!zip) return { ready: false, root, zip: "" };
  await fs.mkdir(root, { recursive: true });
  await extractZip(zip, root);
  const checkedAfter = await checkModels(root);
  return { ready: checkedAfter.ready, root, zip };
}

function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = nodeNet.createConnection({ port, host: "127.0.0.1" }, () => {
      socket.end();
      resolve(true);
    });
    socket.on("error", () => resolve(false));
    socket.setTimeout(500, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

let backendProc = null;
let backendRestartTimer = null;
let backendRestartAttempts = 0;
let appShuttingDown = false;

function clearBackendRestartTimer() {
  try {
    if (backendRestartTimer) clearTimeout(backendRestartTimer);
  } catch {}
  backendRestartTimer = null;
}

function scheduleBackendRestart(reason) {
  if (appShuttingDown) return;
  const explicitApiBase = String(process.env.VITE_API_BASE || process.env.GUI_API_BASE_OVERRIDE || "").trim();
  if (explicitApiBase) return;
  clearBackendRestartTimer();
  backendRestartAttempts += 1;
  const delayMs = Math.min(10000, 1000 * Math.max(1, backendRestartAttempts));
  logMain(`schedule backend restart in ${delayMs}ms (${reason || "unknown"})`);
  backendRestartTimer = setTimeout(() => {
    ensureBackend().catch((err) => {
      logMain(`auto backend restart failed: ${err?.message || String(err)}`);
    });
  }, delayMs);
}
function getBackendExePath() {
  const override = process.env.YGF_BACKEND_EXE;
  if (override && fssync.existsSync(override)) return override;

  // Packaged: backend_server.exe is bundled into resources root via electron-builder extraResources.
  const packagedCandidates = [
    path.join(process.resourcesPath, "backend_server.exe"), // windows
    path.join(process.resourcesPath, "backend_server"), // mac/linux
  ];
  for (const p of packagedCandidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {}
  }

  // Dev: use repo dist/backend_server.exe (relative to apps/desktop/electron).
  // __dirname = <repo>/apps/desktop/electron
  const devCandidates = [
    path.join(getRepoRoot(), "dist", "backend_server.exe"),
    path.join(getRepoRoot(), "dist", "backend_server"),
  ];
  for (const p of devCandidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {}
  }

  return "";
}

function getQualityWorkerExePath() {
  const backendExe = getBackendExePath();
  const candidates = [];
  if (backendExe) {
    candidates.push(path.join(path.dirname(backendExe), "quality_worker.exe"));
  }
  candidates.push(path.join(process.resourcesPath, "quality_worker.exe"));
  for (const p of candidates) {
    try {
      if (fssync.existsSync(p)) return p;
    } catch {}
  }
  return candidates[0] || "";
}

function getWindowsPackRuntimeStatus() {
  const backendExe = getBackendExePath();
  const qualityWorkerExe = getQualityWorkerExePath();
  const configPath = getPackagedConfigPath();
  const { manifestPath, manifest } = loadQualityRuntimeManifest();
  const modelsPackZip = findModelsPackZip();
  const ollamaPackZip = findOllamaPackZip();
  const ollamaRoot = getOllamaRoot();
  const ollamaExe = path.join(ollamaRoot, "ollama.exe");
  const runtimeFiles = Array.isArray(manifest?.quality?.runtimeFiles) ? manifest.quality.runtimeFiles : [];
  return {
    packagedWindowsProduct: isPackagedWindowsProduct(),
    manifestPath,
    manifestExists: pathExists(manifestPath),
    backendExe,
    backendExeExists: pathExists(backendExe),
    qualityWorkerExe,
    qualityWorkerExeExists: pathExists(qualityWorkerExe),
    packagedConfigPath: configPath,
    packagedConfigExists: pathExists(configPath),
    modelsPackZip,
    modelsPackZipExists: pathExists(modelsPackZip),
    ollamaPackZip,
    ollamaPackZipExists: pathExists(ollamaPackZip),
    ollamaRoot,
    ollamaExe,
    ollamaExeExists: pathExists(ollamaExe),
    ollamaModelsRoot: getOllamaModelsRoot(),
    runtimeChecks: runtimeFiles.map((item) => {
      const targetPath = path.join(process.resourcesPath, String(item?.relativePath || ""));
      return {
        key: String(item?.key || ""),
        label: String(item?.label || item?.key || targetPath),
        path: targetPath,
        exists: pathExists(targetPath),
      };
    }),
  };
}

function _canRun(cmd) {
  try {
    const r = spawnSync(cmd, ["-V"], { windowsHide: true, stdio: "ignore" });
    return !!r && r.status === 0;
  } catch {
    return false;
  }
}

function _pythonHasModules(cmd, modules) {
  try {
    const names = Array.isArray(modules) ? modules.filter(Boolean) : [];
    if (!names.length) return true;
    const code =
      "import importlib, sys; " +
      `mods=${JSON.stringify(names)}; ` +
      "missing=[]; " +
      "\nfor m in mods:\n" +
      "  try:\n" +
      "    importlib.import_module(m)\n" +
      "  except Exception:\n" +
      "    missing.append(m)\n" +
      "sys.exit(0 if not missing else 1)";
    const r = spawnSync(cmd, ["-c", code], { windowsHide: true, stdio: "ignore" });
    return !!r && r.status === 0;
  } catch {
    return false;
  }
}

function getPythonUserSite(cmd) {
  try {
    const code = "import site; print(site.getusersitepackages())";
    const r = spawnSync(cmd, ["-c", code], { windowsHide: true, encoding: "utf8" });
    if (!r || r.status !== 0) return "";
    return String(r.stdout || "").trim();
  } catch {
    return "";
  }
}

function getPythonExe() {
  // macOS often has python3 only; Windows sometimes relies on py launcher.
  // Dev priority:
  // 1) explicit env override
  // 2) repo-local venv if present
  // 3) common user-managed interpreters
  // 4) system python fallback
  const preferred = process.env.YGF_PYTHON;
  const repoRoot = getRepoRoot();
  const repoVenvPy = process.platform === "win32"
    ? path.join(repoRoot, ".venv-qwen35", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv-qwen35", "bin", "python");
  const candidates = [
    preferred,
    repoVenvPy,
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "python3",
    "python",
    process.platform === "win32" ? "py" : "",
  ].filter(Boolean);
  const deduped = [...new Set(candidates)];
  // Prefer interpreters that can run the desktop backend stack, especially quality mode.
  for (const c of deduped) {
    if (!_canRun(c)) continue;
    if (_pythonHasModules(c, ["flask", "requests", "TTS", "torch"])) return c;
  }
  for (const c of deduped) {
    if (_canRun(c)) return c;
  }
  // Fallback: keep old behavior (will fail fast, but we will log a clear message).
  return preferred || "python3";
}

function getRepoRoot() {
  // __dirname = <repo>/apps/desktop/electron
  return path.resolve(__dirname, "..", "..", "..");
}
function getLogDir() {
  try {
    const dir = path.join(app.getPath("userData"), "logs");
    fssync.mkdirSync(dir, { recursive: true });
    return dir;
  } catch {
    return process.resourcesPath;
  }
}

function safeReadUtf8(p, limit = 2_000_000) {
  try {
    if (!p || !fssync.existsSync(p)) return "";
    const raw = fssync.readFileSync(p, "utf-8");
    if (raw.length <= limit) return raw;
    return raw.slice(raw.length - limit);
  } catch {
    return "";
  }
}

function getDiagnosticsSummary() {
  const configPath = getPackagedConfigPath();
  return {
    appVersion: app.getVersion(),
    platform: process.platform,
    packaged: !isDev,
    apiBase: process.env.GUI_API_BASE || "http://127.0.0.1:5175",
    configPath,
    requiredOllamaModels: parseConfiguredQualityModelIds(configPath),
    runtimeStatus: getWindowsPackRuntimeStatus(),
  };
}

function pipeChildLogs(proc, name) {
  try {
    const dir = getLogDir();
    const outPath = path.join(dir, `${name}.out.log`);
    const errPath = path.join(dir, `${name}.err.log`);
    const out = fssync.createWriteStream(outPath, { flags: "a" });
    const err = fssync.createWriteStream(errPath, { flags: "a" });
    proc.stdout?.on("data", (d) => out.write(d));
    proc.stderr?.on("data", (d) => err.write(d));
    proc.on("close", () => {
      try { out.end(); } catch {}
      try { err.end(); } catch {}
    });
  } catch {
    // ignore
  }
}

async function waitForPortOpen(port, timeoutMs = 12000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    // eslint-disable-next-line no-await-in-loop
    const ok = await isPortOpen(port);
    if (ok) return true;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

function httpGetJson(url, timeoutMs = 1200) {
  return new Promise((resolve, reject) => {
    try {
      const req = http.request(url, { method: "GET" }, (res) => {
        let data = "";
        res.setEncoding("utf-8");
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          try {
            const code = res.statusCode || 0;
            if (code < 200 || code >= 300) {
              return reject(new Error(`HTTP ${code}`));
            }
            resolve(JSON.parse(data || "{}"));
          } catch (e) {
            reject(e);
          }
        });
      });
      req.on("error", reject);
      req.setTimeout(timeoutMs, () => {
        try {
          req.destroy(new Error("timeout"));
        } catch {}
      });
      req.end();
    } catch (e) {
      reject(e);
    }
  });
}

async function getInstalledOllamaModelIds(timeoutMs = 2500) {
  try {
    const payload = await httpGetJson("http://127.0.0.1:11434/api/tags", timeoutMs);
    const models = Array.isArray(payload?.models) ? payload.models : [];
    const ids = [];
    const seen = new Set();
    for (const item of models) {
      const id = String(item?.model || item?.name || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      ids.push(id);
    }
    return ids;
  } catch {
    return [];
  }
}

function parseOllamaPsOutput(stdout) {
  const lines = String(stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean);
  if (lines.length <= 1) {
    return {
      activeModelCount: 0,
      activeModels: [],
      processorSummary: "空闲（无活动模型）",
      acceleration: "idle",
      usesGpu: false,
    };
  }
  const rows = lines.slice(1);
  const activeModels = [];
  const processors = [];
  for (const row of rows) {
    const cols = String(row || "")
      .trim()
      .split(/\s{2,}/)
      .filter(Boolean);
    if (!cols.length) continue;
    const name = String(cols[0] || "").trim();
    let processor = "";
    for (const col of cols.slice(1)) {
      if (/(^|\s)(CPU|GPU)(\s|$)/i.test(String(col))) {
        processor = String(col).trim();
        break;
      }
    }
    if (name) activeModels.push(name);
    if (processor) processors.push(processor);
  }
  const hasGpu = processors.some((item) => /GPU/i.test(item));
  const hasCpu = processors.some((item) => /CPU/i.test(item));
  const acceleration = !activeModels.length ? "idle" : hasGpu && hasCpu ? "mixed" : hasGpu ? "gpu" : hasCpu ? "cpu" : "unknown";
  return {
    activeModelCount: activeModels.length,
    activeModels,
    processorSummary: activeModels.length
      ? processors.length
        ? processors.join(" / ")
        : "有活动模型，但未解析到处理器"
      : "空闲（无活动模型）",
    acceleration,
    usesGpu: hasGpu,
  };
}

async function getOllamaPsState(timeoutMs = 2500) {
  try {
    const exe = getOllamaExecutable();
    if (!exe || !fssync.existsSync(exe)) {
      return {
        activeModelCount: 0,
        activeModels: [],
        processorSummary: "未发现 Ollama 可执行文件",
        acceleration: "unknown",
        usesGpu: false,
      };
    }
    const env = { ...process.env };
    if (process.platform === "win32") {
      env.OLLAMA_MODELS = getOllamaModelsRoot();
    }
    const ps = spawnSync(exe, ["ps"], {
      cwd: path.dirname(exe),
      env,
      windowsHide: true,
      encoding: "utf8",
      timeout: timeoutMs,
    });
    if (ps.error) {
      return {
        activeModelCount: 0,
        activeModels: [],
        processorSummary: `读取失败：${ps.error.message || "unknown error"}`,
        acceleration: "unknown",
        usesGpu: false,
      };
    }
    return parseOllamaPsOutput(ps.stdout || "");
  } catch (e) {
    return {
      activeModelCount: 0,
      activeModels: [],
      processorSummary: `读取失败：${e && e.message ? e.message : String(e)}`,
      acceleration: "unknown",
      usesGpu: false,
    };
  }
}

async function getBackendRuntime(port) {
  try {
    const p = Number(port || 5175);
    const data = await httpGetJson(`http://127.0.0.1:${p}/api/health`, 900);
    return data && data.runtime ? data.runtime : null;
  } catch {
    return null;
  }
}

async function pickBackendPortDev(preferredPort) {
  const preferred = Number(preferredPort || 5175);
  if (!isDev) return preferred;

  const expectedRoot = path.resolve(getRepoRoot());
  const expectedDockerConfigBasenames = ["quality.yaml", "quality.yml"];
  const preferredPortOpen = await isPortOpen(preferred);
  if (!preferredPortOpen) return preferred;

  let preferredRuntime = null;
  let preferredRuntimeKnown = true;
  try {
    preferredRuntime = await getBackendRuntime(preferred);
  } catch {
    preferredRuntimeKnown = false;
  }

  const initialDecision = chooseDevBackendPort({
    preferredPort: preferred,
    isDev: true,
    expectedRoot,
    expectedDockerConfigBasenames,
    preferredPortOpen,
    preferredRuntime,
    preferredRuntimeKnown,
    nearbyPorts: [],
  });
  if (initialDecision.reason === "preferred-repo") {
    logMain(`dev backend already running on port=${preferred} repo_root=${String(preferredRuntime?.repo_root || "")}; reusing.`);
    return preferred;
  }
  if (initialDecision.reason === "preferred-docker-repo") {
    logMain(`dev detected docker repo backend on port=${preferred} (repo_root=/app config_path=${String(preferredRuntime?.config_path || "")}); reusing.`);
    return preferred;
  }
  if (preferredRuntimeKnown) {
    logMain(
      `dev backend port=${preferred} is in use by another backend (repo_root=${String(preferredRuntime?.repo_root || "?")}); scanning nearby ports...`
    );
  } else {
    logMain(`dev backend port=${preferred} is in use (runtime unknown); scanning nearby ports...`);
  }

  const nearbyPorts = [];
  for (let p = preferred + 1; p < preferred + 30; p += 1) {
    // eslint-disable-next-line no-await-in-loop
    const open = await isPortOpen(p);
    if (!open) {
      nearbyPorts.push({ port: p, open: false, runtime: null });
      continue;
    }
    // eslint-disable-next-line no-await-in-loop
    const rt = await getBackendRuntime(p);
    nearbyPorts.push({ port: p, open: true, runtime: rt });
  }

  const decision = chooseDevBackendPort({
    preferredPort: preferred,
    isDev: true,
    expectedRoot,
    expectedDockerConfigBasenames,
    preferredPortOpen,
    preferredRuntime,
    preferredRuntimeKnown,
    nearbyPorts,
  });
  if (decision.reason === "nearby-repo") {
    const matched = nearbyPorts.find((item) => item.port === decision.port);
    logMain(`dev backend already running on port=${decision.port} repo_root=${String(matched?.runtime?.repo_root || "")}; reusing.`);
    return decision.port;
  }
  if (decision.reason === "preferred-other-backend-first-free" || decision.reason === "preferred-unknown-first-free") {
    logMain(`dev selected first free nearby backend port=${decision.port}.`);
    return decision.port;
  }
  logMain(`dev nearby port scan exhausted; fallback to port=${decision.port}.`);
  return decision.port;
}

async function waitForPortClosed(port, timeoutMs = 6000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    // eslint-disable-next-line no-await-in-loop
    const ok = await isPortOpen(port);
    if (!ok) return true;
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function findListeningPidWin32(port) {
  try {
    const out = execSync("netstat -ano -p TCP", { encoding: "utf-8" }) || "";
    const lines = String(out).split(/\r?\n/);
    for (const line of lines) {
      const s = line.trim();
      if (!s) continue;
      // Example:
      // TCP    0.0.0.0:5175           0.0.0.0:0              LISTENING       1234
      const m = s.match(/^TCP\s+(\S+):(\d+)\s+(\S+):(\S+)\s+LISTENING\s+(\d+)\s*$/i);
      if (!m) continue;
      const p = Number(m[2]);
      const pid = Number(m[5]);
      if (p === Number(port) && pid > 0) return pid;
    }
  } catch {
    // ignore
  }
  return 0;
}

function getProcessImageNameWin32(pid) {
  try {
    // CSV output: "Image Name","PID","Session Name","Session#","Mem Usage"
    const out = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, { encoding: "utf-8" }) || "";
    const first = String(out).trim().split(/\r?\n/)[0] || "";
    if (!first || /No tasks are running/i.test(first)) return "";
    const m = first.match(/^"([^"]+)",/);
    return m ? m[1] : "";
  } catch {
    return "";
  }
}

function killPidTreeWin32(pid) {
  try {
    execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function findListeningPidPosix(port) {
  try {
    const p = Number(port || 0);
    if (!p) return 0;
    const out = execSync(`lsof -t -iTCP:${p} -sTCP:LISTEN 2>/dev/null | head -n 1`, { encoding: "utf-8" }) || "";
    const pid = Number(String(out).trim());
    return Number.isFinite(pid) && pid > 0 ? pid : 0;
  } catch {
    return 0;
  }
}

function readEnvVarFromPidPosix(pid, key) {
  try {
    const k = String(key || "").trim();
    const p = Number(pid || 0);
    if (!k || !p) return "";
    const out = execSync(`ps eww -p ${p}`, { encoding: "utf-8" }) || "";
    // ps output puts env vars after the command, separated by spaces.
    const m = String(out).match(new RegExp(`(?:^|\\s)${k}=([^\\s]+)`));
    return m ? String(m[1] || "") : "";
  } catch {
    return "";
  }
}

async function ensureBackend() {
  // If caller explicitly provides an API base (e.g. dev using docker-compose backend),
  // reuse it and DO NOT spawn a local python backend.
  // This avoids accidental use of macOS CLT python (3.9) which may not have torch/whisperx deps.
  const explicitApiBase = String(process.env.VITE_API_BASE || process.env.GUI_API_BASE_OVERRIDE || "").trim();
  if (explicitApiBase) {
    const m = explicitApiBase.match(/^https?:\/\/(localhost|127\.0\.0\.1):(\d+)\s*\/?$/i);
    const explicitPort = m ? Number(m[2]) : 0;
    if (explicitPort) {
      process.env.GUI_API_BASE = `http://127.0.0.1:${explicitPort}`;
      const ok = await isPortOpen(explicitPort);
      if (ok) return true;
      logMain(`explicit GUI API base provided but port not open: ${process.env.GUI_API_BASE}`);
      // fall through to normal backend spawn logic
    }
  }

  const preferredPort = Number((process.env.GUI_BACKEND_PORT || process.env.YGF_PORT || "5175").trim() || 5175);
  const port = await pickBackendPortDev(preferredPort);
  // Expose the actual backend base URL to renderer via preload.
  process.env.GUI_API_BASE = `http://127.0.0.1:${port}`;

  const ok = await isPortOpen(port);
  if (ok) {
    clearBackendRestartTimer();
    backendRestartAttempts = 0;
    // Common Windows failure mode:
    // - previous backend_server.exe still listening on 5175 (or half-updated during install)
    // - we "reuse" it and UI ends up in lite-only / wrong config.
    // Prefer to evict stale backend processes we don't own and start a fresh one.
    if (process.platform === "win32" && !backendProc) {
      // If the existing backend reports a different YGF_APP_ROOT, it's definitely stale/wrong.
      const rt = await getBackendRuntime(port);
      const reportedRoot = rt && typeof rt.YGF_APP_ROOT === "string" ? rt.YGF_APP_ROOT : "";
      if (reportedRoot && path.resolve(reportedRoot) === path.resolve(process.resourcesPath)) {
        logMain(`backend already running with expected YGF_APP_ROOT=${reportedRoot}; reusing.`);
        return true;
      }
      const pid = findListeningPidWin32(port);
      const name = pid ? getProcessImageNameWin32(pid) : "";
      if (pid && /backend_server\.exe/i.test(name || "")) {
        logMain(
          `backend port ${port} already in use by pid=${pid} (${name})` +
            (reportedRoot ? ` YGF_APP_ROOT=${reportedRoot}` : "") +
            "; killing and restarting..."
        );
        killPidTreeWin32(pid);
        await waitForPortClosed(port, 6000);
      } else {
        // Unknown owner (or could not resolve). Keep existing to avoid killing unrelated services.
        logMain(`backend port ${port} already open (pid=${pid || "?"} name=${name || "?"}); reusing.`);
        return true;
      }
    } else {
      // Dev (mac/linux): if we are reusing an already-running backend from this repo,
      // sync the API token from that process so renderer requests won't 401.
      if (isDev && !backendProc && process.platform !== "win32") {
        try {
          const rt = await getBackendRuntime(port);
          const reportedRoot = rt && typeof rt.repo_root === "string" ? rt.repo_root : "";
          const expectedRoot = path.resolve(getRepoRoot());
          if (reportedRoot && path.resolve(reportedRoot) === expectedRoot) {
            const pid = findListeningPidPosix(port);
            const token = pid ? readEnvVarFromPidPosix(pid, "YGF_API_TOKEN") : "";
            if (token) {
              process.env.YGF_API_TOKEN = token;
              try {
                logMain(`dev synced YGF_API_TOKEN from existing backend pid=${pid} (len=${String(token).length})`);
              } catch {}
            } else {
              try {
                logMain(`dev backend reuse detected on port=${port} but failed to read YGF_API_TOKEN from pid=${pid}`);
              } catch {}
            }
          }
        } catch {
          // ignore
        }
      }
      return true;
    }
  }
  const env = { ...process.env, YGF_MODELS_ROOT: getModelsRoot() };
  // Ensure backend resolves repo_root/config/assets correctly:
  // - dev: point to repo root
  // - packaged: point to resourcesPath
  env.YGF_APP_ROOT = isDev ? getRepoRoot() : process.resourcesPath;
  env.YGF_PORT = String(port);
  // Allow health endpoint without token so main process can verify the instance.
  env.YGF_API_TOKEN_ALLOW_HEALTH = env.YGF_API_TOKEN_ALLOW_HEALTH || "1";
  env.YGF_REQUIRE_CLOUD_LICENSE = env.YGF_REQUIRE_CLOUD_LICENSE || "1";
  env.YGF_AUTH_API_BASE = env.YGF_AUTH_API_BASE || process.env.VITE_AUTH_API_BASE || "https://auth.miaoyichuhai.com";
  env.YGF_PRODUCT_EDITION = env.YGF_PRODUCT_EDITION || getProductEdition();

  // Ensure python can import our backend package (apps/backend/backend) and shared modules (core/).
  // repo_root is needed for `core.*`; apps/backend is needed for `backend.*`.
  try {
    const repo = getRepoRoot();
    const backendRoot = path.join(repo, "apps", "backend");
    const parts = [backendRoot, repo, env.PYTHONPATH || ""].filter(Boolean);
    env.PYTHONPATH = parts.join(path.delimiter);
  } catch {
    // ignore
  }
  // Ensure runtime temp extraction (PyInstaller onefile) and other caches go to userData (prefer D:).
  try {
    const tmp = path.join(app.getPath("userData"), "tmp");
    fssync.mkdirSync(tmp, { recursive: true });
    env.TEMP = tmp;
    env.TMP = tmp;
    // Keep Matplotlib cache stable/off C: to avoid repeated "building the font cache".
    const mpl = path.join(tmp, "matplotlib");
    fssync.mkdirSync(mpl, { recursive: true });
    env.MPLCONFIGDIR = env.MPLCONFIGDIR || mpl;
  } catch {
    // ignore
  }
  // Silence HuggingFace symlink warning (caching still works without symlinks on Windows).
  env.HF_HUB_DISABLE_SYMLINKS_WARNING = env.HF_HUB_DISABLE_SYMLINKS_WARNING || "1";
  // Avoid writing outputs into install/resources directories.
  try {
    const outDir = path.join(app.getPath("userData"), "outputs");
    fssync.mkdirSync(outDir, { recursive: true });
    env.YGF_OUTPUTS_ROOT = outDir;
  } catch {
    // ignore
  }
  try {
    const overrideDir = path.join(app.getPath("userData"), "config_overrides");
    fssync.mkdirSync(overrideDir, { recursive: true });
    env.YGF_CONFIG_OVERRIDE_DIR = overrideDir;
  } catch {
    // ignore
  }
  // Make bundled tools discoverable (ffmpeg/ffprobe etc).
  try {
    const binDir = path.join(process.resourcesPath, "bin");
    if (fssync.existsSync(binDir)) {
      env.PATH = `${binDir};${env.PATH || ""}`;
    }
  } catch {
    // ignore
  }
  // For packaged backend, prefer config from resources/config (filesystem).
  if (!isDev) {
    const cfg = getPackagedConfigPath();
    if (cfg) env.CONFIG_PATH = cfg;
    logMain(`CONFIG_PATH=${env.CONFIG_PATH || ""}`);
    // 第一期开箱即用产品口径只收敛 Windows 打包态：
    // 使用本地 Ollama 进程，而不是 Docker 服务名。
    if (shouldUseLocalWindowsProductFlow()) {
      env.YGF_LLM_ENDPOINT = "http://127.0.0.1:11434/v1";
    }
  }
  try {
    if (isDev) {
      const py = getPythonExe();
      const cwd = getRepoRoot();
      if (!_canRun(py)) {
        logMain(`python not runnable: ${py}. Please install python3 or set env YGF_PYTHON to a valid interpreter.`);
      }
      logMain(`spawn backend dev: ${py} -m backend.app (cwd=${cwd}) port=${port} apiBase=${process.env.GUI_API_BASE}`);
      backendProc = spawn(py, ["-m", "backend.app"], { cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    } else {
      const exe = getBackendExePath();
      if (!exe) {
        logMain("backend exe not found (getBackendExePath empty)");
        return false;
      }
      logMain(`spawn backend exe: ${exe} (cwd=${path.dirname(exe)})`);
      backendProc = spawn(exe, [], { cwd: path.dirname(exe), env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    }
    pipeChildLogs(backendProc, "backend_server");
    backendProc.on("error", (e) => {
      logMain(`backend proc error: ${e && e.message ? e.message : String(e)}`);
      backendProc = null;
      scheduleBackendRestart(`proc error: ${e?.message || String(e)}`);
    });
    backendProc.on("exit", (code, signal) => {
      logMain(`backend proc exit: code=${code} signal=${signal || ""}`);
      backendProc = null;
      scheduleBackendRestart(`proc exit code=${code} signal=${signal || ""}`);
    });
  } catch (e) {
    logMain(`ensureBackend exception: ${e && e.message ? e.message : String(e)}`);
    backendProc = null;
    return false;
  }
  const started = await waitForPortOpen(port);
  if (started) {
    clearBackendRestartTimer();
    backendRestartAttempts = 0;
  }
  return started;
}

let ollamaProc = null;
async function ensureOllama() {
  const ok = await isPortOpen(11434);
  if (ok) return true;
  const exe = getOllamaExecutable();
  if (!exe || !fssync.existsSync(exe)) return false;
  const env = { ...process.env };
  if (process.platform === "win32") {
    env.OLLAMA_MODELS = getOllamaModelsRoot();
  }
  try {
    logMain(`spawn ollama: ${exe} serve (models=${env.OLLAMA_MODELS || ""})`);
    ollamaProc = spawn(exe, ["serve"], { cwd: path.dirname(exe), env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    pipeChildLogs(ollamaProc, "ollama");
    ollamaProc.on("exit", () => {
      ollamaProc = null;
    });
  } catch (e) {
    logMain(`ensureOllama exception: ${e && e.message ? e.message : String(e)}`);
    ollamaProc = null;
    return false;
  }
  return await waitForPortOpen(11434);
}

app.whenReady().then(async () => {
  // localfile:///absolute/path/to/file.mp4
  if (!isDev) {
    Menu.setApplicationMenu(null);
  }
  session.defaultSession.setCertificateVerifyProc((request, callback) => {
    if (AUTH_TRUSTED_COMPAT_HOSTS.includes(String(request?.hostname || ""))) {
      callback(0);
      return;
    }
    callback(-3);
  });
  app.on("certificate-error", (event, _webContents, url, error, _certificate, callback) => {
    if (isTrustedAuthCompatUrl(url)) {
      event.preventDefault();
      logMain(`allow certificate-error for auth compat url=${String(url || "")} error=${String(error || "")}`);
      callback(true);
      return;
    }
    callback(false);
  });
  protocol.registerFileProtocol("localfile", (request, callback) => {
    try {
      const url = String(request.url || "");
      const raw = url.replace(/^localfile:\/\//, "");
      let decoded = decodeURIComponent(raw);
      // Windows: localfile:///C:/... becomes /C:/..., strip leading slash.
      if (/^\/[A-Za-z]:\//.test(decoded)) {
        decoded = decoded.slice(1);
      }
      // Windows: localfile://C/Users/... -> C:/Users/...
      if (/^[A-Za-z]\//.test(decoded) && !/^[A-Za-z]:\//.test(decoded)) {
        decoded = `${decoded[0]}:/${decoded.slice(2)}`;
      }
      callback({ path: decoded });
    } catch (e) {
      callback({ error: -2 }); // FILE_NOT_FOUND
    }
  });

  ipcMain.handle("select-directory", async () => {
    const res = await dialog.showOpenDialog({
      properties: ["openDirectory", "createDirectory"],
      title: "选择输出文件夹",
      buttonLabel: "选择此文件夹",
    });
    if (res.canceled || !res.filePaths || res.filePaths.length === 0) return "";
    return res.filePaths[0];
  });

  ipcMain.handle("get-default-outputs-root", async () => {
    try {
      return path.join(app.getPath("userData"), "outputs");
    } catch {
      return "";
    }
  });

  ipcMain.handle("open-path", async (_evt, targetPath) => {
    const normalized = normalizeOpenTargetPath(targetPath);
    if (!normalized.ok) return { ok: false, error: normalized.error };
    if (!fssync.existsSync(normalized.path)) {
      return { ok: false, error: "目标文件不存在" };
    }
    const err = await shell.openPath(normalized.path);
    if (err) return { ok: false, error: err };
    return { ok: true };
  });

  ipcMain.handle("get-device-code", async () => {
    return getDeviceIdentity().deviceCode || "UNKNOWN";
  });

  ipcMain.handle("get-device-identity", async () => {
    return getDeviceIdentity();
  });

  ipcMain.handle("auth-request", async (_evt, payload) => {
    try {
      return await proxyAuthRequest(payload);
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String(e.message) : String(e);
      return { ok: false, status: 0, text: msg || "auth request failed" };
    }
  });

  ipcMain.handle("ensure-dir", async (_evt, baseDir, relativeDir) => {
    if (!baseDir) throw new Error("baseDir is required");
    const dirPath = safeResolveUnder(baseDir, relativeDir || ".");
    await fs.mkdir(dirPath, { recursive: true });
    return { ok: true, path: dirPath };
  });

  ipcMain.handle("write-file", async (_evt, payload) => {
    const { baseDir, relativePath, bytes } = payload || {};
    if (!baseDir) throw new Error("baseDir is required");
    if (!relativePath) throw new Error("relativePath is required");
    const outPath = safeResolveUnder(baseDir, relativePath);
    await fs.mkdir(path.dirname(outPath), { recursive: true });
    // bytes should be Uint8Array (structured clone) or array-like
    const buf = Buffer.from(bytes || []);
    await fs.writeFile(outPath, buf);
    return { ok: true, path: outPath, size: buf.length };
  });

  ipcMain.handle("e2e-read-local-file", async (_evt, targetPath) => {
    if (!e2eSmokeEnabled) {
      throw new Error("E2E bridge is disabled");
    }
    const resolved = path.resolve(String(targetPath || ""));
    if (!resolved || !fssync.existsSync(resolved)) {
      throw new Error("E2E file not found");
    }
    const buf = await fs.readFile(resolved);
    return {
      path: resolved,
      name: path.basename(resolved),
      bytes: new Uint8Array(buf),
      mimeType: resolved.toLowerCase().endsWith(".mp4") ? "video/mp4" : "application/octet-stream",
    };
  });

  ipcMain.handle("get-model-status", async () => {
    const root = getModelsRoot();
    // Best-effort: if user previously extracted with an extra directory layer,
    // normalize it so status reflects actual files on disk.
    await normalizeExtractedModels(root);
    const checked = await checkModels(root);
    const zip = findModelsPackZip();
    return { ready: checked.ready, root, zip, zipHint: formatZipHint(zip), missing: checked.missing, layout: checked.layout, manifestPath: checked.manifestPath };
  });

  ipcMain.handle("get-runtime-status", async () => {
    return getWindowsPackRuntimeStatus();
  });

  ipcMain.handle("restart-backend", async () => {
    clearBackendRestartTimer();
    backendRestartAttempts = 0;
    try {
      if (backendProc) {
        try {
          backendProc.kill();
        } catch {}
        backendProc = null;
      }
      const port = Number((process.env.GUI_BACKEND_PORT || process.env.YGF_PORT || "5175").trim() || 5175);
      await waitForPortClosed(port, 6000).catch(() => false);
      const ok = await ensureBackend();
      return { ok, apiBase: process.env.GUI_API_BASE || "", port };
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String(e.message) : String(e);
      return { ok: false, error: msg || "重启 backend 失败" };
    }
  });

  ipcMain.handle("collect-diagnostics-bundle", async () => {
    const logDir = getLogDir();
    const files = [
      { name: "logs/main_process.log", path: getMainLogPath() },
      { name: "logs/backend_server.out.log", path: path.join(logDir, "backend_server.out.log") },
      { name: "logs/backend_server.err.log", path: path.join(logDir, "backend_server.err.log") },
      { name: "logs/ollama.out.log", path: path.join(logDir, "ollama.out.log") },
      { name: "logs/ollama.err.log", path: path.join(logDir, "ollama.err.log") },
    ];
    return {
      summary: sanitizeDiagnosticsSummary(getDiagnosticsSummary()),
      files: files
        .map((item) => ({ name: item.name, content: sanitizeDiagnosticsText(safeReadUtf8(item.path)) }))
        .filter((item) => item.content),
    };
  });

  ipcMain.handle("pick-model-pack", async () => {
    const res = await dialog.showOpenDialog({
      properties: ["openFile"],
      filters: [{ name: "Model Pack", extensions: ["zip"] }],
      title: "选择 models_pack.zip",
    });
    if (res.canceled || !res.filePaths || res.filePaths.length === 0) return "";
    return res.filePaths[0];
  });

  ipcMain.handle("extract-model-pack", async (_evt, zipPath) => {
    try {
      const root = getModelsRoot();
      // Fast path: if a previous import extracted with an extra directory layer,
      // fix it without re-extracting the whole archive.
      await normalizeExtractedModels(root);
      const checked0 = await checkModels(root);
      if (checked0.ready) {
        return { ok: true, root, zip: "", zipHint: "", missing: [] };
      }
      const zip = zipPath || findModelsPackZip();
      if (!zip) return { ok: false, error: "models_pack.zip not found" };
      await fs.mkdir(root, { recursive: true });
      await extractZip(zip, root);
      // If the zip contains full paths (e.g. .../dist_electron/models_pack/...), flatten it.
      await normalizeExtractedModels(root);
      const checked = await checkModels(root);
      if (!checked.ready) {
        return {
          ok: false,
          root,
          zip,
          zipHint: formatZipHint(zip),
          missing: checked.missing,
          error: "导入已完成，但模型仍缺失（模型包目录层级不符合，已尝试自动修复）。",
        };
      }
      return { ok: true, root, zip, zipHint: formatZipHint(zip), missing: checked.missing };
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String(e.message) : String(e);
      return { ok: false, error: msg || "导入失败" };
    }
  });

  ipcMain.handle("get-ollama-status", async () => {
    const root = getOllamaRoot();
    await normalizeExtractedOllama(root);
    const configPath = getPackagedConfigPath();
    const exe = getOllamaExecutable();
    const portOpen = await isPortOpen(11434);
    const requiredModels = parseConfiguredQualityModelIds(configPath);
    const installedModels = portOpen ? await getInstalledOllamaModelIds() : [];
    const missingModels = requiredModels.filter((id) => !installedModels.includes(id));
    const runtime = portOpen ? await getOllamaPsState() : {
      activeModelCount: 0,
      activeModels: [],
      processorSummary: "未启动",
      acceleration: "idle",
      usesGpu: false,
    };
    const zip = findOllamaPackZip();
    return {
      ready: !!(exe && fssync.existsSync(exe)) && missingModels.length === 0,
      root,
      exe,
      modelsRoot: getOllamaModelsRoot(),
      portOpen,
      requiredModels,
      installedModels,
      missingModels,
      activeModelCount: runtime.activeModelCount,
      activeModels: runtime.activeModels,
      processorSummary: runtime.processorSummary,
      acceleration: runtime.acceleration,
      usesGpu: runtime.usesGpu,
      zip,
      zipHint: formatZipHint(zip),
    };
  });

  ipcMain.handle("ensure-ollama", async () => {
    try {
      const started = await ensureOllama();
      const root = getOllamaRoot();
      const configPath = getPackagedConfigPath();
      const exe = getOllamaExecutable();
      const portOpen = await isPortOpen(11434);
      const requiredModels = parseConfiguredQualityModelIds(configPath);
      const installedModels = portOpen ? await getInstalledOllamaModelIds() : [];
      const missingModels = requiredModels.filter((id) => !installedModels.includes(id));
      const runtime = portOpen ? await getOllamaPsState() : {
        activeModelCount: 0,
        activeModels: [],
        processorSummary: "未启动",
        acceleration: "idle",
        usesGpu: false,
      };
      return {
        ok: !!started,
        ready: pathExists(exe) && missingModels.length === 0,
        portOpen,
        root,
        exe,
        modelsRoot: getOllamaModelsRoot(),
        requiredModels,
        installedModels,
        missingModels,
        activeModelCount: runtime.activeModelCount,
        activeModels: runtime.activeModels,
        processorSummary: runtime.processorSummary,
        acceleration: runtime.acceleration,
        usesGpu: runtime.usesGpu,
      };
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String(e.message) : String(e);
      return { ok: false, error: msg || "启动 Ollama 失败" };
    }
  });

  ipcMain.handle("pick-ollama-pack", async () => {
    const res = await dialog.showOpenDialog({
      properties: ["openFile"],
      filters: [{ name: "Ollama Pack", extensions: ["zip"] }],
      title: "选择 ollama_pack.zip",
    });
    if (res.canceled || !res.filePaths || res.filePaths.length === 0) return "";
    return res.filePaths[0];
  });

  ipcMain.handle("extract-ollama-pack", async (_evt, zipPath) => {
    try {
      const root = getOllamaRoot();
      await fs.mkdir(root, { recursive: true });
      const zip = zipPath || findOllamaPackZip();
      if (!zip) return { ok: false, error: "ollama_pack.zip not found" };
      await extractZip(zip, root);
      await normalizeExtractedOllama(root);
      const exe = path.join(root, "ollama.exe");
      if (!fssync.existsSync(exe)) {
        return { ok: false, root, zip, zipHint: formatZipHint(zip), error: "导入完成，但未发现 ollama.exe（包结构不正确）" };
      }
      // After importing the pack, try starting Ollama immediately so users don't need to restart the app.
      const started = await ensureOllama().catch(() => false);
      return {
        ok: true,
        root,
        modelsRoot: getOllamaModelsRoot(),
        zip,
        zipHint: formatZipHint(zip),
        started,
        portOpen: await isPortOpen(11434),
      };
    } catch (e) {
      const msg = e && typeof e === "object" && "message" in e ? String(e.message) : String(e);
      return { ok: false, error: msg || "导入失败" };
    }
  });

  // Start services first so preload gets the correct GUI_API_BASE.
  await ensureBackend().catch(() => false);
  if (shouldUseLocalWindowsProductFlow()) {
    ensureOllama().catch(() => {});
  }
  if (isDev) {
    try {
      const devUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
      const m = String(devUrl).match(/:(\d+)\b/);
      const port = m ? Number(m[1]) : 5173;
      await waitForPortOpen(port, 15000);
    } catch {
      // ignore
    }
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    appShuttingDown = true;
    clearBackendRestartTimer();
    if (backendProc) {
      try {
        backendProc.kill();
      } catch {}
    }
    if (ollamaProc) {
      try {
        ollamaProc.kill();
      } catch {}
    }
    app.quit();
  }
});

app.on("before-quit", () => {
  appShuttingDown = true;
  clearBackendRestartTimer();
});
