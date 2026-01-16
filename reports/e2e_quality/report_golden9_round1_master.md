# Round 1 报告（P0 单开关）- 汇总版（带中文注释）

本文件是“产品可读版总报告”，原始评测产物见：
- JSON：`reports/e2e_quality/report_golden9_round1_p0.json`
- Markdown：`reports/e2e_quality/report_golden9_round1_p0.md`

---

## A. 本轮结论速览（P0）

### A0. 指标/字段中文注释（Glossary）

- **passed_rate（交付通过率）**：门禁通过的段比例（越高越好）
- **e2e_mean（E2E 交付体验均分，0–100）**：综合交付体验分（缺产物/截断/字幕门禁/TTS风险等）
- **ASR(100)（识别分，0–100）**：\(100 \times (1 - CER)\)，越高越好
- **MT(final100)（翻译最终分，0–100）**：翻译绝对质量分（baseline=70 锚点）
- **Δ（Delta，相对 baseline 的变化）**：例如 Δe2e=+2.47 表示比基线高 2.47 分
- **p_improve（提升置信度，0–1）**：bootstrap 估计“比基线更好”的概率，越接近 1 越可信

### A1. 开关/Run 中文注释（本轮涉及）

- `baseline`：基线（质量模式默认配置）
- `sentence_unit_on`：句子单元合并（翻译前保守合并短句再拆回）
- `tra_on`：TRA 多步翻译（忠实→反思→润色；传统版）
- `qe_on`：QE 质量评审+选择性修复（本地 LLM）
- `subtitle_postprocess_on`：字幕后处理（软换行 + CPS 修正）
- `tts_script_on`：字幕稿/朗读稿分离（为 TTS 生成 `eng_tts.srt`）
- `tts_fit_on`：TTS 超时裁剪（把朗读稿裁剪到时长预算内）
- `tts_plan_on`：TTS 每段语速规划/停顿审计

### A2. 速览结论

- baseline：passed_rate（交付通过率）=1.0000，e2e_mean（交付体验均分）=83.00，ASR(100)（识别分）=70.10，MT(final100)（翻译分）=70.00
- e2e_mean（交付体验）最优：**subtitle_postprocess_on（字幕后处理）**，85.47
- 风险项：**tts_script_on（朗读稿分离）** e2e_mean=71.35（相对 baseline Δe2e=-11.65）

---

## B. P0 总表（可直接用于评审，带中文注释）

| run（开关/实验） | passed_rate（交付通过率） | e2e_mean（交付体验均分） | Δe2e（相对基线） | p_improve(e2e)（置信度） | ASR(100)（识别分） | ΔASR | p_improve(ASR) | MT(final100)（翻译分） | ΔMT | p_improve(MT) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1.0000 | 83.00 | - | - | 70.10 | - | - | 70.00 | - | - |
| sentence_unit_on | 1.0000 | 82.93 | -0.07 | 0.3985 | 70.10 | 0.00 | 0.0000 | 70.52 | 0.52 | 1.0000 |
| tra_on | 1.0000 | 83.60 | 0.60 | 0.9910 | 70.10 | 0.00 | 0.0000 | 69.31 | -0.69 | 0.0000 |
| qe_on | 1.0000 | 82.93 | -0.07 | 0.0000 | 70.10 | 0.00 | 0.0000 | 70.08 | 0.08 | 0.9760 |
| subtitle_postprocess_on | 1.0000 | 85.47 | 2.47 | 1.0000 | 69.75 | -0.35 | 0.0000 | 69.93 | -0.07 | 0.3050 |
| tts_script_on | 1.0000 | 71.35 | -11.65 | 0.0000 | 70.10 | 0.00 | 0.0000 | 70.12 | 0.12 | 0.9900 |
| tts_fit_on | 1.0000 | 83.00 | 0.00 | 0.4505 | 70.10 | 0.00 | 0.0000 | 70.04 | 0.04 | 0.6650 |
| tts_plan_on | 1.0000 | 83.13 | 0.13 | 0.8340 | 70.10 | 0.00 | 0.0000 | 70.07 | 0.07 | 0.8335 |

---

## C. P1（中优先级）- 预留

- 状态：未运行
- 计划：在确认 P0 结论后，选取 P1 开关（如 denoise/vad/entity_protect/meaning_split/qe_embed/tts_strict_clean 等）补齐评测，并追加到本报告。

---

## D. P2（低优先级）- 预留

- 状态：未运行
- 计划：展示/混音/擦字幕等开关建议单独分桶评测（对主交付体验影响更依赖内容）。

---

## E. 组合开关（Top combos）- 预留

- 状态：未生成
- 计划：基于单开关收益与失败类型，产出 5 组组合（例如 “字幕后处理 + tts_fit/tts_plan” 等），并补跑组合评测。

---

## G. 建议默认策略分级（默认开启 / 高级设置 / 隐藏）

> 说明：本分级**严格基于本轮 golden9 的 P0 结果**（见上表），优先级为 E2E（交付体验）> 稳定交付（passed_rate）> 绝对质量（ASR/MT）。  
> 其中 `p_improve(e2e)` 可理解为“相对 baseline 更好的概率”，越接近 1 越可信。  
> 你提到“九个开关”，但本轮报告里**独立评测并可决策的增强开关是 7 个**（不含 baseline）。

### 1) 默认开启（Recommended ON）

- **`subtitle_postprocess_on（字幕后处理：软换行 + CPS 修正）`**
  - **证据**：Δe2e=+2.47，`p_improve(e2e)=1.0`，`passed_rate=1.0`
  - **产品含义**：交付体验显著提升，且稳定交付不受影响
  - **备注**：对 MT 分数影响不大，但显著改善“可读性门禁/工程体验”

### 2) 高级设置（Advanced / On-demand）

- **`tra_on（TRA 多步翻译：忠实→反思→润色；传统版）`**
  - **证据**：Δe2e=+0.60，`p_improve(e2e)=0.991`（强）
  - **代价/风险**：本轮 `MT(final100)` 反而下降（Δ=-0.69，`p_improve(MT)=0.0`）且耗时显著更高
  - **建议**：作为高级开关，适合“更自然口语”诉求强、且能接受更慢的用户/场景

- **`tts_plan_on（TTS 每段语速规划/停顿审计）`**
  - **证据**：Δe2e=+0.13，`p_improve(e2e)=0.834`（有提升但未达到“强默认”阈值）
  - **建议**：作为高级开关，适合“配音自然度/节奏”敏感场景

- **`sentence_unit_on（句子单元合并）`**
  - **证据**：E2E 无提升（Δe2e=-0.07，`p_improve(e2e)=0.3985`），但 MT 分数提升（ΔMT=+0.52，`p_improve(MT)=1.0`）
  - **建议**：不建议默认开；作为高级开关用于“翻译一致性/语义连贯性”更重要的场景

- **`qe_on（QE 评审+选择性修复）`**
  - **证据**：E2E 无提升（Δe2e=-0.07，`p_improve(e2e)=0.0`），但 MT 分数小幅提升（ΔMT=+0.08，`p_improve(MT)=0.976`）
  - **建议**：不建议默认开；作为高级开关用于“翻译错误容忍度低/专名数字多/术语密集”的场景

- **`tts_fit_on（TTS 超时裁剪）`**
  - **证据**：E2E 基本不变（Δe2e=0.00，`p_improve(e2e)=0.4505`）
  - **产品解释**：它更像“风险兜底”（减少超时/截断），对本轮 9 条样本未体现收益
  - **建议**：暂不默认开，作为高级开关保留；后续可在更长视频/更密集字幕样本上复测

### 3) 隐藏（Hidden / 不推荐对外暴露）

- **`tts_script_on（字幕稿/朗读稿分离）`**
  - **证据**：Δe2e=-11.65，`p_improve(e2e)=0.0`（明显负收益）
  - **建议**：默认关闭并隐藏；若要继续探索，建议作为内部实验单独优化（例如改朗读稿生成策略/约束）

### 4) 备注：未在本轮“独立对比”的默认项（Baseline 内生效）

以下能力本轮没有作为单开关对比（因为在 `config/quality.yaml` baseline 中已默认开启或属于基线流程），因此**不在上述 7 个可决策开关中**：

- `asr_normalize_enable（ASR 文本净化）`：已默认开（低风险）
- `asr_preprocess_enable（ASR 预处理）`：已默认开（有成本/有收益，需单独对比才可决策“是否默认开”）
- `asr_merge_short_enable（合并极短段）`：已默认开（需单独对比确认收益/风险）
- `asr_llm_fix_enable（ASR LLM 保守纠错）`：已默认开（依赖 LLM 稳定，需单独对比确认收益/风险）

如你希望把它们也纳入“默认策略决策”，建议下一轮新增 **ASR 单开关对比组**（只动 ASR，其他冻结）来评估。

---

## F. 复现方式（给工程同学）

```bash
docker compose -p yzh exec -T backend bash -lc '
python /app/scripts/eval_quality_e2e_golden_suite.py \
  --segments /app/eval/e2e_quality/segments_golden_9.docker.jsonl \
  --baseline /app/outputs/eval/e2e_quality_golden9_p0/baseline \
  --runs sentence_unit_on=/app/outputs/eval/e2e_quality_golden9_p0/sentence_unit_on \
        tra_on=/app/outputs/eval/e2e_quality_golden9_p0/tra_on \
        qe_on=/app/outputs/eval/e2e_quality_golden9_p0/qe_on \
        subtitle_postprocess_on=/app/outputs/eval/e2e_quality_golden9_p0/subtitle_postprocess_on \
        tts_script_on=/app/outputs/eval/e2e_quality_golden9_p0/tts_script_on \
        tts_fit_on=/app/outputs/eval/e2e_quality_golden9_p0/tts_fit_on \
        tts_plan_on=/app/outputs/eval/e2e_quality_golden9_p0/tts_plan_on \
  --bootstrap-iters 2000 \
  --out-json /app/reports/e2e_quality/report_golden9_round1_p0.json \
  --out-md /app/reports/e2e_quality/report_golden9_round1_p0.md
'
```
