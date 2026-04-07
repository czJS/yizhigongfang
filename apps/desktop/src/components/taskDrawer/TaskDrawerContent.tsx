import React, { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Collapse,
  Input,
  List,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  apiBase,
  getChsSrt2,
  getEngSrt,
  getQualityReport,
  putChsReviewSrt,
  putEngReviewSrt,
} from "../../api";
import type { AppConfig, QualityReport } from "../../types";
import type { BatchModel } from "../../batchTypes";
import { issueTag, normalizeLegacyQualityIssueText, qualityExampleGroups, suggestForIssue } from "../../app/appHelpers";

const { Text } = Typography;

export function TaskDrawerContent(props: {
  batch: BatchModel;
  taskIndex: number;
  initialTab?: string;
  onOpenOutput: (relDir?: string) => void;
  qualityGates?: AppConfig["quality_gates"];
  showLogs?: boolean;
  logText: string;
  logLoading: boolean;
  onResume: (resumeFrom: "asr" | "mt" | "tts" | "mux") => void;
  onRunReview: (lang: "chs" | "eng") => void;
  onApplyReview: (action: "mux" | "embed" | "mux_embed", use?: "review" | "base") => void;
  onExportDiagnostic: (opts?: { includeMedia?: boolean }) => void;
  onCleanup: (taskIndex: number) => void;
  onUpgradeToQuality?: (task: { inputName: string; inputPath: string; localPath?: string }) => void;
  onGoSystem?: () => void;
}) {
  const {
    batch,
    taskIndex,
    initialTab,
    onOpenOutput,
    qualityGates,
    showLogs,
    logText,
    logLoading,
    onResume,
    onRunReview,
    onApplyReview,
    onExportDiagnostic,
    onCleanup,
    onUpgradeToQuality,
    onGoSystem,
  } = props;
  const t = batch.tasks[taskIndex];
  const arts = t.artifacts || [];
  const downloadUrl = (path: string) => `${apiBase}/api/tasks/${t.taskId}/download?path=${encodeURIComponent(path)}`;

  function hasArtifact(name: string): boolean {
    return (arts || []).some((a) => a && a.name === name);
  }

  function anyMissingDeliverables(qr: QualityReport | null): boolean {
    try {
      const ra: any = (qr as any)?.checks?.required_artifacts || {};
      const missReq = Array.isArray(ra?.missing_required) ? ra.missing_required : Array.isArray(ra?.missing) ? ra.missing : [];
      const missExp = Array.isArray(ra?.missing_expected) ? ra.missing_expected : [];
      return (missReq?.length || 0) > 0 || (missExp?.length || 0) > 0;
    } catch {
      return false;
    }
  }

  function detectLiteFailureHints(): { title: string; items: string[] } | null {
    if (batch.mode !== "lite") return null;
    if (!["failed", "paused"].includes(t.state)) return null;
    const tail = String(logText || "").slice(-12000);
    const s = tail.toLowerCase();
    const items: string[] = [];
    if (s.includes("missing required tool") || s.includes("ffmpeg not found") || s.includes("ffprobe not found")) {
      items.push("可能原因：系统缺少 ffmpeg/ffprobe（或打包资源未正确包含）。");
    }
    if (s.includes("missing required tool") || (s.includes("piper") && s.includes("not found"))) {
      items.push("可能原因：缺少 Piper 可执行文件（或被杀软拦截）。");
    }
    if (s.includes("whisper") && (s.includes("not found") || s.includes("no such file"))) {
      items.push("可能原因：ASR 工具或模型文件路径不存在（模型未导入或目录结构不对）。");
    }
    if (s.includes("hf_hub_offline") || s.includes("transformers_offline")) {
      items.push("可能原因：当前为离线模式，但所需模型未在本地缓存中。");
    }
    if (s.includes("permission denied") || s.includes("access is denied")) {
      items.push("可能原因：权限不足（输出目录不可写 / 文件被占用）。");
    }
    if (s.includes("out of memory") || s.includes("killed") || s.includes("memoryerror")) {
      items.push("可能原因：内存不足导致进程被系统终止（建议关掉其它程序或换更小模型/更短视频测试）。");
    }
    if (!items.length) return null;
    return { title: "可能原因（基于日志关键字推断）", items };
  }

  // Review tab state (kept local to drawer)
  // Lite-Fast UX: zh/en are edited side-by-side; "更新成片" always starts from Chinese (resume_from=mt).
  const [reviewWhich] = useState<"base" | "review">("review");
  // Table-driven editor state: use CHS blocks as the source of truth for timings.
  const [reviewBaseChsBlocks, setReviewBaseChsBlocks] = useState<SrtBlock[]>([]);
  const [reviewChsBlocks, setReviewChsBlocks] = useState<SrtBlock[]>([]);
  const [reviewEngTexts, setReviewEngTexts] = useState<string[]>([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewMergeUndo, setReviewMergeUndo] = useState<{ chs: SrtBlock[]; eng: string[] }[]>([]);

  function cloneBlocks(xs: SrtBlock[]): SrtBlock[] {
    return (xs || []).map((x) => ({ ...x }));
  }

  function pushMergeUndo() {
    setReviewMergeUndo((prev) => {
      const snap = { chs: cloneBlocks(reviewChsBlocks || []), eng: [...(reviewEngTexts || [])] };
      // cap history to avoid unbounded growth
      const next = [snap, ...(prev || [])];
      return next.slice(0, 30);
    });
  }

  type SrtBlock = { idx: number; start: string; end: string; text: string };
  function parseSrtBlocks(raw: string): SrtBlock[] {
    const s = String(raw || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = s.split("\n");
    const out: SrtBlock[] = [];
    let i = 0;
    const isTiming = (ln: string) => ln.includes("-->");
    while (i < lines.length) {
      while (i < lines.length && !lines[i].trim()) i++;
      if (i >= lines.length) break;
      const idxLine = (lines[i] || "").trim();
      const idx = Number(idxLine);
      if (!Number.isFinite(idx)) {
        i++;
        continue;
      }
      i++;
      if (i >= lines.length) break;
      const timing = (lines[i] || "").trim();
      if (!isTiming(timing)) {
        i++;
        continue;
      }
      const parts = timing.split("-->");
      const start = (parts[0] || "").trim();
      const end = (parts[1] || "").trim();
      i++;
      const textLines: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        textLines.push(lines[i]);
        i++;
      }
      out.push({ idx: out.length + 1, start, end, text: textLines.join("\n").trim() });
    }
    return out;
  }

  function blocksToSrt(blocks: SrtBlock[]): string {
    return (
      (blocks || [])
        .map((b, k) => {
          const idx = k + 1;
          const t = (b.text || "").trim();
          return `${idx}\n${b.start} --> ${b.end}\n${t}\n`;
        })
        .join("\n")
        .trimEnd() + "\n"
    );
  }

  function blocksAndEngToSrt(blocks: SrtBlock[], engTexts: string[]): { chs: string; eng: string } {
    const b = blocks || [];
    const e = engTexts || [];
    const chs = blocksToSrt(b);
    const engBlocks: SrtBlock[] = b.map((x, i) => ({ idx: i + 1, start: x.start, end: x.end, text: String(e[i] ?? "").trim() }));
    const eng = blocksToSrt(engBlocks);
    return { chs, eng };
  }

  function srtTimeShort(t: string): string {
    // Display-friendly: "00:00:02,000" -> "00:00:02"
    const s = String(t || "").trim();
    const m = s.match(/^(\d{2}:\d{2}:\d{2})/);
    return m ? m[1] : s;
  }

  function srtDurationSeconds(start: string, end: string): number {
    const parse = (ts: string) => {
      // "HH:MM:SS,mmm"
      const m = String(ts || "")
        .trim()
        .match(/^(\d+):(\d+):(\d+),(\d+)$/);
      if (!m) return 0;
      const hh = Number(m[1] || 0);
      const mm = Number(m[2] || 0);
      const ss = Number(m[3] || 0);
      const ms = Number(m[4] || 0);
      return hh * 3600 + mm * 60 + ss + ms / 1000;
    };
    return Math.max(0, parse(end) - parse(start));
  }

  function mergeBlocks(blocks: SrtBlock[], index0: number, dir: "prev" | "next"): SrtBlock[] {
    const b = [...(blocks || [])];
    if (index0 < 0 || index0 >= b.length) return b;
    const j = dir === "prev" ? index0 - 1 : index0 + 1;
    if (j < 0 || j >= b.length) return b;
    const a = dir === "prev" ? b[j] : b[index0];
    const c = dir === "prev" ? b[index0] : b[j];
    const merged: SrtBlock = {
      idx: 0,
      start: a.start,
      end: c.end,
      text: [a.text, c.text]
        .filter((x) => String(x || "").trim())
        .join("\n")
        .trim(),
    };
    const keepIdx = dir === "prev" ? j : index0;
    b[keepIdx] = merged;
    b.splice(dir === "prev" ? index0 : j, 1);
    return b.map((x, k) => ({ ...x, idx: k + 1 }));
  }

  function extremeShortCandidates(blocks: SrtBlock[]): number[] {
    // Conservative heuristic: extremely short duration or extremely short text.
    const out: number[] = [];
    for (let i = 0; i < (blocks || []).length; i++) {
      const b = blocks[i];
      const dur = srtDurationSeconds(b.start, b.end);
      const text = (b.text || "").replace(/\s+/g, "");
      const shortText = text.length > 0 && text.length <= 3;
      const shortDur = dur > 0 && dur < 0.7;
      if (shortText || shortDur) out.push(i);
    }
    return out;
  }

  const [activeTab, setActiveTab] = useState<string>("quality");
  const logsEnabled = !!showLogs;
  const [diagIncludeMedia, setDiagIncludeMedia] = useState(false);
  const reviewEnabled = (batch.params?.review_enabled ?? true) !== false;
  const isPaused = t.state === "paused";
  const reviewChsOnly = isPaused;
  const inspectionTabLabel = batch.mode === "quality" ? "质量检查" : "交付检查";
  const [qualityLoading, setQualityLoading] = useState(false);
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [salesModalOpen, setSalesModalOpen] = useState(false);
  const [showAllErrors, setShowAllErrors] = useState(false);
  const [showAllWarnings, setShowAllWarnings] = useState(false);

  useEffect(() => {
    if (activeTab !== "review") return;
    if (!reviewEnabled) return;
    if (!t.taskId) return;
    // Tasks can pause for "pre-translation review": allow review while paused (ENG may not exist yet).
    if (!["completed", "failed", "paused"].includes(t.state)) return;
    (async () => {
      setReviewLoading(true);
      try {
        const [chs, chsBase] = await Promise.all([getChsSrt2(t.taskId, reviewWhich), getChsSrt2(t.taskId, "base")]);
        // ENG may not exist when paused before MT; treat as empty instead of failing the whole view.
        const eng = await getEngSrt(t.taskId, reviewWhich).catch(() => ({ name: "eng.srt", content: "" }));
        const chsBlocks = parseSrtBlocks(chs?.content || "");
        const chsBaseBlocks = parseSrtBlocks(chsBase?.content || "");
        const engBlocks = parseSrtBlocks(eng?.content || "");
        const engTexts =
          engBlocks.length === chsBlocks.length
            ? engBlocks.map((b) => b.text || "")
            : (() => {
                // Best-effort alignment: keep timings from CHS; take ENG texts by index.
                const texts = engBlocks.map((b) => b.text || "");
                const out: string[] = [];
                for (let i = 0; i < chsBlocks.length; i++) out.push(texts[i] || "");
                return out;
              })();
        setReviewBaseChsBlocks(chsBaseBlocks);
        setReviewChsBlocks(chsBlocks);
        setReviewEngTexts(engTexts);
        setReviewMergeUndo([]);
      } catch {
        setReviewBaseChsBlocks([]);
        setReviewChsBlocks([]);
        setReviewEngTexts([]);
        setReviewMergeUndo([]);
      } finally {
        setReviewLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, t.taskId, t.state, reviewWhich, reviewEnabled]);

  useEffect(() => {
    if (!initialTab) return;
    if (initialTab === "review" && !reviewEnabled) {
      setActiveTab("quality");
      return;
    }
    setActiveTab(initialTab);
  }, [initialTab, reviewEnabled, t.taskId]);

  useEffect(() => {
    if (activeTab !== "quality") return;
    if (!t.taskId) return;
    if (!["completed", "failed"].includes(t.state)) return;
    if (qualityReport) return;
    (async () => {
      setQualityLoading(true);
      try {
        const isLegacyMsg = (m: string) =>
          /eng\.srt|eng_tts\.srt|reading speed|overly long|non-positive|overlap|glossary terms missing|forbidden variants|output video appears truncated/i.test(
            String(m || ""),
          );
        const qr0 = await getQualityReport(t.taskId).catch(() => null);
        const legacy =
          (!!qr0 && Array.isArray((qr0 as any).errors) && (qr0 as any).errors.some(isLegacyMsg)) ||
          (!!qr0 && Array.isArray((qr0 as any).warnings) && (qr0 as any).warnings.some(isLegacyMsg));
        const qr = legacy ? await getQualityReport(t.taskId, { regen: true }).catch(() => qr0) : qr0;
        setQualityReport((qr as any) || null);
      } catch {
        setQualityReport(null);
      } finally {
        setQualityLoading(false);
      }
    })();
  }, [activeTab, t.taskId, t.state, qualityReport]);

  // Reset expand states when switching tasks/tabs
  useEffect(() => {
    setShowAllErrors(false);
    setShowAllWarnings(false);
    setQualityReport(null);
  }, [t.taskId, activeTab]);

  // Silence TS unused warnings (kept for compatibility with existing UI logic).
  void onOpenOutput;
  void qualityGates;
  void onCleanup;
  void onUpgradeToQuality;
  void downloadUrl;
  void extremeShortCandidates;

  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={(k) => {
          setActiveTab(k);
        }}
        items={[
          {
            key: "quality",
            label: inspectionTabLabel,
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                {!["completed", "failed"].includes(t.state) ? (
                  <Text type="secondary">任务未结束，质量报告将在完成后生成。</Text>
                ) : qualityLoading ? (
                  <Text type="secondary">质量报告加载中…</Text>
                ) : (qualityReport?.passed ?? t.qualityPassed) == null ? (
                  <Text type="secondary">暂无质量报告（任务结束后会自动生成）。</Text>
                ) : (
                  <>
                    <Alert
                      type={(qualityReport?.passed ?? t.qualityPassed) ? "success" : "warning"}
                      showIcon
                      message={(qualityReport?.passed ?? t.qualityPassed) ? "通过：可交付" : "未通过：建议先处理问题再交付"}
                    />

                    {(() => {
                      const passed = !!(qualityReport?.passed ?? t.qualityPassed);
                      const errors = (qualityReport?.errors || t.qualityErrors || []) as string[];
                      const warnings = (qualityReport?.warnings || t.qualityWarnings || []) as string[];
                      const errN = errors.length;
                      const warnN = warnings.length;

                      const limit = 3;
                      const shownErrors = showAllErrors ? errors : errors.slice(0, limit);
                      const shownWarnings = showAllWarnings ? warnings : warnings.slice(0, limit);

                      const showUpsell =
                        batch.mode === "lite" &&
                        (passed === false ||
                          warnings.filter((x) =>
                            /阅读速度过快|单行过长|空行比例|时间轴|含有中文|全角字符|reading speed|overly long|cjk/i.test(String(x || "")),
                          ).length >= 5);

                      const qualityOptimizations =
                        batch.mode === "quality"
                          ? (() => {
                              const p: any = { ...(batch.params || {}), ...(((t as any).paramsOverride || {}) as any) };
                              const items: string[] = [];
                              if (p.subtitle_postprocess_enable) items.push("字幕可读性优化（更好读）");
                              if (p.subtitle_wrap_enable) items.push("自动换行（减少一行太长）");
                              if (p.subtitle_cps_fix_enable) items.push("阅读速度修复（避免太密看不完）");
                              if (p.display_srt_enable) items.push("生成显示字幕（更适合烧录）");
                              if (p.display_use_for_embed) items.push("烧录时优先使用显示字幕");
                              if (p.denoise) items.push("降噪（嘈杂素材更稳）");
                              if (p.tts_plan_enable) items.push("配音节奏规划（减少读不完/卡顿）");
                              if (p.tts_trim_llm_enable) items.push("超预算句自动改写（尽量读得完）");
                              return items;
                            })()
                          : [];

                      return (
                        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                          <Card size="small" title="结论与建议">
                            <Space direction="vertical" size={4} style={{ width: "100%" }}>
                              <Text>{passed ? "结论：质量检查已通过，建议交付。" : "结论：存在影响交付的问题，建议先处理后再交付。"}</Text>
                              <Text type="secondary">建议：先处理「主要问题」，再看「风险提示」。示例仅展示少量片段用于定位。</Text>
                            </Space>
                          </Card>

                          <Card size="small" title={`主要问题（需要处理）`} extra={<Text type="secondary">{errN > 0 ? `${errN} 项` : "无"}</Text>}>
                            {errN > 0 ? (
                              <>
                                <List
                                  size="small"
                                  dataSource={shownErrors}
                                  renderItem={(x) => {
                                    const text = normalizeLegacyQualityIssueText(x);
                                    const tag = issueTag(text);
                                    return (
                                      <List.Item>
                                        <Space direction="vertical" size={2}>
                                          <Space wrap>
                                            <Tag color={tag.color}>{tag.label}</Tag>
                                            <Text>{text}</Text>
                                          </Space>
                                          <Text type="secondary">{suggestForIssue(text)}</Text>
                                        </Space>
                                      </List.Item>
                                    );
                                  }}
                                />
                                {errN > limit && (
                                  <Button type="link" onClick={() => setShowAllErrors((v) => !v)}>
                                    {showAllErrors ? "收起" : `展开全部（${errN}）`}
                                  </Button>
                                )}
                              </>
                            ) : (
                              <Text type="secondary">无</Text>
                            )}
                          </Card>

                          <Card size="small" title={`风险提示（可交付但建议关注）`} extra={<Text type="secondary">{warnN > 0 ? `${warnN} 项` : "无"}</Text>}>
                            {warnN > 0 ? (
                              <>
                                <List
                                  size="small"
                                  dataSource={shownWarnings}
                                  renderItem={(x) => {
                                    const text = normalizeLegacyQualityIssueText(x);
                                    const tag = issueTag(text);
                                    return (
                                      <List.Item>
                                        <Space direction="vertical" size={2}>
                                          <Space wrap>
                                            <Tag color={tag.color}>{tag.label}</Tag>
                                            <Text>{text}</Text>
                                          </Space>
                                          <Text type="secondary">{suggestForIssue(text)}</Text>
                                        </Space>
                                      </List.Item>
                                    );
                                  }}
                                />
                                {warnN > limit && (
                                  <Button type="link" onClick={() => setShowAllWarnings((v) => !v)}>
                                    {showAllWarnings ? "收起" : `展开全部（${warnN}）`}
                                  </Button>
                                )}
                              </>
                            ) : (
                              <Text type="secondary">无</Text>
                            )}
                          </Card>

                          <Card size="small" title="示例与定位（只展示少量片段）">
                            {qualityReport ? (
                              <Collapse
                                items={[
                                  {
                                    key: "examples",
                                    label: "查看示例",
                                    children: (() => {
                                      const groups = qualityExampleGroups(qualityReport);
                                      if (!groups.length) return <Text type="secondary">暂无可展示的示例。</Text>;
                                      return (
                                        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                          {groups.map((g) => (
                                            <div key={g.title}>
                                              <Text strong>{g.title}</Text>
                                              <List
                                                size="small"
                                                bordered
                                                style={{ marginTop: 8 }}
                                                dataSource={g.items}
                                                renderItem={(it) => (
                                                  <List.Item>
                                                    <Text>{it}</Text>
                                                  </List.Item>
                                                )}
                                              />
                                            </div>
                                          ))}
                                        </Space>
                                      );
                                    })(),
                                  },
                                ]}
                              />
                            ) : (
                              <Text type="secondary">暂无详细报告。</Text>
                            )}
                          </Card>

                          {(batch.mode === "lite" || batch.mode === "quality") && (
                            <Card size="small" title={batch.mode === "lite" ? "自助修复 / 升级" : "说明"}>
                              <Space direction="vertical" size="small" style={{ width: "100%" }}>
                                {batch.mode === "quality" && qualityOptimizations.length > 0 && (
                                  <Alert
                                    type="success"
                                    showIcon
                                    message="质量模式：本次已启用部分自动优化"
                                    description={
                                      <List
                                        size="small"
                                        dataSource={qualityOptimizations.slice(0, 8)}
                                        renderItem={(x) => <List.Item>{x}</List.Item>}
                                      />
                                    }
                                  />
                                )}

                                {batch.mode === "lite" &&
                                  (() => {
                                    const hints = detectLiteFailureHints();
                                    const canFixFast = !!t.taskId && ["completed", "failed"].includes(t.state);
                                    const hasEng = hasArtifact("eng.srt");
                                    const hasTts = hasArtifact("tts_full.wav");
                                    const hasDub = hasArtifact("output_en.mp4");
                                    const hasSub = hasArtifact("output_en_sub.mp4");
                                    const needDeliver = anyMissingDeliverables(qualityReport);
                                    const actions: { key: string; title: string; desc: string; onClick: () => void; disabled?: boolean }[] = [];

                                    if (canFixFast && hasEng && !hasTts) {
                                      actions.push({
                                        key: "resume_tts",
                                        title: "补生成配音与成片（从配音继续）",
                                        desc: "适用于：已生成英文字幕，但缺少配音/成片（例如之前选择了“只出字幕”或中途失败）。",
                                        onClick: () => onResume("tts"),
                                      });
                                    }
                                    if (canFixFast && hasTts && !hasDub) {
                                      actions.push({
                                        key: "mux_embed",
                                        title: "只生成成片与硬字幕（无需重跑识别/翻译）",
                                        desc: "适用于：已有配音音频，但成片未生成或丢失。",
                                        onClick: () => onApplyReview("mux_embed", "base"),
                                      });
                                    }
                                    if (canFixFast && hasDub && !hasSub) {
                                      actions.push({
                                        key: "embed_only",
                                        title: "补封装硬字幕（无需重跑）",
                                        desc: "适用于：成片已生成，但缺少带硬字幕版本。",
                                        onClick: () => onApplyReview("embed", "base"),
                                      });
                                    }
                                    if (canFixFast && !hasEng) {
                                      actions.push({
                                        key: "retry_asr",
                                        title: "重试（从识别开始）",
                                        desc: "适用于：英文字幕未生成，通常是流程在翻译前失败。",
                                        onClick: () => onResume("asr"),
                                      });
                                    }

                                    return (
                                      <Space direction="vertical" size="small" style={{ width: "100%" }}>
                                        {hints && (
                                          <Alert
                                            type="warning"
                                            showIcon
                                            message={hints.title}
                                            description={
                                              <Space direction="vertical" size={4}>
                                                <List
                                                  size="small"
                                                  dataSource={hints.items}
                                                  renderItem={(x) => <List.Item>{x}</List.Item>}
                                                />
                                                <Space wrap>
                                                  {!!onGoSystem && (
                                                    <Button size="small" onClick={onGoSystem}>
                                                      去系统页检查模型/环境
                                                    </Button>
                                                  )}
                                                  <Button size="small" onClick={() => onExportDiagnostic({ includeMedia: false })}>
                                                    导出诊断包
                                                  </Button>
                                                </Space>
                                              </Space>
                                            }
                                          />
                                        )}

                                        {actions.length > 0 ? (
                                          <Space direction="vertical" size="small" style={{ width: "100%" }}>
                                            {actions.map((a) => (
                                              <Alert
                                                key={a.key}
                                                type="info"
                                                showIcon
                                                message={a.title}
                                                description={<Text type="secondary">{a.desc}</Text>}
                                                action={
                                                  <Button size="small" type="primary" onClick={a.onClick} disabled={!!a.disabled}>
                                                    执行
                                                  </Button>
                                                }
                                              />
                                            ))}
                                          </Space>
                                        ) : needDeliver ? (
                                          <Text type="secondary">暂无可一键修复的项。你可以导出诊断包或查看日志定位原因。</Text>
                                        ) : (
                                          <Text type="secondary">当前任务产物齐全，无需修复。</Text>
                                        )}
                                      </Space>
                                    );
                                  })()}

                                {showUpsell && (
                                  <Alert
                                    type="info"
                                    showIcon
                                    message="如果你希望字幕更好读 / 告警更少"
                                    description={<Text type="secondary">质量模式会提供更多自动优化（更慢、更吃资源，但更省人工）。如需开通请联系销售。</Text>}
                                    action={
                                      <Button size="small" type="primary" onClick={() => setSalesModalOpen(true)}>
                                        联系销售开通
                                      </Button>
                                    }
                                  />
                                )}
                              </Space>
                            </Card>
                          )}
                        </Space>
                      );
                    })()}
                  </>
                )}
              </Space>
            ),
          },
          ...(reviewEnabled
            ? [
                {
                  key: "review",
                  label: <span className={isPaused ? "ygf-review-tab-blink" : ""}>审核</span>,
                  children: (
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Alert
                        type="info"
                        showIcon
                        message={
                          reviewChsOnly
                            ? "中文审核：仅修改中文字幕。开启后会在 MT 前停在这里。支持将“上下文极短块”与相邻块合并。点击「保存并继续翻译」将从 MT 开始继续（MT→TTS→合成→封装）。"
                            : "审核：中英同页对比修改。支持将“上下文极短块”与相邻块合并。点击「更新成片」将始终从中文开始重跑（MT→TTS→合成→封装）。"
                        }
                      />
                      {!["completed", "failed", "paused"].includes(t.state) && (
                        <Alert type="warning" showIcon message="任务未完成/未暂停。开启“审核”后，会在本轮进入 MT 前暂停；当前状态说明任务还没走到可审核节点。" />
                      )}

                      <Space wrap>
                        <Button
                          type={reviewMergeUndo && reviewMergeUndo.length > 0 ? "primary" : "default"}
                          disabled={!reviewMergeUndo || reviewMergeUndo.length === 0}
                          onClick={() => {
                            setReviewMergeUndo((prev) => {
                              const top = (prev || [])[0];
                              if (top) {
                                setReviewChsBlocks(cloneBlocks(top.chs || []));
                                setReviewEngTexts([...(top.eng || [])]);
                              }
                              return (prev || []).slice(1);
                            });
                          }}
                        >
                          撤回合并
                        </Button>
                        <Text type="secondary">提示：每行可直接编辑中英文；“并上一条/并下一条”用于上下文极短合并。</Text>
                      </Space>

                      <Table
                        size="small"
                        pagination={false}
                        rowKey={(_, idx) => String(idx)}
                        rowClassName={(r: any) => (r?.isShort ? "ygf-review-row-short" : "")}
                        dataSource={(reviewChsBlocks || []).map((b, i) => {
                          const dur = srtDurationSeconds(b.start, b.end);
                          const chsCompact = (b.text || "").replace(/\s+/g, "");
                          const isShort = (chsCompact.length > 0 && chsCompact.length <= 3) || (dur > 0 && dur < 0.7);
                          const baseText = String((reviewBaseChsBlocks || [])[i]?.text || "");
                          return {
                            i,
                            idx: i + 1,
                            start: b.start,
                            end: b.end,
                            dur,
                            isShort,
                            changed: !!baseText && String(b.text || "").trim() !== baseText.trim(),
                            chs: b.text || "",
                            eng: String((reviewEngTexts || [])[i] ?? ""),
                          };
                        })}
                        columns={[
                          {
                            title: "序号",
                            dataIndex: "idx",
                            width: 72,
                            render: (v: number) => <Tag style={{ margin: 0 }}>{v}</Tag>,
                          },
                          {
                            title: "标记",
                            dataIndex: "isShort",
                            width: 120,
                            render: (_: any, r: any) => (
                              <Space direction="vertical" size={2}>
                                {r.changed ? <Tag color="blue">已改写</Tag> : null}
                                {r.isShort ? (
                                  <Tooltip title="极短块：时长很短或文本很短，建议与上下文合并。">
                                    <Badge status="warning" text="极短" />
                                  </Tooltip>
                                ) : null}
                              </Space>
                            ),
                          },
                          {
                            title: "时间轴",
                            dataIndex: "start",
                            width: 210,
                            render: (_: any, r: any) => (
                              <div style={{ fontFamily: "ui-monospace", fontSize: 12, lineHeight: 1.3 }}>
                                <div>{srtTimeShort(r.start)}</div>
                                <div>{srtTimeShort(r.end)}</div>
                              </div>
                            ),
                          },
                          {
                            title: "中文字幕（可编辑）",
                            dataIndex: "chs",
                            render: (_: any, r: any) => (
                              <Input.TextArea
                                value={r.chs}
                                autoSize={{ minRows: 2, maxRows: 6 }}
                                disabled={!["completed", "failed", "paused"].includes(t.state)}
                                onChange={(e) => {
                                  const next = [...(reviewChsBlocks || [])];
                                  const idx = r.i as number;
                                  if (idx >= 0 && idx < next.length) {
                                    next[idx] = { ...next[idx], text: e.target.value };
                                    setReviewChsBlocks(next);
                                  }
                                }}
                              />
                            ),
                          },
                          ...(reviewChsOnly
                            ? []
                            : [
                                {
                                  title: "英文字幕（可编辑）",
                                  dataIndex: "eng",
                                  render: (_: any, r: any) => (
                                    <Input.TextArea
                                      value={r.eng}
                                      autoSize={{ minRows: 2, maxRows: 6 }}
                                      disabled={!["completed", "failed"].includes(t.state)}
                                      onChange={(e) => {
                                        const next = [...(reviewEngTexts || [])];
                                        const idx = r.i as number;
                                        next[idx] = e.target.value;
                                        setReviewEngTexts(next);
                                      }}
                                    />
                                  ),
                                } as any,
                              ]),
                          {
                            title: "操作",
                            dataIndex: "actions",
                            width: 170,
                            render: (_: any, r: any) => {
                              const i = r.i as number;
                              const disabled = !["completed", "failed", "paused"].includes(t.state);
                              return (
                                <Space direction="vertical" size={6}>
                                  <Button
                                    size="small"
                                    disabled={disabled || i <= 0}
                                    onClick={() => {
                                      pushMergeUndo();
                                      const b = mergeBlocks(reviewChsBlocks || [], i, "prev");
                                      const e = [...(reviewEngTexts || [])];
                                      e[i - 1] = [String(e[i - 1] ?? ""), String(e[i] ?? "")]
                                        .filter((x) => x.trim())
                                        .join("\n")
                                        .trim();
                                      e.splice(i, 1);
                                      setReviewChsBlocks(b);
                                      setReviewEngTexts(e);
                                    }}
                                  >
                                    并上一条
                                  </Button>
                                  <Button
                                    size="small"
                                    disabled={disabled || i >= (reviewChsBlocks || []).length - 1}
                                    onClick={() => {
                                      pushMergeUndo();
                                      const b = mergeBlocks(reviewChsBlocks || [], i, "next");
                                      const e = [...(reviewEngTexts || [])];
                                      e[i + 1] = [String(e[i] ?? ""), String(e[i + 1] ?? "")]
                                        .filter((x) => x.trim())
                                        .join("\n")
                                        .trim();
                                      e.splice(i, 1);
                                      setReviewChsBlocks(b);
                                      setReviewEngTexts(e);
                                    }}
                                  >
                                    并下一条
                                  </Button>
                                </Space>
                              );
                            },
                          },
                        ]}
                        scroll={{ x: "max-content", y: 560 }}
                        className="ygf-scrollbars"
                      />

                      <div className="ygf-sticky-review-footer">
                        <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
                          <Text type="secondary">
                            {reviewChsOnly ? "提示：暂停阶段只需要改中文；继续后会从翻译开始跑完。" : "修改后可直接更新成片（始终从中文开始）。"}
                          </Text>
                          <Button
                            type="primary"
                            loading={reviewLoading}
                            disabled={!t.taskId || !["completed", "failed", "paused"].includes(t.state) || (reviewChsBlocks || []).length === 0}
                            onClick={async () => {
                              if (!t.taskId) return;
                              try {
                                setReviewLoading(true);
                                const { chs, eng } = blocksAndEngToSrt(reviewChsBlocks || [], reviewEngTexts || []);
                                await putChsReviewSrt(t.taskId, chs);
                                if (!reviewChsOnly && eng.trim()) await putEngReviewSrt(t.taskId, eng);
                                await onRunReview("chs");
                              } catch (err: any) {
                                message.error(err?.message || "更新失败");
                              } finally {
                                setReviewLoading(false);
                              }
                            }}
                          >
                            {reviewChsOnly ? "保存并继续翻译" : "更新成片"}
                          </Button>
                        </Space>
                      </div>
                    </Space>
                  ),
                },
              ]
            : []),
          ...(logsEnabled
            ? [
                {
                  key: "log",
                  label: "日志",
                  children: (
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Space wrap align="center">
                        <Button onClick={() => onExportDiagnostic({ includeMedia: diagIncludeMedia })} disabled={!t.taskId}>
                          导出诊断包
                        </Button>
                        <Checkbox checked={diagIncludeMedia} onChange={(e) => setDiagIncludeMedia(e.target.checked)}>
                          包含成片/音频
                        </Checkbox>
                      </Space>
                      <div
                        style={{
                          border: "1px solid #f0f0f0",
                          background: "#0f172a",
                          color: "#e5e7eb",
                          borderRadius: 6,
                          minHeight: 240,
                          padding: 12,
                          overflow: "auto",
                          fontFamily: "ui-monospace",
                        }}
                      >
                        <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {logLoading ? "加载中…" : logText || "暂无日志"}
                        </pre>
                      </div>
                    </Space>
                  ),
                } as any,
              ]
            : []),
        ]}
      />

      <Modal
        title="联系销售开通质量模式"
        open={salesModalOpen}
        onCancel={() => setSalesModalOpen(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setSalesModalOpen(false)}>
            我知道了
          </Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>占位：这里将展示销售人员的二维码 / 企业微信 / 飞书名片。</Text>
          <div
            style={{
              width: 240,
              height: 240,
              border: "1px dashed #ccc",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "12px 0",
            }}
          >
            <Text type="secondary">[销售二维码占位]</Text>
          </div>
          <Text type="secondary">质量模式更适合对成片质量有要求的付费用户：通常能带来更好读的字幕、更自然的配音，以及更低的人工审校成本。</Text>
        </Space>
      </Modal>
    </>
  );
}

