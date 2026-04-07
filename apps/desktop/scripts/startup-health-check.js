const { execSync } = require("child_process");
const { summarizeBackendListeners, summarizeDockerBackendContainers } = require("../electron/backendHealthHelpers");

function run(command) {
  try {
    return execSync(command, { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] }) || "";
  } catch {
    return "";
  }
}

function main() {
  if (process.platform === "win32") {
    console.log("[startup-health] skip stale backend audit on win32.");
    return;
  }

  const processListText = run('pgrep -af "python3 -m backend.app|Python -m backend.app|backend.app"');
  if (!String(processListText || "").trim()) {
    console.log("[startup-health] no local backend.app processes detected.");
  } else {
    const rawItems = String(processListText || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    const listenByPid = {};
    for (const line of rawItems) {
      const match = line.match(/^(\d+)\s+/);
      if (!match) continue;
      const pid = Number(match[1]);
      if (!Number.isFinite(pid) || pid <= 0) continue;
      listenByPid[pid] = run(`lsof -nP -p ${pid} -iTCP -sTCP:LISTEN`);
    }

    const summary = summarizeBackendListeners({
      processListText,
      listenByPid,
      preferredPort: 5175,
    });

    if (!summary.hasNearbyConflicts) {
      console.log("[startup-health] no stale local backend listeners near 5175 detected.");
    } else {
      console.warn("[startup-health] detected local backend.app listeners near desktop backend ports:");
      for (const item of summary.nearbyListeners) {
        console.warn(`- pid=${item.pid} ports=${item.ports.join(",")} cmd=${item.command}`);
      }
      if (summary.hasPreferredPortConflict) {
        console.warn("[startup-health] preferred port 5175 is occupied by a local backend.app process; Electron may connect to the wrong backend.");
      }
      console.warn("[startup-health] recommended cleanup: pkill -f \"python3 -m backend.app\" || true");
    }
  }

  const dockerPsText = run("docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'");
  const dockerSummary = summarizeDockerBackendContainers({
    dockerPsText,
    preferredPort: 5175,
    expectedNames: ["yizhi-backend-1"],
  });
  if (!dockerSummary.hasStaleContainers) {
    console.log("[startup-health] no stale docker backend containers detected.");
    return;
  }

  console.warn("[startup-health] detected stale docker backend containers:");
  for (const item of dockerSummary.stale) {
    console.warn(`- name=${item.name} status=${item.status} image=${item.image} ports=${item.ports || "-"}`);
  }
  if (dockerSummary.hasPublishedPreferredConflict) {
    console.warn("[startup-health] preferred port 5175 is also published by a stale docker backend container; Electron may connect to the wrong backend.");
  }
  console.warn("[startup-health] recommended cleanup: docker rm -f yizhi-backend-lite yizhi-backend-dev");
}

main();
