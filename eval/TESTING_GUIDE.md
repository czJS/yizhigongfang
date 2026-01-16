# 测试使用文档（公开集 + 黄金标准；基线 + 增强开关）

本仓库目前已经具备一套可复用的 **英文字幕自然度（允许改写）**评测流水线，并补充了 **中文 ASR（识别）** 的最小 CER 评测脚本，方便你用公开测试集做回归与优化。

> 产物目录约定：  
> - **runs（模型输出）**：`outputs/eval/**/runs/*/preds.jsonl`  
> - **reports（报告）**：`reports/**`（已在 `docker-compose.yml` 挂载到容器 `/app/reports`）

---

## A. 英文自然度评测（Fluency Eval：zh -> en）

### A1. 数据格式（cases.jsonl）
一行一个样本（200 条评测集就是 200 行）：

```json
{"id":"iwslt2017-test-000001","zh":"中文句子","ref_en":"英文参考","source":"iwslt2017","meta":{"split":"test"}}
```

- `id`：唯一 ID（用于对齐 baseline 与各实验 run）
- `zh`：输入中文（来自 ASR 或人工字幕）
- `ref_en`：英文参考（公开集参考译文或你们的英文金标）

### A2. 构建评测集（公开集 / 黄金标准）
现有构建脚本：`scripts/build_fluency_eval_set.py`  
它会做 **分层抽样**（按英文长度区间）并输出 `cases.jsonl`。

#### 1) public-only（只用公开数据）
把公开对照对齐数据放到：
- `eval/fluency_en/public/pairs.jsonl`

格式同上（至少包含 `id/zh/ref_en`）。

构建 200 条：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/public_200.jsonl \
  --n 200 \
  --public-only \
  --public-pairs eval/fluency_en/public/pairs.jsonl
```

#### 2) golden（你提供的黄金标准）
把你们审校后的金标字幕放到：
- `eval/fluency_en/golden/eng.srt`（必选，英文母语自然字幕）
- `eval/fluency_en/golden/chs.srt`（可选）

构建 200 条：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/golden_200.jsonl \
  --n 200 \
  --golden-dir eval/fluency_en/golden
```

#### 2.5) golden_synth（用公开集自动生成“合成金标”，更贴近口语字幕）
当你暂时没有人工审校金标，但又想评估“允许改写、更母语”的增强效果时，可以先生成 **合成金标**：

- 输入：公开集 `cases.jsonl`（例如 `public_200.jsonl`）
- 输出：
  - `eval/fluency_en/golden_synth/eng.srt`（合成英文金标）
  - `eval/fluency_en/golden_synth/chs.srt`（对应中文，便于抽查）
  - `eval/fluency_en/golden_synth/golden.jsonl`（对齐用，包含 gold_en）

在 Docker 容器里运行（推荐）：

```bash
docker compose -p yzh exec -T backend bash -lc '
export LLM_ENDPOINT=http://ollama:11434/v1
export LLM_MODEL=qwen2.5:7b

python /app/scripts/build_fluency_synth_golden.py \
  --cases /app/outputs/eval/fluency_en/sets/public_200.jsonl \
  --n 200 \
  --out-jsonl /app/eval/fluency_en/golden_synth/golden.jsonl \
  --out-eng-srt /app/eval/fluency_en/golden_synth/eng.srt \
  --out-chs-srt /app/eval/fluency_en/golden_synth/chs.srt \
  --resume
'
```

然后用它构建新的评测集合（把 `ref_en` 变成你想要的“更母语字幕”参考）：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/golden_synth_200.jsonl \
  --n 200 \
  --golden-dir eval/fluency_en/golden_synth
```

> 重要提醒：`golden_synth` 是 “公开数据衍生物 + 模型生成”，适合内部回归，不建议对外发布。

#### 3) 混合（公开 + 黄金）
例如黄金占 70%：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/mix_200.jsonl \
  --n 200 \
  --golden-dir eval/fluency_en/golden \
  --public-pairs eval/fluency_en/public/pairs.jsonl \
  --mix-golden-ratio 0.7
```

### A3. 运行基线与增强（生成 preds.jsonl）
现有运行脚本：`scripts/run_fluency_translate.py`

关键参数：
- `--cases`：评测集合（cases.jsonl）
- `--out`：输出 `preds.jsonl`
- `--resume`：**断点续跑**（跳过已完成 `id`，追加写入，不覆盖）
- `--enable-selfcheck`：自检润色增强
- `--enable-tra`：TRA（结构化翻译+改写）增强
- `--topic`：风格/语境提示（作为“组合实验”的第二维）

#### 1) baseline（不加增强）

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out outputs/eval/fluency_en/runs/baseline/preds.jsonl \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY"
```

#### 2) 单开关（示例：TRA）

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out outputs/eval/fluency_en/runs/tra/preds.jsonl \
  --resume \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" \
  --enable-tra
```

#### 3) 组合（示例：TRA + topic）

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out outputs/eval/fluency_en/runs/tra_topic/preds.jsonl \
  --resume \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" \
  --enable-tra --topic "Narration, conversational subtitle English"
```

### A4. 评测与报告（含 100 分制）
现有评测脚本：`scripts/eval_fluency_suite.py`

它会输出（每个 run）：
- BLEU / chrF（0..1）
- `quality_score`（0..1，回归友好）
- `ref_free_score`（0..1，参考无关自然度启发式）
- `final_score_100`（0..100，**baseline=70** 业务可读）
- bootstrap：`p_improve`（置信度）

示例（同时评测多个 run）：

```bash
python3 scripts/eval_fluency_suite.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --baseline outputs/eval/fluency_en/runs/baseline/preds.jsonl \
  --runs tra=outputs/eval/fluency_en/runs/tra/preds.jsonl \
        selfcheck=outputs/eval/fluency_en/runs/selfcheck/preds.jsonl \
        tra_topic=outputs/eval/fluency_en/runs/tra_topic/preds.jsonl \
  --bootstrap-iters 2000 \
  --out reports/fluency_en/report_public_200_multi.json
```

---

## B. 中文 ASR 识别评测（公开测试集）

### B1. 有哪些可用的中文 ASR 公开测试集？
你要评测“中文识别能力”，需要 **音频 + 逐句转写** 的公开数据集。推荐优先级（按“常用/稳定/易用”）：

- **AISHELL-1（推荐首选）**：包含 train/dev/test，公开使用非常普遍；适合做 CER 回归。
- **Common Voice（zh / zh-CN / zh-HK 等）**：覆盖口音较多，噪声更真实；适合看鲁棒性。
- **THCHS-30**：较小但经典；适合快速迭代。
- **Primewords Chinese**：规模较大；可用于扩展训练/评测（注意许可条款）。
- **WenetSpeech**：超大规模，更偏训练；也可抽取子集做评测（工程量较大）。

> 注意：不同数据集许可不同（研究/商用限制）。如果你要商用对外展示，请务必复核数据集 License/ToS。

### B2. 最小评测脚本（CER）
新增脚本：`scripts/eval_asr_cer.py`  
输入一个 JSONL（每行一个样本：`id/ref_zh/pred_zh`），输出总体 CER 与 Top 错误样例。

#### 输入格式（asr_pairs.jsonl）

```json
{"id":"utt-0001","ref_zh":"参考转写","pred_zh":"ASR输出","meta":{"source":"aishell1-test"}}
```

#### 运行

```bash
python3 scripts/eval_asr_cer.py \
  --in-jsonl eval/asr_zh/sets/aishell1_test_pairs.jsonl \
  --out reports/asr_zh/report_aishell1_test.json
```

### B3. 如何把公开 ASR 数据转成 asr_pairs.jsonl？
建议流程：
- 先用公开数据集的官方 test 切分
- 用你当前 ASR（WhisperX/whisper.cpp）对每条音频跑出 `pred_zh`
- 对齐得到 `ref_zh`（原始标注）
- 导出 JSONL 后跑 `scripts/eval_asr_cer.py`

如果你希望我把“某一个指定数据集（例如 AISHELL-1）→ 自动导出 JSONL”的脚本也加上，请告诉我你希望用 **HuggingFace datasets** 还是官方压缩包方式。

---

## C. Docker 中运行建议（长任务）

### C1. 启动服务

```bash
docker compose -p yzh up -d ollama backend
```

### C2. 在容器内跑评测（推荐）
因为容器内已经挂载了 `outputs/` 与 `reports/`，且 LLM endpoint 用服务名更稳定：

```bash
docker compose -p yzh exec -T backend bash -lc '
export LLM_ENDPOINT=http://ollama:11434/v1
export LLM_MODEL=qwen2.5:7b
python /app/scripts/run_fluency_translate.py -h
'
```

### C3. 断点续跑
长任务中断（重启 Docker、断电等）后：
- 直接重复同一条命令并加 `--resume`
- 只要 `--out` 指向同一个 `preds.jsonl`，脚本会跳过已完成 `id`

---

## D. 质量模式全链路（E2E 20 段）评测（ASR→MT→TTS→ffmpeg）

适用场景：你想给“每个增强开关/组合”一个**端到端交付体验**结论（含字幕门禁、TTS 风险、封装截断等），并输出置信度。

### D0. 我只有 8～9 条（每条 2～3 分钟）的视频，并且我能提供「中/英文都带时间戳」的金标，怎么做？

可以，而且更推荐：我们会把长视频**自动切成约 20 段 40～95 秒 clip**（跑起来更快、更稳定），并且同步裁切出每段 clip 对应的：
- `golden chs.srt`（中文金标，带时间戳）
- `golden eng.srt`（英文金标，带时间戳）

这样你可以同时做两类评测：
- **E2E 交付体验（不依赖参考）**：基于 `quality_report.json` → `e2e_score_100` / `passed_rate`
- **参考对齐的绝对质量（依赖金标）**：
  - ASR：用金标 `chs.srt` 做 CER/WER（可选）
  - 翻译：用金标 `eng.srt` 做 chrF/BLEU + 100 分制（推荐）

#### D0.1 你需要准备的目录结构（推荐放到仓库内，方便容器访问）

把每条视频放一个子目录（子目录名随意，但建议用稳定 id）：
- `eval/e2e_quality/golden_videos/<video_id>/video.mp4`
- `eval/e2e_quality/golden_videos/<video_id>/chs.srt`
- `eval/e2e_quality/golden_videos/<video_id>/eng.srt`

要求：
- `chs.srt` 与 `eng.srt` **必须与 `video.mp4` 同一时间轴**（同一偏移、同一开头）
- 时间戳可不必逐帧精确，但不要整体错位

#### D0.2 一键生成「E2E-20 数据集」（clips + 分段金标）

在宿主机运行（会调用本机 `ffmpeg/ffprobe`）：

```bash
python3 scripts/build_e2e20_from_videos.py \
  --in-dir eval/e2e_quality/golden_videos \
  --out-dir eval/e2e_quality/e2e20_from_videos \
  --n 20
```

产物：
- `eval/e2e_quality/e2e20_from_videos/clips/seg-0001.mp4 ...`
- `eval/e2e_quality/e2e20_from_videos/golden_segments/seg-0001/{chs.srt,eng.srt} ...`
- `eval/e2e_quality/e2e20_from_videos/segments_20.docker.jsonl`（给容器内 E2E 用）
- `eval/e2e_quality/e2e20_from_videos/fluency_cases_20.jsonl`（给翻译绝对分评测用）

> 备注：脚本会优先用英文字幕时间戳做切分；如果英文字幕很稀疏，会退化为按时长切分。

入口：
- 说明：`eval/e2e_quality/README.md`
- 跑实验：`scripts/run_quality_e2e.py`
- 出报告：`scripts/eval_quality_e2e_suite.py`

### D1. 叠加「ASR/翻译绝对质量分」（强烈推荐：更可解释）

当你提供了 **中文金标（chs.srt，带时间戳）** 和 **英文金标（eng.srt，带时间戳）** 时，建议在 E2E 汇总报告之外，再叠加：
- ASR：**CER**（越低越好）+ `asr_score_100=100*(1-CER)`（越高越好）
- 翻译：chrF/BLEU/`final_score_100`（更业务可读，baseline=70 锚点）

这样你就能同时回答：
- 这个开关是否更“可交付”（E2E passed_rate / e2e_score_100）
- 这个开关到底是“识别更准”还是“翻译更准”（ASR/翻译绝对分）

#### D1.1 segments.jsonl 如何提供金标路径？

在 segments 的 `meta` 中增加两项即可（容器内路径）：
- `gold_chs_srt`: `/app/.../xxx.srt`
- `gold_eng_srt`: `/app/.../yyy.srt`（也支持 `*.srt_en` 这种命名，只要内容是标准 SRT 时间戳）

你现在可以直接用：
- `eval/e2e_quality/segments_golden_9.docker.jsonl`（9 条金标视频版）

#### D1.2 生成 “E2E + 金标绝对分” 报告（JSON + Markdown）

```bash
docker compose -p yzh exec -T backend bash -lc '
python /app/scripts/eval_quality_e2e_golden_suite.py \
  --segments /app/eval/e2e_quality/segments_golden_9.docker.jsonl \
  --baseline /app/outputs/eval/e2e_quality_golden9/baseline \
  --runs tra_on=/app/outputs/eval/e2e_quality_golden9/tra_on \
        qe_on=/app/outputs/eval/e2e_quality_golden9/qe_on \
  --bootstrap-iters 2000 \
  --out-json /app/reports/e2e_quality/report_golden9_e2e_asr_mt.json
'
```

产物：
- JSON：`reports/e2e_quality/report_golden9_e2e_asr_mt.json`
- Markdown（可读性更好）：同名 `.md`（脚本自动生成）

> 对齐方式：按时间戳重叠选取最匹配的预测字幕块，再计算指标；避免“按行号对齐”带来的错位。

---

### D2. 自动结论：如何把报告变成“默认开启/默认关闭/仅高级”的决策（傻瓜式）

你跑完 `report_*.md` 后，不需要逐段看日志，只要按下面的“硬门槛 → 软收益 → 成本”三步走，就能给每个增强开关/组合一个清晰结论。

#### D2.1 先看硬门槛（不满足就直接判负：默认关闭/删除候选）

满足以下任意一条，**不建议默认开启**（优先“默认关闭”，若长期稳定负面可考虑“删除”）：

- **交付失败变多**：`passed_rate` 低于 baseline 超过 0.02（掉 2 个百分点以上）
- **交付体验明显变差**：`e2e_score_100_mean` 比 baseline 低 1 分以上（Δ<-1.0）
- **出现硬失败特征**（看 per_segment 里常见字段）：缺产物、视频截断、严重时间轴异常等（即使平均分看起来还行，也不默认开）

> 解释：质量模式的第一原则是“能稳定交付”。能交付比“偶尔更好看”重要。

#### D2.2 再看收益（满足以下条件才进入“默认开启候选”）

如果硬门槛都过了，再看收益与置信度（bootstrap）：

- **E2E 主收益**（推荐作为默认开关的主要依据）
  - Δ`e2e_score_100_mean` ≥ +1.0
  - 且 `p_improve(e2e)` ≥ 0.90

- **ASR/翻译解释性收益**（用于判断“为什么变好/变差”，也用于阶段性优化）
  - ASR：Δ`asr_score_100_mean` ≥ +0.5 且 `p_improve(ASR)` ≥ 0.85
  - 翻译：Δ`mt_final_score_100_mean` ≥ +0.5 且 `p_improve(MT)` ≥ 0.85

> 解释：E2E 反映“交付体验”；ASR/翻译绝对分反映“到底是哪一段变好了”。默认开关以 E2E 为主，ASR/翻译为辅。

#### D2.3 最后看成本（决定“默认开启 vs 仅高级”）

即使收益满足，也要看成本，否则用户体验会被“慢/贵/不稳定”拖垮：

- **建议“仅高级/按需开启”** 的典型情况：
  - 总耗时显著增加（例如平均每段慢 > 30% 或明显拉长队列）
  - 依赖 LLM 多轮（TRA/QE/回译）导致波动大，偶发超时/失败
  - 对特定内容有用（如术语密集、专名数字多、噪声大），但通用场景收益不稳定

- **建议“默认开启”** 的典型情况：
  - E2E 稳定提升（`p_improve(e2e)` 高），且耗时增加可接受
  - 没引入新失败类型（passed_rate 不掉，硬失败不增）

#### D2.4 最终分级（你可以直接照抄到产品默认策略）

按下列规则给每个开关/组合贴一个标签：

- **默认开启（Recommended ON）**
  - 硬门槛通过
  - ΔE2E ≥ +1.0 且 `p_improve(e2e) ≥ 0.90`
  - 成本可接受（耗时增长不明显 / 故障不增）

- **仅高级（Advanced / On-demand）**
  - 硬门槛通过
  - 在 ASR 或翻译上有明确提升（Δ≥+0.5 且 p_improve≥0.85），但 E2E 提升不稳定或成本偏高

- **默认关闭（Default OFF）**
  - 硬门槛不通过（掉通过率/掉 E2E/引入硬失败）
  - 或 `p_improve` 接近随机（例如 0.4~0.6）且收益很小

- **删除候选（Deprecate）**
  - 多轮实验长期显示负收益（ΔE2E<0 且 p_improve 很低，如 <0.2）
  - 且功能与其它开关明显重叠、维护复杂（优先“降复杂度”）

#### D2.5 报告读法：你只需要看 Markdown 的一张总表

`report_*.md` 里“汇总表”那一行一行就是你的决策输入：
- **先看** `passed_rate`、`e2e_mean`、`p_improve(e2e)`
- **再看** `ASR(100)`、`MT(final100)` 和它们各自的 `p_improve`

你如果希望我直接基于某次跑出来的 `report_*.md` 给你输出一份“最终默认策略建议表”（每个开关/组合：默认开/默认关/仅高级 + 理由），把报告文件贴我即可。


