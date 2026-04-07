import { useCallback, useEffect, useMemo, useState } from "react";
import { getLog } from "../api";
import type { BatchModel } from "../batchTypes";

export function useTaskDrawer(opts: { activeBatch: BatchModel | null; showTaskLogs: boolean }) {
  const [open, setOpen] = useState(false);
  const [taskIndex, setTaskIndex] = useState<number>(-1);
  const [initialTab, setInitialTab] = useState<string>("quality");
  const [log, setLog] = useState<string>("");
  const [logOffset, setLogOffset] = useState(0);
  const [logLoading, setLogLoading] = useState(false);

  const calcWidth = useCallback(() => {
    try {
      const w = typeof window !== "undefined" ? window.innerWidth : 1200;
      // Wide enough for review table; bounded to avoid covering whole screen.
      return Math.min(1500, Math.max(920, Math.floor(w * 0.92)));
    } catch {
      return 1200;
    }
  }, []);

  const [width, setWidth] = useState<number>(() => calcWidth());

  useEffect(() => {
    const onResize = () => setWidth(calcWidth());
    try {
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    } catch {
      return;
    }
  }, [calcWidth]);

  const close = useCallback(() => setOpen(false), []);

  const openTaskDrawer = useCallback(
    async (idx: number, tab: string = "quality") => {
      setTaskIndex(idx);
      setInitialTab(tab);
      setOpen(true);
      setLog("");
      setLogOffset(0);
      const b = opts.activeBatch;
      if (!b) return;
      const t = b.tasks[idx];
      if (t?.taskId && opts.showTaskLogs) {
        setLogLoading(true);
        try {
          // Use tail mode so users see "latest" instead of being stuck on the first 8k chars.
          const res = await getLog(t.taskId, -8000);
          setLog(res.content || "");
          setLogOffset(res.next_offset || 0);
        } catch {
          setLog("");
          setLogOffset(0);
        } finally {
          setLogLoading(false);
        }
      }
    },
    [opts.activeBatch, opts.showTaskLogs],
  );

  return useMemo(
    () => ({
      drawerOpen: open,
      setDrawerOpen: setOpen,
      drawerTaskIndex: taskIndex,
      setDrawerTaskIndex: setTaskIndex,
      drawerInitialTab: initialTab,
      setDrawerInitialTab: setInitialTab,
      drawerLog: log,
      setDrawerLog: setLog,
      drawerLogOffset: logOffset,
      setDrawerLogOffset: setLogOffset,
      drawerLogLoading: logLoading,
      drawerWidth: width,
      setDrawerWidth: setWidth,
      openTaskDrawer,
      closeTaskDrawer: close,
    }),
    [open, taskIndex, initialTab, log, logOffset, logLoading, width, openTaskDrawer, close],
  );
}

