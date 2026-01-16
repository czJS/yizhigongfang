# Round1.5 S1（short3 两点快筛）

- baseline: passed_rate=1.0 e2e_mean=94.2

| rank | key | stage | cli | point | value | delta_e2e | p_improve | passed_rate | e2e_mean | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `asr_merge_min_dur_s` | ASR | `--asr-merge-min-dur-s` | A | 1 | 0.6 | 1.0 | 1.0 | 94.8 |  |
| 2 | `max_sentence_len` | MT/QE/TRA | `--max-sentence-len` | A | 60 | 0.6 | 1.0 | 1.0 | 94.8 |  |
| 3 | `sentence_unit_min_chars` | MT/QE/TRA | `--sentence-unit-min-chars` | A | 32 | 0.6 | 1.0 | 1.0 | 94.8 |  |
| 4 | `tts_gain_db` | TTS | `--tts-gain-db` | A | 3 | 0.6 | 1.0 | 1.0 | 94.8 |  |
| 5 | `asr_llm_fix_max_items` | ASR | `--asr-llm-fix-max-items` | A | 90 | 0.6 | 0.9705 | 1.0 | 94.8 |  |
| 6 | `sentence_unit_max_gap_s` | MT/QE/TRA | `--sentence-unit-max-gap-s` | A | 0.7 | 0.6 | 0.96 | 1.0 | 94.8 |  |
| 7 | `asr_preprocess_highpass` | ASR | `--asr-preprocess-highpass` | A | 100 | 0.4 | 0.9705 | 1.0 | 94.6 |  |
| 8 | `qe_backtranslate_overlap_threshold` | MT/QE/TRA | `--qe-backtranslate-overlap-threshold` | A | 0.45 | 0.4 | 0.9705 | 1.0 | 94.6 |  |
| 9 | `display_max_lines` | FFmpeg/字幕/混音/擦除 | `--display-max-lines` | A | 4 | 0.4 | 0.9705 | 1.0 | 94.6 |  |
| 10 | `display_merge_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-merge-max-chars` | A | 120 | 0.4 | 0.9705 | 1.0 | 94.6 |  |
| 11 | `display_split_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-split-max-chars` | A | 129 | 0.4 | 0.9705 | 1.0 | 94.6 |  |
| 12 | `asr_merge_min_chars` | ASR | `--asr-merge-min-chars` | A | 8 | 0.4 | 0.9615 | 1.0 | 94.6 |  |
| 13 | `entity_protect_min_len` | MT/QE/TRA | `--entity-protect-min-len` | A | 4 | 0.4 | 0.9615 | 1.0 | 94.6 |  |
| 14 | `qe_backtranslate_max_items` | MT/QE/TRA | `--qe-backtranslate-max-items` | A | 80 | 0.4 | 0.9615 | 1.0 | 94.6 |  |
| 15 | `tts_fit_min_words` | TTS | `--tts-fit-min-words` | A | 4 | 0.4 | 0.9615 | 1.0 | 94.6 |  |
| 16 | `sentence_unit_max_chars` | MT/QE/TRA | `--sentence-unit-max-chars` | A | 90 | 0.4 | 0.96 | 1.0 | 94.6 |  |
| 17 | `subtitle_cps_safety_gap` | FFmpeg/字幕/混音/擦除 | `--subtitle-cps-safety-gap` | A | 0.25 | 0.4 | 0.96 | 1.0 | 94.6 |  |
| 18 | `min_sub_duration` | 工程/流程/输出 | `--min-sub-dur` | A | 2.16 | 0.4 | 0.6885 | 1.0 | 94.6 |  |
| 19 | `asr_llm_fix_mode` | ASR | `--asr-llm-fix-mode` | A | all | 0.2 | 0.746 | 1.0 | 94.4 |  |
| 20 | `display_merge_max_gap_s` | FFmpeg/字幕/混音/擦除 | `--display-merge-max-gap-s` | A | 0.3 | 0.2 | 0.746 | 1.0 | 94.4 |  |
| 21 | `mt_topic_auto_max_segs` | MT/QE/TRA | `--mt-topic-auto-max-segs` | A | 30 | 0.2 | 0.709 | 1.0 | 94.4 |  |
| 22 | `sentence_unit_max_segs` | MT/QE/TRA | `--sentence-unit-max-segs` | A | 4 | 0.2 | 0.709 | 1.0 | 94.4 |  |
| 23 | `tts_fit_wps` | TTS | `--tts-fit-wps` | A | 3 | 0.2 | 0.709 | 1.0 | 94.4 |  |
| 24 | `tts_speed_max` | TTS | `--tts-speed-max` | A | 1.2 | 0.2 | 0.709 | 1.0 | 94.4 |  |
| 25 | `entity_protect_min_freq` | MT/QE/TRA | `--entity-protect-min-freq` | A | 8 | 0.2 | 0.7015 | 1.0 | 94.4 |  |
| 26 | `qe_embed_threshold` | MT/QE/TRA | `--qe-embed-threshold` | A | 0.65 | 0.2 | 0.7015 | 1.0 | 94.4 |  |
| 27 | `tts_plan_min_cap` | TTS | `--tts-plan-min-cap` | A | 1.26 | 0.2 | 0.7015 | 1.0 | 94.4 |  |
| 28 | `display_max_chars_per_line` | FFmpeg/字幕/混音/擦除 | `--display-max-chars-per-line` | A | 48 | 0.2 | 0.7015 | 1.0 | 94.4 |  |
| 29 | `subtitle_max_chars_per_line` | FFmpeg/字幕/混音/擦除 | `--subtitle-max-chars-per-line` | A | 100 | 0.2 | 0.7015 | 1.0 | 94.4 |  |
| 30 | `asr_llm_fix_min_chars` | ASR | `--asr-llm-fix-min-chars` | A | 16 | 0.2 | 0.6885 | 1.0 | 94.4 |  |
| 31 | `tts_split_len` | TTS | `--tts-split-len` | A | 96 | 0.2 | 0.6885 | 1.0 | 94.4 |  |
| 32 | `bgm_gain_db` | FFmpeg/字幕/混音/擦除 | `--bgm-gain-db` | A | -6 | 0.2 | 0.6885 | 1.0 | 94.4 |  |
| 33 | `qe_max_items` | MT/QE/TRA | `--qe-max-items` | A | 300 | 0.0 | 0.5875 | 1.0 | 94.2 |  |
| 34 | `asr_merge_max_group_chars` | ASR | `--asr-merge-max-group-chars` | A | 180 | 0.0 | 0.369 | 1.0 | 94.2 |  |
| 35 | `vad_min_dur` | ASR | `--vad-min-dur` | A | 1.6 | 0.0 | 0.369 | 1.0 | 94.2 |  |
| 36 | `entity_protect_max_len` | MT/QE/TRA | `--entity-protect-max-len` | A | 8 | 0.0 | 0.369 | 1.0 | 94.2 |  |
| 37 | `vad_threshold` | ASR | `--vad-thold` | A | 0.7 | 0.0 | 0.363 | 1.0 | 94.2 |  |
| 38 | `sample_rate` | 工程/流程/输出 | `--sample-rate` | A | 19200 | 0.0 | 0.363 | 1.0 | 94.2 |  |
| 39 | `asr_merge_max_gap_s` | ASR | `--asr-merge-max-gap-s` | A | 0.3 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 40 | `asr_preprocess_lowpass` | ASR | `--asr-preprocess-lowpass` | A | 9500 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 41 | `entity_protect_max_items` | MT/QE/TRA | `--entity-protect-max-items` | A | 8 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 42 | `meaning_split_max_parts` | MT/QE/TRA | `--meaning-split-max-parts` | A | 4 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 43 | `meaning_split_min_chars` | MT/QE/TRA | `--meaning-split-min-chars` | A | 110 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 44 | `qe_embed_max_segs` | MT/QE/TRA | `--qe-embed-max-segs` | A | 3010 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 45 | `qe_mode` | MT/QE/TRA | `--qe-mode` | A | suspect | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 46 | `qe_threshold` | MT/QE/TRA | `--qe-threshold` | A | 4.2 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 47 | `tts_plan_safety_margin` | TTS | `--tts-plan-safety-margin` | A | 0.06 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 48 | `bgm_sample_rate` | FFmpeg/字幕/混音/擦除 | `--bgm-sample-rate` | A | 48000 | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 49 | `bgm_separate_method` | FFmpeg/字幕/混音/擦除 | `--bgm-separate-method` | A | demucs | 0.0 | 0.0 | 1.0 | 94.2 |  |
| 50 | `subtitle_max_cps` | FFmpeg/字幕/混音/擦除 | `--subtitle-max-cps` | A | 23 | 0.0 | 0.0 | 1.0 | 94.2 |  |

