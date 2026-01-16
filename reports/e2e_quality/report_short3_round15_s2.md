# Round1.5 S2（short3 入围项复核）

- baseline: passed_rate=1.0 e2e_mean=94.4

| rank | key | stage | cli | point | value | delta_e2e | p_improve | passed_rate | e2e_mean | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `display_merge_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-merge-max-chars` | A | 120 | 0.2 | 0.716 | 1.0 | 94.6 |  |
| 2 | `asr_llm_fix_max_items` | ASR | `--asr-llm-fix-max-items` | P | 30 | 0.2 | 0.7015 | 1.0 | 94.6 |  |
| 3 | `asr_llm_fix_max_items` | ASR | `--asr-llm-fix-max-items` | A | 90 | 0.2 | 0.7015 | 1.0 | 94.6 |  |
| 4 | `display_max_lines` | FFmpeg/字幕/混音/擦除 | `--display-max-lines` | A | 4 | 0.2 | 0.7015 | 1.0 | 94.6 |  |
| 5 | `entity_protect_min_len` | MT/QE/TRA | `--entity-protect-min-len` | A | 4 | 0.2 | 0.7015 | 1.0 | 94.6 |  |
| 6 | `asr_merge_min_dur_s` | ASR | `--asr-merge-min-dur-s` | A | 1 | 0.2 | 0.6885 | 1.0 | 94.6 |  |
| 7 | `qe_backtranslate_max_items` | MT/QE/TRA | `--qe-backtranslate-max-items` | A | 80 | 0.2 | 0.6885 | 1.0 | 94.6 |  |
| 8 | `sentence_unit_min_chars` | MT/QE/TRA | `--sentence-unit-min-chars` | P | 1 | 0.0 | 0.716 | 1.0 | 94.4 |  |
| 9 | `display_merge_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-merge-max-chars` | P | 40 | 0.0 | 0.576 | 1.0 | 94.4 |  |
| 10 | `display_split_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-split-max-chars` | P | 43 | 0.0 | 0.576 | 1.0 | 94.4 |  |
| 11 | `sentence_unit_min_chars` | MT/QE/TRA | `--sentence-unit-min-chars` | A | 32 | 0.0 | 0.57 | 1.0 | 94.4 |  |
| 12 | `qe_backtranslate_overlap_threshold` | MT/QE/TRA | `--qe-backtranslate-overlap-threshold` | A | 0.45 | 0.0 | 0.57 | 1.0 | 94.4 |  |
| 13 | `display_max_lines` | FFmpeg/字幕/混音/擦除 | `--display-max-lines` | P | 2 | 0.0 | 0.57 | 1.0 | 94.4 |  |
| 14 | `tts_fit_min_words` | TTS | `--tts-fit-min-words` | A | 4 | 0.0 | 0.57 | 1.0 | 94.4 |  |
| 15 | `max_sentence_len` | MT/QE/TRA | `--max-sentence-len` | P | 40 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 16 | `max_sentence_len` | MT/QE/TRA | `--max-sentence-len` | A | 60 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 17 | `sentence_unit_max_gap_s` | MT/QE/TRA | `--sentence-unit-max-gap-s` | A | 0.7 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 18 | `asr_preprocess_highpass` | ASR | `--asr-preprocess-highpass` | A | 100 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 19 | `asr_merge_min_chars` | ASR | `--asr-merge-min-chars` | A | 8 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 20 | `qe_backtranslate_max_items` | MT/QE/TRA | `--qe-backtranslate-max-items` | P | 40 | 0.0 | 0.0 | 1.0 | 94.4 |  |
| 21 | `tts_gain_db` | TTS | `--tts-gain-db` | A | 3 | -0.2 | 0.363 | 1.0 | 94.2 |  |
| 22 | `display_split_max_chars` | FFmpeg/字幕/混音/擦除 | `--display-split-max-chars` | A | 129 | -0.2 | 0.363 | 1.0 | 94.2 |  |
| 23 | `asr_merge_min_dur_s` | ASR | `--asr-merge-min-dur-s` | P | 0.6 | -0.2 | 0.255 | 1.0 | 94.2 |  |
| 24 | `tts_gain_db` | TTS | `--tts-gain-db` | P | -3 | -0.2 | 0.0 | 1.0 | 94.2 |  |
| 25 | `qe_backtranslate_overlap_threshold` | MT/QE/TRA | `--qe-backtranslate-overlap-threshold` | P | 0.25 | -0.2 | 0.0 | 1.0 | 94.2 |  |
| 26 | `asr_merge_min_chars` | ASR | `--asr-merge-min-chars` | P | 4 | -0.2 | 0.0 | 1.0 | 94.2 |  |
| 27 | `entity_protect_min_len` | MT/QE/TRA | `--entity-protect-min-len` | P | 2 | -0.2 | 0.0 | 1.0 | 94.2 |  |
| 28 | `tts_fit_min_words` | TTS | `--tts-fit-min-words` | P | 2 | -0.2 | 0.0 | 1.0 | 94.2 |  |
| 29 | `sentence_unit_max_gap_s` | MT/QE/TRA | `--sentence-unit-max-gap-s` | P | 0.5 | -0.4 | 0.0 | 1.0 | 94.0 |  |
| 30 | `asr_preprocess_highpass` | ASR | `--asr-preprocess-highpass` | P | 60 | -0.4 | 0.0 | 1.0 | 94.0 |  |

