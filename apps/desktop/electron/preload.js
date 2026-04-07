const { contextBridge, ipcRenderer } = require("electron");
const isDev = !!process.env.VITE_DEV_SERVER_URL;
const e2eEnabled = String(process.env.YGF_E2E_SMOKE || process.env.YGF_E2E_ENABLED || "").trim() === "1";

contextBridge.exposeInMainWorld("bridge", {
  apiBase: process.env.GUI_API_BASE || "http://127.0.0.1:5175",
  apiToken: process.env.YGF_API_TOKEN || "",
  authRequest: (payload) => ipcRenderer.invoke("auth-request", payload),
  runtimeInfo: {
    platform: process.platform,
    packaged: !isDev,
    windowsPackagedProduct: !isDev && process.platform === "win32",
    productEdition: process.env.YGF_PRODUCT_EDITION || "lite",
    e2eEnabled,
  },
  selectDirectory: () => ipcRenderer.invoke("select-directory"),
  getDefaultOutputsRoot: () => ipcRenderer.invoke("get-default-outputs-root"),
  openPath: (targetPath) => ipcRenderer.invoke("open-path", targetPath),
  ensureDir: (baseDir, relativeDir) => ipcRenderer.invoke("ensure-dir", baseDir, relativeDir),
  writeFile: (baseDir, relativePath, bytes) => ipcRenderer.invoke("write-file", { baseDir, relativePath, bytes }),
  getDeviceCode: () => ipcRenderer.invoke("get-device-code"),
  getDeviceIdentity: () => ipcRenderer.invoke("get-device-identity"),
  getRuntimeStatus: () => ipcRenderer.invoke("get-runtime-status"),
  restartBackend: () => ipcRenderer.invoke("restart-backend"),
  collectDiagnosticsBundle: () => ipcRenderer.invoke("collect-diagnostics-bundle"),
  getModelStatus: () => ipcRenderer.invoke("get-model-status"),
  pickModelPack: () => ipcRenderer.invoke("pick-model-pack"),
  extractModelPack: (zipPath) => ipcRenderer.invoke("extract-model-pack", zipPath),
  getOllamaStatus: () => ipcRenderer.invoke("get-ollama-status"),
  ensureOllama: () => ipcRenderer.invoke("ensure-ollama"),
  pickOllamaPack: () => ipcRenderer.invoke("pick-ollama-pack"),
  extractOllamaPack: (zipPath) => ipcRenderer.invoke("extract-ollama-pack", zipPath),
  ...(e2eEnabled
    ? {
        readFileForE2E: (targetPath) => ipcRenderer.invoke("e2e-read-local-file", targetPath),
      }
    : {}),
});


