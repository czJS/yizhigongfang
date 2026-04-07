import { createId } from "../../app/appHelpers";
import type { RulesetDoc } from "../../types";

export type ReviewSpan = {
  start: number;
  end: number;
  text: string;
  type?: string;
  risk?: string;
  reasons?: string[];
  confidence?: number;
  source?: string;
  meta?: any;
};

export type ReviewSuspectItem = {
  idx: number;
  text: string;
  spans?: ReviewSpan[];
  rule_reasons?: string[];
  risk?: string;
  need_review?: boolean;
  reasons?: string[];
  changed?: boolean;
  base?: string;
  opt?: string;
  polished?: boolean;
};

export type ReviewSuspectsDoc = {
  items?: ReviewSuspectItem[];
  meta?: Record<string, any>;
};

export type ReviewBlock = {
  idx: number;
  start: string;
  end: string;
  text: string;
};

export type ReplacementRule = {
  src: string;
  tgt: string;
};

export type RuleEditorRow = {
  id: string;
  src: string;
  tgt: string;
  savedSrc?: string;
  savedTgt?: string;
};

function countOccurrences(text: string, needle: string): number {
  if (!text || !needle) return 0;
  let count = 0;
  let start = 0;
  while (start < text.length) {
    const idx = text.indexOf(needle, start);
    if (idx < 0) break;
    count += 1;
    start = idx + needle.length;
  }
  return count;
}

function findAllOccurrences(text: string, needle: string): number[] {
  if (!text || !needle) return [];
  const out: number[] = [];
  let start = 0;
  while (start < text.length) {
    const idx = text.indexOf(needle, start);
    if (idx < 0) break;
    out.push(idx);
    start = idx + needle.length;
  }
  return out;
}

function normalizeReplacementText(value: string): string {
  return String(value || "").trim();
}

function uniqueSpanTexts(spans: ReviewSpan[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const sp of spans || []) {
    if (String(sp?.source || "") === "forced") continue;
    const text = normalizeReplacementText(String(sp?.text || ""));
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

export function buildRuleEditorRows(spans: ReviewSpan[], doc?: Partial<RulesetDoc> | null): RuleEditorRow[] {
  const asrFixes = Array.isArray(doc?.asr_fixes) ? doc!.asr_fixes : [];
  const texts = uniqueSpanTexts(spans);
  const rows = texts.map((src) => {
    const matched = asrFixes.find((it: any) => normalizeReplacementText(String(it?.src || "")) === src);
    const tgt = normalizeReplacementText(String((matched as any)?.tgt || ""));
    return {
      id: createId(),
      src,
      tgt,
      savedSrc: matched ? src : "",
      savedTgt: matched ? tgt : "",
    };
  });
  return rows.length > 0 ? rows : [{ id: createId(), src: "", tgt: "", savedSrc: "", savedTgt: "" }];
}

export function inferReplacementRule(baseText: string, editedText: string, spans: ReviewSpan[]): ReplacementRule | null {
  const base = normalizeReplacementText(baseText);
  const edited = normalizeReplacementText(editedText);
  if (!base || !edited || base === edited) return null;
  const candidates = [...(spans || [])]
    .filter((sp) => String(sp?.source || "") !== "forced" && normalizeReplacementText(String(sp?.text || "")).length >= 2)
    .map((sp) => normalizeReplacementText(String(sp.text || "")))
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  const uniq = Array.from(new Set(candidates));
  for (const src of uniq) {
    const at = base.indexOf(src);
    if (at < 0) continue;
    const prefix = base.slice(0, at);
    const suffix = base.slice(at + src.length);
    if (!edited.startsWith(prefix) || !edited.endsWith(suffix)) continue;
    const tgt = edited.slice(prefix.length, edited.length - suffix.length);
    if (!tgt || tgt === src) continue;
    return { src, tgt };
  }
  return null;
}

export function resolveRuleForSave(baseText: string, editedText: string, spans: ReviewSpan[]): ReplacementRule | null {
  const inferred = inferReplacementRule(baseText, editedText, spans);
  if (inferred) return inferred;
  const base = normalizeReplacementText(baseText);
  const edited = normalizeReplacementText(editedText);
  if (!base || !edited || base === edited) return null;
  return { src: base, tgt: edited };
}

export function applyReplacementRuleToBlocks(blocks: ReviewBlock[], rule: ReplacementRule): {
  blocks: ReviewBlock[];
  changedLines: number;
  changedIdxs: number[];
} {
  const src = normalizeReplacementText(rule.src);
  const tgt = normalizeReplacementText(rule.tgt);
  if (!src || !tgt || src === tgt) return { blocks: blocks || [], changedLines: 0, changedIdxs: [] };
  let changedLines = 0;
  const changedIdxs: number[] = [];
  const next = (blocks || []).map((block) => {
    const text = String(block.text || "");
    if (!text.includes(src)) return block;
    const replaced = text.split(src).join(tgt);
    if (replaced === text) return block;
    changedLines += 1;
    changedIdxs.push(Number(block.idx));
    return { ...block, text: replaced };
  });
  return { blocks: next, changedLines, changedIdxs };
}

export function upsertAsrFixToRuleset(doc: Partial<RulesetDoc> | null | undefined, rule: ReplacementRule, note = ""): RulesetDoc {
  const src = normalizeReplacementText(rule.src);
  const tgt = normalizeReplacementText(rule.tgt);
  const cur = doc || ({} as Partial<RulesetDoc>);
  const asr = Array.isArray(cur.asr_fixes) ? [...cur.asr_fixes] : [];
  const i = asr.findIndex((it: any) => normalizeReplacementText(String(it?.src || "")) === src);
  const nextItem = {
    id: i >= 0 ? String((asr[i] as any)?.id || createId()) : createId(),
    src,
    tgt,
    note: normalizeReplacementText(note),
    scope: "global",
  };
  if (i >= 0) asr[i] = { ...(asr[i] as any), ...nextItem };
  else asr.push(nextItem as any);
  return {
    version: Number((cur as any).version || 1),
    updated_at: Number((cur as any).updated_at || 0),
    asr_fixes: asr as any,
    en_fixes: Array.isArray(cur.en_fixes) ? cur.en_fixes : [],
    settings: typeof cur.settings === "object" && cur.settings ? cur.settings : {},
  };
}

export function removeAsrFixFromRuleset(doc: Partial<RulesetDoc> | null | undefined, srcValue: string): RulesetDoc {
  const src = normalizeReplacementText(srcValue);
  const cur = doc || ({} as Partial<RulesetDoc>);
  const asr = (Array.isArray(cur.asr_fixes) ? cur.asr_fixes : []).filter(
    (it: any) => normalizeReplacementText(String(it?.src || "")) !== src,
  );
  return {
    version: Number((cur as any).version || 1),
    updated_at: Number((cur as any).updated_at || 0),
    asr_fixes: asr as any,
    en_fixes: Array.isArray(cur.en_fixes) ? cur.en_fixes : [],
    settings: typeof cur.settings === "object" && cur.settings ? cur.settings : {},
  };
}

export function augmentRepeatedSpanOccurrences(baseBlocks: ReviewBlock[], doc: ReviewSuspectsDoc): ReviewSuspectsDoc {
  const items = [...(doc?.items || [])];
  if (!baseBlocks.length || !items.length) return doc;

  const prototypeByText = new Map<string, ReviewSpan>();
  for (const item of items) {
    for (const span of item.spans || []) {
      const text = normalizeReplacementText(String(span?.text || ""));
      if (!text || String(span?.source || "") === "forced") continue;
      if (!prototypeByText.has(text)) prototypeByText.set(text, span);
    }
  }

  const repeatedTexts = Array.from(prototypeByText.keys()).filter((text) => {
    let total = 0;
    for (const block of baseBlocks) total += countOccurrences(String(block.text || ""), text);
    return total >= 2;
  });
  if (!repeatedTexts.length) return doc;

  const byIdx = new Map<number, ReviewSuspectItem>();
  for (const item of items) byIdx.set(Number(item.idx), { ...item, spans: [...(item.spans || [])] });

  for (const block of baseBlocks) {
    const idx = Number(block.idx);
    const row = byIdx.get(idx) || {
      idx,
      text: block.text,
      base: block.text,
      opt: block.text,
      changed: false,
      need_review: true,
      rule_reasons: [],
      spans: [],
    };
    const existingKeys = new Set(
      (row.spans || []).map((sp) => `${sp.start}:${sp.end}:${normalizeReplacementText(String(sp.text || ""))}`),
    );
    for (const text of repeatedTexts) {
      const proto = prototypeByText.get(text);
      if (!proto) continue;
      for (const start of findAllOccurrences(String(block.text || ""), text)) {
        const end = start + text.length;
        const key = `${start}:${end}:${text}`;
        if (existingKeys.has(key)) continue;
        existingKeys.add(key);
        (row.spans ||= []).push({
          ...proto,
          start,
          end,
          text,
          meta: { ...(proto.meta || {}), repeated_occurrence: true },
        });
      }
    }
    if ((row.spans || []).length > 0) byIdx.set(idx, row);
  }

  return {
    ...(doc || {}),
    items: Array.from(byIdx.values()).sort((a, b) => Number(a.idx) - Number(b.idx)),
  };
}
