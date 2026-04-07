import { describe, expect, it } from "vitest";
import { filterLiteFastParams, normalizePerTaskOverrideValues, pickPerTaskOverrideValues } from "./batchParams";

describe("batchParams", () => {
  it("pickPerTaskOverrideValues only keeps known override keys", () => {
    const got = pickPerTaskOverrideValues({
      erase_subtitle_x: 0.1,
      sub_font_size: 22,
      unknown_key: 123,
    });
    expect(got).toMatchObject({
      erase_subtitle_x: 0.1,
      sub_font_size: 22,
    });
    expect((got as any).unknown_key).toBeUndefined();
  });

  it("normalizePerTaskOverrideValues drops undefined and unknown keys", () => {
    const got = normalizePerTaskOverrideValues({
      erase_subtitle_x: 0.1,
      erase_subtitle_y: undefined,
      sub_outline: 1,
      bad: "x",
    });
    expect(got).toEqual({
      erase_subtitle_x: 0.1,
      sub_outline: 1,
    });
  });

  it("filterLiteFastParams keeps minimal lite knobs + per-task overrides + review/rules fields", () => {
    const got = filterLiteFastParams({
      whispercpp_threads: 4,
      min_sub_duration: 1.2,
      tts_speed_max: 1.1,
      erase_subtitle_x: 0.2,
      erase_subtitle_y: 0.7,
      review_enabled: true,
      ruleset_disable_global: true,
      ruleset_template_id: "tpl_1",
      ruleset_override: { version: 1, asr_fixes: [], en_fixes: [], settings: {} },
      some_other_param: 999,
    });
    expect(got.whispercpp_threads).toBe(4);
    expect(got.erase_subtitle_x).toBe(0.2);
    expect(got.review_enabled).toBe(true);
    expect(got.ruleset_disable_global).toBe(true);
    expect(got.ruleset_template_id).toBe("tpl_1");
    expect(got.ruleset_override).toBeTruthy();
    expect((got as any).some_other_param).toBeUndefined();
  });

  it("filterLiteFastParams removes undefined keys from output", () => {
    const got = filterLiteFastParams({
      whispercpp_threads: undefined,
      min_sub_duration: 1,
      erase_subtitle_x: undefined,
      review_enabled: undefined,
    });
    expect(got).toEqual({ min_sub_duration: 1 });
  });
});

