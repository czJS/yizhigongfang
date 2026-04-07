export type Tier = "normal" | "mid" | "high";

export interface HardwareInfo {
  cpu_cores: number;
  memory_gb: number;
  gpu_name: string | null;
  gpu_vram_gb: number | null;
  cuda_available?: boolean;
  tier: Tier;
  device_policy?: {
    quality_allow_gpu?: boolean;
    asr_device?: string;
    tts_device?: string;
    llm_runtime?: string;
    gpu_effective?: boolean;
  };
  presets?: Record<string, any>;
}

export interface PresetConfig {
  label?: string;
  hardware_hint?: string;
  asr_model?: string;
  mt_model?: string;
  tts_backend?: string;
  tts_model?: string;
  tts_device?: string;
  vad_enable?: boolean;
  dedupe?: boolean;
  bilingual_srt?: boolean;
  sentence_unit_enable?: boolean;
  max_sentence_len?: number;
  [key: string]: any;
}

export interface AppConfig {
  paths?: {
    script?: string;
    whispercpp_bin?: string;
    ffmpeg_bin?: string;
    models_root?: string;
    tts_home?: string;
    outputs_root?: string;
  };
  // Dev/runtime diagnostics (best-effort; present in /api/config and /api/health runtime)
  runtime?: {
    repo_root?: string;
    cwd?: string;
    config_path?: string;
    CONFIG_PATH?: string;
    YGF_APP_ROOT?: string;
    YGF_PORT?: string;
    pid?: number;
    sys_executable?: string;
    sys_executable_name?: string;
    is_frozen?: boolean;
    [key: string]: any;
  };
  config_stack?: {
    root?: string;
    defaults_path?: string;
    active_config_path?: string;
    override_dir?: string;
    override_files?: string[];
    source_chain?: string[];
    merged_hash?: string;
    [key: string]: any;
  };
  defaults?: Record<string, any>;
  presets?: Record<string, PresetConfig>;
  quality_gates?: {
    allow_cjk_in_english_srt?: boolean;
    max_chars_per_line?: number;
    max_cps?: number;
    max_empty_ratio?: number;
    max_truncation_s?: number;
    max_truncation_ratio?: number;
    [key: string]: any;
  };
  ui?: {
    api_base?: string;
    polling_ms?: number;
    quality_teaser_only?: boolean;
    online_disabled?: boolean;
  };
  available_modes?: string[];
}

export interface TaskStatus {
  id: string;
  video: string;
  // Backend may return queued while waiting for worker.
  state: "queued" | "running" | "completed" | "failed" | "cancelled" | "paused";
  stage: number | null;
  stage_name: string;
  progress: number;
  message: string;
  started_at: number;
  ended_at: number | null;
  work_dir: string;
  mode?: string;
  resume_from?: "asr" | "mt" | "tts" | "mux" | null;
  created_at?: number | null;
  resumed_at?: number | null;
}

export interface Artifact {
  name: string;
  path: string;
  size: number;
}

export interface LogResponse {
  content: string;
  next_offset: number;
}

export interface QualityHit {
  idx?: number;
  text?: string;
  len?: number;
  cps?: number;
  dur_s?: number;
}

export interface TerminologySample {
  idx?: number;
  src?: string;
  tgt?: string;
  bad?: string;
  en?: string;
}

export interface TerminologyCheck {
  glossary_items_n: number;
  hits_n: number;
  missing_n: number;
  forbidden_n: number;
  missing_samples?: TerminologySample[];
  forbidden_samples?: TerminologySample[];
  skipped?: boolean;
  reason?: string;
}

export interface QualityReport {
  version: number;
  task_id: string;
  mode: string;
  work_dir: string;
  passed: boolean;
  errors: string[];
  warnings: string[];
  checks: {
    // v2 schema (preferred):
    // - missing_required: hard required artifacts missing (currently only eng.srt)
    // - missing_expected: common deliverables that might be absent depending on mode/options
    // Legacy schema may contain `missing`.
    required_artifacts?: {
      required?: string[];
      missing_required?: string[];
      expected?: string[];
      missing_expected?: string[];
      // legacy fallback
      missing?: string[];
    };
    english_purity?: { allow_cjk: boolean; cjk_hits_n: number; cjk_hits: QualityHit[] };
    format_numbering_bullets?: { hits_n: number; hits: QualityHit[] };
    line_length?: { max_chars_per_line: number; hits_n: number; hits: QualityHit[] };
    reading_speed?: { max_cps: number; hits_n: number; hits: QualityHit[] };
    terminology?: TerminologyCheck;
    video_truncation?: any;
  };
  metrics: Record<string, any>;
}

export interface GlossaryItem {
  id: string;
  src: string;
  tgt: string;
  aliases?: string[];
  forbidden?: string[];
  note?: string;
  scope?: string;
}

export interface GlossaryDoc {
  version: number;
  updated_at: number;
  items: GlossaryItem[];
}

export interface RulesetAsrFix {
  id: string;
  src: string;
  tgt: string;
  note?: string;
  scope?: string;
}

export interface RulesetEnFix {
  id: string;
  src: string;
  tgt: string;
  note?: string;
  scope?: string;
}

export interface RulesetDoc {
  version: number;
  updated_at: number;
  asr_fixes: RulesetAsrFix[];
  en_fixes?: RulesetEnFix[];
  settings?: Record<string, any>;
}

export interface RulesetTemplateInfo {
  id: string;
  name: string;
  updated_at: number;
}

export interface RulesetTemplate {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  doc: RulesetDoc;
}

