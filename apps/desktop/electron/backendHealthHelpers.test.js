import { describe, expect, it } from "vitest";
import backendHealthHelpers from "./backendHealthHelpers";

const { parseBackendProcessList, parseListeningPorts, summarizeBackendListeners } = backendHealthHelpers;
const { parseDockerContainerList, summarizeDockerBackendContainers } = backendHealthHelpers;

describe("backendHealthHelpers", () => {
  it("parses backend process list output", () => {
    const items = parseBackendProcessList(`
58292 /Library/.../Python -m backend.app
79533 /Library/.../Python -m backend.app
`);

    expect(items).toEqual([
      { pid: 58292, command: "/Library/.../Python -m backend.app" },
      { pid: 79533, command: "/Library/.../Python -m backend.app" },
    ]);
  });

  it("extracts unique listening ports from lsof output", () => {
    const ports = parseListeningPorts(`
Python    58292 chengzheng    4u  IPv4 0x1 0t0 TCP 127.0.0.1:5175 (LISTEN)
Python    58292 chengzheng    5u  IPv4 0x2 0t0 TCP 127.0.0.1:5175 (LISTEN)
Python    58292 chengzheng    6u  IPv4 0x3 0t0 TCP 127.0.0.1:5180 (LISTEN)
`);

    expect(ports).toEqual([5175, 5180]);
  });

  it("summarizes stale backend listeners around the preferred port", () => {
    const summary = summarizeBackendListeners({
      processListText: `
58292 /Library/.../Python -m backend.app
79533 /Library/.../Python -m backend.app
`,
      listenByPid: {
        58292: "Python 58292 user 4u IPv4 0x1 0t0 TCP 127.0.0.1:5175 (LISTEN)",
        79533: "Python 79533 user 4u IPv4 0x2 0t0 TCP 127.0.0.1:5176 (LISTEN)",
      },
      preferredPort: 5175,
    });

    expect(summary.hasNearbyConflicts).toBe(true);
    expect(summary.hasPreferredPortConflict).toBe(true);
    expect(summary.nearbyListeners).toHaveLength(2);
    expect(summary.preferredListeners[0]).toMatchObject({ pid: 58292, ports: [5175] });
  });

  it("ignores backend processes that are not listening near the desktop backend range", () => {
    const summary = summarizeBackendListeners({
      processListText: "60025 /Library/.../Python -m backend.app",
      listenByPid: {
        60025: "Python 60025 user 4u IPv4 0x1 0t0 TCP 127.0.0.1:6000 (LISTEN)",
      },
      preferredPort: 5175,
    });

    expect(summary.hasNearbyConflicts).toBe(false);
    expect(summary.hasPreferredPortConflict).toBe(false);
  });

  it("parses docker backend container rows", () => {
    const items = parseDockerContainerList(`
yizhi-backend-1\tUp 5 minutes\tyzh-backend:quality\t0.0.0.0:5175->5175/tcp
yizhi-backend-lite\tExited (137) 1 minute ago\tyzh-backend:quality-arm64\t
`);

    expect(items).toEqual([
      {
        name: "yizhi-backend-1",
        status: "Up 5 minutes",
        image: "yzh-backend:quality",
        ports: "0.0.0.0:5175->5175/tcp",
      },
      {
        name: "yizhi-backend-lite",
        status: "Exited (137) 1 minute ago",
        image: "yzh-backend:quality-arm64",
        ports: "",
      },
    ]);
  });

  it("flags stale docker backend containers and conflicting published ports", () => {
    const summary = summarizeDockerBackendContainers({
      dockerPsText: `
yizhi-backend-1\tUp 5 minutes\tyzh-backend:quality\t0.0.0.0:5175->5175/tcp
yizhi-backend-lite\tUp 3 hours\tyzh-backend:quality-arm64\t0.0.0.0:5175->5175/tcp
yizhi-backend-dev\tExited (255) 3 months ago\tyzh-backend:dev\t
`,
      preferredPort: 5175,
      expectedNames: ["yizhi-backend-1"],
    });

    expect(summary.hasStaleContainers).toBe(true);
    expect(summary.hasPublishedPreferredConflict).toBe(true);
    expect(summary.stale.map((item) => item.name)).toEqual(["yizhi-backend-lite", "yizhi-backend-dev"]);
  });

  it("does not treat exited docker containers as active preferred-port conflicts", () => {
    const summary = summarizeDockerBackendContainers({
      dockerPsText: `
yizhi-backend-1\tUp 5 minutes\tyzh-backend:quality\t0.0.0.0:5175->5175/tcp
yizhi-backend-dev\tExited (255) 3 months ago\tyzh-backend:dev\t0.0.0.0:5175->5175/tcp
`,
      preferredPort: 5175,
      expectedNames: ["yizhi-backend-1"],
    });

    expect(summary.hasStaleContainers).toBe(true);
    expect(summary.hasPublishedPreferredConflict).toBe(false);
  });
});
