import { useCallback, useEffect, useRef, useState } from "react";
import type { BatchModel } from "../batchTypes";
import { loadActiveBatchId, loadBatches, saveActiveBatchId, saveBatches } from "../batchStorage";

export function usePersistedBatches(opts?: { onRestoreActiveBatch?: () => void }) {
  const [batches, _setBatches] = useState<BatchModel[]>([]);
  const [activeBatchId, _setActiveBatchId] = useState<string>("");
  const batchesRef = useRef<BatchModel[]>([]);
  const activeBatchIdRef = useRef<string>("");

  // IMPORTANT:
  // Queue scheduling relies on batchesRef.current. React may defer/batch state updaters,
  // so updating the ref inside setState(updater) can still be too late.
  // We compute next from the ref synchronously, update ref first, then setState(next).
  const setBatches = useCallback((updater: React.SetStateAction<BatchModel[]>) => {
    const prev = batchesRef.current;
    const next = typeof updater === "function" ? (updater as any)(prev) : updater;
    batchesRef.current = next as BatchModel[];
    try {
      saveBatches(next as BatchModel[]);
    } catch {
      // ignore
    }
    _setBatches(next as BatchModel[]);
  }, []);

  const setActiveBatchId = useCallback((id: React.SetStateAction<string>) => {
    const prev = activeBatchIdRef.current;
    const next = typeof id === "function" ? (id as any)(prev) : id;
    activeBatchIdRef.current = String(next || "");
    try {
      saveActiveBatchId(String(next || ""));
    } catch {
      // ignore
    }
    _setActiveBatchId(String(next || ""));
  }, []);

  useEffect(() => {
    const saved = loadBatches();
    _setBatches(saved);
    batchesRef.current = saved;

    const act = loadActiveBatchId();
    if (act && saved.some((b) => b.id === act)) {
      _setActiveBatchId(act);
      activeBatchIdRef.current = act;
      opts?.onRestoreActiveBatch?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { batches, setBatches, activeBatchId, setActiveBatchId, batchesRef, activeBatchIdRef };
}

