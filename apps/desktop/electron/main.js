// Minimal Electron entry for Vite dev + packaged build
const { app, BrowserWindow, dialog, ipcMain, shell, protocol, Menu } = require("electron");
const { spawn, execSync } = require("child_process");
const http = require("http");
const net = require("net");
const os = require("os");

const LICENSE_CDKEY = "123123";
const LICENSE_MACS = ["00-D8-61-6F-79-94", "FC-9D-05-25-82-B5"];

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
const fs = require("fs/promises");
const fssync = require("fs");
const path = require("path");

process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";

const isDev = !!process.env.VITE_DEV_SERVER_URL;

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
protocol.registerSchemesAsPrivileged([
  {
    scheme: "localfile",
    privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
  },
]);

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

  if (isDev) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL);
    win.webContents.openDevTools({ mode: "detach" });
  } else {
    const indexHtml = path.join(__dirname, "../dist/index.html");
    win.loadFile(indexHtml);
    win.setMenuBarVisibility(false);
  }
}

function getModelsRoot() {
  return path.join(app.getPath("userData"), "models");
}

function getOllamaRoot() {
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
  return path.join(getModelsRoot(), "ollama_models");
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

async function checkModels(root) {
  const r = root || getModelsRoot();
  const required = [
    {
      key: "whisperx",
      label: "WhisperX (medium)",
      path: path.join(r, "whisperx", "models--Systran--faster-whisper-medium", "model.bin"),
    },
    {
      key: "align",
      label: "对齐模型",
      path: path.join(
        r,
        "whisperx",
        "models--jonatasgrosman--wav2vec2-large-xlsr-53-chinese-zh-cn",
        "pytorch_model.bin"
      ),
    },
    {
      key: "tts",
      label: "TTS",
      path: path.join(r, "tts", "tts", "tts_models--en--ljspeech--tacotron2-DDC", "model_file.pth"),
    },
  ];
  const missing = [];
  for (const item of required) {
    try {
      await fs.stat(item.path);
    } catch {
      missing.push({ key: item.key, label: item.label, path: item.path });
    }
  }
  return { ready: missing.length === 0, root: r, missing };
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
    if (names.includes("whisperx") || names.includes("tts") || names.includes("ollama_models")) return root;
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
  const wanted = ["whisperx", "tts", "ollama_models"];
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
    const socket = net.createConnection({ port, host: "127.0.0.1" }, () => {
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
function getBackendExePath() {
  const override = process.env.YGF_BACKEND_EXE;
  if (override && fssync.existsSync(override)) return override;

  // Packaged: backend_server.exe is bundled into resources root via electron-builder extraResources.
  const packagedExe = path.join(process.resourcesPath, "backend_server.exe");
  if (fssync.existsSync(packagedExe)) return packagedExe;

  // Dev: use repo dist/backend_server.exe (relative to frontend/electron).
  // __dirname = <repo>/frontend/electron
  const devExe = path.resolve(__dirname, "..", "..", "dist", "backend_server.exe");
  if (fssync.existsSync(devExe)) return devExe;

  return "";
}

function getPythonExe() {
  return process.env.YGF_PYTHON || "python";
}

function getRepoRoot() {
  // __dirname = <repo>/frontend/electron
  return path.resolve(__dirname, "..", "..");
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

async function getBackendRuntime() {
  try {
    const data = await httpGetJson("http://127.0.0.1:5175/api/health", 900);
    return data && data.runtime ? data.runtime : null;
  } catch {
    return null;
  }
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

async function ensureBackend() {
  const ok = await isPortOpen(5175);
  if (ok) {
    // Common Windows failure mode:
    // - previous backend_server.exe still listening on 5175 (or half-updated during install)
    // - we "reuse" it and UI ends up in lite-only / wrong config.
    // Prefer to evict stale backend processes we don't own and start a fresh one.
    if (process.platform === "win32" && !backendProc) {
      // If the existing backend reports a different YGF_APP_ROOT, it's definitely stale/wrong.
      const rt = await getBackendRuntime();
      const reportedRoot = rt && typeof rt.YGF_APP_ROOT === "string" ? rt.YGF_APP_ROOT : "";
      if (reportedRoot && path.resolve(reportedRoot) === path.resolve(process.resourcesPath)) {
        logMain(`backend already running with expected YGF_APP_ROOT=${reportedRoot}; reusing.`);
        return true;
      }
      const pid = findListeningPidWin32(5175);
      const name = pid ? getProcessImageNameWin32(pid) : "";
      if (pid && /backend_server\.exe/i.test(name || "")) {
        logMain(
          `backend port 5175 already in use by pid=${pid} (${name})` +
            (reportedRoot ? ` YGF_APP_ROOT=${reportedRoot}` : "") +
            "; killing and restarting..."
        );
        killPidTreeWin32(pid);
        await waitForPortClosed(5175, 6000);
      } else {
        // Unknown owner (or could not resolve). Keep existing to avoid killing unrelated services.
        logMain(`backend port 5175 already open (pid=${pid || "?"} name=${name || "?"}); reusing.`);
        return true;
      }
    } else {
      return true;
    }
  }
  const env = { ...process.env, YGF_MODELS_ROOT: getModelsRoot() };
  // Ensure backend resolves repo_root/scripts/config/assets against packaged resources.
  env.YGF_APP_ROOT = process.resourcesPath;
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
    // Packaged app uses local ollama process, not docker hostname.
    env.YGF_LLM_ENDPOINT = "http://127.0.0.1:11434/v1";
  }
  try {
    if (isDev) {
      const py = getPythonExe();
      const cwd = getRepoRoot();
      logMain(`spawn backend dev: ${py} -m backend.app (cwd=${cwd})`);
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
    });
    backendProc.on("exit", (code, signal) => {
      logMain(`backend proc exit: code=${code} signal=${signal || ""}`);
      backendProc = null;
    });
  } catch (e) {
    logMain(`ensureBackend exception: ${e && e.message ? e.message : String(e)}`);
    backendProc = null;
    return false;
  }
  return await waitForPortOpen(5175);
}

let ollamaProc = null;
async function ensureOllama() {
  const ok = await isPortOpen(11434);
  if (ok) return true;
  const exe = path.join(getOllamaRoot(), "ollama.exe");
  if (!fssync.existsSync(exe)) return false;
  const env = { ...process.env, OLLAMA_MODELS: getOllamaModelsRoot() };
  try {
    logMain(`spawn ollama: ${exe} serve (models=${env.OLLAMA_MODELS || ""})`);
    ollamaProc = spawn(exe, ["serve"], { cwd: getOllamaRoot(), env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
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

app.whenReady().then(() => {
  // localfile:///absolute/path/to/file.mp4
  if (!isDev) {
    Menu.setApplicationMenu(null);
  }
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

  ipcMain.handle("get-cwd", async () => {
    try {
      return process.cwd();
    } catch {
      return "";
    }
  });

  // Repo root (frontend/electron/../..) —用于在未指定输出目录时，默认打开项目 outputs 目录
  ipcMain.handle("get-project-root", async () => {
    try {
      return path.resolve(__dirname, "../..");
    } catch {
      return "";
    }
  });

  ipcMain.handle("open-path", async (_evt, targetPath) => {
    if (!targetPath) return { ok: false, error: "路径为空" };
    const err = await shell.openPath(String(targetPath));
    if (err) return { ok: false, error: err };
    return { ok: true };
  });

  ipcMain.handle("get-device-code", async () => {
    const macs = getMacAddresses();
    return macs[0] || "UNKNOWN";
  });

  ipcMain.handle("verify-license", async (_evt, cdkey) => {
    const macs = getMacAddresses();
    const deviceCode = macs[0] || "UNKNOWN";
    const cdkeyOk = String(cdkey || "").trim() === LICENSE_CDKEY;
    const macOk = macs.some((m) => LICENSE_MACS.includes(m));
    return { ok: cdkeyOk && macOk, deviceCode };
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

  ipcMain.handle("get-model-status", async () => {
    const root = getModelsRoot();
    // Best-effort: if user previously extracted with an extra directory layer,
    // normalize it so status reflects actual files on disk.
    await normalizeExtractedModels(root);
    const checked = await checkModels(root);
    const zip = findModelsPackZip();
    return { ready: checked.ready, root, zip, zipHint: formatZipHint(zip), missing: checked.missing };
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
    const exe = path.join(root, "ollama.exe");
    const portOpen = await isPortOpen(11434);
    const zip = findOllamaPackZip();
    return {
      ready: !!(exe && fssync.existsSync(exe)),
      root,
      modelsRoot: getOllamaModelsRoot(),
      portOpen,
      zip,
      zipHint: formatZipHint(zip),
    };
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

  createWindow();

  // Start services (do NOT auto-import models).
  ensureOllama().catch(() => {});
  ensureBackend().catch(() => {});

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
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

