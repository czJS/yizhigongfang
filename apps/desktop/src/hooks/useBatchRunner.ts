import { useCallback, useRef } from "react";
import { message } from "antd";
import { startTask } from "../api";
import { findBatchIdWithRunningTask } from "../app/queueHelpers";
import type { BatchModel } from "../batchTypes";
import { resetCancelledTasksFromFirstCancelled } from "../app/domains/queue/queueScheduler";

export function useBatchRunner(opts: {
  batchesRef: React.MutableRefObject<BatchModel[]>;
  updateActiveBatchById: (batchId: string, updater: (b: BatchModel) => BatchModel) => void;
  startPollingForTask: (batchId: string, taskIdx: number, taskId: string) => void;
  tickGlobalQueue: () => void;
  getTaskCreationBlockReason?: () => string;
}) {
  // Prevent double-start when startNextIfNeeded is triggered concurrently (e.g. timer + UI action),
  // before React state updates are reflected in batchesRef.current.
  const startingRef = useRef<Record<string, boolean>>({});
  const updateBatchForTaskStart = useCallback(
    (batchId: string, taskIdx: number) => {
      opts.updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        tasks[taskIdx] = { ...tasks[taskIdx], state: "running", progress: 0, stageName: "", message: "开始处理中…" };
        return { ...bb, tasks, currentTaskIndex: taskIdx };
      });
    },
    [opts],
  );

  const updateBatchStateIfAllDone = useCallback(
    (batchId: string) => {
      opts.updateActiveBatchById(batchId, (bb) => {
        const allFinished = bb.tasks.every((t) => ["completed", "failed", "cancelled", "paused"].includes(t.state));
        if (!allFinished) return bb;
        const hasPaused = bb.tasks.some((t) => t.state === "paused");
        // zh gate / review workflow: paused means "needs user action", not "batch completed".
        // Keep the batch paused so UI can offer a unified review entry.
        if (hasPaused) return { ...bb, state: "paused", currentTaskIndex: undefined };
        return { ...bb, state: "completed", currentTaskIndex: undefined };
      });
    },
    [opts],
  );

  const startNextIfNeeded = useCallback(
    async function startNextIfNeeded(batchId: string) {
      if (startingRef.current[batchId]) return;
      // 全局串行：如果有其它批次正在跑任务，则本批次不启动新任务
      const runningTaskBatchId = findBatchIdWithRunningTask(opts.batchesRef.current);
      if (runningTaskBatchId && runningTaskBatchId !== batchId) return;

      const b = opts.batchesRef.current.find((x) => x.id === batchId);
      if (!b) return;
      if (b.state !== "running") return;
      // if already have a running task, keep polling
      const runningIdx = b.tasks.findIndex((t) => t.state === "running");
      if (runningIdx >= 0) {
        const taskId = b.tasks[runningIdx].taskId;
        if (taskId) opts.startPollingForTask(batchId, runningIdx, taskId);
        return;
      }
      const nextIdx = b.tasks.findIndex((t) => t.state === "pending");
      if (nextIdx < 0) {
        // 如果没有 pending 但存在已取消，则从第一个取消任务开始重置为 pending 再继续
        const firstCancelled = b.tasks.findIndex((t) => t.state === "cancelled");
        if (firstCancelled >= 0) {
          opts.updateActiveBatchById(batchId, (bb) => resetCancelledTasksFromFirstCancelled(bb));
          setTimeout(() => startNextIfNeeded(batchId), 0);
          return;
        }
        // finished
        updateBatchStateIfAllDone(batchId);
        setTimeout(() => opts.tickGlobalQueue(), 0);
        return;
      }
      try {
        startingRef.current[batchId] = true;
        const nextTask = b.tasks[nextIdx];
        const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
        if (blockReason) {
          opts.updateActiveBatchById(batchId, (bb) => ({ ...bb, state: "paused", currentTaskIndex: undefined }));
          message.warning(blockReason);
          return;
        }
        updateBatchForTaskStart(batchId, nextIdx);
        const mergedParams = { ...(b.params || {}), ...((nextTask as any).paramsOverride || {}) };
        const id = await startTask({ video: nextTask.inputPath, params: mergedParams, preset: b.preset, mode: b.mode });
        opts.updateActiveBatchById(batchId, (bb) => {
          const tasks = [...bb.tasks];
          tasks[nextIdx] = {
            ...tasks[nextIdx],
            taskId: id,
            state: "running",
            startedAt: Date.now(),
            resumeFrom: null,
            createdAtBackend: null,
            resumedAt: null,
          };
          return { ...bb, tasks, currentTaskIndex: nextIdx };
        });
        opts.startPollingForTask(batchId, nextIdx, id);
        message.success(`已开始：${nextTask.inputName}`);
      } catch (err: any) {
        // Mark as failed and continue
        opts.updateActiveBatchById(batchId, (bb) => {
          const tasks = [...bb.tasks];
          tasks[nextIdx] = { ...tasks[nextIdx], state: "failed", failureReason: err?.message || "启动失败" };
          return { ...bb, tasks };
        });
        message.error(err?.message || "启动失败");
        setTimeout(() => startNextIfNeeded(batchId), 0);
      } finally {
        startingRef.current[batchId] = false;
      }
    },
    [opts, updateBatchForTaskStart, updateBatchStateIfAllDone],
  );

  return { startNextIfNeeded, updateBatchForTaskStart, updateBatchStateIfAllDone };
}

