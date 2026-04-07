import { useCallback } from "react";
import { message } from "antd";
import { applyReview, cancelTask, getArtifacts, getQualityReport, resumeTask2, runReview } from "../services/taskApi";
import type { BatchModel } from "../batchTypes";

export function useTaskActions(opts: {
  activeBatch: BatchModel | null;
  batchesRef?: React.MutableRefObject<BatchModel[]>;
  updateActiveBatch: (updater: (b: BatchModel) => BatchModel) => void;
  startPollingForTask: (batchId: string, taskIdx: number, taskId: string) => void;
  deliverTaskToOutputDir: (batchId: string, taskIdx: number, artifactsOverride?: { name: string; path: string; size: number }[]) => Promise<void>;
  getTaskCreationBlockReason?: () => string;
}) {
  const cancelCurrent = useCallback(async () => {
    const b = opts.activeBatch;
    if (!b) return;
    const idx = b.currentTaskIndex ?? -1;
    if (idx < 0) return;
    const t = b.tasks[idx];
    if (!t.taskId || !["running", "paused"].includes(t.state)) return;
    try {
      await cancelTask(t.taskId);
      message.success("已请求取消当前任务");
    } catch (err: any) {
      message.error(err?.message || "取消失败");
    }
  }, [opts.activeBatch]);

  const cancelCurrentById = useCallback(
    async (batchId: string) => {
      const b = opts.batchesRef?.current?.find((x) => x.id === batchId);
      if (!b) return;
      const idx = b.currentTaskIndex ?? b.tasks.findIndex((t) => t.state === "running");
      if (idx < 0) return;
      const t = b.tasks[idx];
      if (!t.taskId || !["running", "paused"].includes(t.state)) return;
      try {
        await cancelTask(t.taskId);
        message.success("已请求取消当前任务");
      } catch (err: any) {
        message.error(err?.message || "取消失败");
      }
    },
    [opts.batchesRef],
  );

  const resumeTaskInPlace = useCallback(
    async (taskIdx: number, resumeFrom: "asr" | "mt" | "tts" | "mux") => {
      const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
      if (blockReason) {
        message.warning(blockReason);
        return;
      }
      const b = opts.activeBatch;
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t.taskId) {
        message.error("该任务还没有 task_id");
        return;
      }
      try {
        opts.updateActiveBatch((bb) => {
          const next = { ...bb, tasks: [...bb.tasks] };
          next.tasks[taskIdx] = { ...next.tasks[taskIdx], state: "running", failureReason: "" };
          next.currentTaskIndex = taskIdx;
          return next;
        });
        const mergedParams = { ...(b.params || {}), ...((t as any).paramsOverride || {}) };
        const rid = await resumeTask2(t.taskId, { resume_from: resumeFrom, params: mergedParams, preset: b.preset });
        opts.startPollingForTask(b.id, taskIdx, rid);
        message.success("已从上次继续");
      } catch (err: any) {
        message.error(err?.message || "继续失败");
      }
    },
    [opts],
  );

  const runReviewAndPoll = useCallback(
    async (taskIdx: number, lang: "chs" | "eng") => {
      const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
      if (blockReason) {
        message.warning(blockReason);
        return;
      }
      const b = opts.activeBatch;
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t.taskId) {
        message.error("该任务还没有 task_id");
        return;
      }
      try {
        opts.updateActiveBatch((bb) => ({ ...bb, state: "running", currentTaskIndex: taskIdx }));
        opts.updateActiveBatch((bb) => {
          const tasks = [...bb.tasks];
          tasks[taskIdx] = { ...tasks[taskIdx], state: "running", message: "正在重新生成…" };
          return { ...bb, tasks };
        });
        const res = await runReview(t.taskId, lang);
        opts.startPollingForTask(b.id, taskIdx, res.task_id);
        message.success("已开始重新生成（后台处理中）");
      } catch (err: any) {
        message.error(err?.message || "重新生成失败");
      }
    },
    [opts],
  );

  const applyReviewAndRefresh = useCallback(
    async (taskIdx: number, action: "mux" | "embed" | "mux_embed", use: "review" | "base" = "review") => {
      const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
      if (blockReason) {
        message.warning(blockReason);
        return;
      }
      const b = opts.activeBatch;
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t.taskId) {
        message.error("该任务还没有 task_id");
        return;
      }
      try {
        message.loading({ content: "正在应用审校并生成交付物…", key: `apply_${t.taskId}`, duration: 0 });
        // Important: pass current effective params so regen respects latest UI settings (font size / placement box etc.)
        const effectiveParams = { ...(b.params || {}), ...((t as any).paramsOverride || {}) };
        await applyReview(t.taskId, { action, use, params: effectiveParams });
        // refresh artifacts + quality report (best-effort)
        const [arts, qr] = await Promise.all([getArtifacts(t.taskId).catch(() => []), getQualityReport(t.taskId).catch(() => null)]);
        opts.updateActiveBatch((bb) => {
          const tasks = [...bb.tasks];
          tasks[taskIdx] = {
            ...tasks[taskIdx],
            artifacts: arts,
            qualityPassed: qr ? !!qr.passed : tasks[taskIdx].qualityPassed,
            qualityErrors: qr?.errors || tasks[taskIdx].qualityErrors,
            qualityWarnings: qr?.warnings || tasks[taskIdx].qualityWarnings,
          };
          return { ...bb, tasks };
        });
        message.success({ content: "已应用审校（交付物已更新）", key: `apply_${t.taskId}` });
        // re-deliver to output dir if configured
        if (b.outputDir && window.bridge?.writeFile && window.bridge?.ensureDir) {
          await opts.deliverTaskToOutputDir(b.id, taskIdx);
        }
      } catch (err: any) {
        message.error({ content: err?.message || "应用失败", key: `apply_${t.taskId}` });
      }
    },
    [opts],
  );

  return { cancelCurrent, cancelCurrentById, resumeTaskInPlace, runReviewAndPoll, applyReviewAndRefresh };
}

