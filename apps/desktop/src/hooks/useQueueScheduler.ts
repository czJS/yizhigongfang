import { useCallback } from "react";
import { message } from "antd";
import type { BatchModel } from "../batchTypes";
import { computeShouldQueueOnStart, computeTickGlobalQueueNext, resetCancelledTasksFromFirstCancelled } from "../app/domains/queue/queueScheduler";

export function useQueueScheduler(opts: {
  batchesRef: React.MutableRefObject<BatchModel[]>;
  setBatches: React.Dispatch<React.SetStateAction<BatchModel[]>>;
  activeBatchId: string;
  setActiveBatchId: (id: string) => void;
  setRoute: (r: any) => void;
  updateActiveBatch: (updater: (b: BatchModel) => BatchModel) => void;
  updateActiveBatchById: (batchId: string, updater: (b: BatchModel) => BatchModel) => void;
  startNextIfNeeded: (batchId: string) => Promise<void> | void;
  getTaskCreationBlockReason?: () => string;
}) {
  const tickGlobalQueue = useCallback(() => {
    const list = opts.batchesRef.current;
    const next = computeTickGlobalQueueNext(list);
    if (!next) return;
    opts.setBatches((prev) => prev.map((b) => (b.id === next.id ? { ...b, state: "running" } : b)));
    setTimeout(() => opts.startNextIfNeeded(next.id), 0);
  }, [opts]);

  const startQueue = useCallback(
    async (batchId: string, more?: { navigate?: boolean }) => {
      const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
      if (blockReason) {
        message.warning(blockReason);
        return;
      }
      const navigate = more?.navigate ?? true;
      opts.setActiveBatchId(batchId);
      if (navigate) opts.setRoute("workbench");

      // 如果该批次没有待处理任务，但存在“已取消”，则从第一个取消任务开始重置为待处理再继续
      opts.setBatches((prev) =>
        prev.map((b) => {
          if (b.id !== batchId) return b;
          return resetCancelledTasksFromFirstCancelled(b);
        }),
      );

      const list = opts.batchesRef.current;
      const shouldQueue = computeShouldQueueOnStart(list, batchId);

      opts.setBatches((prev) => prev.map((b) => (b.id === batchId ? { ...b, state: shouldQueue ? "queued" : "running" } : b)));

      if (!shouldQueue) {
        setTimeout(() => opts.startNextIfNeeded(batchId), 0);
      } else {
        message.info("已加入队列：当前有任务正在处理，会在前一批完成后自动开始。");
        setTimeout(() => tickGlobalQueue(), 0);
      }
    },
    [opts, tickGlobalQueue],
  );

  const pauseQueue = useCallback(async () => {
    if (!opts.activeBatchId) return;
    opts.updateActiveBatch((b) => ({ ...b, state: "paused" }));
    message.info("已暂停队列：当前任务会继续运行，完成后不会自动进入下一个。");
  }, [opts]);

  const resumeQueue = useCallback(async () => {
    if (!opts.activeBatchId) return;
    const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
    if (blockReason) {
      message.warning(blockReason);
      return;
    }
    const batchId = opts.activeBatchId;
    opts.updateActiveBatch((b) => ({ ...b, state: "running" }));
    setTimeout(() => opts.startNextIfNeeded(batchId), 0);
  }, [opts]);

  const pauseQueueById = useCallback(async (batchId: string) => {
    opts.updateActiveBatchById(batchId, (b) => ({ ...b, state: "paused" }));
    message.info("已暂停队列：当前任务会继续运行，完成后不会自动进入下一个。");
  }, [opts]);

  const resumeQueueById = useCallback(
    async (batchId: string) => {
      const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
      if (blockReason) {
        message.warning(blockReason);
        return;
      }
      opts.updateActiveBatchById(batchId, (b) => ({ ...b, state: "running" }));
      setTimeout(() => opts.startNextIfNeeded(batchId), 0);
    },
    [opts],
  );

  return { tickGlobalQueue, startQueue, pauseQueue, resumeQueue, pauseQueueById, resumeQueueById };
}

