import { describe, expect, it } from "vitest";
import type { BatchModel } from "../../../batchTypes";
import { computeShouldQueueOnStart, computeTickGlobalQueueNext, resetCancelledTasksFromFirstCancelled } from "./queueScheduler";

function mkTask(state: any) {
  return {
    index: 1,
    inputName: "a.mp4",
    inputPath: "/a.mp4",
    state,
    taskId: state === "running" ? "t1" : undefined,
  };
}

function mkBatch(id: string, more?: Partial<BatchModel>): BatchModel {
  return {
    id,
    name: id,
    createdAt: 1,
    mode: "lite",
    params: {},
    outputDir: "",
    state: "draft",
    tasks: [mkTask("pending")],
    ...more,
  };
}

describe("queueScheduler helpers", () => {
  it("computeShouldQueueOnStart returns true when another batch has running task", () => {
    const list = [mkBatch("b1"), mkBatch("b2", { tasks: [mkTask("running")], state: "running" })];
    expect(computeShouldQueueOnStart(list, "b1")).toBe(true);
  });

  it("computeShouldQueueOnStart returns false when no other batch is effectively running", () => {
    const list = [mkBatch("b1"), mkBatch("b2", { state: "running", tasks: [mkTask("completed")] })];
    expect(computeShouldQueueOnStart(list, "b1")).toBe(false);
  });

  it("resetCancelledTasksFromFirstCancelled resets cancelled tail to pending when no unfinished tasks", () => {
    const b = mkBatch("b1", {
      currentTaskIndex: 3 as any,
      tasks: [
        { ...mkTask("completed"), index: 1 },
        { ...mkTask("cancelled"), index: 2, taskId: "x" as any, progress: 0.9 as any },
        { ...mkTask("cancelled"), index: 3, taskId: "y" as any },
      ] as any,
    });
    const next = resetCancelledTasksFromFirstCancelled(b);
    expect(next.currentTaskIndex).toBeUndefined();
    expect(next.tasks[0].state).toBe("completed");
    expect(next.tasks[1].state).toBe("pending");
    expect(next.tasks[1].taskId).toBeUndefined();
    expect(next.tasks[2].state).toBe("pending");
  });

  it("computeTickGlobalQueueNext picks next queued batch when nothing is running", () => {
    const list = [
      mkBatch("b1", { state: "queued", tasks: [mkTask("pending")] }),
      mkBatch("b2", { state: "queued", tasks: [mkTask("pending")] }),
    ];
    const next = computeTickGlobalQueueNext(list);
    expect(next?.id).toBe("b1");
  });

  it("computeTickGlobalQueueNext returns null when some task is running", () => {
    const list = [mkBatch("b1", { state: "queued" }), mkBatch("b2", { state: "running", tasks: [mkTask("running")] })];
    const next = computeTickGlobalQueueNext(list);
    expect(next).toBeNull();
  });
});

