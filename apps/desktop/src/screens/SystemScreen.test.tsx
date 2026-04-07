// @vitest-environment jsdom

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SystemProvider } from "../app/contexts/SystemContext";
import { SystemScreen } from "./SystemScreen";

function buildContext(overrides: Record<string, any> = {}) {
  return {
    health: "ok",
    hardware: { gpu_name: "", tier: "normal", device_policy: { gpu_effective: false } },
    loadingBoot: false,
    onBootstrap: vi.fn(),
    mode: "lite",
    requireLocalPacks: true,
    modelsReady: false,
    modelsRoot: "",
    modelsZipHint: "",
    missingLabels: [],
    modelsError: "",
    modelsImporting: false,
    onPickModels: vi.fn(),
    ollamaReady: false,
    ollamaPortOpen: false,
    ollamaRoot: "",
    ollamaModelsRoot: "",
    ollamaZipHint: "",
    ollamaProcessorSummary: "",
    ollamaAcceleration: "idle",
    ollamaActiveModels: [],
    ollamaUsesGpu: false,
    ollamaError: "",
    ollamaImporting: false,
    ollamaStarting: false,
    ollamaLoading: false,
    onPickOllama: vi.fn(),
    onEnsureOllama: vi.fn(),
    onRefreshOllamaStatus: vi.fn(),
    runtimeStatus: null,
    runtimeLoading: false,
    runtimeError: "",
    backendRestarting: false,
    onRefreshRuntimeStatus: vi.fn(),
    onRestartBackend: vi.fn(),
    devToolsEnabled: false,
    authUserEmail: "",
    authStatusText: "",
    authLicenseExpireAt: "",
    authLicenseLoading: false,
    onAuthLogout: vi.fn(),
    canRenewInApp: false,
    openRenewalModal: vi.fn(),
    ...overrides,
  };
}

describe("SystemScreen", () => {
  it("shows lite system guidance and supports re-bootstrap", async () => {
    const user = userEvent.setup();
    const ctx = buildContext();

    render(
      <SystemProvider value={ctx}>
        <SystemScreen />
      </SystemProvider>,
    );

    expect(screen.getByText("轻量模式无需额外资源。切换到质量模式后，这里会提示需要的资源。")).toBeInTheDocument();
    expect(screen.queryByText("本地大模型")).not.toBeInTheDocument();

    await user.click(screen.getByText("重新检测"));

    expect(screen.getByText("当前模式：轻量")).toBeInTheDocument();
    expect(ctx.onBootstrap).toHaveBeenCalled();
  });

  it("shows backend unavailable state and hardware acceleration summaries", () => {
    const ctx = buildContext({
      health: "down",
      hardware: {
        gpu_name: "RTX 4090",
        gpu_vram_gb: 24,
        tier: "high",
        device_policy: { gpu_effective: true },
      },
      ollamaReady: true,
      ollamaPortOpen: true,
      ollamaAcceleration: "gpu",
      ollamaActiveModels: ["qwen2.5"],
      mode: "quality",
      requireLocalPacks: false,
    });

    render(
      <SystemProvider value={ctx}>
        <SystemScreen />
      </SystemProvider>,
    );

    expect(screen.getByText("后端不可用")).toBeInTheDocument();
    expect(screen.getByText("当前模式：质量")).toBeInTheDocument();
    expect(screen.getByText("显卡")).toBeInTheDocument();
    expect(screen.getByText("24 GB")).toBeInTheDocument();
    expect(screen.getByText("RTX 4090")).toBeInTheDocument();
    expect(screen.getByText("可用 GPU 加速")).toBeInTheDocument();
    expect(screen.getByText("本地大模型运行中")).toBeInTheDocument();
    expect(screen.getByText("GPU")).toBeInTheDocument();
    expect(screen.getByText("当前为开发环境：模型与服务由后端提供，无需手动导入。")).toBeInTheDocument();
  });

  it("shows auth account card and supports friendly logout confirmation", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      authUserEmail: "demo@example.com",
      authStatusText: "授权有效",
      authLicenseExpireAt: "2026-05-02T03:15:17Z",
    });

    render(
      <SystemProvider value={ctx}>
        <SystemScreen />
      </SystemProvider>,
    );

    expect(screen.getByText("账号与授权")).toBeInTheDocument();
    expect(screen.getByText("当前登录：demo@example.com")).toBeInTheDocument();
    expect(screen.getByText("授权有效")).toBeInTheDocument();
    expect(screen.getByText("到期时间：2026-05-02")).toBeInTheDocument();
    expect(screen.getByText("退出登录")).toBeInTheDocument();

    await user.click(screen.getByText("退出登录"));
    expect(screen.getByText("退出当前账号？")).toBeInTheDocument();

    await user.click(screen.getByText("确认退出"));
    expect(ctx.onAuthLogout).toHaveBeenCalled();
  });

  it("shows quality resource readiness and runtime details", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      mode: "quality",
      requireLocalPacks: true,
      modelsReady: false,
      missingLabels: ["质量模型包", "术语包"],
      modelsError: "模型目录不可读",
      modelsZipHint: "quality-pack.zip",
      ollamaReady: false,
      ollamaPortOpen: false,
      ollamaZipHint: "ollama-pack.zip",
      ollamaProcessorSummary: "Apple M3 Max",
      ollamaAcceleration: "mixed",
      ollamaActiveModels: ["llama3"],
      ollamaError: "Ollama 未安装",
      runtimeError: "runtime manifest missing",
      runtimeStatus: {
        packagedWindowsProduct: true,
        manifestExists: false,
        backendExeExists: true,
        qualityWorkerExeExists: false,
        packagedConfigExists: true,
        modelsPackZipExists: true,
        ollamaPackZipExists: false,
        ollamaExeExists: false,
        runtimeChecks: [{ key: "worker", label: "质量 Worker", exists: false }],
      },
    });

    render(
      <SystemProvider value={ctx}>
        <SystemScreen />
      </SystemProvider>,
    );

    expect(screen.getByText("未就绪")).toBeInTheDocument();
    expect(screen.getByText("已发现资源包：quality-pack.zip")).toBeInTheDocument();
    expect(screen.getByText("缺失：质量模型包、术语包")).toBeInTheDocument();
    expect(screen.getByText("模型目录不可读")).toBeInTheDocument();
    expect(screen.getByText("已发现 Ollama 包：ollama-pack.zip")).toBeInTheDocument();
    expect(screen.getByText("处理器：Apple M3 Max")).toBeInTheDocument();
    expect(screen.getByText("活跃模型：llama3")).toBeInTheDocument();
    expect(screen.getByText("Ollama 未安装")).toBeInTheDocument();
    expect(screen.getByText("runtime manifest missing")).toBeInTheDocument();
    expect(screen.getByText("当前口径：Windows 打包产品态")).toBeInTheDocument();
    expect(screen.getByText("缺少运行时资源：质量 Worker")).toBeInTheDocument();

    await user.click(screen.getByText("导入质量模型…"));
    await user.click(screen.getByText("导入 Ollama…"));
    await user.click(screen.getByText("启动 Ollama"));
    await user.click(screen.getByText("刷新状态"));
    await user.click(screen.getByText("刷新链路"));
    await user.click(screen.getByText("重启 backend"));

    expect(ctx.onPickModels).toHaveBeenCalled();
    expect(ctx.onPickOllama).toHaveBeenCalled();
    expect(ctx.onEnsureOllama).toHaveBeenCalled();
    expect(ctx.onRefreshOllamaStatus).toHaveBeenCalled();
    expect(ctx.onRefreshRuntimeStatus).toHaveBeenCalled();
    expect(ctx.onRestartBackend).toHaveBeenCalled();
  });
});
