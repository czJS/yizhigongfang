import { useRef } from "react";
import { Modal, message } from "antd";
import type { UploadRequestOption as RcCustomRequestOptions } from "rc-upload/lib/interface";
import type { FormInstance } from "antd";
import { uploadFile } from "../api";
import { createId } from "../app/appHelpers";
import type { BatchModel } from "../batchTypes";
import { getUploadDisplayName } from "../uploadInputHelpers";
import { defaultBatchName, nowTs } from "../utils";

export const MAX_BATCH_FILES = 10;

export function useWizardBatchCreation(opts: {
  wizardTasks: any[];
  setWizardTasks: (updater: (prev: any[]) => any[]) => void;
  setWizardUploading: (v: boolean) => void;
  mode: "lite" | "quality" | "online";
  outputDir: string;
  form: FormInstance;
  reviewEnabled: boolean;
  currentBatchRulesetOverride: () => any | null;
  savedSubtitleSettingsRef: React.MutableRefObject<any>;
  savedSubtitleSettings: any;
  regionPickerRect: { x: number; y: number; w: number; h: number };
  filterLiteFastParams: (p: Record<string, any>) => Record<string, any>;
  preset: string;
  batchName: string;
  setBatches: React.Dispatch<React.SetStateAction<BatchModel[]>>;
  setActiveBatchId: (id: string) => void;
  setWizardStep: (n: number) => void;
  setBatchName: (s: string) => void;
  setRoute: (r: any) => void;
  startQueue: (batchId: string, opts?: { navigate?: boolean }) => void;
  getTaskCreationBlockReason?: () => string;
}) {
  const uploadActiveCountRef = useRef(0);

  async function handleAddUpload(options: RcCustomRequestOptions) {
    const file = options.file as File;
    try {
      uploadActiveCountRef.current += 1;
      opts.setWizardUploading(true);
      const path = await uploadFile(file);
      const localPath = String((file as any)?.path || "");
      opts.setWizardTasks((prev) => {
        const nextItem = { inputName: getUploadDisplayName(file as any), inputPath: path, localPath, overrides: {} as Record<string, any> };
        if (prev.some((x: any) => String(x.inputPath || "") === path)) return prev;
        if (prev.length >= MAX_BATCH_FILES) {
          message.warning(`最多支持 ${MAX_BATCH_FILES} 个视频，超出的文件已忽略。`);
          return prev;
        }
        return [...prev, nextItem].slice(0, MAX_BATCH_FILES);
      });
      options.onSuccess?.({ path }, new XMLHttpRequest());
      message.success(`已选择：${getUploadDisplayName(file as any)}`);
    } catch (err: any) {
      options.onError?.(err);
      message.error(err?.message || "上传失败");
    } finally {
      uploadActiveCountRef.current = Math.max(0, uploadActiveCountRef.current - 1);
      opts.setWizardUploading(uploadActiveCountRef.current > 0);
    }
  }

  function moveTask(idx: number, delta: -1 | 1) {
    opts.setWizardTasks((prev) => {
      const next = [...prev];
      const j = idx + delta;
      if (j < 0 || j >= next.length) return prev;
      const tmp = next[idx];
      next[idx] = next[j];
      next[j] = tmp;
      return next;
    });
  }

  function removeTask(idx: number) {
    opts.setWizardTasks((prev) => prev.filter((_: any, i: number) => i !== idx));
  }

  async function createBatchAndGo(startNow: boolean) {
    const blockReason = String(opts.getTaskCreationBlockReason?.() || "").trim();
    if (blockReason) {
      message.warning(blockReason);
      return;
    }
    if (opts.wizardTasks.length === 0) {
      message.error("请先添加视频");
      return;
    }
    if (opts.wizardTasks.length > MAX_BATCH_FILES) {
      message.error(`最多支持 ${MAX_BATCH_FILES} 个视频`);
      return;
    }
    if (!opts.outputDir) {
      // Allow without output dir (web mode), but warn.
      message.warning("你还没有选择输出文件夹。你仍可继续，但需要手动下载交付物。");
    }
    let params = opts.form.getFieldsValue(true) || {};
    params.review_enabled = opts.reviewEnabled;
    const override = opts.currentBatchRulesetOverride();
    if (override) params.ruleset_override = override;
    const saved = opts.savedSubtitleSettingsRef.current || opts.savedSubtitleSettings;
    if (saved?.source === "has") {
      const rect = saved.rect || opts.regionPickerRect;
      params.erase_subtitle_enable = true;
      params.erase_subtitle_method = String(saved.values?.erase_subtitle_method || params.erase_subtitle_method || "auto");
      params.erase_subtitle_coord_mode = "ratio";
      params.erase_subtitle_x = rect.x;
      params.erase_subtitle_y = rect.y;
      params.erase_subtitle_w = rect.w;
      params.erase_subtitle_h = rect.h;
      params.erase_subtitle_blur_radius = Number(saved.values?.erase_subtitle_blur_radius || params.erase_subtitle_blur_radius || 0);
      params.sub_place_enable = true;
      params.sub_place_coord_mode = "ratio";
      params.sub_place_x = rect.x;
      params.sub_place_y = rect.y;
      params.sub_place_w = rect.w;
      params.sub_place_h = rect.h;
      if (typeof saved.fontSize === "number") {
        params.sub_font_size = saved.fontSize;
      }
    } else if (saved?.source === "none") {
      params.erase_subtitle_enable = false;
      Object.assign(params, saved.values || {});
    } else {
      params.erase_subtitle_enable = false;
    }

    // Quality mode: reduce user cognition.
    // Keep UX-only keys in params; backend will normalize them into effective pipeline params.
    if (opts.mode === "lite") {
      params = opts.filterLiteFastParams(params);
    }
    const batch: BatchModel = {
      id: createId(),
      name: opts.batchName || defaultBatchName(),
      createdAt: nowTs(),
      mode: opts.mode,
      preset: opts.preset,
      params,
      outputDir: opts.outputDir || "",
      state: startNow ? "running" : "draft",
      tasks: opts.wizardTasks.map((t: any, i: number) => ({
        index: i + 1,
        inputName: t.inputName,
        inputPath: t.inputPath,
        localPath: String((t as any)?.localPath || ""),
        state: "pending",
        paramsOverride: t.overrides || {},
      })),
    };
    opts.setBatches((prev) => [batch, ...prev]);
    opts.setActiveBatchId(batch.id);
    if (startNow) {
      // UX：开始处理后，清空向导，方便继续新建
      opts.setWizardStep(0);
      opts.setWizardTasks(() => []);
      opts.setBatchName(defaultBatchName());
      // 保留 outputDir / mode / preset / params（更贴近日常使用）
      Modal.confirm({
        title: "已开始处理",
        content: `批量任务「${batch.name}」已加入队列，将按上传顺序串行处理 ${batch.tasks.length} 个视频。`,
        centered: true,
        okText: "去任务中心",
        cancelText: "继续新建",
        onOk: () => opts.setRoute("workbench"),
        onCancel: () => opts.setRoute("wizard"),
      });
      opts.setRoute("wizard");
      // start after state applied
      setTimeout(() => opts.startQueue(batch.id, { navigate: false }), 0);
      return;
    }
    opts.setRoute("workbench");
  }

  return { handleAddUpload, moveTask, removeTask, createBatchAndGo };
}

