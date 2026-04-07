import { useCallback, useRef } from "react";
import { getLog, getStatus } from "../services/taskApi";
import { uiStateFromBackend } from "../app/appHelpers";
import type { TaskStatus } from "../types";

export function useTaskPolling(opts: {
  pollingMs: number;
  drawerOpen: boolean;
  drawerTaskIndex: number;
  drawerLogOffset: number;
  showTaskLogs: boolean;
  setDrawerLog: (updater: (prev: string) => string) => void;
  setDrawerLogOffset: (n: number) => void;
  updateActiveBatchById: (batchId: string, updater: (b: any) => any) => void;
  finalizeTask: (batchId: string, taskIdx: number, taskId: string, st: TaskStatus) => Promise<void>;
}) {
  const pollingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeBackendTaskIdRef = useRef<string>("");

  const stopPolling = useCallback(() => {
    if (pollingTimer.current) {
      clearTimeout(pollingTimer.current);
      pollingTimer.current = null;
    }
    activeBackendTaskIdRef.current = "";
  }, []);

  const pollOnce = useCallback(
    async (batchId: string, taskIdx: number, taskId: string, logOffset: number) => {
      if (activeBackendTaskIdRef.current !== taskId) return;
      try {
        const st = await getStatus(taskId);
        const shouldFetchLog = Boolean(opts.showTaskLogs && opts.drawerOpen && opts.drawerTaskIndex === taskIdx);
        const log = shouldFetchLog ? await getLog(taskId, logOffset) : null;
        // update task status
        opts.updateActiveBatchById(batchId, (bb) => {
          const tasks = [...bb.tasks];
          const prev = tasks[taskIdx];
          tasks[taskIdx] = {
            ...prev,
            taskId,
            state: uiStateFromBackend(st.state),
            progress: st.progress,
            stage: st.stage,
            stageName: st.stage_name,
            message: st.message,
            workDir: st.work_dir,
            resumeFrom: st.resume_from ?? null,
            createdAtBackend: st.created_at ?? null,
            resumedAt: st.resumed_at ?? null,
          };
          return { ...bb, tasks, currentTaskIndex: taskIdx };
        });

        // drawer log auto append if drawer is showing this task
        if (shouldFetchLog && log?.content) {
          opts.setDrawerLog((prev) => (prev + log.content).slice(-20000));
          opts.setDrawerLogOffset(log.next_offset || logOffset + (log.content?.length || 0));
        }

        // Treat queued as still-active (waiting for worker). Do NOT finalize, otherwise:
        // - UI will think task ended and may try to re-start it
        // - quality_report?regen=1 will 404 and spam the network panel
        if (st.state !== "running" && st.state !== "queued") {
          stopPolling();
          await opts.finalizeTask(batchId, taskIdx, taskId, st);
          return;
        }
        const nextOffset = shouldFetchLog ? (log?.next_offset || logOffset) : logOffset;
        pollingTimer.current = setTimeout(() => pollOnce(batchId, taskIdx, taskId, nextOffset), opts.pollingMs);
      } catch {
        pollingTimer.current = setTimeout(
          () => pollOnce(batchId, taskIdx, taskId, logOffset),
          Math.max(opts.pollingMs * 2, 2000),
        );
      }
    },
    [opts, stopPolling],
  );

  const startPollingForTask = useCallback(
    (batchId: string, taskIdx: number, taskId: string) => {
      stopPolling();
      activeBackendTaskIdRef.current = taskId;
      const seedOffset =
        opts.showTaskLogs && opts.drawerOpen && opts.drawerTaskIndex === taskIdx ? Math.max(0, opts.drawerLogOffset || 0) : 0;
      pollOnce(batchId, taskIdx, taskId, seedOffset);
    },
    [pollOnce, stopPolling],
  );

  return { startPollingForTask, stopPolling, pollingTimer, activeBackendTaskIdRef };
}

