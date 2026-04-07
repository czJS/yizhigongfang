export const PER_TASK_OVERRIDE_KEYS = [
  "erase_subtitle_enable",
  "erase_subtitle_method",
  "erase_subtitle_coord_mode",
  "erase_subtitle_x",
  "erase_subtitle_y",
  "erase_subtitle_w",
  "erase_subtitle_h",
  "erase_subtitle_blur_radius",
  // subtitle burn-in style
  "sub_font_name",
  "sub_font_size",
  "sub_outline",
  "sub_shadow",
  "sub_margin_v",
  "sub_alignment",
  // subtitle placement box (optional)
  "sub_place_enable",
  "sub_place_coord_mode",
  "sub_place_x",
  "sub_place_y",
  "sub_place_w",
  "sub_place_h",
  // mux sync (hearing-first)
  "mux_sync_strategy",
  "mux_slow_max_ratio",
  "mux_slow_threshold_s",
] as const;

// Lite-Fast (速度优先) 最小可调集合：
// - 只保留少量“不会把系统调崩”的参数
// - 其余功能向/质量向开关不在轻量模式 UI 与请求里出现，由后端强制收敛
export const LITE_FAST_BATCH_KEYS = ["whispercpp_threads", "min_sub_duration", "tts_split_len", "tts_speed_max", "skip_tts"] as const;

export function pickPerTaskOverrideValues(src: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const k of PER_TASK_OVERRIDE_KEYS) out[k] = src?.[k];
  return out;
}

export function filterLiteFastParams(src: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  // keep minimal lite knobs
  for (const k of LITE_FAST_BATCH_KEYS) out[k] = src?.[k];
  // keep subtitle-related per-task overrides (hard-sub behavior stays as-is)
  for (const k of PER_TASK_OVERRIDE_KEYS) out[k] = src?.[k];
  // keep workflow flag
  out.review_enabled = src?.review_enabled;
  // keep rules override (created by Rules Center)
  if (src?.ruleset_override !== undefined) out.ruleset_override = src.ruleset_override;
  // keep rules switches (template/global) for this task
  if (src?.ruleset_disable_global !== undefined) out.ruleset_disable_global = src.ruleset_disable_global;
  if (src?.ruleset_template_id !== undefined) out.ruleset_template_id = src.ruleset_template_id;
  // drop undefineds to keep payload clean
  for (const [k, v] of Object.entries(out)) {
    if (v === undefined) delete out[k];
  }
  return out;
}

export function normalizePerTaskOverrideValues(vals: Record<string, any>): Record<string, any> {
  // 只保存这组字段，避免把其它批次参数误当成“单视频覆盖”
  const picked = pickPerTaskOverrideValues(vals || {});
  // 去掉 undefined，保持存储干净
  for (const [k, v] of Object.entries(picked)) {
    if (v === undefined) delete picked[k];
  }
  return picked;
}

