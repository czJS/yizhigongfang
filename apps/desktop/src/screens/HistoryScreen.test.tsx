// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HistoryProvider } from "../app/contexts/HistoryContext";
import { HistoryScreen } from "./HistoryScreen";

function buildBatch() {
  return {
    id: "history-1",
    name: "历史批次",
    mode: "lite",
    state: "completed",
    createdAt: Date.now(),
    outputDir: "/tmp/history",
    tasks: [
      {
        index: 1,
        inputName: "demo.mp4",
        state: "completed",
        deliveredDir: "历史批次-01",
      },
    ],
  };
}

function buildContext(overrides: Record<string, any> = {}) {
  return {
    batches: [],
    batchCounts: vi.fn(() => ({ total: 1, done: 1, failed: 0, pending: 0 })),
    onNewBatch: vi.fn(),
    onOpenBatchOutputFolder: vi.fn(),
    onOpenDeliveredDirForTask: vi.fn(),
    onDeleteBatch: vi.fn(),
    ...overrides,
  };
}

function renderScreen(overrides: Record<string, any> = {}) {
  const ctx = buildContext(overrides);
  const rendered = render(
    <HistoryProvider value={ctx}>
      <HistoryScreen />
    </HistoryProvider>,
  );
  return { ...rendered, ctx };
}

describe("HistoryScreen", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts a new batch from empty history", async () => {
    const user = userEvent.setup();
    const { ctx } = renderScreen();

    await user.click(screen.getByRole("button", { name: "新建批次" }));

    expect(screen.getByText("暂无历史记录")).toBeInTheDocument();
    expect(ctx.onNewBatch).toHaveBeenCalled();
  });

  it("opens batch output and deletes local history records", async () => {
    const user = userEvent.setup();
    const batch = buildBatch();
    const { ctx } = renderScreen({ batches: [batch] });

    await user.click(screen.getAllByRole("button", { name: /打\s*开/ })[1]);
    expect(ctx.onOpenBatchOutputFolder).toHaveBeenCalledWith(batch);

    await user.click(screen.getByRole("button", { name: "删除记录" }));
    const confirmButtons = screen.getAllByRole("button", { name: /删\s*除/ });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    expect(ctx.onDeleteBatch).toHaveBeenCalledWith(batch.id);
  });

  it("opens delivered directory for a completed task row", async () => {
    const user = userEvent.setup();
    const batch = buildBatch();
    const { ctx } = renderScreen({ batches: [batch] });

    await user.click(screen.getByRole("button", { name: /collapsed .*历史批次/ }));
    await user.click(screen.getAllByRole("button", { name: /打\s*开/ }).slice(-1)[0]);

    expect(ctx.onOpenDeliveredDirForTask).toHaveBeenCalledWith(batch, batch.tasks[0]);
  });

  it("shows archived marker for cleaned batches", () => {
    const batch = { ...buildBatch(), archivedAt: Date.now() };
    renderScreen({ batches: [batch] });

    expect(screen.getByText("已清理")).toBeInTheDocument();
  });

  it("shows archived timestamp after expanding a cleaned batch", async () => {
    const user = userEvent.setup();
    const batch = { ...buildBatch(), archivedAt: Date.now() };
    renderScreen({ batches: [batch] });

    await user.click(screen.getByRole("button", { name: /collapsed .*历史批次/ }));

    expect(screen.getByText(/清理时间：/)).toBeInTheDocument();
  });
});
