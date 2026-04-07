const fs = require("fs");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");
const electronBinary = require("electron");

function resolveRepoRoot() {
  return path.resolve(__dirname, "..", "..", "..");
}

function ensureFileExists(targetPath) {
  if (!fs.existsSync(targetPath)) {
    throw new Error(`smoke file not found: ${targetPath}`);
  }
}

function httpGet(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({ statusCode: res.statusCode || 0, body: Buffer.concat(chunks).toString("utf-8") }));
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`timeout for ${url}`));
    });
    req.on("error", reject);
  });
}

async function ensureServiceReady(url, name) {
  try {
    const res = await httpGet(url);
    if (res.statusCode >= 200 && res.statusCode < 500) return;
  } catch {}
  throw new Error(`${name} not ready: ${url}`);
}

async function main() {
  const desktopRoot = path.resolve(__dirname, "..");
  const repoRoot = resolveRepoRoot();
  const smokeFile =
    process.env.YGF_E2E_SMOKE_FILE ||
    path.join(repoRoot, "reports", "lite_phase1", "golden20_lite_1min", "clips", "golden20_001.mp4");
  ensureFileExists(smokeFile);

  const devUrl = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173";
  const backendUrl = process.env.GUI_API_BASE_OVERRIDE || "http://127.0.0.1:5175";
  await ensureServiceReady(devUrl, "renderer dev server");
  await ensureServiceReady(`${backendUrl}/api/health`, "desktop backend");

  const childEnv = {
    ...process.env,
    VITE_DEV_SERVER_URL: devUrl,
    YGF_E2E_SMOKE: "1",
    YGF_E2E_SMOKE_FILE: smokeFile,
  };
  delete childEnv.ELECTRON_RUN_AS_NODE;

  const child = spawn(electronBinary, ["."], {
    cwd: desktopRoot,
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
  });

  let output = "";
  const timeout = setTimeout(() => {
    child.kill("SIGTERM");
  }, 90_000);

  const onChunk = (chunk) => {
    const text = String(chunk || "");
    output += text;
    process.stdout.write(text);
  };
  child.stdout.on("data", onChunk);
  child.stderr.on("data", onChunk);

  const exitCode = await new Promise((resolve) => {
    child.on("close", (code) => resolve(Number(code || 0)));
  });
  clearTimeout(timeout);

  const match = output.match(/YGF_E2E_SMOKE_RESULT=(\{.*\})/);
  if (!match) {
    throw new Error(`smoke result not found in electron output (exit=${exitCode})`);
  }
  const payload = JSON.parse(match[1]);
  if (!payload?.ok) {
    throw new Error(payload?.error || "electron upload smoke failed");
  }
  console.log(`[electron-upload-smoke] uploaded=${payload.uploadedName} tasks=${payload.taskCount}`);
}

main().catch((err) => {
  console.error(`[electron-upload-smoke] ${err?.message || String(err)}`);
  process.exit(1);
});
