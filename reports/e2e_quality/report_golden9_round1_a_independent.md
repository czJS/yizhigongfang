# 质量模式 E2E + 金标绝对分 评测报告

## 一句话结论怎么读

- **先看 passed_rate**：是否能稳定交付；掉通过率的开关不建议默认开。

- **再看 e2e_mean**：交付体验是否变好（门禁/截断/字幕工程/TTS风险综合）。

- **最后看 ASR(100)/MT(final100)**：有金标时，确认“识别/翻译”是否真的更准（更可解释）。


## 汇总表

| run | passed_rate | e2e_mean | ASR(100) | MT(final100) | p_improve(e2e) | p_improve(ASR) | p_improve(MT) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1.0 | 83.13 | 70.12 | 70.0 | - | - | - |
| vad_off | 1.0 | 82.13 | 58.53 | 68.46 | 0.2095 | 0.006 | 0.0105 |
| denoise_off | 1.0 | 82.93 | 67.96 | 69.47 | 0.23 | 0.153 | 0.06 |
| entity_protect_on | 1.0 | 83.07 | 70.12 | 69.88 | 0.406 | 0.0 | 0.1675 |
| meaning_split_on | 1.0 | 83.13 | 70.12 | 69.98 | 0.553 | 0.0 | 0.3865 |
| mt_json_on | 1.0 | 83.4 | 70.12 | 68.82 | 0.8245 | 0.0 | 0.0 |
| mt_topic_auto_on | 1.0 | 82.47 | 70.12 | 70.22 | 0.0045 | 0.0 | 0.9215 |


## 配置与参数

- segments: `/app/eval/e2e_quality/segments_golden_9.docker.jsonl`

- baseline: `/app/outputs/eval/e2e_quality_golden9_round1_a_independent/baseline`

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

