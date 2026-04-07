import type { QualityReport, TaskStatus } from "../types";
import type { BatchTask, UiTaskState } from "../batchTypes";

export function uiStateFromBackend(state: TaskStatus["state"]): UiTaskState {
  if (state === "running") return "running";
  // Backend may report "queued" while waiting for an available worker. Treat it as running in UI
  // to prevent the scheduler from re-starting the same task.
  if (state === "queued") return "running";
  if (state === "completed") return "completed";
  if (state === "failed") return "failed";
  if (state === "cancelled") return "cancelled";
  if (state === "paused") return "paused";
  return "pending";
}

export function tagColorForUiState(state: UiTaskState) {
  switch (state) {
    case "running":
      return "processing";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "paused":
      return "warning";
    case "cancelled":
      return "default";
    default:
      return "default";
  }
}

export function shortReason(task: BatchTask): string {
  if (task.state === "failed") return task.failureReason || "失败（点开查看原因）";
  if (task.state === "paused") return "已暂停（需要你处理后继续）";
  if (task.state === "cancelled") return "已取消";
  if (task.state === "completed") {
    if (task.qualityPassed === false) return "已完成（质量检查未通过）";
    return "可交付";
  }
  if (task.state === "running") return task.stageName || task.message || "处理中…";
  return "等待处理";
}

export function createId(): string {
  // @ts-ignore
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function demonstrateDownloadAnchor(a: HTMLAnchorElement, url: string, filename: string) {
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Allow the browser to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  demonstrateDownloadAnchor(a, url, filename);
}

export function suggestForIssue(msg: string) {
  const s = (msg || "").toLowerCase();
  // Readability / subtitle-specific (user-friendly)
  if (s.includes("阅读速度过快") || s.includes("reading speed") || s.includes("cps")) {
    return "建议：字幕太密会不好读。可在「审校」里适当删减/拆分句子；若要一键改善可读性，建议用质量模式重跑（默认开启可读性修复）。";
  }
  if (s.includes("单行过长") || s.includes("每行不超过") || s.includes("overly long lines") || s.includes("chars")) {
    return "建议：把长句拆成两行或精简表达；质量模式会默认做字幕可读性优化（更省时间）。";
  }
  if (s.includes("含有中文") || s.includes("全角字符") || s.includes("非英文字符") || s.includes("cjk")) {
    return "建议：检查英文字幕是否混入中文（常见于双语/占位符残留）。可在「审校」里修正后再生成交付物。";
  }
  if (s.includes("时间轴重叠") || s.includes("重叠") || s.includes("overlap")) {
    return "建议：字幕时间轴重叠可能导致闪烁/覆盖。优先检查是否有合并/断句异常；必要时在「审校」里合并短句或调整断句。";
  }
  if (s.includes("时长为 0") || s.includes("负数") || s.includes("non-positive")) {
    return "建议：字幕时长异常会影响播放与配音对齐。建议重新生成字幕或在「审校」中合并极短块。";
  }
  if (s.includes("missing") || s.includes("not found") || s.includes("缺失")) {
    return "建议：检查产物是否生成完整；必要时重新生成。";
  }
  if (s.includes("未生成部分交付产物") || s.includes("未生成")) {
    return "建议：若你需要成片/配音，请确认没有开启“只出字幕”等选项；也可以查看日志定位失败原因。";
  }
  if (s.includes("duration") || s.includes("length") || s.includes("时长")) {
    return "建议：检查视频/音频是否截断；可尝试重新生成。";
  }
  if (s.includes("srt") || s.includes("subtitle") || s.includes("字幕")) {
    return "建议：检查字幕格式与时间轴；必要时重新生成字幕。";
  }
  if (s.includes("audio") || s.includes("tts") || s.includes("音频")) {
    return "建议：检查配音质量；可尝试调整参数后重跑。";
  }
  return "建议：查看日志或导出诊断包定位原因。";
}

export function normalizeLegacyQualityIssueText(msg: string): string {
  const s = String(msg || "").trim();
  if (!s) return s;
  // Legacy English readability messages (older quality_report.json)
  // Examples:
  // - "eng.srt reading speed too high (> 20.0 cps): 10 items"
  // - "eng.srt overly long lines (> 42 chars): 12 items"
  let m = s.match(/^eng\.srt\s+reading\s+speed\s+too\s+high\s+\(>\s*([0-9.]+)\s*cps\)\s*:\s*([0-9]+)\s*items?/i);
  if (m) {
    const max = m[1];
    const n = m[2];
    return `英文字幕阅读速度过快：发现 ${n} 条（建议不超过 ${max} 字符/秒）。`;
  }
  m = s.match(/^eng\.srt\s+overly\s+long\s+lines\s+\(>\s*([0-9.]+)\s*chars?\)\s*:\s*([0-9]+)\s*items?/i);
  if (m) {
    const max = m[1];
    const n = m[2];
    return `英文字幕单行过长：发现 ${n} 条（建议单行不超过 ${max} 字符）。`;
  }
  // General cleanup: strip noisy prefixes like "eng.srt "
  if (/^eng\.srt\s+/i.test(s)) return s.replace(/^eng\.srt\s+/i, "英文字幕：");
  return s;
}

export function issueTag(msg: string): { label: string; color: string } {
  const s = (msg || "").toLowerCase();
  if (
    s.includes("阅读速度过快") ||
    s.includes("reading speed") ||
    s.includes("cps") ||
    s.includes("单行过长") ||
    s.includes("overly long lines") ||
    s.includes("空行比例") ||
    s.includes("编号/项目符号") ||
    s.includes("numbering/bullets")
  ) {
    return { label: "可读性", color: "geekblue" };
  }
  if (s.includes("含有中文") || s.includes("全角字符") || s.includes("非英文字符") || s.includes("cjk")) {
    return { label: "语言", color: "magenta" };
  }
  if (s.includes("时间轴") || s.includes("重叠") || s.includes("时长为 0") || s.includes("负数")) {
    return { label: "时间轴", color: "orange" };
  }
  if (s.includes("missing") || s.includes("not found") || s.includes("缺失")) return { label: "产物", color: "red" };
  if (s.includes("duration") || s.includes("length") || s.includes("时长") || s.includes("truncate")) return { label: "时长", color: "orange" };
  if (s.includes("srt") || s.includes("subtitle") || s.includes("字幕")) return { label: "字幕", color: "blue" };
  if (s.includes("audio") || s.includes("tts") || s.includes("音频")) return { label: "音频", color: "purple" };
  return { label: "其它", color: "default" };
}

export type QualityExampleGroup = { title: string; items: string[] };

export function qualityExampleGroups(qr?: QualityReport | null): QualityExampleGroup[] {
  const checks: any = (qr as any)?.checks || {};
  const groups: QualityExampleGroup[] = [];

  const ra = checks?.required_artifacts || {};
  const missingRequired: any[] = (ra?.missing_required || ra?.missing || []) as any[];
  const missingExpected: any[] = (ra?.missing_expected || []) as any[];
  if (Array.isArray(missingRequired) && missingRequired.length) {
    groups.push({ title: "缺少关键产物（会影响交付）", items: missingRequired.map((x) => String(x)) });
  }
  if (Array.isArray(missingExpected) && missingExpected.length) {
    groups.push({ title: "未生成的交付物（可能是选项/失败导致）", items: missingExpected.map((x) => String(x)) });
  }

  const cjk = checks?.english_purity;
  if (cjk && Number(cjk.cjk_hits_n || 0) > 0) {
    const hits = Array.isArray(cjk.cjk_hits) ? cjk.cjk_hits : [];
    groups.push({
      title: `英文字幕含中文/全角字符（${Number(cjk.cjk_hits_n || hits.length)}）`,
      items: hits.slice(0, 3).map((h: any) => `#${h?.idx ?? "?"}：${String(h?.text || "").slice(0, 80)}`),
    });
  }

  const ll = checks?.line_length;
  if (ll && Number(ll.hits_n || 0) > 0) {
    const hits = Array.isArray(ll.hits) ? ll.hits : [];
    groups.push({
      title: `英文字幕单行过长（${Number(ll.hits_n || hits.length)}）`,
      items: hits.slice(0, 3).map((h: any) => `#${h?.idx ?? "?"}：${String(h?.text || "").slice(0, 80)}`),
    });
  }

  const rs = checks?.reading_speed;
  if (rs && Number(rs.hits_n || 0) > 0) {
    const hits = Array.isArray(rs.hits) ? rs.hits : [];
    groups.push({
      title: `英文字幕阅读速度过快（${Number(rs.hits_n || hits.length)}）`,
      items: hits
        .slice(0, 3)
        .map((h: any) => `#${h?.idx ?? "?"}：${String(h?.text || "").slice(0, 60)}（约 ${h?.cps ?? "?"} 字符/秒）`),
    });
  }

  const ts = checks?.timeline_sanity;
  if (ts && (Number(ts.negative_or_zero_dur_n || 0) > 0 || Number(ts.overlap_n || 0) > 0)) {
    const items: string[] = [];
    const neg = Array.isArray(ts.negative_or_zero_dur) ? ts.negative_or_zero_dur : [];
    const ov = Array.isArray(ts.overlaps) ? ts.overlaps : [];
    for (const h of neg.slice(0, 2)) items.push(`#${h?.idx ?? "?"}：时长异常（${h?.dur_s ?? "?"}s）`);
    for (const h of ov.slice(0, 2)) items.push(`#${h?.idx ?? "?"}：与上一条重叠（约 ${h?.overlap_s ?? "?"}s）`);
    groups.push({ title: "字幕时间轴异常（可能导致闪烁/覆盖）", items });
  }

  return groups.filter((g) => (g.items || []).length > 0);
}

export function splitList(text: string): string[] {
  if (!text) return [];
  return text
    .split(/[,;\n]/g)
    .map((x) => x.trim())
    .filter(Boolean);
}

export function joinList(items: string[]): string {
  return (items || []).filter(Boolean).join(", ");
}

