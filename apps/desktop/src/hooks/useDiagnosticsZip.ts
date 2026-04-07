import { useCallback } from "react";
import { message } from "antd";
import JSZip from "jszip";
import { downloadTaskFileBytes, getLog, getQualityReport } from "../api";
import type { BatchModel } from "../batchTypes";
import { safeStem, twoDigitIndex } from "../utils";

export function useDiagnosticsZip(opts: {
  activeBatch: BatchModel | null;
  getDefaultOutputsRoot: () => Promise<string>;
  openPath: (p: string) => Promise<void>;
}) {
  const exportDiagnosticZipForTask = useCallback(
    async (taskIdx: number, more?: { includeMedia?: boolean }) => {
      const b = opts.activeBatch;
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t.taskId) {
        message.error("该任务还没有 task_id");
        return;
      }
      if (!b.outputDir || !window.bridge?.writeFile || !window.bridge?.ensureDir) {
        message.info("请先选择输出文件夹（桌面版支持导出诊断包）。");
        return;
      }
      const includeMedia = !!more?.includeMedia;
      try {
        message.loading({ content: "正在打包诊断包…", key: `zip_${t.taskId}`, duration: 0 });
        const zip = new JSZip();
        const baseName = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
        const relDir = baseName;
        const baseDir = b.outputDir || (await opts.getDefaultOutputsRoot());
        if (!baseDir) {
          message.info("当前环境无法定位输出目录（仅 Electron 桌面版支持）。");
          return;
        }
        await window.bridge.ensureDir(baseDir, relDir);

        // 1) metadata
        zip.file(
          "批次信息.json",
          JSON.stringify({ id: b.id, name: b.name, mode: b.mode, preset: b.preset, createdAt: b.createdAt }, null, 2),
        );
        zip.file("任务信息.json", JSON.stringify({ index: t.index, inputName: t.inputName, taskId: t.taskId, state: t.state }, null, 2));
        try {
          const bundle = await window.bridge?.collectDiagnosticsBundle?.();
          if (bundle?.summary) {
            zip.file("应用信息.json", JSON.stringify(bundle.summary, null, 2));
          }
          for (const item of bundle?.files || []) {
            if (!item?.name || !item?.content) continue;
            zip.file(item.name, item.content);
          }
        } catch {
          // best effort
        }

        // 2) quality report (prefer API; fallback to artifacts download)
        try {
          const qr = await getQualityReport(t.taskId);
          zip.file("质量摘要.json", JSON.stringify(qr, null, 2));
        } catch {
          const q = t.artifacts?.find((x) => x.name === "quality_report.json");
          if (q) {
            const bytes = await downloadTaskFileBytes(t.taskId, q.path);
            zip.file("质量摘要.json", bytes);
          }
        }

        // 3) log (via API, loop to full)
        let offset = 0;
        let logAll = "";
        for (let i = 0; i < 300; i++) {
          const lr = await getLog(t.taskId, offset);
          if (!lr?.content) break;
          logAll += lr.content;
          offset = lr.next_offset || offset + lr.content.length;
          if (logAll.length > 2_000_000) break; // cap at ~2MB
        }
        zip.file("日志.txt", logAll || "暂无日志");

        // 4) key artifacts
        const wanted = new Set([
          "chs.srt",
          "eng.srt",
          "bilingual.srt",
          "chs.review.srt",
          "eng.review.srt",
          "terminology.json",
          "task_meta.json",
          "task_state.json",
          "quality_report.json",
        ]);
        const media = new Set(["output_en_sub.mp4", "output_en.mp4", "tts_full.wav"]);
        for (const a of t.artifacts || []) {
          if (wanted.has(a.name) || (includeMedia && media.has(a.name))) {
            const bytes = await downloadTaskFileBytes(t.taskId, a.path);
            zip.file(a.name, bytes);
          }
        }

        const zipBytes = await zip.generateAsync({ type: "uint8array" });
        const zipName = includeMedia ? "诊断包_含媒体.zip" : "诊断包.zip";
        await window.bridge.writeFile(baseDir, `${relDir}/${zipName}`, zipBytes);
        message.success({ content: "诊断包已导出", key: `zip_${t.taskId}` });
        await opts.openPath(`${baseDir}/${relDir}`);
      } catch (err: any) {
        message.error({ content: err?.message || "导出诊断包失败", key: `zip_${t.taskId}` });
      }
    },
    [opts],
  );

  return { exportDiagnosticZipForTask };
}

