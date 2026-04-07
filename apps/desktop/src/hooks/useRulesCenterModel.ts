import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { message } from "antd";
import {
  createRulesetTemplate,
  deleteRulesetTemplate,
  getGlobalRuleset,
  getRulesetTemplate,
  listRulesetTemplates,
  putGlobalRuleset,
  putRulesetTemplate,
  uploadRulesetFile,
  uploadRulesetTemplateFile,
} from "../services/rulesApi";
import { downloadTextFile } from "../app/appHelpers";
import type { RulesetTemplateInfo } from "../types";
import type { ReplaceRuleRow } from "../app/domains/rules/replaceRows";
import { hasAnyRulesInReplaceRows, newReplaceRuleRow, replaceRowsFromRulesetDoc, rulesetDocFromReplaceRows } from "../app/domains/rules/replaceRows";

export function useRulesCenterModel() {
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState<string>("");
  const [globalReplaceRows, setGlobalReplaceRows] = useState<ReplaceRuleRow[]>([]);
  const [batchReplaceRows, setBatchReplaceRows] = useState<ReplaceRuleRow[]>([]);

  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesError, setTemplatesError] = useState<string>("");
  const [templates, setTemplates] = useState<RulesetTemplateInfo[]>([]);

  const refreshRulesTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    setTemplatesError("");
    try {
      const items = await listRulesetTemplates();
      setTemplates(items || []);
    } catch (err: any) {
      setTemplates([]);
      setTemplatesError(err?.message || "加载模板失败");
    } finally {
      setTemplatesLoading(false);
    }
  }, []);

  const onOpenRules = useCallback(async () => {
    setRulesLoading(true);
    setRulesError("");
    try {
      const rs = await getGlobalRuleset();
      suppressAutoSaveRef.current = true;
      setGlobalReplaceRows(replaceRowsFromRulesetDoc(rs as any));
      setTimeout(() => {
        suppressAutoSaveRef.current = false;
      }, 0);
      // batch override is per-wizard session; keep current edits; templates list is loaded via bootstrap best-effort
    } catch (err: any) {
      setGlobalReplaceRows([]);
      setRulesError(err?.message || "加载失败");
    } finally {
      setRulesLoading(false);
    }
  }, []);

  const onSaveGlobalRules = useCallback(
    async (docOverride?: any, opts?: { silent?: boolean }) => {
      const silent = !!opts?.silent;
      if (!silent) {
        setRulesLoading(true);
        setRulesError("");
      }
      try {
        const doc = docOverride || rulesetDocFromReplaceRows(globalReplaceRows, "global");
        await putGlobalRuleset(doc as any);
      } catch (err: any) {
        // Avoid noisy UI; surface the error in the warning banner.
        setRulesError(err?.message || "保存失败");
        if (!silent) message.error(err?.message || "保存失败");
      } finally {
        if (!silent) setRulesLoading(false);
      }
    },
    [globalReplaceRows],
  );

  // Auto-save global rules (debounced). This removes the need for a separate "保存全局" mental model.
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressAutoSaveRef = useRef(false);
  useEffect(() => {
    if (suppressAutoSaveRef.current) return;
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    const doc = rulesetDocFromReplaceRows(globalReplaceRows, "global");
    // If empty doc, still save (user cleared all rules intentionally).
    autoSaveTimerRef.current = setTimeout(() => {
      onSaveGlobalRules(doc, { silent: true }).catch(() => {});
    }, 800);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [globalReplaceRows, onSaveGlobalRules]);

  const onUploadRulesetFile = useCallback(async (file: File) => {
    setRulesLoading(true);
    setRulesError("");
    try {
      const rs = await uploadRulesetFile(file);
      suppressAutoSaveRef.current = true;
      setGlobalReplaceRows(replaceRowsFromRulesetDoc(rs as any));
      message.success("已导入");
      setTimeout(() => {
        suppressAutoSaveRef.current = false;
      }, 0);
    } catch (err: any) {
      setRulesError(err?.message || "导入失败");
      message.error(err?.message || "导入失败");
    } finally {
      setRulesLoading(false);
    }
  }, []);

  const currentBatchRulesetOverride = useCallback((): any | null => {
    if (!hasAnyRulesInReplaceRows(batchReplaceRows)) return null;
    return rulesetDocFromReplaceRows(batchReplaceRows, "batch");
  }, [batchReplaceRows]);

  const onImportBatchOverride = useCallback(async (file: File) => {
    try {
      const text = await (file as any).text?.();
      const obj = JSON.parse(String(text || "{}"));
      setBatchReplaceRows(replaceRowsFromRulesetDoc(obj as any));
      message.success("已导入到本次");
    } catch (e: any) {
      message.error(e?.message || "导入失败");
    }
  }, []);

  const onDownloadGlobalRules = useCallback(() => {
    downloadTextFile(
      "ruleset.global.json",
      JSON.stringify(
        rulesetDocFromReplaceRows(globalReplaceRows, "global"),
        null,
        2,
      ),
    );
  }, [globalReplaceRows]);

  const onDownloadBatchOverride = useCallback(() => {
    downloadTextFile(
      "ruleset_override.json",
      JSON.stringify(currentBatchRulesetOverride() || { version: 1, asr_fixes: [], en_fixes: [], settings: {} }, null, 2),
    );
  }, [currentBatchRulesetOverride]);

  const onClearBatchOverride = useCallback(() => {
    setBatchReplaceRows([]);
    message.success("已清空");
  }, []);

  const onAddGlobalReplaceRow = useCallback((stage?: "asr" | "en") => {
    // If user starts editing while we're suppressing auto-save (e.g. immediately after open),
    // lift suppression so the first edit can still be auto-saved.
    suppressAutoSaveRef.current = false;
    setGlobalReplaceRows((prev) => [...prev, newReplaceRuleRow(stage || "asr")]);
  }, []);
  const onRemoveGlobalReplaceRow = useCallback((id: string) => {
    suppressAutoSaveRef.current = false;
    setGlobalReplaceRows((prev) => prev.filter((r) => r.id !== id));
  }, []);
  const onUpdateGlobalReplaceRow = useCallback((id: string, patch: Partial<ReplaceRuleRow>) => {
    suppressAutoSaveRef.current = false;
    setGlobalReplaceRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }, []);

  const onAddBatchReplaceRow = useCallback((stage?: "asr" | "en") => {
    setBatchReplaceRows((prev) => [...prev, newReplaceRuleRow(stage || "asr")]);
  }, []);
  const onRemoveBatchReplaceRow = useCallback((id: string) => {
    setBatchReplaceRows((prev) => prev.filter((r) => r.id !== id));
  }, []);
  const onUpdateBatchReplaceRow = useCallback((id: string, patch: Partial<ReplaceRuleRow>) => {
    setBatchReplaceRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }, []);

  const applyBatchOverrideFromDoc = useCallback((doc: any) => {
    setBatchReplaceRows(replaceRowsFromRulesetDoc(doc));
  }, []);

  const resetWizardRulesState = useCallback(() => {
    setBatchReplaceRows([]);
  }, []);

  const fetchTemplate = useCallback(async (templateId: string) => {
    return await getRulesetTemplate(templateId);
  }, []);

  const saveTemplate = useCallback(async (templateId: string, payload: { name?: string; doc?: any }) => {
    return await putRulesetTemplate(templateId, payload);
  }, []);

  const removeTemplate = useCallback(async (templateId: string) => {
    await deleteRulesetTemplate(templateId);
  }, []);

  const createTemplate = useCallback(async (payload: { name: string; doc?: any }) => {
    return await createRulesetTemplate(payload);
  }, []);

  const importTemplateFile = useCallback(async (file: File) => {
    return await uploadRulesetTemplateFile(file);
  }, []);

  const api = useMemo(
    () => ({
      rulesError,
      rulesLoading,
      globalReplaceRows,
      batchReplaceRows,
      onOpenRules,
      onSaveGlobalRules,
      onUploadRulesetFile,
      onDownloadGlobalRules,
      onDownloadBatchOverride,
      onImportBatchOverride,
      onClearBatchOverride,
      onAddGlobalReplaceRow,
      onRemoveGlobalReplaceRow,
      onUpdateGlobalReplaceRow,
      onAddBatchReplaceRow,
      onRemoveBatchReplaceRow,
      onUpdateBatchReplaceRow,
      currentBatchRulesetOverride,
      applyBatchOverrideFromDoc,
      resetWizardRulesState,
      templates,
      templatesLoading,
      templatesError,
      refreshRulesTemplates,
      fetchTemplate,
      saveTemplate,
      removeTemplate,
      createTemplate,
      importTemplateFile,
    }),
    [
      rulesError,
      rulesLoading,
      globalReplaceRows,
      batchReplaceRows,
      onOpenRules,
      onSaveGlobalRules,
      onUploadRulesetFile,
      onDownloadGlobalRules,
      onDownloadBatchOverride,
      onImportBatchOverride,
      onClearBatchOverride,
      onAddGlobalReplaceRow,
      onRemoveGlobalReplaceRow,
      onUpdateGlobalReplaceRow,
      onAddBatchReplaceRow,
      onRemoveBatchReplaceRow,
      onUpdateBatchReplaceRow,
      currentBatchRulesetOverride,
      applyBatchOverrideFromDoc,
      resetWizardRulesState,
      templates,
      templatesLoading,
      templatesError,
      refreshRulesTemplates,
      fetchTemplate,
      saveTemplate,
      removeTemplate,
      createTemplate,
      importTemplateFile,
    ],
  );

  return api;
}

