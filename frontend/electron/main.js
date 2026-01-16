// Minimal Electron entry for Vite dev + packaged build
const { app, BrowserWindow, dialog, ipcMain, shell, protocol } = require("electron");
const fs = require("fs/promises");
const path = require("path");

process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";

const isDev = !!process.env.VITE_DEV_SERVER_URL;

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
  }
}

app.whenReady().then(() => {
  // localfile:///absolute/path/to/file.mp4
  protocol.registerFileProtocol("localfile", (request, callback) => {
    try {
      const url = String(request.url || "");
      const raw = url.replace(/^localfile:\/\//, "");
      const decoded = decodeURIComponent(raw);
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

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

