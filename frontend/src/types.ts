export type Tier = "normal" | "mid" | "high";

export interface HardwareInfo {
  cpu_cores: number;
  memory_gb: number;
  gpu_name: string | null;
  gpu_vram_gb: number | null;
  tier: Tier;
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
  };
  available_modes?: string[];
}

export interface TaskStatus {
  id: string;
  video: string;
  state: "running" | "completed" | "failed" | "cancelled" | "paused";
  stage: number | null;
  stage_name: string;
  progress: number;
  message: string;
  started_at: number;
  ended_at: number | null;
  work_dir: string;
  mode?: string;
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
    required_artifacts?: { required: string[]; missing: string[] };
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

