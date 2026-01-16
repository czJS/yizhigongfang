# 轻量模式测评纵览（Round1 / Round1.5 / Round2）

- 数据集：`short3`（n=3）
- 预设：`normal`
- 产物目录：`/app/outputs/eval/e2e_lite_flow/short3/lite_normal`
- 原始报告：`/app/reports/lite/report_short3_lite_flow_20260111_074636.json`

## Round1：单开关（Lite）

| run | passed_rate | e2e_mean | p_improve(e2e) |
| --- | --- | --- | --- |
| baseline | 1.0 | 97.8 | - |
| asr_normalize_off | 1.0 | 97.8 | 0.0 |
| bilingual_srt_off | 1.0 | 97.8 | 0.0 |
| denoise_on | 1.0 | 96.6 | 0.0 |
| vad_on | 1.0 | 97.8 | 0.0 |
| sentence_unit_on | 1.0 | 97.4 | 0.0 |
| entity_protect_on | 1.0 | 97.8 | 0.0 |

> 判读建议：优先看 passed_rate 是否下降；再看 e2e_mean 与 p_improve（一般 p_improve≥0.9 才考虑默认开）。基线为 `baseline`。

## Round1.5：三点扫参（Lite，short3 快筛）

| run | passed_rate | e2e_mean | p_improve(e2e) |
| --- | --- | --- | --- |
| baseline | 1.0 | 97.8 | - |
| vad_threshold=0.5 | 1.0 | 97.8 | 0.0 |
| vad_threshold=0.6 | 1.0 | 97.8 | 0.0 |
| vad_threshold=0.7 | 1.0 | 97.8 | 0.0 |
| vad_min_dur=0.8 | 1.0 | 97.8 | 0.0 |
| vad_min_dur=1.5 | 1.0 | 97.8 | 0.0 |
| vad_min_dur=2.0 | 1.0 | 97.8 | 0.0 |
| min_sub_duration=1.0 | 1.0 | 97.8 | 0.0 |
| min_sub_duration=1.5 | 1.0 | 97.8 | 0.0 |
| min_sub_duration=2.0 | 1.0 | 97.8 | 0.0 |
| tts_split_len=60 | 1.0 | 97.8 | 0.0 |
| tts_split_len=80 | 1.0 | 97.8 | 0.0 |
| tts_split_len=120 | 1.0 | 97.8 | 0.0 |
| tts_speed_max=1.05 | 1.0 | 97.8 | 0.0 |
| tts_speed_max=1.1 | 1.0 | 97.8 | 0.0 |
| tts_speed_max=1.2 | 1.0 | 97.8 | 0.0 |

> 判读建议：优先看 passed_rate 是否下降；再看 e2e_mean 与 p_improve（一般 p_improve≥0.9 才考虑默认开）。基线为 `baseline`。

