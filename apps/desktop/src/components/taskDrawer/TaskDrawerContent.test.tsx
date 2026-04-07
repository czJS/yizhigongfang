// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskDrawerContent } from "./TaskDrawerContent";

const getQualityReportMock = vi.fn();
const getChsSrt2Mock = vi.fn();
const getEngSrtMock = vi.fn();
const putChsReviewSrtMock = vi.fn();
const putEngReviewSrtMock = vi.fn();

vi.mock("../../api", () => ({
  apiBase: "http://127.0.0.1:5175",
  getQualityReport: (...args: any[]) => getQualityReportMock(...args),
  getChsSrt2: (...args: any[]) => getChsSrt2Mock(...args),
  getEngSrt: (...args: any[]) => getEngSrtMock(...args),
  putChsReviewSrt: (...args: any[]) => putChsReviewSrtMock(...args),
  putEngReviewSrt: (...args: any[]) => putEngReviewSrtMock(...args),
}));

function buildBatch(taskOverrides: Record<string, any> = {}) {
  return {
    id: "batch-1",
    mode: "lite",
    params: { review_enabled: true },
    tasks: [
      {
        index: 1,
        inputName: "demo.mp4",
        inputPath: "/tmp/demo.mp4",
        state: "completed",
        taskId: "task-1",
        qualityPassed: false,
        qualityErrors: [],
        qualityWarnings: [],
        artifacts: [],
        ...taskOverrides,
      },
    ],
  } as any;
}

function renderDrawer(taskOverrides: Record<string, any> = {}, propsOverrides: Record<string, any> = {}) {
  const props = {
    batch: buildBatch(taskOverrides),
    taskIndex: 0,
    initialTab: "quality",
    onOpenOutput: vi.fn(),
    qualityGates: {},
    showLogs: false,
    logText: "",
    logLoading: false,
    onResume: vi.fn(),
    onRunReview: vi.fn(),
    onApplyReview: vi.fn(),
    onExportDiagnostic: vi.fn(),
    onCleanup: vi.fn(),
    onUpgradeToQuality: vi.fn(),
    onGoSystem: vi.fn(),
    ...propsOverrides,
  };
  const rendered = render(<TaskDrawerContent {...props} />);
  return { ...rendered, props };
}

function getButtonByText(label: string, pick: "first" | "last" = "first"): HTMLButtonElement {
  const matches = screen
    .getAllByRole("button")
    .filter((el) => el.textContent?.replace(/\s+/g, "") === label.replace(/\s+/g, ""));
  const button = pick === "last" ? matches[matches.length - 1] : matches[0];
  if (!button) throw new Error(`button not found: ${label}`);
  return button as HTMLButtonElement;
}

describe("TaskDrawerContent", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
    getQualityReportMock.mockReset();
    getChsSrt2Mock.mockReset();
    getEngSrtMock.mockReset();
    putChsReviewSrtMock.mockReset();
    putEngReviewSrtMock.mockReset();
    getQualityReportMock.mockResolvedValue({
      passed: false,
      errors: [],
      warnings: [],
      checks: { required_artifacts: { missing_required: ["tts_full.wav"] } },
    });
  });

  it("shows a lite self-fix action to resume from tts", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer({
      artifacts: [{ name: "eng.srt" }],
    });

    expect(await screen.findByText("补生成配音与成片（从配音继续）")).toBeInTheDocument();

    await user.click(getButtonByText("执行"));

    expect(props.onResume).toHaveBeenCalledWith("tts");
  });

  it("shows self-fix action to rebuild mux and embed from existing tts", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer({
      artifacts: [{ name: "tts_full.wav" }],
    });

    expect(await screen.findByText("只生成成片与硬字幕（无需重跑识别/翻译）")).toBeInTheDocument();

    await user.click(getButtonByText("执行"));

    expect(props.onApplyReview).toHaveBeenCalledWith("mux_embed", "base");
  });

  it("shows self-fix action to embed subtitles only", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer({
      artifacts: [{ name: "output_en.mp4" }],
    });

    expect(await screen.findByText("补封装硬字幕（无需重跑）")).toBeInTheDocument();

    await user.click(getButtonByText("执行"));

    expect(props.onApplyReview).toHaveBeenCalledWith("embed", "base");
  });

  it("shows self-fix action to retry from asr when subtitles are missing", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer({
      artifacts: [],
    });

    expect(await screen.findByText("重试（从识别开始）")).toBeInTheDocument();

    await user.click(getButtonByText("执行"));

    expect(props.onResume).toHaveBeenCalledWith("asr");
  });

  it("shows failure hints and lets users jump to system or export diagnostics", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer(
      {
        state: "failed",
        artifacts: [],
      },
      {
        logText: "ffmpeg not found\npermission denied",
      },
    );

    expect(await screen.findByText("可能原因（基于日志关键字推断）")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "去系统页检查模型/环境" }));
    await user.click(screen.getByRole("button", { name: "导出诊断包" }));

    expect(props.onGoSystem).toHaveBeenCalled();
    expect(props.onExportDiagnostic).toHaveBeenCalledWith({ includeMedia: false });
  });

  it("exports diagnostics with media from the log tab", async () => {
    const user = userEvent.setup();
    const { props } = renderDrawer(
      {
        artifacts: [{ name: "eng.srt" }, { name: "tts_full.wav" }],
      },
      {
        showLogs: true,
      },
    );

    await user.click(screen.getByRole("tab", { name: "日志" }));
    await user.click(screen.getByRole("checkbox", { name: "包含成片/音频" }));
    await user.click(getButtonByText("导出诊断包", "last"));

    expect(props.onExportDiagnostic).toHaveBeenLastCalledWith({ includeMedia: true });
  });

  it("opens sales modal from the lite upsell card", async () => {
    const user = userEvent.setup();
    renderDrawer({
      qualityWarnings: ["reading speed 1", "reading speed 2", "reading speed 3", "reading speed 4", "reading speed 5"],
    });

    await waitFor(() => expect(getButtonByText("联系销售开通")).toBeInTheDocument());
    await user.click(getButtonByText("联系销售开通"));

    expect(screen.getByText("联系销售开通质量模式")).toBeInTheDocument();
    expect(screen.getByText("[销售二维码占位]")).toBeInTheDocument();
  });

  it("expands full quality details and example groups", async () => {
    const user = userEvent.setup();
    getQualityReportMock.mockResolvedValue({
      passed: false,
      errors: [
        "error 1",
        "error 2",
        "error 3",
        "error 4",
      ],
      warnings: [
        "warning 1",
        "warning 2",
        "warning 3",
        "warning 4",
      ],
      checks: {
        required_artifacts: { missing_required: ["tts_full.wav"] },
        line_length: { hits_n: 1, hits: [{ idx: 9, text: "a very long subtitle line" }] },
        reading_speed: { hits_n: 1, hits: [{ idx: 5, text: "fast subtitle", cps: 23.4 }] },
      },
    });

    renderDrawer({
      state: "completed",
    });

    expect(await screen.findByText("结论：存在影响交付的问题，建议先处理后再交付。")).toBeInTheDocument();

    const expandButtons = await screen.findAllByRole("button", { name: /展开全部（4）/ });
    await user.click(expandButtons[0]);
    await user.click(expandButtons[1]);
    await user.click(screen.getByRole("button", { name: /查看示例/ }));

    expect(screen.getByText("error 4")).toBeInTheDocument();
    expect(screen.getByText("warning 4")).toBeInTheDocument();
    expect(screen.getByText("缺少关键产物（会影响交付）")).toBeInTheDocument();
    expect(screen.getByText("英文字幕单行过长（1）")).toBeInTheDocument();
    expect(screen.getByText("英文字幕阅读速度过快（1）")).toBeInTheDocument();
  });

  it("re-fetches quality report with regen when legacy messages are detected", async () => {
    getQualityReportMock
      .mockResolvedValueOnce({
        passed: false,
        errors: ["eng.srt reading speed too high (> 20.0 cps): 10 items"],
        warnings: [],
        checks: {},
      })
      .mockResolvedValueOnce({
        passed: false,
        errors: ["英文字幕阅读速度过快：发现 10 条（建议不超过 20.0 字符/秒）。"],
        warnings: [],
        checks: {},
      });

    renderDrawer({
      state: "completed",
    });

    expect(await screen.findByText("英文字幕阅读速度过快：发现 10 条（建议不超过 20.0 字符/秒）。")).toBeInTheDocument();
    await waitFor(() => expect(getQualityReportMock).toHaveBeenCalledTimes(2));
    expect(getQualityReportMock.mock.calls[0]).toEqual(["task-1"]);
    expect(getQualityReportMock.mock.calls[1]).toEqual(["task-1", { regen: true }]);
  });

  it("shows fallback diagnostics hint when delivery is incomplete but no one-click fix applies", async () => {
    getQualityReportMock.mockResolvedValue({
      passed: false,
      errors: [],
      warnings: [],
      checks: { required_artifacts: { missing_required: ["report.json"] } },
    });

    renderDrawer({
      state: "completed",
      artifacts: [
        { name: "eng.srt" },
        { name: "tts_full.wav" },
        { name: "output_en.mp4" },
        { name: "output_en_sub.mp4" },
      ],
    });

    expect(await screen.findByText("暂无可一键修复的项。你可以导出诊断包或查看日志定位原因。")).toBeInTheDocument();
  });

  it("shows completed hint when all deliverables are present", async () => {
    getQualityReportMock.mockResolvedValue({
      passed: true,
      errors: [],
      warnings: [],
      checks: { required_artifacts: { missing_required: [] } },
    });

    renderDrawer({
      state: "completed",
      qualityPassed: true,
      artifacts: [
        { name: "eng.srt" },
        { name: "tts_full.wav" },
        { name: "output_en.mp4" },
        { name: "output_en_sub.mp4" },
      ],
    });

    expect(await screen.findByText("当前任务产物齐全，无需修复。")).toBeInTheDocument();
  });

  it("supports review editing, merge undo and rerun from chs", async () => {
    getChsSrt2Mock.mockResolvedValue({
      content: "1\n00:00:00,000 --> 00:00:01,000\n你 好\n\n2\n00:00:01,000 --> 00:00:02,000\n世 界\n",
    });
    getEngSrtMock.mockResolvedValue({
      content: "1\n00:00:00,000 --> 00:00:01,000\nhello\n\n2\n00:00:01,000 --> 00:00:02,000\nworld\n",
    });
    const { props } = renderDrawer(
      {
        state: "completed",
      },
      {
        initialTab: "review",
      },
    );

    expect(await screen.findByText("审核：中英同页对比修改。支持将“上下文极短块”与相邻块合并。点击「更新成片」将始终从中文开始重跑（MT→TTS→合成→封装）。")).toBeInTheDocument();

    fireEvent.change(await screen.findByDisplayValue("你 好"), { target: { value: "你好呀" } });
    fireEvent.change(screen.getByDisplayValue("hello"), { target: { value: "hello there" } });

    fireEvent.click(screen.getAllByRole("button", { name: "并下一条" })[0]);
    expect(screen.getByRole("button", { name: "撤回合并" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "撤回合并" }));
    fireEvent.click(screen.getByRole("button", { name: /更新成片/ }));

    await waitFor(() => expect(putChsReviewSrtMock).toHaveBeenCalled());
    expect(putEngReviewSrtMock).toHaveBeenCalled();
    expect(putChsReviewSrtMock.mock.calls[0][1]).toContain("你好呀");
    expect(putEngReviewSrtMock.mock.calls[0][1]).toContain("hello there");
    expect(props.onRunReview).toHaveBeenCalledWith("chs");
  }, 20000);

  it("saves chinese review and continues translation from paused state", async () => {
    getChsSrt2Mock.mockResolvedValue({
      content: "1\n00:00:00,000 --> 00:00:01,000\n暂停稿\n",
    });
    getEngSrtMock.mockResolvedValue({
      content: "1\n00:00:00,000 --> 00:00:01,000\npaused draft\n",
    });
    const { props } = renderDrawer(
      {
        state: "paused",
        taskId: "task-paused",
      },
      {
        initialTab: "review",
      },
    );

    expect(await screen.findByText("中文审核：仅修改中文字幕。开启后会在 MT 前停在这里。支持将“上下文极短块”与相邻块合并。点击「保存并继续翻译」将从 MT 开始继续（MT→TTS→合成→封装）。")).toBeInTheDocument();
    expect(screen.queryByText("英文字幕（可编辑）")).not.toBeInTheDocument();

    fireEvent.change(await screen.findByDisplayValue("暂停稿"), { target: { value: "继续翻译稿" } });
    fireEvent.click(screen.getByRole("button", { name: /保存并继续翻译/ }));

    await waitFor(() => expect(putChsReviewSrtMock).toHaveBeenCalled());
    expect(putChsReviewSrtMock.mock.calls[0][1]).toContain("继续翻译稿");
    expect(putEngReviewSrtMock).not.toHaveBeenCalled();
    expect(props.onRunReview).toHaveBeenCalledWith("chs");
  }, 20000);
});
