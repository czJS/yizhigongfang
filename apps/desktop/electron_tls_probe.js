const { app, net, session } = require("electron");

const target = process.argv[2] || "https://auth.miaoyichuhai.com/api/health";
const disablePostQuantumKyber = process.env.DISABLE_PQ === "1";
const method = String(process.env.PROBE_METHOD || "GET").trim().toUpperCase();
const body = String(process.env.PROBE_BODY || "");
const trustedCompatHosts = Array.from(
  new Set(
    String(process.env.PROBE_TRUSTED_COMPAT_HOSTS || "8.149.245.13")
      .split(",")
      .map((item) => String(item || "").trim())
      .filter(Boolean)
  )
);

if (disablePostQuantumKyber) {
  app.commandLine.appendSwitch("disable-features", "PostQuantumKyber");
}

app.whenReady().then(() => {
  session.defaultSession.setCertificateVerifyProc((request, callback) => {
    if (trustedCompatHosts.includes(String(request?.hostname || ""))) {
      callback(0);
      return;
    }
    callback(-3);
  });
  app.on("certificate-error", (event, _webContents, url, _error, _certificate, callback) => {
    try {
      const parsed = new URL(String(url || ""));
      if (trustedCompatHosts.includes(parsed.hostname)) {
        event.preventDefault();
        callback(true);
        return;
      }
    } catch {}
    callback(false);
  });

  const req = net.request({ url: target, method });
  if (body) {
    req.setHeader("Content-Type", "application/json");
    req.write(body);
  }
  req.on("response", (res) => {
    let body = "";
    res.on("data", (chunk) => {
      body += chunk.toString();
    });
    res.on("end", () => {
      console.log(JSON.stringify({
        ok: true,
        statusCode: res.statusCode,
        disablePostQuantumKyber,
        body,
      }));
      app.quit();
    });
  });
  req.on("error", (err) => {
    console.error(JSON.stringify({
      ok: false,
      disablePostQuantumKyber,
      code: err?.code || "",
      message: err?.message || String(err),
    }));
    app.exit(1);
  });
  req.end();
});
