# 质量模式 E2E + 金标绝对分 评测报告

## 一句话结论怎么读

- **先看 passed_rate（交付通过率）**：是否能稳定交付；掉通过率的开关不建议默认开。

- **再看 e2e_mean（E2E 交付体验均分，0–100）**：交付体验是否变好（门禁/截断/字幕工程/TTS 风险综合）。

- **最后看 ASR(100)（识别分）/MT(final100)（翻译最终分）**：有金标时，确认“识别/翻译”是否真的更准（更可解释）。

> 注：`p_improve（提升置信度，0–1）` 是 bootstrap 估计“比 baseline 更好”的概率，越接近 1 越可信。


## 汇总表

| run（开关/实验） | passed_rate（交付通过率） | e2e_mean（交付体验均分） | ASR(100)（识别分） | MT(final100)（翻译分） | p_improve(e2e)（置信度） | p_improve(ASR) | p_improve(MT) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1.0 | 83.0 | 70.1 | 70.0 | - | - | - |
| sentence_unit_on | 1.0 | 82.93 | 70.1 | 70.52 | 0.3985 | 0.0 | 1.0 |
| tra_on | 1.0 | 83.6 | 70.1 | 69.31 | 0.991 | 0.0 | 0.0 |
| qe_on | 1.0 | 82.93 | 70.1 | 70.08 | 0.0 | 0.0 | 0.976 |
| subtitle_postprocess_on | 1.0 | 85.47 | 69.75 | 69.93 | 1.0 | 0.0 | 0.305 |
| tts_script_on | 1.0 | 71.35 | 70.1 | 70.12 | 0.0 | 0.0 | 0.99 |
| tts_fit_on | 1.0 | 83.0 | 70.1 | 70.04 | 0.4505 | 0.0 | 0.665 |
| tts_plan_on | 1.0 | 83.13 | 70.1 | 70.07 | 0.834 | 0.0 | 0.8335 |


## 配置与参数

- segments: `/app/eval/e2e_quality/segments_golden_9.docker.jsonl`

- baseline: `/app/outputs/eval/e2e_quality_golden9_p0/baseline`

- min_overlap_ratio: `0.2`

- bootstrap_iters: `2000`


## 说明（中文）

{
  "用途": "质量模式 E2E（交付体验）+ 金标绝对质量（ASR/翻译）评测。用于评估单开关/组合开关是否值得默认开启。",
  "三类指标": {
    "E2E": "不依赖参考译文：基于 quality_report.json 的门禁/交付体验评分 e2e_score_100 与 passed_rate。",
    "ASR": "依赖中文金标 chs.srt：用时间戳对齐后计算 CER（越低越好），并提供 asr_score_100=100*(1-CER) 便于对比。",
    "翻译": "依赖英文金标 eng.srt：用时间戳对齐后计算 chrF/BLEU/quality_score/ref_free_score，并给出 final_score_100（baseline=70 锚点）。"
  },
  "时间戳对齐": "对每个金标字幕块，按时间重叠选取最匹配的预测字幕块（min_overlap_ratio 可调），再做文本指标计算；这能避免仅按行号对齐导致的错位。",
  "置信度": "bootstrap：对每段视频抽样，统计候选 run 相对 baseline 的提升概率 p_improve。"
}

