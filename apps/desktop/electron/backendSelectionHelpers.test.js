import { describe, expect, it } from "vitest";
import backendSelectionHelpers from "./backendSelectionHelpers";

const { isRepoBackendRuntime, isDockerRepoBackendRuntime, chooseDevBackendPort } = backendSelectionHelpers;

describe("backendSelectionHelpers", () => {
  it("recognizes current repo backend instances", () => {
    expect(
      isRepoBackendRuntime(
        { repo_root: "/Users/chengzheng/Desktop/译制工坊" },
        "/Users/chengzheng/Desktop/译制工坊",
      ),
    ).toBe(true);

    expect(
      isRepoBackendRuntime(
        { repo_root: "/app" },
        "/Users/chengzheng/Desktop/译制工坊",
      ),
    ).toBe(false);
  });

  it("recognizes docker repo backend runtime from config path", () => {
    expect(
      isDockerRepoBackendRuntime({
        repo_root: "/app",
        config_path: "/app/configs/quality.yaml",
      }),
    ).toBe(true);

    expect(
      isDockerRepoBackendRuntime({
        repo_root: "/app",
        config_path: "/app/configs/defaults.yaml",
      }),
    ).toBe(true);

    expect(
      isDockerRepoBackendRuntime(
        {
          repo_root: "/app",
          config_path: "/app/configs/defaults.yaml",
        },
        ["quality.yaml"],
      ),
    ).toBe(false);

    expect(
      isDockerRepoBackendRuntime(
        {
          repo_root: "/app",
          config_path: "/app/configs/quality.yaml",
        },
        ["quality.yaml"],
      ),
    ).toBe(true);

    expect(
      isDockerRepoBackendRuntime({
        repo_root: "/app",
        config_path: "/app/configs/online.yaml",
      }),
    ).toBe(false);
  });

  it("reuses preferred port when repo backend is already running", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      preferredPortOpen: true,
      preferredRuntime: { repo_root: "/Users/chengzheng/Desktop/译制工坊" },
    });

    expect(decision).toEqual({ port: 5175, reason: "preferred-repo" });
  });

  it("reuses preferred port when docker backend is already running", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      expectedDockerConfigBasenames: ["quality.yaml"],
      preferredPortOpen: true,
      preferredRuntime: { repo_root: "/app", config_path: "/app/configs/quality.yaml" },
    });

    expect(decision).toEqual({ port: 5175, reason: "preferred-docker-repo" });
  });

  it("rejects stale docker backend when config does not match expected runtime", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      expectedDockerConfigBasenames: ["quality.yaml"],
      preferredPortOpen: true,
      preferredRuntime: { repo_root: "/app", config_path: "/app/configs/defaults.yaml" },
      nearbyPorts: [{ port: 5176, open: false }],
    });

    expect(decision).toEqual({ port: 5176, reason: "preferred-other-backend-first-free" });
  });

  it("prefers an already-running nearby repo backend over the first free port", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      expectedDockerConfigBasenames: ["quality.yaml"],
      preferredPortOpen: true,
      preferredRuntime: { repo_root: "/some/other/project" },
      nearbyPorts: [
        { port: 5176, open: false },
        { port: 5177, open: true, runtime: { repo_root: "/Users/chengzheng/Desktop/译制工坊" } },
      ],
    });

    expect(decision).toEqual({ port: 5177, reason: "nearby-repo" });
  });

  it("falls back to the first free nearby port when preferred is occupied by another backend", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      expectedDockerConfigBasenames: ["quality.yaml"],
      preferredPortOpen: true,
      preferredRuntime: { repo_root: "/some/other/project" },
      nearbyPorts: [
        { port: 5176, open: false },
        { port: 5177, open: true, runtime: { repo_root: "/another/project" } },
      ],
    });

    expect(decision).toEqual({ port: 5176, reason: "preferred-other-backend-first-free" });
  });

  it("returns the preferred port directly when it is free", () => {
    const decision = chooseDevBackendPort({
      preferredPort: 5175,
      isDev: true,
      expectedRoot: "/Users/chengzheng/Desktop/译制工坊",
      expectedDockerConfigBasenames: ["quality.yaml"],
      preferredPortOpen: false,
      preferredRuntime: null,
    });

    expect(decision).toEqual({ port: 5175, reason: "preferred-free" });
  });
});
