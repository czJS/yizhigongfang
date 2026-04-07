import { createId } from "../../appHelpers";
import type { RulesetDoc } from "../../../types";

export type ReplaceStage = "asr" | "en";

export type ReplaceRuleRow = {
  id: string;
  src: string;
  tgt: string;
  stage: ReplaceStage;
  aliases: string;
  forbidden: string;
  note: string;
  critical: boolean;
};

export function replaceRowsFromRulesetDoc(doc: any): ReplaceRuleRow[] {
  const asr = Array.isArray(doc?.asr_fixes) ? doc.asr_fixes : [];
  const en = Array.isArray(doc?.en_fixes) ? doc.en_fixes : [];
  const asrRows: ReplaceRuleRow[] = asr.map((it: any, idx: number) => ({
    id: String(it?.id || `a${String(idx + 1).padStart(4, "0")}`),
    src: String(it?.src || ""),
    tgt: String(it?.tgt || ""),
    stage: "asr",
    aliases: "",
    forbidden: "",
    note: String(it?.note || ""),
    critical: false,
  }));
  const enRows: ReplaceRuleRow[] = en.map((it: any, idx: number) => ({
    id: String(it?.id || `e${String(idx + 1).padStart(4, "0")}`),
    src: String(it?.src || ""),
    tgt: String(it?.tgt || ""),
    stage: "en" as any,
    aliases: "",
    forbidden: "",
    note: String(it?.note || ""),
    critical: false,
  }));
  return [...asrRows, ...enRows];
}

export function hasAnyRulesInReplaceRows(rows: ReplaceRuleRow[]): boolean {
  const arr = rows || [];
  return arr.some((r) => {
    const src = (r.src || "").trim();
    const tgt = (r.tgt || "").trim();
    if (!src) return false;
    if (r.stage === "asr") return !!tgt;
    if ((r.stage as any) === "en") return !!tgt;
    return true;
  });
}

export function rulesetDocFromReplaceRows(rows: ReplaceRuleRow[], scope: "global" | "batch" | "template"): Partial<RulesetDoc> {
  const arr = rows || [];
  const asr_fixes = arr
    .filter((r) => r.stage === "asr" && (r.src || "").trim() && (r.tgt || "").trim())
    .map((r, idx) => ({
      id: r.id || `a${String(idx + 1).padStart(4, "0")}`,
      src: r.src.trim(),
      tgt: r.tgt.trim(),
      note: (r.note || "").trim(),
      scope,
    }));
  const en_fixes = arr
    .filter((r) => (r.stage as any) === "en" && (r.src || "").trim() && (r.tgt || "").trim())
    .map((r, idx) => ({
      id: r.id || `e${String(idx + 1).padStart(4, "0")}`,
      src: r.src.trim(),
      tgt: r.tgt.trim(),
      note: (r.note || "").trim(),
      scope,
    }));
  return { version: 1, asr_fixes: asr_fixes as any, en_fixes: en_fixes as any, settings: {} } as any;
}

export function newReplaceRuleRow(stage: ReplaceStage): ReplaceRuleRow {
  const st = stage as any;
  return { id: createId(), src: "", tgt: "", stage: st, aliases: "", forbidden: "", note: "", critical: false };
}

