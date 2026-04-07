import { describe, expect, it } from "vitest";
import {
  issueTag,
  joinList,
  normalizeLegacyQualityIssueText,
  qualityExampleGroups,
  shortReason,
  splitList,
  suggestForIssue,
  tagColorForUiState,
  uiStateFromBackend,
} from "./appHelpers";

describe("appHelpers", () => {
  it("uiStateFromBackend maps backend states", () => {
    expect(uiStateFromBackend("running" as any)).toBe("running");
    expect(uiStateFromBackend("queued" as any)).toBe("running");
    expect(uiStateFromBackend("completed" as any)).toBe("completed");
    expect(uiStateFromBackend("failed" as any)).toBe("failed");
    expect(uiStateFromBackend("cancelled" as any)).toBe("cancelled");
    expect(uiStateFromBackend("paused" as any)).toBe("paused");
    expect(uiStateFromBackend("unknown" as any)).toBe("pending");
  });

  it("normalizeLegacyQualityIssueText converts legacy English readability messages", () => {
    expect(normalizeLegacyQualityIssueText("eng.srt reading speed too high (> 20.0 cps): 10 items")).toContain("英文字幕阅读速度过快");
    expect(normalizeLegacyQualityIssueText("eng.srt overly long lines (> 42 chars): 12 items")).toContain("英文字幕单行过长");
  });

  it("shortReason prioritizes task state and quality outcome", () => {
    expect(shortReason({ state: "failed", failureReason: "OOM" } as any)).toBe("OOM");
    expect(shortReason({ state: "paused" } as any)).toContain("已暂停");
    expect(shortReason({ state: "cancelled" } as any)).toBe("已取消");
    expect(shortReason({ state: "completed", qualityPassed: false } as any)).toContain("质量检查未通过");
    expect(shortReason({ state: "completed", qualityPassed: true } as any)).toBe("可交付");
    expect(shortReason({ state: "running", stageName: "翻译中" } as any)).toBe("翻译中");
    expect(shortReason({ state: "pending" } as any)).toBe("等待处理");
  });

  it("tagColorForUiState maps states to UI tag colors", () => {
    expect(tagColorForUiState("running" as any)).toBe("processing");
    expect(tagColorForUiState("completed" as any)).toBe("success");
    expect(tagColorForUiState("failed" as any)).toBe("error");
    expect(tagColorForUiState("paused" as any)).toBe("warning");
    expect(tagColorForUiState("cancelled" as any)).toBe("default");
    expect(tagColorForUiState("pending" as any)).toBe("default");
  });

  it("suggestForIssue returns user-facing guidance by issue type", () => {
    expect(suggestForIssue("reading speed too high")).toContain("字幕太密");
    expect(suggestForIssue("overly long lines")).toContain("拆成两行");
    expect(suggestForIssue("contains cjk")).toContain("混入中文");
    expect(suggestForIssue("timeline overlap")).toContain("时间轴重叠");
    expect(suggestForIssue("missing output_en_sub.mp4")).toContain("产物是否生成完整");
  });

  it("issueTag classifies common quality issue families", () => {
    expect(issueTag("reading speed too high")).toEqual({ label: "可读性", color: "geekblue" });
    expect(issueTag("contains CJK chars")).toEqual({ label: "语言", color: "magenta" });
    expect(issueTag("时间轴重叠")).toEqual({ label: "时间轴", color: "orange" });
    expect(issueTag("missing output_en_sub.mp4")).toEqual({ label: "产物", color: "red" });
    expect(issueTag("audio duration mismatch")).toEqual({ label: "时长", color: "orange" });
    expect(issueTag("subtitle parse error")).toEqual({ label: "字幕", color: "blue" });
    expect(issueTag("tts failed")).toEqual({ label: "音频", color: "purple" });
    expect(issueTag("unknown")).toEqual({ label: "其它", color: "default" });
  });

  it("qualityExampleGroups summarizes major report sections", () => {
    const groups = qualityExampleGroups({
      checks: {
        required_artifacts: {
          missing_required: ["eng.srt"],
          missing_expected: ["output_en_sub.mp4"],
        },
        english_purity: {
          cjk_hits_n: 1,
          cjk_hits: [{ idx: 3, text: "Hello 你好" }],
        },
        line_length: {
          hits_n: 1,
          hits: [{ idx: 4, text: "A very long subtitle line that should be shortened." }],
        },
        reading_speed: {
          hits_n: 1,
          hits: [{ idx: 5, text: "Too fast subtitle", cps: 23.8 }],
        },
        timeline_sanity: {
          negative_or_zero_dur_n: 1,
          negative_or_zero_dur: [{ idx: 6, dur_s: 0 }],
          overlap_n: 1,
          overlaps: [{ idx: 7, overlap_s: 0.35 }],
        },
      },
    } as any);

    expect(groups.map((g) => g.title)).toEqual([
      "缺少关键产物（会影响交付）",
      "未生成的交付物（可能是选项/失败导致）",
      "英文字幕含中文/全角字符（1）",
      "英文字幕单行过长（1）",
      "英文字幕阅读速度过快（1）",
      "字幕时间轴异常（可能导致闪烁/覆盖）",
    ]);
    expect(groups[0].items).toEqual(["eng.srt"]);
    expect(groups[5].items.join(" ")).toContain("重叠");
  });

  it("splitList and joinList normalize comma-like inputs", () => {
    expect(splitList(" a, b;\n c ,, ")).toEqual(["a", "b", "c"]);
    expect(joinList(["a", "", "b"])).toBe("a, b");
  });
});

