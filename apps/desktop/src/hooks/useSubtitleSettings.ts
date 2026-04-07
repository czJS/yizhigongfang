import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { message } from "antd";

export type SavedSubtitleSettings = {
  source: "has" | "none";
  values: Record<string, any>;
  rect?: { x: number; y: number; w: number; h: number };
  fontSize?: number;
};

export function useSubtitleSettings(opts: {
  route: string;
  wizardStep: number;
  subtitleSource: "has" | "none";
  setSubtitleSource: (v: "has" | "none") => void;
  wizardTasks: { inputName: string; localPath?: string }[];

  form: { getFieldsValue: (all?: any) => any; setFieldsValue: (vals: any) => void };
  regionPickerRect: { x: number; y: number; w: number; h: number };
  setRegionPickerRect: (r: { x: number; y: number; w: number; h: number }) => void;
  regionPickerSampleFontSize: number;
  setRegionPickerSampleFontSize: (n: number) => void;
  setFinalSubtitleFontSize: (n: number) => void;

  toFileUrl: (p: string) => string;
  regionPickerPreviewSource: string;
  setRegionPickerPreviewSource: (s: string) => void;
  regionPickerVideoPath: string;
  setRegionPickerVideoPath: (s: string) => void;
  setRegionPickerVideoReady: (v: boolean) => void;
  setRegionPickerVideoError: (s: string) => void;
  setRegionPickerVideoInfo: (v: any) => void;
  resetRegionPickerVideo: () => void;
}) {
  const [savedSubtitleSettings, setSavedSubtitleSettings] = useState<SavedSubtitleSettings | null>(null);
  // Avoid "save then immediately next" races: keep latest saved settings in a ref.
  const savedSubtitleSettingsRef = useRef<SavedSubtitleSettings | null>(null);

  const baselineHasSettingsRef = useRef<any | null>(null);
  const baselineNoneSettingsRef = useRef<any | null>(null);

  const round3 = useCallback((v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000)), []);

  const currentHasSettingsSnapshot = useCallback(() => {
    const vals = opts.form.getFieldsValue(true) || {};
    const rect = {
      x: round3(opts.regionPickerRect.x),
      y: round3(opts.regionPickerRect.y),
      w: round3(opts.regionPickerRect.w),
      h: round3(opts.regionPickerRect.h),
    };
    return {
      values: {
        erase_subtitle_method: String(vals.erase_subtitle_method || "auto"),
        erase_subtitle_x: round3(Number(vals.erase_subtitle_x || 0)),
        erase_subtitle_y: round3(Number(vals.erase_subtitle_y || 0)),
        erase_subtitle_w: round3(Number(vals.erase_subtitle_w || 0)),
        erase_subtitle_h: round3(Number(vals.erase_subtitle_h || 0)),
        erase_subtitle_blur_radius: Number(vals.erase_subtitle_blur_radius || 0),
      },
      rect,
      fontSize: Math.round((Number(opts.regionPickerSampleFontSize) || 0) * 100) / 100,
    };
  }, [opts, round3]);

  const currentNoneSettingsSnapshot = useCallback(() => {
    const vals = opts.form.getFieldsValue(true) || {};
    return {
      values: {
        sub_font_size: Number(vals.sub_font_size || 0),
        sub_margin_v: Number(vals.sub_margin_v || 0),
        sub_outline: Number(vals.sub_outline || 0),
        sub_alignment: Number(vals.sub_alignment || 0),
      },
    };
  }, [opts]);

  const hasUnsavedHasSettings = useCallback(() => {
    if (opts.subtitleSource !== "has") return false;
    const saved = savedSubtitleSettingsRef.current || savedSubtitleSettings;
    if (!saved || saved.source !== "has") {
      const base = baselineHasSettingsRef.current;
      if (!base) return false;
      const current = currentHasSettingsSnapshot();
      return JSON.stringify(current) !== JSON.stringify(base);
    }
    const current = currentHasSettingsSnapshot();
    const savedRect = saved.rect || { x: 0, y: 0, w: 0, h: 0 };
    const savedSnap = {
      values: {
        erase_subtitle_method: String(saved.values?.erase_subtitle_method || "auto"),
        erase_subtitle_x: round3(Number(saved.values?.erase_subtitle_x || 0)),
        erase_subtitle_y: round3(Number(saved.values?.erase_subtitle_y || 0)),
        erase_subtitle_w: round3(Number(saved.values?.erase_subtitle_w || 0)),
        erase_subtitle_h: round3(Number(saved.values?.erase_subtitle_h || 0)),
        erase_subtitle_blur_radius: Number(saved.values?.erase_subtitle_blur_radius || 0),
      },
      rect: {
        x: round3(Number(savedRect.x || 0)),
        y: round3(Number(savedRect.y || 0)),
        w: round3(Number(savedRect.w || 0)),
        h: round3(Number(savedRect.h || 0)),
      },
      fontSize: Math.round((Number(saved.fontSize) || 0) * 100) / 100,
    };
    return JSON.stringify(current) !== JSON.stringify(savedSnap);
  }, [opts.subtitleSource, savedSubtitleSettings, currentHasSettingsSnapshot, round3]);

  const hasUnsavedNoneSettings = useCallback(() => {
    if (opts.subtitleSource !== "none") return false;
    const saved = savedSubtitleSettingsRef.current || savedSubtitleSettings;
    if (!saved || saved.source !== "none") {
      const base = baselineNoneSettingsRef.current;
      if (!base) return false;
      const current = currentNoneSettingsSnapshot();
      return JSON.stringify(current) !== JSON.stringify(base);
    }
    const current = currentNoneSettingsSnapshot();
    const savedVals = saved.values || {};
    const savedSnap = {
      values: {
        sub_font_size: Number(savedVals.sub_font_size || 0),
        sub_margin_v: Number(savedVals.sub_margin_v || 0),
        sub_outline: Number(savedVals.sub_outline || 0),
        sub_alignment: Number(savedVals.sub_alignment || 0),
      },
    };
    return JSON.stringify(current) !== JSON.stringify(savedSnap);
  }, [opts.subtitleSource, savedSubtitleSettings, currentNoneSettingsSnapshot]);

  const saveSubtitleSettings = useCallback((o?: { silent?: boolean }) => {
    const silent = !!o?.silent;
    if (opts.subtitleSource === "has") {
      // Use current rectangle + font size as source of truth (more robust than waiting for form sync).
      const r = {
        x: round3(opts.regionPickerRect.x),
        y: round3(opts.regionPickerRect.y),
        w: round3(opts.regionPickerRect.w),
        h: round3(opts.regionPickerRect.h),
      };
      const vals = opts.form.getFieldsValue(true) || {};
      const next: SavedSubtitleSettings = {
        source: "has",
        values: {
          erase_subtitle_enable: true,
          erase_subtitle_method: String(vals.erase_subtitle_method || "auto"),
          erase_subtitle_coord_mode: "ratio",
          erase_subtitle_x: r.x,
          erase_subtitle_y: r.y,
          erase_subtitle_w: r.w,
          erase_subtitle_h: r.h,
          erase_subtitle_blur_radius: Number(vals.erase_subtitle_blur_radius || 0),
        },
        rect: r,
        fontSize: opts.regionPickerSampleFontSize,
      };
      savedSubtitleSettingsRef.current = next;
      setSavedSubtitleSettings(next);
      if (!silent) message.success("已保存有字幕设置");
      return;
    }
    const vals = opts.form.getFieldsValue(true) || {};
    const next: SavedSubtitleSettings = {
      source: "none",
      values: {
        sub_font_size: vals.sub_font_size,
        sub_margin_v: vals.sub_margin_v,
        sub_outline: vals.sub_outline,
        sub_alignment: vals.sub_alignment,
      },
    };
    savedSubtitleSettingsRef.current = next;
    setSavedSubtitleSettings(next);
    if (!silent) message.success("已保存无字幕设置");
  }, [opts, round3]);

  useEffect(() => {
    // Wizard Step2 ("has subtitles") should feel auto-saved:
    // - Rectangle changes and font size changes must persist even if user clicks "下一步" immediately.
    if (!(opts.route === "wizard" && opts.wizardStep === 1 && opts.subtitleSource === "has")) return;
    const t = setTimeout(() => {
      // Silent: avoid toast spam while user drags sliders.
      saveSubtitleSettings({ silent: true });
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    opts.route,
    opts.wizardStep,
    opts.subtitleSource,
    opts.regionPickerRect.x,
    opts.regionPickerRect.y,
    opts.regionPickerRect.w,
    opts.regionPickerRect.h,
    opts.regionPickerSampleFontSize,
    saveSubtitleSettings,
  ]);

  const applySavedSubtitleSettings = useCallback(() => {
    const saved = savedSubtitleSettingsRef.current || savedSubtitleSettings;
    if (!saved) {
      // If no saved settings, best-effort revert to baseline snapshot when available.
      if (opts.subtitleSource === "has" && baselineHasSettingsRef.current) {
        const base = baselineHasSettingsRef.current;
        opts.setSubtitleSource("has");
        opts.form.setFieldsValue({
          erase_subtitle_enable: true,
          erase_subtitle_method: String(base.values.erase_subtitle_method || "auto"),
          erase_subtitle_coord_mode: "ratio",
          erase_subtitle_x: base.rect.x,
          erase_subtitle_y: base.rect.y,
          erase_subtitle_w: base.rect.w,
          erase_subtitle_h: base.rect.h,
          erase_subtitle_blur_radius: base.values.erase_subtitle_blur_radius,
        });
        opts.setRegionPickerRect(base.rect);
        opts.setRegionPickerSampleFontSize(base.fontSize);
        opts.setFinalSubtitleFontSize(base.fontSize);
        return;
      }
      if (opts.subtitleSource === "none" && baselineNoneSettingsRef.current) {
        const base = baselineNoneSettingsRef.current;
        opts.setSubtitleSource("none");
        opts.form.setFieldsValue(base.values || {});
        return;
      }
      // Fallback: disable erase
      opts.form.setFieldsValue({ erase_subtitle_enable: false });
      return;
    }
    opts.setSubtitleSource(saved.source);
    opts.form.setFieldsValue(saved.values || {});
    if (saved.rect) opts.setRegionPickerRect(saved.rect);
    if (typeof saved.fontSize === "number") {
      opts.setRegionPickerSampleFontSize(saved.fontSize);
      opts.setFinalSubtitleFontSize(saved.fontSize);
    }
  }, [opts, savedSubtitleSettings]);

  useEffect(() => {
    // Initialize dirty-check baselines when entering Step2, so the first "下一步" can prompt correctly.
    if (!(opts.route === "wizard" && opts.wizardStep === 1)) return;
    const saved = savedSubtitleSettingsRef.current || savedSubtitleSettings;
    if (opts.subtitleSource === "has") {
      if (saved?.source === "has") {
        baselineHasSettingsRef.current = null;
        return;
      }
      if (!baselineHasSettingsRef.current) {
        baselineHasSettingsRef.current = currentHasSettingsSnapshot();
      }
      baselineNoneSettingsRef.current = null;
      return;
    }
    // none
    if (saved?.source === "none") {
      baselineNoneSettingsRef.current = null;
      return;
    }
    if (!baselineNoneSettingsRef.current) {
      baselineNoneSettingsRef.current = currentNoneSettingsSnapshot();
    }
    baselineHasSettingsRef.current = null;
  }, [opts, savedSubtitleSettings, currentHasSettingsSnapshot, currentNoneSettingsSnapshot]);

  useEffect(() => {
    if (!(opts.route === "wizard" && opts.wizardStep === 1 && opts.subtitleSource === "has")) return;
    const localPath = opts.wizardTasks?.[0]?.localPath || "";
    if (!localPath) {
      if (opts.regionPickerPreviewSource) {
        opts.resetRegionPickerVideo();
        opts.setRegionPickerPreviewSource("");
      }
      return;
    }
    if (opts.regionPickerPreviewSource === localPath && opts.regionPickerVideoPath) return;
    const src = opts.toFileUrl(localPath);
    if (!src) return;
    opts.setRegionPickerPreviewSource(localPath);
    opts.setRegionPickerVideoPath(src);
    opts.setRegionPickerVideoReady(false);
    opts.setRegionPickerVideoError("");
    opts.setRegionPickerVideoInfo({ name: opts.wizardTasks?.[0]?.inputName || "预览视频" });
  }, [opts]);

  useEffect(() => {
    if (!(opts.route === "wizard" && opts.wizardStep === 1 && opts.subtitleSource === "has")) return;
    const r = opts.regionPickerRect;
    const round = (v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
    opts.form.setFieldsValue({
      erase_subtitle_enable: true,
      erase_subtitle_method: String((opts.form.getFieldsValue(true) || {}).erase_subtitle_method || "auto"),
      erase_subtitle_coord_mode: "ratio",
      erase_subtitle_x: round(r.x),
      erase_subtitle_y: round(r.y),
      erase_subtitle_w: round(r.w),
      erase_subtitle_h: round(r.h),
    });
  }, [opts]);

  return useMemo(
    () => ({
      savedSubtitleSettings,
      setSavedSubtitleSettings,
      savedSubtitleSettingsRef,
      saveSubtitleSettings,
      applySavedSubtitleSettings,
      hasUnsavedHasSettings,
      hasUnsavedNoneSettings,
    }),
    [savedSubtitleSettings, saveSubtitleSettings, applySavedSubtitleSettings, hasUnsavedHasSettings, hasUnsavedNoneSettings],
  );
}

