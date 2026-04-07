export type RiskLevel = "低" | "中" | "高" | "实验" | "未知";

export function stageOfToggle(key: string): string {
  // 按“阶段”分组（中文友好）
  if (key.startsWith("llm_selfcheck_") || key.startsWith("zh_gate_")) return "翻译";
  if (key.endsWith("_endpoint") || key.endsWith("_api_key") || key.startsWith("llm_")) return "开发者";
  if (key === "allow_gpu" || key === "allow_heavy_models" || key === "offline") return "开发者";
  // Lite-Fast user knobs (no prefix)
  if (key === "whispercpp_threads" || key === "min_sub_duration" || key === "sample_rate") return "语音识别";
  if (key === "skip_tts") return "配音";
  if (key.startsWith("asr_") || key === "vad_enable" || key === "denoise") return "语音识别";
  if (key.startsWith("mt_") || key === "entity_protect_enable" || key === "sentence_unit_enable") return "翻译";
  if (key.startsWith("zh_phrase_") || key === "review_enabled" || key === "zh_post_polish_enable") return "翻译";
  if (key.startsWith("tts_")) return "配音";
  if (
    key.startsWith("subtitle_") ||
    key.startsWith("display_") ||
    key.startsWith("mux_") ||
    key === "bilingual_srt"
  )
    return "合成";
  return "开发者";
}

export function riskTagColor(level: RiskLevel) {
  if (level === "低") return "green";
  if (level === "中") return "gold";
  if (level === "高") return "orange";
  if (level === "实验") return "red";
  return "default";
}

export const toggleMeta: Record<
  string,
  { label: string; desc?: string; risk?: RiskLevel; riskHint?: string; recommend?: "建议开启" | "建议关闭" | "按需" }
> = {
  // ASR/audio
  offline: { label: "离线模式", desc: "禁止运行时联网下载（更稳）。", risk: "低", recommend: "建议开启", riskHint: "开启后更稳；若本地缺模型会直接报错提示手动放置模型。" },
  vad_enable: { label: "人声检测", desc: "自动跳过长静音，改善切分。", risk: "中", recommend: "按需", riskHint: "静音多、断句怪时开启；若漏词增多请关闭。" },
  denoise: { label: "去噪", desc: "减弱底噪与嘶声。", risk: "中", recommend: "按需", riskHint: "底噪明显时开启；若音色变闷请关闭。" },
  asr_preprocess_enable: { label: "识别前音频预处理", desc: "响度与滤波校正。", risk: "中", recommend: "按需", riskHint: "音量忽大忽小/杂音多时开启。" },
  asr_merge_short_enable: { label: "合并极短片段", desc: "减少一两个字的碎片字幕。", risk: "中", recommend: "按需", riskHint: "字幕过碎时开启；若合并过头请关闭。" },
  asr_llm_fix_enable: { label: "识别保守纠错", desc: "修正明显同音错字。", risk: "高", recommend: "按需", riskHint: "错字多时开启；可能引入改写，建议抽样看结果。" },
  // MT/text
  sentence_unit_enable: { label: "句子单元（合并再拆回）", desc: "提升短句连贯性。", risk: "中", recommend: "按需", riskHint: "字幕太碎/翻译跳跃时开启。" },
  entity_protect_enable: { label: "人名地名统一", desc: "自动找出常见专名，尽量统一英文写法。", risk: "中", recommend: "按需", riskHint: "专名多、错译多时开启；一般素材可不管。" },
  // Subtitles / deliverables
  subtitle_postprocess_enable: { label: "字幕后处理", desc: "优化阅读速度与断行。", risk: "中", recommend: "按需", riskHint: "阅读速度告警/行太长时开启。" },
  subtitle_wrap_enable: { label: "字幕软换行", desc: "更易读（可能改变行数）。", risk: "中", recommend: "按需", riskHint: "对交付观感更好；但会改变行结构。建议需要更美观时开启。" },
  display_srt_enable: { label: "生成显示版字幕", desc: "更适合观看的版本。", risk: "中", recommend: "按需", riskHint: "生成额外版本通常安全；但若后续用于封装，则可能影响最终字幕外观。" },
  display_use_for_embed: { label: "封装使用显示版字幕", desc: "成片字幕采用显示版样式。", risk: "高", recommend: "按需", riskHint: "字幕外观会变化，建议先预览再开。" },
  display_merge_enable: { label: "显示字幕：合并短段", desc: "让显示字幕更连贯。", risk: "中", recommend: "按需", riskHint: "可能更顺，但也可能合并不当。建议抽样检查。" },
  display_split_enable: { label: "显示字幕：拆分长行", desc: "长行拆成更易读的多行。", risk: "低", recommend: "按需", riskHint: "显示字幕过长时开启。" },
  erase_subtitle_enable: { label: "硬字幕擦除", desc: "画面里有烧录字幕时使用。", risk: "高", recommend: "按需", riskHint: "可能擦不干净或伤画面。建议只在确实需要去除烧录字幕时开启。" },
  // TTS
  tts_fit_enable: { label: "配音超时裁剪", desc: "解决读不完的问题。", risk: "高", recommend: "按需", riskHint: "会改写朗读稿；配音总超时再开。" },
  tts_plan_enable: { label: "配音语速规划与停顿", desc: "让语速更稳。", risk: "高", recommend: "按需", riskHint: "会更慢；重要交付再开。" },
  // zh_polish / review
  review_enabled: {
    label: "提前校审闸门",
    desc: "只控制是否在翻译前进入人工闸门；不影响默认中文优化主链路。",
    risk: "中",
    recommend: "按需",
    riskHint: "开启后只有达到高风险阈值才会暂停；普通任务会继续自动跑完。",
  },
  zh_phrase_enable: {
    label: "中文优化：短语风险识别",
    desc: "质量模式默认主链路。先抽取易误译短语，再给后续中文优化和闸门判断使用。",
    risk: "低",
    recommend: "建议开启",
    riskHint: "关闭后会降低中文优化与高风险识别能力，一般不建议改。",
  },
  zh_post_polish_enable: {
    label: "中文优化：受约束改写",
    desc: "只改疑似行，并锁定高风险短语，给 MT 提供更稳定的中文输入。",
    risk: "中",
    recommend: "建议开启",
    riskHint: "关闭后更快，但会减少中文预处理收益。",
  },
  zh_gate_on_phrase_error: {
    label: "提前校审：短语识别报错也暂停",
    desc: "当短语识别失败时，是否直接进入人工闸门。",
    risk: "中",
    recommend: "按需",
    riskHint: "开启后更保守，但也更容易把异常当成需要人工介入的任务。",
  },
  zh_phrase_force_one_per_line: {
    label: "短语识别：强制每句至少 1 条",
    desc: "覆盖模式：每句都生成一个短语（用于尽早沉淀规则/逐句校审）。",
    risk: "中",
    recommend: "按需",
    riskHint: "会显著增加噪声与人工工作量，且可能让“suspects”覆盖到所有句。建议只在项目早期/专项素材开启。",
  },
  zh_phrase_idiom_enable: {
    label: "短语识别：成语词典命中",
    desc: "确定性补充：从成语表中直接命中成语并标注为高风险。",
    risk: "低",
    recommend: "建议开启",
    riskHint: "不增加大模型消耗；能显著提升成语/固定搭配的命中率。",
  },
  zh_phrase_second_pass_enable: {
    label: "短语识别：召回增强二次抽取",
    desc: "当第一轮几乎没抽到短语时，再对少量可疑行重跑一次（召回更高）。",
    risk: "中",
    recommend: "建议开启",
    riskHint: "会在触发时额外多跑一轮短语识别（更慢），但能显著降低“整条素材没抽到短语”的风险。",
  },
  // Perf
  allow_gpu: { label: "允许使用显卡加速", desc: "如有显卡则尝试使用。", risk: "低", recommend: "按需", riskHint: "一般建议开启（若驱动/环境稳定）。若遇到显卡相关报错，可关闭以求稳。" },
  allow_heavy_models: { label: "允许使用更重模型", desc: "更慢更吃资源。", risk: "中", recommend: "按需", riskHint: "可能提升质量，但更慢。建议在高配机器上按需开启。" },

  // DevOnly / debug / workflow / placeholder (make them meaningful in Chinese)
  asr_preprocess_loudnorm: { label: "识别预处理：响度归一", desc: "仅在开启“识别前音频预处理”后生效。", risk: "中", recommend: "按需", riskHint: "可能提升音量一致性，但会更慢；用于排障/专项素材更合适。" },
  asr_merge_save_debug: { label: "保存识别合并调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
  asr_llm_fix_save_debug: { label: "保存识别纠错调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
  tts_fit_save_raw: { label: "保存配音裁剪原始数据", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
  mt_skip_if_present: { label: "复用已有翻译（如果存在）", desc: "工作流/加速项：复用旧结果。", risk: "高", recommend: "按需", riskHint: "可能导致结果不可比（参数变了但复用旧翻译）。仅用于开发排障/重复跑测试。" },
  skip_tts: { label: "跳过配音（只产出字幕）", desc: "用于只交付字幕的场景。", risk: "中", recommend: "按需", riskHint: "不会生成成片与配音，这是预期行为。普通交付成片时不要开启。" },
  diarization: { label: "说话人分离（占位/不建议）", desc: "当前更偏占位/专项能力。", risk: "实验", recommend: "按需", riskHint: "结论不可泛化；仅用于开发探索。" },
  dedupe: { label: "去重（占位）", desc: "轻量模式目前不生效（脚本不读取该参数）。", risk: "未知", recommend: "按需", riskHint: "建议不要在交付中依赖它；后续可选择接入或移除。" },
};

export const paramMeta: Record<
  string,
  { label: string; desc?: string; risk?: RiskLevel; riskHint?: string; recommend?: "建议默认" | "按需"; unit?: string }
> = {
  sample_rate: {
    label: "音频采样率",
    desc: "一般不需要改；少数兼容场景可调整。",
    unit: "Hz",
    risk: "中",
    recommend: "建议默认",
    riskHint: "采样率越高文件更大；过低可能影响识别效果。默认 16000 较稳。",
  },
  whispercpp_threads: {
    label: "识别线程数",
    desc: "线程越多越快，但会更占 CPU。",
    risk: "中",
    recommend: "按需",
    riskHint: "线程过高会导致卡顿。建议按 CPU 核数酌情设置。",
  },
  vad_threshold: {
    label: "人声检测阈值",
    desc: "越高越严格（更容易判静音）。",
    risk: "高",
    recommend: "按需",
    riskHint: "调高可能漏词；调低可能切得太碎。建议只在已启用人声检测且需要微调时调整。",
  },
  vad_min_dur: {
    label: "最短静音时长",
    desc: "越大越不容易切分（段更长）。",
    unit: "秒",
    risk: "中",
    recommend: "按需",
    riskHint: "过大可能把不该合并的句子合在一起。建议在切分过碎时小步调整。",
  },
  min_sub_duration: {
    label: "最短字幕时长",
    desc: "减少“闪字幕”，也降低配音对齐压力。",
    unit: "秒",
    risk: "中",
    recommend: "建议默认",
    riskHint: "会延长字幕尾部时间轴；极端情况可能影响对齐精细度。",
  },
  tts_split_len: {
    label: "配音拆分阈值",
    desc: "英文句子过长时先拆分再合成。",
    unit: "字符",
    risk: "中",
    recommend: "建议默认",
    riskHint: "值过小会更碎；值过大可能增加配音重复/卡顿风险。",
  },
  tts_speed_max: {
    label: "最大语速上限",
    desc: "控制配音最快能到多快。",
    unit: "倍",
    risk: "中",
    recommend: "建议默认",
    riskHint: "越大越快，容易出现明显加速。",
  },
  mux_slow_max_ratio: {
    label: "音画对齐：最大慢放比例",
    desc: "配音更长时视频可放慢的上限。",
    unit: "倍",
    risk: "中",
    recommend: "建议默认",
    riskHint: "越大画面越慢，过大显得拖沓。",
  },
  mux_slow_threshold_s: {
    label: "音画对齐：触发阈值",
    desc: "音频超时到这个值才慢放。",
    unit: "秒",
    risk: "中",
    recommend: "建议默认",
    riskHint: "值过小会频繁触发。",
  },
  // ---- lite/quality：句子单元、专名保护 ----
  sentence_unit_min_chars: { label: "句子单元：最小字数", unit: "字", risk: "中", recommend: "按需", desc: "过小会合并太多。", riskHint: "建议先开“句子单元”，再小步微调。" },
  sentence_unit_max_chars: { label: "句子单元：最大字数", unit: "字", risk: "中", recommend: "按需", desc: "过大会让段太长。", riskHint: "建议先开“句子单元”，再小步微调。" },
  sentence_unit_max_segs: { label: "句子单元：最多合并段数", unit: "段", risk: "中", recommend: "按需", desc: "控制合并幅度。", riskHint: "数值越大越可能改变时间轴结构。" },
  sentence_unit_max_gap_s: { label: "句子单元：最大跨段间隔", unit: "秒", risk: "中", recommend: "按需", desc: "间隔太大也会被合并。", riskHint: "字幕断句怪时可调；建议 0.1~0.8 小步试。" },
  entity_protect_min_len: {
    label: "专名保护：最短长度",
    unit: "字",
    risk: "中",
    recommend: "按需",
    desc: "过短会把普通词当专名。",
    riskHint: "误保护变多时调大。",
  },
  entity_protect_max_len: {
    label: "专名保护：最长长度",
    unit: "字",
    risk: "中",
    recommend: "按需",
    desc: "过长会把长句当专名。",
    riskHint: "保护范围过大时调小。",
  },
  entity_protect_min_freq: {
    label: "专名保护：最小出现次数",
    unit: "次",
    risk: "中",
    recommend: "按需",
    desc: "出现次数达到才会保护。",
    riskHint: "保护不足时可调小。",
  },
  entity_protect_max_items: {
    label: "专名保护：最多保护条目",
    unit: "条",
    risk: "中",
    recommend: "按需",
    desc: "限制保护数量，避免过多干预。",
    riskHint: "专名很多时可调大。",
  },

  // ---- lite：识别预处理 / 合并短段 / 纠错 ----
  asr_preprocess_highpass: { label: "识别预处理：高通滤波", unit: "Hz", risk: "中", recommend: "按需", desc: "去除低频隆隆声。", riskHint: "仅在开启“识别前音频预处理”后生效。" },
  asr_preprocess_lowpass: { label: "识别预处理：低通滤波", unit: "Hz", risk: "中", recommend: "按需", desc: "削弱高频噪声。", riskHint: "仅在开启“识别前音频预处理”后生效。" },
  asr_merge_min_dur_s: { label: "合并短段：最短段时长", unit: "秒", risk: "中", recommend: "按需" },
  asr_merge_min_chars: { label: "合并短段：最少字数", unit: "字", risk: "中", recommend: "按需" },
  asr_merge_max_gap_s: { label: "合并短段：最大间隔", unit: "秒", risk: "中", recommend: "按需" },
  asr_merge_max_group_chars: { label: "合并短段：最大合并字数", unit: "字", risk: "中", recommend: "按需" },
  asr_llm_fix_max_items: { label: "识别纠错：最多处理条目", unit: "条", risk: "高", recommend: "按需" },
  asr_llm_fix_min_chars: { label: "识别纠错：最小字数门槛", unit: "字", risk: "高", recommend: "按需" },

  // ---- quality：LLM / QE ----
  llm_chunk_size: { label: "LLM：每次处理段数", unit: "段", risk: "中", recommend: "建议默认", desc: "越小越稳定，越大越快。", riskHint: "值太大会增加服务崩溃概率。" },
  mt_context_window: { label: "翻译：上下文窗口", unit: "行", risk: "中", recommend: "建议默认", desc: "为每行翻译提供前/后一行上下文。", riskHint: "过大可能带来“串台”，一般 1~2 足够。" },
  mt_request_timeout_s: { label: "翻译：兼容接口超时", unit: "秒", risk: "中", recommend: "建议默认", desc: "Ollama OpenAI 兼容接口单次请求最长等待时间。", riskHint: "过大容易出现“看起来卡死”；过小会增加重试。" },
  mt_request_retries: { label: "翻译：兼容接口重试次数", unit: "次", risk: "中", recommend: "建议默认", desc: "兼容接口失败时的重试次数。", riskHint: "过大只会拉长长尾耗时。" },
  llm_selfcheck_max_lines: { label: "翻译自检：最多行数", unit: "行", risk: "中", recommend: "建议默认", desc: "只对少量高风险行做二次自检。", riskHint: "值越大越慢，建议保持小范围。" },
  llm_selfcheck_max_ratio: { label: "翻译自检：最大比例", unit: "", risk: "中", recommend: "建议默认", desc: "控制自检最多覆盖多少比例的字幕行。", riskHint: "比例过高会把自检变成主路径。" },
  mt_topic_auto_max_segs: {
    label: "自动主题：最多采样段数",
    unit: "段",
    risk: "中",
    recommend: "建议默认",
    desc: "采样越多越慢。",
    riskHint: "不稳定时调小。",
  },
  glossary_placeholder_max: {
    label: "术语占位符：最多条目",
    unit: "条",
    risk: "中",
    recommend: "按需",
    desc: "限制参与保护的术语数量。",
    riskHint: "术语很多时可调大。",
  },
  max_sentence_len: { label: "句子最大长度", unit: "词/字", risk: "中", recommend: "建议默认", desc: "控制断句/处理上限。", riskHint: "过小可能切碎，过大可能变慢。" },

  // ---- quality：字幕后处理 / 显示字幕 ----
  subtitle_wrap_max_lines: { label: "字幕软换行：最多行数", unit: "行", risk: "中", recommend: "建议默认" },
  display_max_chars_per_line: { label: "显示字幕：每行最多字符", unit: "字", risk: "中", recommend: "建议默认" },
  display_max_lines: { label: "显示字幕：最多行数", unit: "行", risk: "中", recommend: "建议默认" },
  display_merge_max_gap_s: { label: "显示字幕合并：最大间隔", unit: "秒", risk: "中", recommend: "建议默认" },
  display_merge_max_chars: { label: "显示字幕合并：最大字数", unit: "字", risk: "中", recommend: "建议默认" },
  display_split_max_chars: { label: "显示字幕拆分：最大字数", unit: "字", risk: "低", recommend: "建议默认" },
  // ---- quality：zh_polish / review gate ----
  zh_phrase_max_spans: { label: "短语识别：每句最多短语数", unit: "条", risk: "中", recommend: "按需", desc: "提高召回会增加噪声。", riskHint: "一般 2~5 合理；过大容易全是误中。" },
  zh_phrase_max_total: { label: "短语识别：总短语上限", unit: "条", risk: "中", recommend: "按需", desc: "限制总量以控制耗时与输出大小。", riskHint: "若开启“强制每句至少 1 条”，建议 >= 句子数。" },
  zh_phrase_chunk_lines: { label: "短语识别：每批行数", unit: "行", risk: "中", recommend: "按需", desc: "越大请求次数越少，但单次输出更长。", riskHint: "过大可能更易输出截断；过小会增加请求次数。" },
  zh_phrase_candidate_max_lines: { label: "短语识别：候选行上限", unit: "行", risk: "中", recommend: "按需", desc: "长视频/多任务时，只抽取更“像风险”的行以降耗。", riskHint: "越小越省但可能漏；1 分钟视频可设 30~80。" },
  zh_gate_min_high_risk: { label: "提前校审：高风险最少条数", unit: "条", risk: "中", recommend: "建议默认", desc: "达到这个高风险数量才会在翻译前暂停。", riskHint: "越小越容易停；越大越偏自动继续。" },
  zh_gate_min_total_suspects: { label: "提前校审：疑似总数阈值", unit: "条", risk: "中", recommend: "按需", desc: "即使没有很多高风险，也可在疑似特别多时触发闸门。", riskHint: "建议作为兜底阈值，不要设得太低。" },

  // ---- quality：硬字幕擦除 ----
  erase_subtitle_x: { label: "硬字幕区域：X（起点）", risk: "高", recommend: "按需", desc: "配合坐标模式使用。" },
  erase_subtitle_y: { label: "硬字幕区域：Y（起点）", risk: "高", recommend: "按需" },
  erase_subtitle_w: { label: "硬字幕区域：宽度", risk: "高", recommend: "按需" },
  erase_subtitle_h: { label: "硬字幕区域：高度", risk: "高", recommend: "按需" },
  erase_subtitle_blur_radius: { label: "硬字幕擦除：模糊半径", unit: "px", risk: "高", recommend: "按需" },

  // ---- quality：配音贴合/规划 ----
  tts_fit_wps: { label: "配音裁剪：目标语速", unit: "词/秒", risk: "高", recommend: "按需" },
  tts_fit_min_words: { label: "配音裁剪：最少词数", unit: "词", risk: "高", recommend: "按需" },
  tts_plan_safety_margin: { label: "语速规划：安全余量", risk: "高", recommend: "建议默认" },
  tts_plan_min_cap: { label: "语速规划：最低上限", unit: "倍", risk: "高", recommend: "建议默认" },
};

export const textMeta: Record<
  string,
  {
    label: string;
    desc?: string;
    risk?: RiskLevel;
    riskHint?: string;
    recommend?: "建议默认" | "按需";
    kind?: "text" | "password" | "select";
    placeholder?: string;
    options?: { label: string; value: string }[];
  }
> = {
  // lite
  mt_model: { label: "翻译模型", desc: "离线翻译模型名称（HuggingFace）。", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：Helsinki-NLP/opus-mt-zh-en" },
  mt_device: { label: "翻译设备", desc: "auto 会自动选择（推荐）。", risk: "低", recommend: "建议默认", kind: "select", options: [{ label: "自动", value: "auto" }, { label: "CPU", value: "cpu" }, { label: "GPU", value: "cuda" }] },
  tts_backend: { label: "配音引擎", desc: "轻量模式默认使用 Piper（更易离线）。", risk: "中", recommend: "按需", kind: "select", options: [{ label: "Piper（离线）", value: "piper" }, { label: "Coqui（质量更好）", value: "coqui" }] },
  coqui_model: { label: "Coqui 模型", desc: "仅在配音引擎为 Coqui 时生效。", risk: "中", recommend: "按需", kind: "text" },
  coqui_device: { label: "Coqui 设备", desc: "auto 推荐。", risk: "低", recommend: "建议默认", kind: "select", options: [{ label: "自动", value: "auto" }, { label: "CPU", value: "cpu" }, { label: "GPU", value: "cuda" }] },

  // quality
  llm_endpoint: { label: "LLM 服务地址", desc: "本地/局域网 LLM 的 OpenAI 兼容地址（/v1）。", risk: "高", recommend: "按需", kind: "text", placeholder: "例如：http://127.0.0.1:11434/v1" },
  llm_model: { label: "LLM 模型名", desc: "需与服务端已安装的模型一致。", risk: "高", recommend: "按需", kind: "text", placeholder: "例如：qwen3.5:9b" },
  zh_phrase_llm_model: { label: "短语识别：模型名", desc: "仅用于短语识别/门禁，不影响翻译质量。", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：qwen3.5:9b" },
  zh_phrase_idiom_path: { label: "短语识别：成语表路径", desc: "4字成语列表（每行一个）。", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：assets/zh_phrase/idioms_4char.txt" },
  zh_phrase_same_pinyin_path: { label: "短语识别：同音表路径（成语近似命中）", desc: "用于“成语同音近似命中”（例如：神龙百尾≈神龙摆尾）。", risk: "低", recommend: "按需", kind: "text", placeholder: "例如：assets/zh_phrase/pycorrector_same_pinyin.txt" },
  llm_api_key: { label: "LLM 密钥", desc: "如服务需要鉴权则填写。", risk: "中", recommend: "按需", kind: "password" },
  erase_subtitle_method: {
    label: "硬字幕擦除方法",
    desc: "优先推荐“智能适配”：黑底字幕条会优先走黑带覆盖，普通画面字幕优先走柔化/修补。",
    risk: "高",
    recommend: "建议默认",
    kind: "select",
    options: [
      { label: "智能适配（推荐）", value: "auto" },
      { label: "黑带覆盖", value: "fill" },
      { label: "柔化覆盖", value: "blur" },
      { label: "细节修补", value: "delogo" },
    ],
  },
  erase_subtitle_coord_mode: { label: "硬字幕坐标模式", desc: "ratio 为比例坐标。", risk: "高", recommend: "按需", kind: "select", options: [{ label: "比例（ratio）", value: "ratio" }, { label: "像素（px）", value: "px" }] },

  // online
  asr_endpoint: { label: "在线识别服务地址", risk: "高", recommend: "按需", kind: "text" },
  asr_api_key: { label: "在线识别密钥", risk: "高", recommend: "按需", kind: "password" },
  mt_endpoint: { label: "在线翻译服务地址", risk: "高", recommend: "按需", kind: "text" },
  mt_api_key: { label: "在线翻译密钥", risk: "高", recommend: "按需", kind: "password" },
  tts_endpoint: { label: "在线配音服务地址", risk: "高", recommend: "按需", kind: "text" },
  tts_api_key: { label: "在线配音密钥", risk: "高", recommend: "按需", kind: "password" },
  tts_voice: { label: "在线配音音色", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：en-US-amy" },
};

