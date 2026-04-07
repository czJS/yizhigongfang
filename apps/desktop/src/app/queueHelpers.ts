import type { BatchModel } from "../batchTypes";

export function batchHasUnfinishedTasks(b: BatchModel) {
  return b.tasks.some((t) => t.state === "pending" || t.state === "running");
}

export function findBatchIdWithRunningTask(list: BatchModel[]): string {
  const hit = list.find((b) => b.tasks.some((t) => t.state === "running"));
  return hit?.id || "";
}

export function findNextQueuedBatch(list: BatchModel[]): BatchModel | null {
  const queued = list
    .filter((b) => b.state === "queued" && batchHasUnfinishedTasks(b))
    .slice()
    .sort((a, c) => a.createdAt - c.createdAt);
  return queued[0] || null;
}

