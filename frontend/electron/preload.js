const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bridge", {
  apiBase: process.env.GUI_API_BASE || "http://127.0.0.1:5175",
  selectDirectory: () => ipcRenderer.invoke("select-directory"),
  getCwd: () => ipcRenderer.invoke("get-cwd"),
  getProjectRoot: () => ipcRenderer.invoke("get-project-root"),
  openPath: (targetPath) => ipcRenderer.invoke("open-path", targetPath),
  ensureDir: (baseDir, relativeDir) => ipcRenderer.invoke("ensure-dir", baseDir, relativeDir),
  writeFile: (baseDir, relativePath, bytes) => ipcRenderer.invoke("write-file", { baseDir, relativePath, bytes }),
});


