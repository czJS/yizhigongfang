import { useCallback } from "react";
import { message } from "antd";
import { getArtifacts, getQualityReport } from "../services/taskApi";
import type { BatchModel } from "../batchTypes";
import type { TaskStatus } from "../types";
import { uiStateFromBackend } from "../app/appHelpers";

export async function finalizeTaskImpl(args: {
  batchId: string;
  taskIdx: number;
  taskId: string;
  st: TaskStatus;
  getArtifacts: (taskId: string) => Promise<any[]>;
  getQualityReport: (taskId: string, opts?: any) => Promise<any | null>;
  updateActiveBatchById: (batchId: string, updater: (b: BatchModel) => BatchModel) => void;
  canAutoDeliver: boolean;
  deliverTaskToOutputDir: (batchId: string, taskIdx: number, artifactsOverride?: { name: string; path: string; size: number }[]) => Promise<void>;
  warn: (content: string, key: string) => void;
  updateBatchStateIfAllDone: (batchId: string) => void;
  startNextIfNeeded: (batchId: string) => void;
  tickGlobalQueue: () => void;
  defer: (fn: () => void) => void;
}) {
  const { batchId, taskIdx, taskId, st } = args;
  try {
    const [arts, qr] = await Promise.all([
      args.getArtifacts(taskId).catch(() => []),
      args.getQualityReport(taskId, { regen: true }).catch(() => null),
    ]);
    args.updateActiveBatchById(batchId, (bb) => {
      const tasks = [...bb.tasks];
      const prev = tasks[taskIdx];
      const failureReason =
        st.state === "failed"
          ? st.message && !/^Exited with \d+$/i.test(st.message)
            ? st.message
            : qr?.errors?.[0] || "失败（点开查看原因）"
          : st.state === "paused"
            ? "已暂停：需要你处理后继续"
            : st.state === "cancelled"
              ? "已取消"
              : "";
      tasks[taskIdx] = {
        ...prev,
        state: uiStateFromBackend(st.state),
        progress: st.progress,
        stageName: st.stage_name,
        message: st.message,
        startedAt: st.started_at ? Math.floor(st.started_at * 1000) : prev.startedAt,
        endedAt: st.ended_at ? Math.floor(st.ended_at * 1000) : null,
        workDir: st.work_dir,
        resumeFrom: st.resume_from ?? null,
        createdAtBackend: st.created_at ? Math.floor(st.created_at * 1000) : null,
        resumedAt: st.resumed_at ? Math.floor(st.resumed_at * 1000) : null,
        artifacts: arts,
        qualityPassed: qr ? !!qr.passed : undefined,
        qualityErrors: qr?.errors || [],
        qualityWarnings: qr?.warnings || [],
        failureReason,
      };
      return { ...bb, tasks };
    });

    if (args.canAutoDeliver) {
      try {
        await args.deliverTaskToOutputDir(batchId, taskIdx, arts as any);
      } catch (err: any) {
        args.warn(`自动保存交付物失败（可在“交付物”里手动下载）：${err?.message || "未知错误"}`, `deliver_auto_${taskId}`);
      }
    }
  } catch {
    // swallow (but still allow queue to proceed)
  } finally {
    args.updateBatchStateIfAllDone(batchId);
    args.defer(() => {
      args.startNextIfNeeded(batchId);
      args.tickGlobalQueue();
    });
  }
}

export function useTaskFinalize(opts: {
  batchesRef: React.MutableRefObject<BatchModel[]>;
  updateActiveBatchById: (batchId: string, updater: (b: BatchModel) => BatchModel) => void;
  deliverTaskToOutputDir: (batchId: string, taskIdx: number, artifactsOverride?: { name: string; path: string; size: number }[]) => Promise<void>;
  updateBatchStateIfAllDone: (batchId: string) => void;
  startNextIfNeeded: (batchId: string) => void;
  tickGlobalQueue: () => void;
}) {
  const finalizeTask = useCallback(
    async (batchId: string, taskIdx: number, taskId: string, st: TaskStatus) => {
      await finalizeTaskImpl({
        batchId,
        taskIdx,
        taskId,
        st,
        getArtifacts: (id) => getArtifacts(id),
        getQualityReport: (id, more) => getQualityReport(id, more),
        updateActiveBatchById: opts.updateActiveBatchById,
        canAutoDeliver: !!(window.bridge?.writeFile && window.bridge?.ensureDir),
        deliverTaskToOutputDir: opts.deliverTaskToOutputDir,
        warn: (content, key) => message.warning({ content, key }),
        updateBatchStateIfAllDone: opts.updateBatchStateIfAllDone,
        startNextIfNeeded: opts.startNextIfNeeded,
        tickGlobalQueue: opts.tickGlobalQueue,
        defer: (fn) => setTimeout(fn, 0),
      });
    },
    [opts],
  );

  return { finalizeTask };
}

