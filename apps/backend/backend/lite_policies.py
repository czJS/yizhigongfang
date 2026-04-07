from __future__ import annotations

from typing import Any, Dict

LITE_REMOVED_TASK_KEYS = {
    # Retired MT heuristics
    "sentence_unit_enable",
    "sentence_unit_min_chars",
    "sentence_unit_max_chars",
    "sentence_unit_max_segs",
    "sentence_unit_max_gap_s",
    "sentence_unit_boundary_punct",
    "sentence_unit_break_words",
    "entity_protect_enable",
    "entity_protect_min_len",
    "entity_protect_max_len",
    "entity_protect_min_freq",
    "entity_protect_max_items",
    # Retired lite delivery tuning surface
    "subtitle_postprocess_enable",
    "subtitle_wrap_enable",
    "subtitle_max_chars_per_line",
    "subtitle_wrap_max_lines",
    "subtitle_max_cps",
    "subtitle_cps_fix_enable",
    "subtitle_cps_safety_gap",
    "display_srt_enable",
    "display_use_for_embed",
    "display_max_chars_per_line",
    "display_max_lines",
    "display_merge_enable",
    "display_merge_max_gap_s",
    "display_merge_max_chars",
    "display_split_enable",
    "display_split_max_chars",
    "tts_fit_enable",
    "tts_fit_wps",
    "tts_fit_min_words",
    "tts_fit_save_raw",
    "tts_plan_enable",
    "tts_plan_safety_margin",
    "tts_plan_min_cap",
    "tts_script_enable",
    "tts_script_strict_clean_enable",
    # Retired lite ASR tuning surface
    "asr_preprocess_enable",
    "asr_preprocess_loudnorm",
    "asr_preprocess_highpass",
    "asr_preprocess_lowpass",
    "asr_preprocess_ffmpeg_extra",
    "asr_merge_short_enable",
    "asr_merge_min_dur_s",
    "asr_merge_min_chars",
    "asr_merge_max_gap_s",
    "asr_merge_max_group_chars",
    "asr_merge_save_debug",
    # Retired lite LLM stage1 path
    "asr_llm_fix_enable",
    "asr_llm_fix_mode",
    "asr_llm_fix_max_items",
    "asr_llm_fix_max_ratio",
    "asr_llm_fix_min_chars",
    "asr_llm_fix_batch_size",
    "asr_llm_fix_timeout_s",
    "asr_llm_fix_retries",
    "asr_llm_fix_budget_s",
    "asr_llm_fix_verify_enable",
    "asr_llm_fix_verify_timeout_s",
    "asr_llm_fix_save_debug",
    "asr_llm_fix_model",
    "asr_llm_fix_endpoint",
    "asr_llm_fix_api_key",
    # Retired/dead lite MT workflow keys
    "mt_topic",
    "mt_json_enable",
    "mt_topic_auto_enable",
    "mt_topic_auto_max_segs",
    "mt_pause_before_translate",
    "meaning_split_enable",
    "meaning_split_min_chars",
    "meaning_split_max_parts",
    "meaning_split_save_debug",
    "glossary_prompt_enable",
    "tra_enable",
    "tra_save_debug",
    "tra_json_enable",
    "tra_auto_enable",
    "qe_enable",
    "qe_threshold",
    "qe_mode",
    "qe_max_items",
    "qe_save_report",
    "qe_model",
    "qe_time_budget_s",
    "qe_embed_enable",
    "qe_embed_model",
    "qe_embed_threshold",
    "qe_embed_max_segs",
    "qe_backtranslate_enable",
    "qe_backtranslate_model",
    "qe_backtranslate_max_items",
    "qe_backtranslate_overlap_threshold",
    "glossary_placeholder_enable",
    "glossary_placeholder_max",
}


def apply_lite_fixed_policies(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single source of truth for product-fixed lite-mode policies.

    Lite is intentionally narrow:
    - keep only a tiny user-facing parameter surface
    - retire experimental / quality-style knobs from task params
    - force low-risk, broadly beneficial text cleanup on
    """
    p: Dict[str, Any] = dict(params or {})
    for key in LITE_REMOVED_TASK_KEYS:
        p.pop(key, None)
    p["asr_normalize_enable"] = True
    p["asr_glossary_fix_enable"] = True
    p["asr_low_cost_clean_enable"] = True
    p["asr_badline_detect_enable"] = True
    p["mt_batch_enable"] = True
    return p
