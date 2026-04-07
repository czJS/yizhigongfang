import { describe, expect, it } from "vitest";
import { batchHasUnfinishedTasks, findBatchIdWithRunningTask, findNextQueuedBatch } from "./queueHelpers";
import type { BatchModel, BatchTask } from "../batchTypes";

function mkTask(p: Partial<BatchTask>): BatchTask {
  return {
    index: 1,
    inputName: "a.mp4",
    inputPath: "/tmp/a.mp4",
    state: "pending",
    ...p,
  };
}

function mkBatch(p: Partial<BatchModel>): BatchModel {
  return {
    id: "b",
    name: "batch",
    createdAt: 0,
    mode: "lite",
    params: {},
    outputDir: "",
    state: "draft",
    tasks: [],
    ...p,
  };
}

describe("queueHelpers", () => {
  it("batchHasUnfinishedTasks true when pending/running exist", () => {
    expect(batchHasUnfinishedTasks(mkBatch({ tasks: [mkTask({ state: "completed" }), mkTask({ state: "pending" })] }))).toBe(true);
    expect(batchHasUnfinishedTasks(mkBatch({ tasks: [mkTask({ state: "running" })] }))).toBe(true);
    expect(batchHasUnfinishedTasks(mkBatch({ tasks: [mkTask({ state: "completed" }), mkTask({ state: "failed" })] }))).toBe(false);
  });

  it("findBatchIdWithRunningTask returns first batch with running task", () => {
    const a = mkBatch({ id: "a", tasks: [mkTask({ state: "pending" })] });
    const b = mkBatch({ id: "b", tasks: [mkTask({ state: "running" })] });
    const c = mkBatch({ id: "c", tasks: [mkTask({ state: "running" })] });
    expect(findBatchIdWithRunningTask([a, b, c])).toBe("b");
    expect(findBatchIdWithRunningTask([a])).toBe("");
  });

  it("findNextQueuedBatch picks oldest queued with unfinished tasks", () => {
    const b1 = mkBatch({ id: "b1", state: "queued", createdAt: 2, tasks: [mkTask({ state: "pending" })] });
    const b2 = mkBatch({ id: "b2", state: "queued", createdAt: 1, tasks: [mkTask({ state: "pending" })] });
    const b3 = mkBatch({ id: "b3", state: "queued", createdAt: 0, tasks: [mkTask({ state: "completed" })] }); // finished -> ignored
    const next = findNextQueuedBatch([b1, b2, b3]);
    expect(next?.id).toBe("b2");
  });
});

