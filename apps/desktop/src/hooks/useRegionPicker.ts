import { useCallback, useState } from "react";

export function useRegionPicker() {
  const [regionPickerRect, setRegionPickerRect] = useState<{ x: number; y: number; w: number; h: number }>({
    // Default: 16:9 subtitle-safe area.
    // - roughly 90% width (5% side margins)
    // - lower-third placement for common hard-sub videos
    x: 0.05,
    y: 0.745,
    w: 0.9,
    h: 0.22,
  });
  const [regionPickerSampleFontSize, setRegionPickerSampleFontSize] = useState<number>(34);
  const [regionPickerSampleText, setRegionPickerSampleText] = useState<string>("字幕的大小会是这样的");

  const clamp01 = useCallback((v: number): number => {
    if (!Number.isFinite(v)) return 0;
    return Math.max(0, Math.min(1, v));
  }, []);

  const setRegionRectSafe = useCallback(
    (patch: Partial<{ x: number; y: number; w: number; h: number }>) => {
      setRegionPickerRect((prev) => {
        const next = { ...prev, ...patch };
        let x = clamp01(Number(next.x));
        let y = clamp01(Number(next.y));
        let w = clamp01(Number(next.w));
        let h = clamp01(Number(next.h));
        // Avoid 0-size rectangles
        w = Math.max(0.01, w);
        h = Math.max(0.01, h);

        // When changing width, keep the horizontal center (shrink/expand from both sides).
        if (patch.w !== undefined && Number.isFinite(prev.x) && Number.isFinite(prev.w)) {
          const centerX = clamp01(prev.x + prev.w / 2);
          x = centerX - w / 2;
        }
        // Y is the box start in the UI, so changing height should keep the top edge stable.

        // Keep inside frame
        if (x < 0) x = 0;
        if (y < 0) y = 0;
        if (x + w > 1) x = Math.max(0, 1 - w);
        if (y + h > 1) y = Math.max(0, 1 - h);
        return { x, y, w, h };
      });
    },
    [clamp01],
  );

  return {
    regionPickerRect,
    setRegionPickerRect,
    setRegionRectSafe,
    regionPickerSampleFontSize,
    setRegionPickerSampleFontSize,
    regionPickerSampleText,
    setRegionPickerSampleText,
  };
}

