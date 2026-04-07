import { describe, expect, it, vi } from "vitest";
import type { BatchModel } from "../batchTypes";
import { finalizeTaskImpl } from "./useTaskFinalize";

function mkBatch(): BatchModel {
  return {
    id: "b1",
    name: "batch",
    createdAt: 1,
    mode: "lite",
    params: {},
    outputDir: "",
    state: "running",
    tasks: [
      {
        index: 1,
        inputName: "a.mp4",
        inputPath: "/a.mp4",
        state: "running",
        taskId: "t1",
      },
    ],
  };
}

describe("finalizeTaskImpl", () => {
  it("updates task fields and failure reason from quality report when failed with generic message", async () => {
    let batch = mkBatch();
    const updateActiveBatchById = vi.fn((_id: string, updater: any) => {
      batch = updater(batch);
    });
    const deliverTaskToOutputDir = vi.fn(async () => {});
    const warn = vi.fn();
    const updateBatchStateIfAllDone = vi.fn();
    const startNextIfNeeded = vi.fn();
    const tickGlobalQueue = vi.fn();

    await finalizeTaskImpl({
      batchId: "b1",
      taskIdx: 0,
      taskId: "t1",
      st: {
        state: "failed",
        progress: 0.5,
        stage_name: "x",
        message: "Exited with 1",
        started_at: 1,
        ended_at: 2,
        work_dir: "/w",
      } as any,
      getArtifacts: async () => [{ name: "a", path: "/p", size: 1 }],
      getQualityReport: async () => ({ passed: false, errors: ["E1"], warnings: [] }),
      updateActiveBatchById,
      canAutoDeliver: false,
      deliverTaskToOutputDir,
      warn,
      updateBatchStateIfAllDone,
      startNextIfNeeded,
      tickGlobalQueue,
      defer: (fn) => fn(),
    });

    expect(batch.tasks[0].state).toBe("failed");
    expect(batch.tasks[0].failureReason).toBe("E1");
    expect(batch.tasks[0].artifacts?.length).toBe(1);
    expect(deliverTaskToOutputDir).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    expect(updateBatchStateIfAllDone).toHaveBeenCalledWith("b1");
    expect(startNextIfNeeded).toHaveBeenCalledWith("b1");
    expect(tickGlobalQueue).toHaveBeenCalled();
  });

  it("uses backend message as failure reason when failed with non-generic message", async () => {
    let batch = mkBatch();
    const updateActiveBatchById = vi.fn((_id: string, updater: any) => {
      batch = updater(batch);
    });
    await finalizeTaskImpl({
      batchId: "b1",
      taskIdx: 0,
      taskId: "t1",
      st: { state: "failed", message: "OOM", stage_name: "", progress: 0 } as any,
      getArtifacts: async () => [],
      getQualityReport: async () => ({ passed: false, errors: ["E1"], warnings: [] }),
      updateActiveBatchById,
      canAutoDeliver: false,
      deliverTaskToOutputDir: async () => {},
      warn: () => {},
      updateBatchStateIfAllDone: () => {},
      startNextIfNeeded: () => {},
      tickGlobalQueue: () => {},
      defer: (fn) => fn(),
    });
    expect(batch.tasks[0].failureReason).toBe("OOM");
  });

  it("attempts auto-delivery and warns if delivery fails", async () => {
    let batch = mkBatch();
    const updateActiveBatchById = vi.fn((_id: string, updater: any) => {
      batch = updater(batch);
    });
    const deliverTaskToOutputDir = vi.fn(async () => {
      throw new Error("write fail");
    });
    const warn = vi.fn();
    await finalizeTaskImpl({
      batchId: "b1",
      taskIdx: 0,
      taskId: "t1",
      st: { state: "completed", message: "", stage_name: "", progress: 1 } as any,
      getArtifacts: async () => [],
      getQualityReport: async () => ({ passed: true, errors: [], warnings: [] }),
      updateActiveBatchById,
      canAutoDeliver: true,
      deliverTaskToOutputDir,
      warn,
      updateBatchStateIfAllDone: () => {},
      startNextIfNeeded: () => {},
      tickGlobalQueue: () => {},
      defer: (fn) => fn(),
    });
    expect(deliverTaskToOutputDir).toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
  });
});

