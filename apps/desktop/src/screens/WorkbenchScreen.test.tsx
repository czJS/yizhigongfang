// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkbenchProvider } from "../app/contexts/WorkbenchContext";
import { WorkbenchScreen } from "./WorkbenchScreen";

vi.mock("../components/taskDrawer/TaskDrawerContent", () => ({
  TaskDrawerContent: (props: any) => (
    <div>
      <div>task-drawer-content</div>
      <button onClick={() => props.onResume("tts")}>resume-task</button>
      <button onClick={() => props.onCleanup(0)}>cleanup-task</button>
      <button onClick={() => props.onGoSystem()}>go-system</button>
    </div>
  ),
}));

vi.mock("../components/unifiedReview/UnifiedReviewDrawer", () => ({
  UnifiedReviewDrawer: (props: any) =>
    props.open ? <div>{`review-drawer-${props.initialSelectedTaskId || "empty"}`}</div> : null,
}));

function buildBatch() {
  return {
    id: "batch-1",
    name: "轻量批次",
    mode: "lite",
    state: "paused",
    createdAt: Date.now(),
    outputDir: "/tmp/out",
    tasks: [
      {
        index: 1,
        inputName: "demo.mp4",
        state: "completed",
        stage: 8,
        taskId: "task-1",
        deliveredDir: "轻量批次-01",
      },
    ],
  };
}

function buildTask(overrides: Record<string, any> = {}) {
  return {
    index: 1,
    inputName: "demo.mp4",
    state: "completed",
    stage: 8,
    taskId: "task-1",
    deliveredDir: "轻量批次-01",
    ...overrides,
  };
}

function buildContext(overrides: Record<string, any> = {}) {
  const batch = buildBatch();
  return {
    batches: [],
    activeBatchId: "",
    batchCounts: vi.fn(() => ({ total: 1, done: 1, failed: 0, pending: 0 })),
    onNewBatch: vi.fn(),
    onSetActiveBatchId: vi.fn(),
    onOpenTaskDrawer: vi.fn(),
    onOpenBatchOutputFolder: vi.fn(),
    onOpenPath: vi.fn(),
    onOpenDefaultOutputsFolder: vi.fn(),
    onDeliverTaskToOutputDir: vi.fn(() => Promise.resolve()),
    onPauseQueue: vi.fn(),
    onResumeQueue: vi.fn(),
    onStartQueue: vi.fn(),
    safeStem: vi.fn(() => "轻量批次"),
    drawerOpen: false,
    drawerTaskIndex: -1,
    drawerWidth: 720,
    drawerInitialTab: "quality",
    drawerLog: "",
    drawerLogLoading: false,
    activeBatch: batch,
    qualityGates: {},
    isDockerDev: false,
    onCloseDrawer: vi.fn(),
    onResumeTaskInPlace: vi.fn(),
    onRunReviewAndPoll: vi.fn(),
    onApplyReviewAndRefresh: vi.fn(),
    onExportDiagnosticZipForTask: vi.fn(),
    onOpenCleanupDialog: vi.fn(),
    onOpenQualityUpgradeWizardFromTask: vi.fn(),
    onGoSystem: vi.fn(),
    showTaskLogs: false,
    onCancelTaskInBatch: vi.fn(),
    onRestartTaskInBatch: vi.fn(),
    onArchiveBatch: vi.fn(),
    uiPrefs: {},
    onSetUiPrefs: vi.fn(),
    onSaveUiPrefs: vi.fn(),
    ...overrides,
  };
}

function renderScreen(overrides: Record<string, any> = {}) {
  const ctx = buildContext(overrides);
  const rendered = render(
    <WorkbenchProvider value={ctx}>
      <WorkbenchScreen />
    </WorkbenchProvider>,
  );
  return { ...rendered, ctx };
}

function getButtonByText(label: string): HTMLButtonElement {
  const button = screen
    .getAllByRole("button")
    .find((el) => el.textContent?.replace(/\s+/g, "") === label.replace(/\s+/g, ""));
  if (!button) throw new Error(`button not found: ${label}`);
  return button as HTMLButtonElement;
}

describe("WorkbenchScreen", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows empty state and starts a new batch", async () => {
    const user = userEvent.setup();
    const { ctx } = renderScreen();

    await user.click(screen.getByRole("button", { name: "新建任务" }));

    expect(screen.getByText("还没有任务")).toBeInTheDocument();
    expect(ctx.onNewBatch).toHaveBeenCalled();
  });

  it("opens task details for a lite batch item", async () => {
    const user = userEvent.setup();
    const batch = buildBatch();
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    await user.click(screen.getByRole("button", { name: "详情" }));

    expect(screen.getByText("任务中心")).toBeInTheDocument();
    expect(screen.getByText("轻量")).toBeInTheDocument();
    expect(ctx.onSetActiveBatchId).toHaveBeenCalledWith(batch.id);
    expect(ctx.onOpenTaskDrawer).toHaveBeenCalledWith(0, "quality");
  });

  it("pauses a running batch from the header", async () => {
    const user = userEvent.setup();
    const runningBatch = { ...buildBatch(), id: "running", state: "running", tasks: [buildTask({ state: "running" })] };
    const { ctx } = renderScreen({
      batches: [runningBatch],
      activeBatchId: runningBatch.id,
      activeBatch: runningBatch,
    });

    await user.click(getButtonByText("暂停"));

    expect(ctx.onPauseQueue).toHaveBeenCalledWith("running");
  });

  it("resumes a paused batch from the header", async () => {
    const user = userEvent.setup();
    const pausedBatch = { ...buildBatch(), id: "paused-batch", state: "paused", tasks: [buildTask({ state: "paused" })] };
    const { ctx } = renderScreen({
      batches: [pausedBatch],
      activeBatchId: pausedBatch.id,
      activeBatch: pausedBatch,
    });

    await user.click(getButtonByText("继续"));

    expect(ctx.onResumeQueue).toHaveBeenCalledWith("paused-batch");
  });

  it("starts an idle batch from the header", async () => {
    const user = userEvent.setup();
    const idleBatch = { ...buildBatch(), id: "idle-batch", state: "idle", tasks: [buildTask({ state: "pending", taskId: "" })] };
    const { ctx } = renderScreen({
      batches: [idleBatch],
      activeBatchId: idleBatch.id,
      activeBatch: idleBatch,
    });

    await user.click(getButtonByText("开始"));

    expect(ctx.onStartQueue).toHaveBeenCalledWith("idle-batch");
  });

  it("wires drawer actions for resume, cleanup and system navigation", async () => {
    const batch = buildBatch();
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
      drawerOpen: true,
      drawerTaskIndex: 0,
    });

    screen.getByRole("button", { name: "resume-task" }).click();
    screen.getByRole("button", { name: "cleanup-task" }).click();
    screen.getByRole("button", { name: "go-system" }).click();

    expect(ctx.onResumeTaskInPlace).toHaveBeenCalledWith(0, "tts");
    expect(ctx.onOpenCleanupDialog).toHaveBeenCalledWith(0);
    expect(ctx.onGoSystem).toHaveBeenCalled();
  });

  it("opens unified review for a paused task row", async () => {
    const user = userEvent.setup();
    const batch = { ...buildBatch(), tasks: [buildTask({ state: "paused", taskId: "task-paused", deliveredDir: "" })] };
    renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    await user.click(screen.getAllByRole("button", { name: "审核" })[0]);

    expect(screen.getByText("review-drawer-task-paused")).toBeInTheDocument();
  });

  it("cancels a running task row", async () => {
    const user = userEvent.setup();
    const runningBatch = { ...buildBatch(), id: "running-batch", state: "running", tasks: [buildTask({ state: "running", taskId: "task-run", deliveredDir: "" })] };
    const { ctx } = renderScreen({
      batches: [runningBatch],
      activeBatchId: runningBatch.id,
      activeBatch: runningBatch,
    });

    await user.click(screen.getAllByRole("button", { name: "终止" })[0]);

    expect(ctx.onCancelTaskInBatch).toHaveBeenCalledWith("running-batch", 0);
  });

  it("restarts a failed task row", async () => {
    const user = userEvent.setup();
    const failedBatch = { ...buildBatch(), id: "failed-batch", state: "completed", tasks: [buildTask({ state: "failed", taskId: "task-failed", deliveredDir: "" })] };
    const { ctx } = renderScreen({
      batches: [failedBatch],
      activeBatchId: failedBatch.id,
      activeBatch: failedBatch,
    });

    await user.click(screen.getByRole("button", { name: "开始" }));

    expect(ctx.onRestartTaskInBatch).toHaveBeenCalledWith("failed-batch", 0);
  });

  it("opens delivered files for a completed task row", async () => {
    const user = userEvent.setup();
    const completedBatch = { ...buildBatch(), id: "done-batch", state: "completed", tasks: [buildTask({ state: "completed", taskId: "task-done", deliveredDir: "done-01" })] };
    const { ctx } = renderScreen({
      batches: [completedBatch],
      activeBatchId: completedBatch.id,
      activeBatch: completedBatch,
    });

    await user.click(screen.getByRole("button", { name: "文件" }));

    expect(ctx.onOpenPath).toHaveBeenCalledWith("/tmp/out/done-01");
  });

  it("opens batch output folder from the batch header", async () => {
    const user = userEvent.setup();
    const batch = buildBatch();
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    await user.click(getButtonByText("交付"));

    expect(ctx.onOpenBatchOutputFolder).toHaveBeenCalledWith(batch);
  });

  it("archives a completed batch from the workbench", async () => {
    const user = userEvent.setup();
    const batch = { ...buildBatch(), state: "completed", tasks: [buildTask({ state: "completed" })] };
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    await user.click(getButtonByText("清理"));
    const confirmButtons = screen.getAllByRole("button", { name: /清\s*理/ });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    expect(ctx.onArchiveBatch).toHaveBeenCalledWith(batch.id);
  });

  it("persists skip-confirm preference when archive checkbox is selected", async () => {
    const user = userEvent.setup();
    const batch = { ...buildBatch(), state: "completed", tasks: [buildTask({ state: "completed" })] };
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    await user.click(getButtonByText("清理"));
    await user.click(screen.getByRole("checkbox", { name: "以后不再提示" }));
    const confirmButtons = screen.getAllByRole("button", { name: /清\s*理/ });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    expect(ctx.onArchiveBatch).toHaveBeenCalledWith(batch.id);
    expect(ctx.onSetUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ skipArchiveConfirm: true }));
    expect(ctx.onSaveUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ skipArchiveConfirm: true }));
  });

  it("archives directly when skip-confirm preference is enabled", async () => {
    const user = userEvent.setup();
    const batch = { ...buildBatch(), state: "completed", tasks: [buildTask({ state: "completed" })] };
    const { ctx } = renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
      uiPrefs: { skipArchiveConfirm: true },
    });

    await user.click(getButtonByText("清理"));

    expect(ctx.onArchiveBatch).toHaveBeenCalledWith(batch.id);
    expect(screen.queryByRole("checkbox", { name: "以后不再提示" })).not.toBeInTheDocument();
  });

  it("collapses the active batch panel", async () => {
    const user = userEvent.setup();
    const batch = buildBatch();
    renderScreen({
      batches: [batch],
      activeBatchId: batch.id,
      activeBatch: batch,
    });

    const expandedHeader = screen.getByRole("button", { name: /expanded .*轻量批次/ });
    await user.click(expandedHeader);

    expect(screen.getByRole("button", { name: /collapsed .*轻量批次/ })).toBeInTheDocument();
  });
});
