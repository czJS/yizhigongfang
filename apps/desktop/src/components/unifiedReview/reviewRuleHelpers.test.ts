import { describe, expect, it } from "vitest";
import {
  applyReplacementRuleToBlocks,
  augmentRepeatedSpanOccurrences,
  buildRuleEditorRows,
  inferReplacementRule,
  removeAsrFixFromRuleset,
  resolveRuleForSave,
  upsertAsrFixToRuleset,
} from "./reviewRuleHelpers";

describe("reviewRuleHelpers", () => {
  it("augments repeated hits across the whole material", () => {
    const baseBlocks = [
      { idx: 1, start: "00:00:01,000", end: "00:00:03,000", text: "羽人本是这个国家的公主。" },
      { idx: 2, start: "00:00:03,000", end: "00:00:05,000", text: "为了救羽人，大家决定出发。" },
      { idx: 3, start: "00:00:05,000", end: "00:00:07,000", text: "西域路远，他们只能流亡。" },
    ];
    const doc = {
      items: [
        {
          idx: 2,
          text: "为了救羽人，大家决定出发。",
          spans: [{ start: 3, end: 5, text: "羽人", source: "llm", risk: "medium" }],
        },
      ],
    };

    const next = augmentRepeatedSpanOccurrences(baseBlocks, doc);
    const idx1 = next.items?.find((it) => it.idx === 1);
    const idx2 = next.items?.find((it) => it.idx === 2);

    expect(idx1?.spans?.some((sp) => sp.text === "羽人")).toBe(true);
    expect(idx2?.spans?.filter((sp) => sp.text === "羽人")).toHaveLength(1);
  });

  it("infers a single-span replacement and applies it everywhere", () => {
    const baseText = "羽人本是这个国家的公主。";
    const editedText = "翼人本是这个国家的公主。";
    const spans = [{ start: 0, end: 2, text: "羽人", source: "llm", risk: "medium" }];

    const rule = inferReplacementRule(baseText, editedText, spans);
    expect(rule).toEqual({ src: "羽人", tgt: "翼人" });

    const applied = applyReplacementRuleToBlocks(
      [
        { idx: 1, start: "", end: "", text: "羽人本是这个国家的公主。" },
        { idx: 2, start: "", end: "", text: "为了救羽人，大家决定出发。" },
        { idx: 3, start: "", end: "", text: "西域路远，他们只能流亡。" },
      ],
      rule!,
    );

    expect(applied.changedLines).toBe(2);
    expect(applied.changedIdxs).toEqual([1, 2]);
    expect(applied.blocks[0].text).toContain("翼人");
    expect(applied.blocks[1].text).toContain("翼人");
    expect(applied.blocks[2].text).toContain("流亡");
  });

  it("upserts the inferred replacement into the global ruleset", () => {
    const next = upsertAsrFixToRuleset(
      {
        version: 1,
        updated_at: 0,
        asr_fixes: [{ id: "a0001", src: "羽人", tgt: "羽民", note: "旧值", scope: "global" } as any],
        en_fixes: [],
        settings: {},
      },
      { src: "羽人", tgt: "翼人" },
      "审核保存：测试素材",
    );

    expect(next.asr_fixes).toHaveLength(1);
    expect(next.asr_fixes[0].src).toBe("羽人");
    expect(next.asr_fixes[0].tgt).toBe("翼人");
    expect(next.asr_fixes[0].note).toContain("审核保存");
  });

  it("falls back to whole-line save when no local span replacement can be inferred", () => {
    const rule = resolveRuleForSave("百姓更是连连叫跑", "百姓更是连连叫苦", []);
    expect(rule).toEqual({ src: "百姓更是连连叫跑", tgt: "百姓更是连连叫苦" });
  });

  it("builds default rule rows from detected non-forced spans", () => {
    const rows = buildRuleEditorRows(
      [
        { start: 0, end: 2, text: "羽人", source: "llm", risk: "medium" },
        { start: 3, end: 5, text: "羽人", source: "llm", risk: "medium" },
        { start: 6, end: 8, text: "西域", source: "forced", risk: "medium" },
      ],
      {
        version: 1,
        updated_at: 0,
        asr_fixes: [{ id: "a1", src: "羽人", tgt: "翼人", scope: "global" } as any],
        en_fixes: [],
        settings: {},
      },
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].src).toBe("羽人");
    expect(rows[0].tgt).toBe("翼人");
  });

  it("removes a saved asr fix by source text", () => {
    const next = removeAsrFixFromRuleset(
      {
        version: 1,
        updated_at: 0,
        asr_fixes: [
          { id: "a1", src: "羽人", tgt: "翼人", scope: "global" } as any,
          { id: "a2", src: "痔床", tgt: "痔疮", scope: "global" } as any,
        ],
        en_fixes: [],
        settings: {},
      },
      "羽人",
    );
    expect(next.asr_fixes).toHaveLength(1);
    expect(next.asr_fixes[0].src).toBe("痔床");
  });
});
