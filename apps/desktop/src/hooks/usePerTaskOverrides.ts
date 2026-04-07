import { useCallback, useMemo, useState } from "react";
import { Form } from "antd";
import type { BatchModel } from "../batchTypes";
import { normalizePerTaskOverrideValues, pickPerTaskOverrideValues } from "../app/domains/params/batchParams";

export function usePerTaskOverrides(opts: {
  form: { getFieldsValue: (all?: any) => any };
  wizardTasks: { localPath?: string; overrides?: Record<string, any> }[];
  setWizardTasks: (updater: (prev: any[]) => any[]) => void;
  batchesRef: React.MutableRefObject<BatchModel[]>;
  setBatches: React.Dispatch<React.SetStateAction<BatchModel[]>>;
}) {
  const [overrideModalOpen, setOverrideModalOpen] = useState(false);
  const [overrideEditing, setOverrideEditing] = useState<
    | { kind: "wizard"; wizardIdx: number }
    | { kind: "batch"; batchId: string; taskIndex: number }
    | null
  >(null);
  const [overrideForm] = Form.useForm();

  const currentOverrideLocalPath = useCallback((): string => {
    if (!overrideEditing) return "";
    if (overrideEditing.kind === "wizard") return opts.wizardTasks?.[overrideEditing.wizardIdx]?.localPath || "";
    const b = opts.batchesRef.current.find((x) => x.id === overrideEditing.batchId);
    return (b?.tasks?.[overrideEditing.taskIndex] as any)?.localPath || "";
  }, [overrideEditing, opts.batchesRef, opts.wizardTasks]);

  const openEraseSubOverrideEditor = useCallback(
    (target: { kind: "wizard"; wizardIdx: number } | { kind: "batch"; batchId: string; taskIndex: number }) => {
      setOverrideEditing(target as any);
      const batchBase = opts.form.getFieldsValue(true) || {};
      let current: Record<string, any> = {};
      if (target.kind === "wizard") {
        current = opts.wizardTasks[target.wizardIdx]?.overrides || {};
      } else {
        const b = opts.batchesRef.current.find((x) => x.id === target.batchId);
        current = (b?.tasks?.[target.taskIndex] as any)?.paramsOverride || {};
      }
      const merged = { ...pickPerTaskOverrideValues(batchBase), ...pickPerTaskOverrideValues(current) };
      overrideForm.setFieldsValue(merged);
      setOverrideModalOpen(true);
    },
    [opts, overrideForm],
  );

  const applyEraseSubOverrideToWizard = useCallback(
    (wizardIdx: number, values: Record<string, any>) => {
      opts.setWizardTasks((prev) => {
        const next = [...prev];
        const item = next[wizardIdx];
        next[wizardIdx] = { ...item, overrides: normalizePerTaskOverrideValues(values) };
        return next;
      });
    },
    [opts],
  );

  const applyEraseSubOverrideToBatch = useCallback(
    (batchId: string, taskIndex: number, values: Record<string, any>) => {
      opts.setBatches((prev) =>
        prev.map((b) => {
          if (b.id !== batchId) return b;
          const tasks = [...b.tasks];
          const t = tasks[taskIndex];
          tasks[taskIndex] = { ...t, paramsOverride: normalizePerTaskOverrideValues(values) };
          return { ...b, tasks };
        }),
      );
    },
    [opts],
  );

  return useMemo(
    () => ({
      overrideModalOpen,
      setOverrideModalOpen,
      overrideEditing,
      setOverrideEditing,
      overrideForm,
      currentOverrideLocalPath,
      openEraseSubOverrideEditor,
      applyEraseSubOverrideToWizard,
      applyEraseSubOverrideToBatch,
    }),
    [
      overrideModalOpen,
      overrideEditing,
      overrideForm,
      currentOverrideLocalPath,
      openEraseSubOverrideEditor,
      applyEraseSubOverrideToWizard,
      applyEraseSubOverrideToBatch,
    ],
  );
}

