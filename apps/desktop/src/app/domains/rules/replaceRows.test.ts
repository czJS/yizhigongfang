import { describe, expect, it } from "vitest";
import {
  hasAnyRulesInReplaceRows,
  newReplaceRuleRow,
  replaceRowsFromRulesetDoc,
  rulesetDocFromReplaceRows,
} from "./replaceRows";

describe("replaceRows", () => {
  it("builds editable rows from a ruleset doc", () => {
    const rows = replaceRowsFromRulesetDoc({
      asr_fixes: [{ id: "a1", src: "旧词", tgt: "新词", note: "asr" }],
      en_fixes: [{ src: "foo", tgt: "bar", note: "en" }],
    });

    expect(rows).toEqual([
      {
        id: "a1",
        src: "旧词",
        tgt: "新词",
        stage: "asr",
        aliases: "",
        forbidden: "",
        note: "asr",
        critical: false,
      },
      {
        id: "e0001",
        src: "foo",
        tgt: "bar",
        stage: "en",
        aliases: "",
        forbidden: "",
        note: "en",
        critical: false,
      },
    ]);
  });

  it("detects whether there are any valid replacement rules", () => {
    expect(hasAnyRulesInReplaceRows([{ id: "1", src: "", tgt: "x", stage: "asr" } as any])).toBe(false);
    expect(hasAnyRulesInReplaceRows([{ id: "2", src: "a", tgt: "", stage: "en" } as any])).toBe(false);
    expect(hasAnyRulesInReplaceRows([{ id: "3", src: "a", tgt: "b", stage: "asr" } as any])).toBe(true);
  });

  it("converts editable rows back to a ruleset doc and filters empty rows", () => {
    const doc = rulesetDocFromReplaceRows(
      [
        { id: "a1", src: " 旧词 ", tgt: " 新词 ", stage: "asr", aliases: "", forbidden: "", note: " note ", critical: false },
        { id: "e1", src: " foo ", tgt: " bar ", stage: "en", aliases: "", forbidden: "", note: "", critical: false },
        { id: "skip", src: "only-src", tgt: "", stage: "en", aliases: "", forbidden: "", note: "", critical: false },
      ],
      "template",
    );

    expect(doc).toEqual({
      version: 1,
      asr_fixes: [{ id: "a1", src: "旧词", tgt: "新词", note: "note", scope: "template" }],
      en_fixes: [{ id: "e1", src: "foo", tgt: "bar", note: "", scope: "template" }],
      settings: {},
    });
  });

  it("creates a new empty row with requested stage", () => {
    const row = newReplaceRuleRow("en");
    expect(row.stage).toBe("en");
    expect(row.src).toBe("");
    expect(row.tgt).toBe("");
    expect(row.id).toBeTruthy();
  });
});
