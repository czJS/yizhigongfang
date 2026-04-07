import { describe, expect, it } from "vitest";
import { defaultBatchName, prettySize, safeStem, twoDigitIndex } from "./utils";

describe("utils", () => {
  it("formats default batch names from timestamps", () => {
    const ts = new Date(2026, 2, 23, 9, 7).getTime();
    expect(defaultBatchName(ts)).toBe("批次-20260323-0907");
  });

  it("sanitizes filenames into safe stems", () => {
    expect(safeStem("my demo!.mp4")).toBe("my_demo");
    expect(safeStem("中文 名称?.srt")).toBe("中文_名称");
    expect(safeStem("***")).toBe("未命名");
  });

  it("pads numeric indexes to three digits", () => {
    expect(twoDigitIndex(1)).toBe("001");
    expect(twoDigitIndex(12)).toBe("012");
    expect(twoDigitIndex(123)).toBe("123");
  });

  it("formats pretty byte sizes", () => {
    expect(prettySize(undefined as any)).toBe("-");
    expect(prettySize(0)).toBe("0 B");
    expect(prettySize(1024)).toBe("1.0 KB");
    expect(prettySize(1024 * 1024)).toBe("1.0 MB");
  });
});
