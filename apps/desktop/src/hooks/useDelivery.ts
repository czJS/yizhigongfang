import { useCallback } from "react";
import { message } from "antd";
import { downloadTaskFileBytes, getArtifacts } from "../api";
import type { BatchModel, BatchTask } from "../batchTypes";
import type { UiPrefs } from "../batchStorage";
import { safeStem, twoDigitIndex } from "../utils";

export function useDelivery(opts: {
  batchesRef: React.MutableRefObject<BatchModel[]>;
  updateActiveBatchById: (batchId: string, updater: (b: BatchModel) => BatchModel) => void;
  openPath: (p: string) => Promise<void>;
  uiPrefsRef?: React.MutableRefObject<UiPrefs>;
}) {
  const getDefaultOutputsRoot = useCallback(async (): Promise<string> => {
    const runtimeOutputsRoot = (await window.bridge?.getDefaultOutputsRoot?.()) || "";
    if (runtimeOutputsRoot) return runtimeOutputsRoot;
    return "";
  }, []);

  const openDefaultOutputsFolder = useCallback(
    async (relDir?: string) => {
      const base = await getDefaultOutputsRoot();
      if (!base) {
        message.info("当前环境无法定位默认 outputs 目录（仅 Electron 桌面版支持）。");
        return;
      }
      const p = relDir ? `${base}/${relDir}` : base;
      await opts.openPath(p);
    },
    [getDefaultOutputsRoot, opts],
  );

  const openBatchOutputFolder = useCallback(
    async (b: BatchModel) => {
      try {
        const base = b.outputDir || (await getDefaultOutputsRoot());
        if (!base) {
          message.info("当前环境无法定位输出目录。");
          return;
        }
        const delivered = (b.tasks || []).filter((t) => !!t.deliveredDir);
        const target = delivered.length === 1 ? `${base}/${delivered[0].deliveredDir}` : base;
        await opts.openPath(target);
      } catch (err: any) {
        message.error(err?.message || "打开失败");
      }
    },
    [getDefaultOutputsRoot, opts],
  );

  const openDeliveredDirForTask = useCallback(
    async (b: BatchModel, t: BatchTask) => {
      try {
        const base = b.outputDir || (await getDefaultOutputsRoot());
        if (!base) {
          message.info("当前环境无法定位输出目录。");
          return;
        }
        if (!t.deliveredDir) {
          message.info("该任务尚未交付");
          return;
        }
        await opts.openPath(`${base}/${t.deliveredDir}`);
      } catch (err: any) {
        message.error(err?.message || "打开失败");
      }
    },
    [getDefaultOutputsRoot, opts],
  );

  const deliverTaskToOutputDir = useCallback(
    async (batchId: string, taskIdx: number, artifactsOverride?: { name: string; path: string; size: number }[]) => {
      const b = opts.batchesRef.current.find((x) => x.id === batchId);
      if (!b) return;
      const t = b.tasks[taskIdx];
      let arts = artifactsOverride || t.artifacts || [];
      if (!t.taskId) return;
      // 某些情况下，任务刚结束时 artifacts 还没刷新到前端 state；这里补一次拉取 + 重试，避免“没自动保存”
      if (!arts || arts.length === 0) {
        try {
          arts = await getArtifacts(t.taskId);
        } catch {
          arts = [];
        }
      }
      if (!arts || arts.length === 0) return;
      const baseDir = b.outputDir || (await getDefaultOutputsRoot());
      if (!baseDir) return;
      const ensureDir = window.bridge?.ensureDir;
      const writeFile = window.bridge?.writeFile;
      if (!ensureDir || !writeFile) return;

      const baseName = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
      const relDir = baseName;
      await ensureDir(baseDir, relDir);

      const includeOptionals = Boolean(opts.uiPrefsRef?.current?.deliveryIncludeOptionals);

      const wantedMinimal: { name: string; label: string; out: string }[] = [
        { name: "output_en_sub.mp4", label: "成片（带字幕）", out: `${baseName}.mp4` },
        { name: "eng.srt", label: "字幕（英文）", out: "eng.srt" },
        { name: "quality_report.json", label: "质量报告", out: "quality_report.json" },
      ];
      const wantedOptionals: { name: string; label: string; out: string }[] = [
        { name: "output_en.mp4", label: "成片（不带字幕）", out: `${baseName}.no_sub.mp4` },
        { name: "tts_full.wav", label: "配音全音频", out: "tts_full.wav" },
        { name: "audio.wav", label: "抽取音频", out: "audio.wav" },
        // Optional subtitle variants (helpful for review / troubleshooting)
        { name: "chs.srt", label: "字幕（中文）", out: "chs.srt" },
        { name: "bilingual.srt", label: "字幕（双语）", out: "bilingual.srt" },
        { name: "display.srt", label: "字幕（展示/可读性）", out: "display.srt" },
      ];

      const wanted = includeOptionals ? [...wantedMinimal, ...wantedOptionals] : wantedMinimal;

      const present = wanted
        .map((w) => ({ w, a: arts.find((x) => x.name === w.name) }))
        .filter((x) => !!x.a) as { w: (typeof wanted)[number]; a: { name: string; path: string; size: number } }[];

      if (present.length === 0) return;

      // Small UX: show one message, keep it brief
      message.loading({ content: `正在保存交付物：${t.inputName}`, key: `deliver_${t.taskId}`, duration: 0 });
      const deliveredFiles: { label: string; filename: string }[] = [];
      for (const item of present) {
        const bytes = await downloadTaskFileBytes(t.taskId, item.a.path);
        await writeFile(baseDir, `${relDir}/${item.w.out}`, bytes);
        deliveredFiles.push({ label: item.w.label, filename: item.w.out });
      }
      message.success({ content: `已保存到输出目录：${t.inputName}`, key: `deliver_${t.taskId}` });

      opts.updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        tasks[taskIdx] = { ...tasks[taskIdx], deliveredDir: relDir, deliveredFiles };
        return { ...bb, tasks };
      });
    },
    [getDefaultOutputsRoot, opts],
  );

  return { getDefaultOutputsRoot, openDefaultOutputsFolder, openBatchOutputFolder, openDeliveredDirForTask, deliverTaskToOutputDir };
}

