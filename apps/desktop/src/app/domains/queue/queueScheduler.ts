import type { BatchModel } from "../../../batchTypes";
import { batchHasUnfinishedTasks, findBatchIdWithRunningTask, findNextQueuedBatch } from "../../queueHelpers";

export function computeTickGlobalQueueNext(list: BatchModel[]): BatchModel | null {
  if (findBatchIdWithRunningTask(list)) return null;
  return findNextQueuedBatch(list);
}

export function computeShouldQueueOnStart(list: BatchModel[], batchId: string): boolean {
  const otherRunningTaskBatchId = list.find((b) => b.id !== batchId && b.tasks.some((t) => t.state === "running"))?.id || "";
  const otherRunningBatchId = list.find((b) => b.id !== batchId && b.state === "running" && batchHasUnfinishedTasks(b))?.id || "";
  return !!(otherRunningTaskBatchId || otherRunningBatchId);
}

export function resetCancelledTasksFromFirstCancelled(b: BatchModel): BatchModel {
  const hasUnfinished = b.tasks.some((t) => t.state === "pending" || t.state === "running");
  if (hasUnfinished) return b;
  const firstCancelled = b.tasks.findIndex((t) => t.state === "cancelled");
  if (firstCancelled < 0) return b;
  const tasks = b.tasks.map((t, i) => {
    if (i < firstCancelled) return t;
    if (t.state !== "cancelled") return t;
    return {
      ...t,
      state: "pending" as const,
      taskId: undefined,
      progress: 0,
      stageName: "",
      message: "",
      startedAt: undefined,
      endedAt: undefined,
      workDir: undefined,
      failureReason: "",
      artifacts: [],
      qualityPassed: undefined,
      qualityErrors: [],
      qualityWarnings: [],
    };
  });
  return { ...b, tasks, currentTaskIndex: undefined };
}

