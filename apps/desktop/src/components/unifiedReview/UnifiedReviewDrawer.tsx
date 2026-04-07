import React, { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Badge, Button, Card, Drawer, Input, Select, Space, Tag, Typography, message } from "antd";
import type { BatchModel } from "../../batchTypes";
import { createId } from "../../app/appHelpers";
import { downloadTaskFileText, getArtifacts, putChsReviewSrt } from "../../api";
import { taskStateLabel } from "../../app/labels";
import { getGlobalRuleset, putGlobalRuleset } from "../../services/rulesApi";
import type { RulesetDoc } from "../../types";
import {
  applyReplacementRuleToBlocks,
  augmentRepeatedSpanOccurrences,
  buildRuleEditorRows,
  inferReplacementRule,
  removeAsrFixFromRuleset,
  type RuleEditorRow as ReviewRuleEditorRow,
  upsertAsrFixToRuleset,
} from "./reviewRuleHelpers";

const { Text, Title } = Typography;

type ZhSpan = {
  start: number;
  end: number;
  text: string;
  type?: string;
  risk?: "high" | "medium" | string;
  reasons?: string[];
  confidence?: number;
  source?: string;
  meta?: any;
};

type ZhSuspectItem = {
  idx: number;
  text: string;
  spans?: ZhSpan[];
  rule_reasons?: string[];
  risk?: "low" | "medium" | "high" | string;
  need_review?: boolean;
  reasons?: string[];
  changed?: boolean;
  base?: string;
  opt?: string;
  polished?: boolean;
};

type ZhSuspectsDoc = {
  items?: ZhSuspectItem[];
  meta?: {
    phrase_extraction_error?: string;
    optimization_error?: string;
    zh_polish_enabled?: boolean;
    review_gate_enabled?: boolean;
    zh_opt_enabled?: boolean;
  };
};

type SrtBlock = { idx: number; start: string; end: string; text: string };
type RuleEditorRowState = ReviewRuleEditorRow & { saving?: boolean };

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
    const texts: string[] = [];
    while (i < lines.length && lines[i].trim()) {
      texts.push(lines[i]);
      i++;
    }
    out.push({ idx, start, end, text: texts.join("\n").trim() });
  }
  return out;
}

function blocksToSrt(blocks: SrtBlock[]): string {
  const arr = blocks || [];
  return arr
    .map((b, i) => {
      const idx = i + 1;
      const start = (b.start || "").trim();
      const end = (b.end || "").trim();
      const text = String(b.text || "").trim();
      return `${idx}\n${start} --> ${end}\n${text}\n`;
    })
    .join("\n")
    .trim()
    .concat("\n");
}

function srtTimeShort(t: string): string {
  const s = String(t || "").trim();
  const m = s.match(/^(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : s;
}

function safeJsonParse<T>(raw: string, fallback: T): T {
  try {
    const obj = JSON.parse(String(raw || ""));
    return (obj as any) ?? fallback;
  } catch {
    return fallback;
  }
}

function normalizeRuleValue(value: string): string {
  return String(value || "").trim();
}

function alignedBlocksDiffer(baseBlocks: SrtBlock[], candidateBlocks: SrtBlock[]): boolean {
  if (!candidateBlocks.length || candidateBlocks.length !== baseBlocks.length) return false;
  for (let i = 0; i < baseBlocks.length; i++) {
    if (String(candidateBlocks[i]?.text || "").trim() !== String(baseBlocks[i]?.text || "").trim()) return true;
  }
  return false;
}

function renderHighlighted(text: string, spans: ZhSpan[]): React.ReactNode {
  const s = String(text || "");
  const sorted = [...(spans || [])]
    .filter((x) => Number.isFinite(x.start) && Number.isFinite(x.end) && x.end > x.start)
    .sort((a, b) => a.start - b.start);
  if (!sorted.length) return <span>{s}</span>;
  let cursor = 0;
  const nodes: React.ReactNode[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const sp = sorted[i];
    const start = Math.max(0, Math.min(s.length, sp.start));
    const end = Math.max(0, Math.min(s.length, sp.end));
    if (start > cursor) nodes.push(<span key={`t-${i}-pre`}>{s.slice(cursor, start)}</span>);
    const frag = s.slice(start, end);
    nodes.push(
      <mark key={`t-${i}-m`} style={{ background: "#fff1b8", padding: "0 2px" }}>
        {frag}
      </mark>,
    );
    cursor = Math.max(cursor, end);
  }
  if (cursor < s.length) nodes.push(<span key="t-tail">{s.slice(cursor)}</span>);
  return <span>{nodes}</span>;
}

function splitSpans(spans: ZhSpan[]) {
  const forced = (spans || []).filter((x) => String(x?.source || "") === "forced");
  const nonForced = (spans || []).filter((x) => String(x?.source || "") !== "forced");
  return { forced, nonForced };
}

function spanTagColor(sp: ZhSpan): string {
  const risk = String(sp?.risk || "").toLowerCase();
  if (risk.startsWith("h")) return "red";
  const src = String(sp?.source || "");
  if (src === "spell") return "gold";
  if (src === "dict") return "geekblue";
  if (src === "pattern") return "purple";
  if (src === "forced") return "default";
  return "orange";
}

function spanTagText(sp: ZhSpan): string {
  const src = String(sp?.source || "");
  const t = String(sp?.text || "");
  if (src === "forced") return `${t}（兜底）`;
  if (src === "dict") {
    const sug = String((sp as any)?.meta?.suggest || "").trim();
    return sug ? `${t}（成语≈${sug}）` : `${t}（成语）`;
  }
  if (src === "pattern") return `${t}（模式）`;
  return t;
}

export function UnifiedReviewDrawer(props: {
  open: boolean;
  batch: BatchModel | null;
  isDockerDev?: boolean;
  initialSelectedTaskId?: string;
  onClose: () => void;
  onRunReviewForTaskIndex: (taskIndex0: number) => Promise<void>;
}) {
  const batch = props.batch;
  const pausedTasks = useMemo(() => {
    const b = batch;
    if (!b) return [];
    const enabled = (b.params?.review_enabled ?? true) !== false;
    if (!enabled) return [];
    return (b.tasks || []).filter((t) => t.state === "paused" && !!t.taskId);
  }, [batch]);

  const [selectedTaskId, setSelectedTaskId] = useState<string>("");
  const [loadingTaskId, setLoadingTaskId] = useState<string>("");
  const [taskErr, setTaskErr] = useState<string>("");

  const [blocks, setBlocks] = useState<SrtBlock[]>([]);
  const [blocksBase, setBlocksBase] = useState<SrtBlock[]>([]);
  const [draftByTaskId, setDraftByTaskId] = useState<
    Record<
      string,
      {
        blocks: SrtBlock[];
        blocksBase: SrtBlock[];
        suspectsDoc: ZhSuspectsDoc;
      }
    >
  >({});
  const [globalRuleset, setGlobalRuleset] = useState<RulesetDoc | null>(null);
  const [ruleRowsByTaskId, setRuleRowsByTaskId] = useState<Record<string, Record<number, RuleEditorRowState[]>>>({});
  const [suspectsDoc, setSuspectsDoc] = useState<ZhSuspectsDoc>({ items: [], meta: {} });
  const augmentedSuspectsDoc = useMemo(
    () => augmentRepeatedSpanOccurrences(blocksBase, suspectsDoc),
    [blocksBase, suspectsDoc],
  );
  const suspectsByIdx = useMemo(() => {
    const map = new Map<number, ZhSuspectItem>();
    for (const it of augmentedSuspectsDoc?.items || []) map.set(Number(it.idx), it);
    return map;
  }, [augmentedSuspectsDoc]);

  const selectedTask = useMemo(() => pausedTasks.find((t) => t.taskId === selectedTaskId) || null, [pausedTasks, selectedTaskId]);
  const currentTaskRuleRows = useMemo(
    () => ruleRowsByTaskId[String(selectedTaskId || "")] || {},
    [ruleRowsByTaskId, selectedTaskId],
  );

  const [showForced, setShowForced] = useState(false);

  useEffect(() => {
    if (!props.open) return;
    getGlobalRuleset()
      .then((doc) => setGlobalRuleset(doc as RulesetDoc))
      .catch(() => {});
  }, [props.open]);

  useEffect(() => {
    if (!props.open) return;
    if (props.initialSelectedTaskId) {
      const wanted = String(props.initialSelectedTaskId);
      setSelectedTaskId(wanted);
      if (pausedTasks.length > 0 && !pausedTasks.some((t) => String(t.taskId) === wanted)) {
        setTaskErr("当前点击的任务已不在可审核列表中，请从对应任务重新打开审核。");
        setBlocks([]);
        setBlocksBase([]);
        setSuspectsDoc({ items: [], meta: {} });
      }
      return;
    }
    // auto-select first paused task only when there is no explicit task target.
    if ((!selectedTaskId || !pausedTasks.some((t) => String(t.taskId) === String(selectedTaskId))) && pausedTasks.length > 0) {
      setSelectedTaskId(String(pausedTasks[0].taskId));
    }
  }, [props.open, props.initialSelectedTaskId, pausedTasks, selectedTaskId]);

  useEffect(() => {
    const taskKey = String(selectedTaskId || "");
    if (!props.open || !taskKey || (blocksBase || []).length === 0) return;
    setRuleRowsByTaskId((prev) => {
      const prevTaskRows = { ...(prev[taskKey] || {}) };
      let changedAny = false;
      for (let idx = 1; idx <= blocksBase.length; idx++) {
        const spans = suspectsByIdx.get(idx)?.spans || [];
        const suggestedRows = buildRuleEditorRows(spans as any, globalRuleset || undefined).map((row) => ({ ...row, saving: false }));
        const existingRows = prevTaskRows[idx];
        if (!existingRows || existingRows.length === 0) {
          prevTaskRows[idx] = suggestedRows;
          changedAny = true;
          continue;
        }
        let rowChanged = false;
        const mergedRows = existingRows.map((row) => {
          const src = normalizeRuleValue(row.src);
          if (!src || normalizeRuleValue(row.tgt) || normalizeRuleValue(row.savedTgt)) return row;
          const matched = suggestedRows.find((it) => normalizeRuleValue(it.src) === src && normalizeRuleValue(it.tgt));
          if (!matched) return row;
          rowChanged = true;
          return {
            ...row,
            tgt: matched.tgt,
            savedSrc: matched.savedSrc,
            savedTgt: matched.savedTgt,
          };
        });
        if (rowChanged) {
          prevTaskRows[idx] = mergedRows;
          changedAny = true;
        }
      }
      if (!changedAny) return prev;
      return { ...(prev || {}), [taskKey]: prevTaskRows };
    });
  }, [props.open, selectedTaskId, blocksBase, suspectsByIdx, globalRuleset]);

  async function loadTask(taskId: string, opts?: { preferDraft?: boolean }) {
    if (!taskId) return;
    setLoadingTaskId(taskId);
    setTaskErr("");
    try {
      if (opts?.preferDraft && draftByTaskId[String(taskId)]) {
        const d = draftByTaskId[String(taskId)];
        setBlocks(d.blocks || []);
        setBlocksBase(d.blocksBase || []);
        setSuspectsDoc(d.suspectsDoc || { items: [], meta: {} });
        setLoadingTaskId("");
        // Still refresh suspects in background to keep highlights up-to-date.
        const arts = await getArtifacts(taskId).catch(() => []);
        const has = (name: string) => arts.some((a) => a?.name === name);
        const suspectsPath = has("chs.suspects.json") ? "chs.suspects.json" : "chs.suspects.json";
        const susRaw = await downloadTaskFileText(taskId, suspectsPath).catch(() => "");
        const doc = safeJsonParse<ZhSuspectsDoc>(susRaw, { items: [], meta: {} });
        setSuspectsDoc(doc);
        setDraftByTaskId((prev) => ({ ...(prev || {}), [String(taskId)]: { ...d, suspectsDoc: doc } }));
        return;
      }
      // Prefer files from artifacts when available, else fall back to known names.
      const arts = await getArtifacts(taskId).catch(() => []);
      const has = (name: string) => arts.some((a) => a?.name === name);
      const chsPath = has("chs.srt") ? "chs.srt" : "chs.srt";
      // Review flow sources (priority):
      // - Prefer real user review overrides when they materially differ from base
      // - Else prefer zh_polish LLM suggestion when it differs from base
      // - Else fall back to base ASR
      const suspectsPath = has("chs.suspects.json") ? "chs.suspects.json" : "chs.suspects.json";
      const [chsRaw, reviewRaw, llmRaw, susRaw] = await Promise.all([
        downloadTaskFileText(taskId, chsPath).catch(() => ""),
        has("chs.review.srt") ? downloadTaskFileText(taskId, "chs.review.srt").catch(() => "") : Promise.resolve(""),
        has("chs.llm.srt") ? downloadTaskFileText(taskId, "chs.llm.srt").catch(() => "") : Promise.resolve(""),
        downloadTaskFileText(taskId, suspectsPath).catch(() => ""),
      ]);
      const baseBlocks = parseSrtBlocks(chsRaw || "");
      setBlocksBase(baseBlocks);
      const reviewBlocks = reviewRaw ? parseSrtBlocks(reviewRaw || "") : [];
      const llmBlocks = llmRaw ? parseSrtBlocks(llmRaw || "") : [];
      const useReview = alignedBlocksDiffer(baseBlocks, reviewBlocks);
      const useLlm = alignedBlocksDiffer(baseBlocks, llmBlocks);
      if (useReview) setBlocks(reviewBlocks);
      else if (useLlm) setBlocks(llmBlocks);
      else if (reviewBlocks.length === baseBlocks.length && reviewBlocks.length > 0) setBlocks(reviewBlocks);
      else setBlocks(baseBlocks);
      const doc = safeJsonParse<ZhSuspectsDoc>(susRaw, { items: [], meta: {} });
      setSuspectsDoc(doc);
      setShowForced(false);
    } catch (e: any) {
      setBlocks([]);
      setBlocksBase([]);
      setSuspectsDoc({ items: [], meta: {} });
      setTaskErr(e?.message || "加载失败");
    } finally {
      setLoadingTaskId("");
    }
  }

  // Preserve per-task drafts in memory when switching tasks, then load next.
  const lastSelectedTaskIdRef = useRef<string>("");
  useEffect(() => {
    if (!props.open) return;
    const cur = String(selectedTaskId || "");
    if (!cur) return;
    const last = String(lastSelectedTaskIdRef.current || "");
    if (last && last !== cur) {
      setDraftByTaskId((prev) => ({
        ...(prev || {}),
        [String(last)]: {
          blocks: blocks || [],
          blocksBase: blocksBase || [],
          suspectsDoc: suspectsDoc || { items: [], meta: {} },
        },
      }));
    }
    lastSelectedTaskIdRef.current = cur;
    loadTask(cur, { preferDraft: true }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open, selectedTaskId]);

  async function saveCurrentChsReview(): Promise<void> {
    if (!selectedTaskId) return;
    const content = blocksToSrt(blocks || []);
    await putChsReviewSrt(selectedTaskId, content);
    // Also update in-memory draft, so "全部继续（串行）" can save correctly.
    setDraftByTaskId((prev) => ({
      ...(prev || {}),
      [String(selectedTaskId)]: {
        blocks: blocks || [],
        blocksBase: blocksBase || [],
        suspectsDoc: suspectsDoc || { items: [], meta: {} },
      },
    }));
  }

  async function saveAndContinueCurrent(): Promise<void> {
    if (!batch || !selectedTask) return;
    const idx0 = batch.tasks.findIndex((x) => x.taskId === selectedTask.taskId);
    if (idx0 < 0) return;
    await saveCurrentChsReview();
    await props.onRunReviewForTaskIndex(idx0);
  }

  function updateRuleRowsForLine(lineIdx: number, updater: (rows: RuleEditorRowState[]) => RuleEditorRowState[]) {
    const taskKey = String(selectedTaskId || "");
    if (!taskKey) return;
    setRuleRowsByTaskId((prev) => {
      const prevTaskRows = prev[taskKey] || {};
      const currentRows = prevTaskRows[lineIdx] || [];
      return {
        ...(prev || {}),
        [taskKey]: {
          ...prevTaskRows,
          [lineIdx]: updater(currentRows),
        },
      };
    });
  }

  function addRuleRow(lineIdx: number) {
    updateRuleRowsForLine(lineIdx, (rows) => [
      ...(rows || []),
      { id: createId(), src: "", tgt: "", savedSrc: "", savedTgt: "", saving: false },
    ]);
  }

  function updateRuleRow(lineIdx: number, rowId: string, patch: Partial<RuleEditorRowState>) {
    updateRuleRowsForLine(lineIdx, (rows) => rows.map((row) => (row.id === rowId ? { ...row, ...patch } : row)));
  }

  async function persistRuleRow(lineIdx: number, rowId: string, opts?: { remove?: boolean }) {
    const taskKey = String(selectedTaskId || "");
    const rows = ruleRowsByTaskId[taskKey]?.[lineIdx] || [];
    const row = rows.find((it) => it.id === rowId);
    if (!row) return;

    const src = normalizeRuleValue(row.src);
    const tgt = normalizeRuleValue(row.tgt);
    const savedSrc = normalizeRuleValue(row.savedSrc || "");
    const savedTgt = normalizeRuleValue(row.savedTgt || "");

    if (opts?.remove) {
      updateRuleRowsForLine(lineIdx, (prevRows) => {
        const rest = prevRows.filter((it) => it.id !== rowId);
        return rest.length > 0 ? rest : [{ id: createId(), src: "", tgt: "", savedSrc: "", savedTgt: "", saving: false }];
      });
      const removeSrc = savedSrc;
      if (!removeSrc) return;
      try {
        const current = (globalRuleset || (await getGlobalRuleset())) as RulesetDoc;
        const next = removeAsrFixFromRuleset(current as any, removeSrc);
        if ((next.asr_fixes || []).length === (current.asr_fixes || []).length) return;
        await putGlobalRuleset(next as any);
        setGlobalRuleset(next);
        message.success(`已从规则中心删除：${removeSrc}`);
      } catch (e: any) {
        message.error(e?.message || "删除规则失败");
      }
      return;
    }

    if (!src || !tgt) return;
    if (src === tgt) {
      message.warning("规则中心中的“识别短语”和“修改为”不能相同。");
      return;
    }
    if (src === savedSrc && tgt === savedTgt) return;

    updateRuleRow(lineIdx, rowId, { saving: true });
    try {
      let current = (globalRuleset || (await getGlobalRuleset())) as RulesetDoc;
      if (savedSrc && savedSrc !== src) {
        current = removeAsrFixFromRuleset(current as any, savedSrc);
      }
      const taskName = String(selectedTask?.inputName || "").trim();
      const note = taskName ? `审核保存：${taskName}` : "审核保存";
      const next = upsertAsrFixToRuleset(current as any, { src, tgt }, note);
      await putGlobalRuleset(next as any);
      setGlobalRuleset(next);
      updateRuleRow(lineIdx, rowId, { src, tgt, savedSrc: src, savedTgt: tgt, saving: false });
      message.success(`已自动保存规则：${src} -> ${tgt}`);
    } catch (e: any) {
      updateRuleRow(lineIdx, rowId, { saving: false });
      message.error(e?.message || "保存规则失败");
    }
  }

  const drawerTitle = batch ? `审核：${batch.name}` : "审核";

  return (
    <>
      <Drawer
        title={drawerTitle}
        open={props.open}
        onClose={props.onClose}
        width={Math.min(1180, Math.max(760, Math.floor((typeof window !== "undefined" ? window.innerWidth : 1200) * 0.78)))}
      >
        {!batch ? (
          <Alert type="info" showIcon message="未选中批次" />
        ) : pausedTasks.length === 0 ? (
          <Alert type="info" showIcon message="当前批次没有需要审核的任务" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="翻译前审核"
              description={<Text type="secondary">开启审核后，流程会在本轮进入 MT 前停在这里。这里保留中文问题定位、建议修复和人工确认。</Text>}
            />

            <Card size="small" title="任务选择与操作">
              <Space direction="vertical" size="small" style={{ width: "100%" }}>
                <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
                  <Space wrap>
                    <Text type="secondary">选择任务：</Text>
                    <Select
                      style={{ minWidth: 420 }}
                      value={selectedTaskId}
                      onChange={(v) => setSelectedTaskId(String(v))}
                      options={pausedTasks.map((t) => ({ value: String(t.taskId), label: t.inputName }))}
                      showSearch
                      optionFilterProp="label"
                    />
                    <Tag>{taskStateLabel((selectedTask?.state as any) || "paused")}</Tag>
                    <Badge status={(augmentedSuspectsDoc?.items || []).length > 0 ? "warning" : "default"} text={`疑点 ${(augmentedSuspectsDoc?.items || []).length || 0}`} />
                    {(augmentedSuspectsDoc?.meta?.optimization_error || augmentedSuspectsDoc?.meta?.phrase_extraction_error || "") ? <Tag color="orange">分析失败</Tag> : null}
                  </Space>
                  <Space wrap>
                    <Button type="primary" disabled={!selectedTaskId || blocks.length === 0} onClick={saveAndContinueCurrent}>
                      保存并继续当前
                    </Button>
                  </Space>
                </Space>

              </Space>
            </Card>

            <div>
              <Title level={5} style={{ margin: 0 }}>
                中文字幕审核
              </Title>
              <Text type="secondary">提示：这里会自动补齐全片重复命中；句子下方可直接维护规则中心短语替换，输入后自动保存。</Text>
            </div>

            {taskErr ? <Alert type="warning" showIcon message={taskErr} /> : null}
            {(augmentedSuspectsDoc?.meta?.optimization_error || augmentedSuspectsDoc?.meta?.phrase_extraction_error || "") ? (
              <Alert
                type="warning"
                showIcon
                message="中文优化分析失败"
                description={<Text type="secondary">{augmentedSuspectsDoc?.meta?.optimization_error || augmentedSuspectsDoc?.meta?.phrase_extraction_error}</Text>}
              />
            ) : null}
            <div style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}>
              {(blocks || []).length === 0 ? (
                <Text type="secondary">{loadingTaskId ? "加载中…" : "暂无字幕数据"}</Text>
              ) : (
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  {(blocks || []).map((b, i) => {
                    const suspect = suspectsByIdx.get(Number(i + 1));
                    const spans = suspect?.spans || [];
                    const high = spans.some((x) => String(x.risk || "").toLowerCase().startsWith("h"));
                    const risk = String(suspect?.risk || "").toLowerCase();
                    const baseText = String((blocksBase || [])[i]?.text ?? b.text ?? "");
                    const { forced, nonForced } = splitSpans(spans);
                    const spansForHighlight = nonForced.length > 0 ? nonForced : [];
                    const changed = String((b.text || "").trim()) !== String((blocksBase || [])[i]?.text ?? "").trim();
                    const currentText = String(b.text || "").trim();
                    const aiOptText = String(suspect?.opt || "").trim();
                    const aiChanged = Boolean(suspect?.changed) && currentText !== baseText.trim() && currentText === aiOptText;
                    const ruleRows = currentTaskRuleRows[Number(i + 1)] || [
                      { id: `line-${i + 1}-empty`, src: "", tgt: "", savedSrc: "", savedTgt: "", saving: false },
                    ];
                    return (
                      <div key={`${i}`} style={{ borderBottom: "1px dashed #f0f0f0", paddingBottom: 10 }}>
                        <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
                          <Space wrap>
                            <Tag style={{ margin: 0 }}>{i + 1}</Tag>
                            <Text type="secondary" style={{ fontFamily: "ui-monospace" }}>
                              {srtTimeShort(b.start)} - {srtTimeShort(b.end)}
                            </Text>
                            {spans.length > 0 ? (
                              <Tag color={high ? "red" : "orange"}>
                                短语 {nonForced.length}
                                {forced.length > 0 ? ` + 兜底 ${forced.length}` : ""}
                              </Tag>
                            ) : null}
                            {risk === "high" ? <Tag color="red">高风险</Tag> : null}
                            {risk === "medium" ? <Tag color="orange">中风险</Tag> : null}
                            {suspect?.need_review ? <Tag color="gold">需复核</Tag> : null}
                            {aiChanged ? <Tag color="purple">AI修改</Tag> : null}
                            {changed ? <Tag color="blue">已改写</Tag> : null}
                          </Space>
                        </Space>

                        {spansForHighlight.length > 0 ? (
                          <div style={{ marginTop: 6 }}>
                            <Text type="secondary">原句标注：</Text> {renderHighlighted(baseText, spansForHighlight)}
                          </div>
                        ) : null}

                        <div style={{ marginTop: 8 }}>
                          <Space wrap={false} align="start" style={{ width: "100%" }}>
                            <Input.TextArea
                              value={baseText}
                              disabled
                              autoSize={{ minRows: 1, maxRows: 4 }}
                              style={{
                                flex: "1 1 420px",
                                minWidth: 360,
                                background: "#fafafa",
                                color: "rgba(0,0,0,0.45)",
                              }}
                            />
                            <Text type="secondary" style={{ paddingTop: 6, whiteSpace: "nowrap" }}>
                              改为
                            </Text>
                            <Input.TextArea
                              value={b.text}
                              autoSize={{ minRows: 1, maxRows: 4 }}
                              style={{ flex: "1 1 520px", minWidth: 420 }}
                              placeholder="输入你想改成的中文；命中词如果形成明确替换，会自动同步同任务中的相同出现"
                              onChange={(e) => {
                                const next = [...(blocks || [])];
                                next[i] = { ...next[i], text: e.target.value };
                                setBlocks(next);
                              }}
                              onBlur={(e) => {
                                const latest = String(e?.target?.value ?? (blocks || [])[i]?.text ?? "");
                                const nextBlocks = [...(blocks || [])];
                                nextBlocks[i] = { ...nextBlocks[i], text: latest };
                                const inferred = inferReplacementRule(baseText, latest, nonForced as any);
                                if (!inferred) {
                                  setBlocks(nextBlocks);
                                  return;
                                }
                                const applied = applyReplacementRuleToBlocks(nextBlocks || [], inferred);
                                setBlocks(applied.blocks as any);
                                if (applied.changedLines <= 1) return;
                                message.success(`已同步相同命中：${inferred.src} -> ${inferred.tgt}（${applied.changedLines} 处）`);
                              }}
                            />
                          </Space>
                        </div>

                        <div style={{ marginTop: 8, background: "#fafafa", border: "1px solid #f0f0f0", borderRadius: 6, padding: 10 }}>
                          <Space direction="vertical" size="small" style={{ width: "100%" }}>
                            <Text type="secondary">规则中心短语替换（自动保存）</Text>
                            {ruleRows.map((row) => (
                              <Space key={row.id} wrap style={{ width: "100%" }} align="start">
                                <Input
                                  style={{ width: 240 }}
                                  value={row.src}
                                  placeholder="识别的短语"
                                  onChange={(e) => updateRuleRow(i + 1, row.id, { src: e.target.value })}
                                  onBlur={() => persistRuleRow(i + 1, row.id).catch(() => {})}
                                  onPressEnter={() => persistRuleRow(i + 1, row.id).catch(() => {})}
                                />
                                <Text type="secondary" style={{ paddingTop: 5 }}>
                                  修改为
                                </Text>
                                <Input
                                  style={{ width: 240 }}
                                  value={row.tgt}
                                  placeholder="要改成的短语"
                                  status={row.saving ? "warning" : undefined}
                                  onChange={(e) => updateRuleRow(i + 1, row.id, { tgt: e.target.value })}
                                  onBlur={() => persistRuleRow(i + 1, row.id).catch(() => {})}
                                  onPressEnter={() => persistRuleRow(i + 1, row.id).catch(() => {})}
                                />
                                <Button size="small" onClick={() => addRuleRow(i + 1)}>
                                  增加
                                </Button>
                                <Button size="small" danger onClick={() => persistRuleRow(i + 1, row.id, { remove: true }).catch(() => {})}>
                                  删除
                                </Button>
                              </Space>
                            ))}
                          </Space>
                        </div>

                        {spans.length > 0 ? (
                          <div style={{ marginTop: 8 }}>
                            <Space wrap>
                              {(nonForced || []).map((sp, j) => (
                                <Tag key={`${i}-${j}`} color={spanTagColor(sp)}>
                                  {spanTagText(sp)}
                                </Tag>
                              ))}
                              {forced.length > 0 ? (
                                <Button size="small" type="link" onClick={() => setShowForced((v) => !v)}>
                                  {showForced ? "隐藏兜底" : `显示兜底（${forced.length}）`}
                                </Button>
                              ) : null}
                            </Space>
                            {showForced && forced.length > 0 ? (
                              <Space wrap style={{ marginTop: 6 }}>
                                {forced.map((sp, j) => (
                                  <Tag key={`${i}-f-${j}`} color={spanTagColor(sp)}>
                                    {spanTagText(sp)}
                                  </Tag>
                                ))}
                              </Space>
                            ) : null}
                          </div>
                        ) : null}

                      </div>
                    );
                  })}
                </Space>
              )}
            </div>
          </Space>
        )}
      </Drawer>
    </>
  );
}

