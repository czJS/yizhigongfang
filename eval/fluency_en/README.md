# 英文自然度评测集（Fluency Eval）

目标：评估“英文字幕更母语、更口语自然（允许改写）”的增强效果，并输出可解释的**置信度**（通过 bootstrap 估计“增强优于基线”的概率）。

本评测集支持：
- **只跑公开测试集（public-only）**：不需要你提供金标准字幕。
- **加入自有金标准（golden）**：把你们的审校产物放入 `golden/`，可与公开集合并或单独跑。
- **单开关/多开关**：每个实验 run 都是一个配置组合；评测脚本会对比 baseline 与多个 run。

---

## 目录结构

- `public/`：公开测试集输入（推荐放 `pairs.jsonl`）
- `golden/`：你们自己的金标准字幕（SRT）
- `sets/`：抽样/分层抽样后的评测集合（`cases.jsonl`）
- `runs/`：各实验的模型输出（`preds.jsonl`）
- `reports/`：评测输出（`report.json`/`report.md`）

---

## 1) public-only：只跑公开测试集（不需要 golden）

### 准备公开数据（两种方式）

**方式 A（最稳，无需额外依赖）：放入 pairs.jsonl**

将公开数据整理成 JSONL，每行一个样本：

```json
{"id":"iwslt17-test-0001","zh":"……","ref_en":"……","source":"iwslt17","meta":{"split":"test"}}
```

把文件放到：`eval/fluency_en/public/pairs.jsonl`

**方式 B（可选）：使用 HuggingFace datasets 自动下载**

需要你自行安装 `datasets`（环境可联网时）：

```bash
python3 -m pip install datasets
```

然后用 build 脚本的 `--public-dataset ...` 参数构建。

### 构建 200 条评测集合（public-only）

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/public_200.jsonl \
  --n 200 \
  --public-only \
  --public-pairs eval/fluency_en/public/pairs.jsonl
```

---

## 2) 加入你们自己的 golden（金标准字幕）

把你们审校后的字幕放到：
- `eval/fluency_en/golden/chs.srt`（可选）
- `eval/fluency_en/golden/eng.srt`（必选：英文母语自然字幕）

构建评测集合（优先抽样黄金字幕；也可与 public 合并）：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/golden_200.jsonl \
  --n 200 \
  --golden-dir eval/fluency_en/golden
```

---

## 3) 生成实验输出（单开关/多开关）

每个 run 输出一个 `preds.jsonl`（与 cases 的 id 对齐）。

baseline：

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out eval/fluency_en/runs/baseline/preds.jsonl \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY"
```

单开关（示例：开启 TRA）：

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out eval/fluency_en/runs/tra/preds.jsonl \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" \
  --enable-tra
```

多开关（示例：TRA + 主题提示）：

```bash
python3 scripts/run_fluency_translate.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --out eval/fluency_en/runs/tra_topic/preds.jsonl \
  --endpoint "$LLM_ENDPOINT" --model "$LLM_MODEL" --api-key "$LLM_API_KEY" \
  --enable-tra --topic "Narration, conversational subtitle English"
```

---

## 4) 评测与置信度输出

对比 baseline 与多个 run，输出 report：

```bash
python3 scripts/eval_fluency_suite.py \
  --cases eval/fluency_en/sets/public_200.jsonl \
  --baseline eval/fluency_en/runs/baseline/preds.jsonl \
  --runs tra=eval/fluency_en/runs/tra/preds.jsonl tra_topic=eval/fluency_en/runs/tra_topic/preds.jsonl \
  --out eval/fluency_en/reports/report_public_200.json
```

report 里会包含：
- BLEU / chrF（轻量实现，无额外依赖）
- 长度、标点、重复等统计
- bootstrap 得到的 **P(Δmetric > 0)**（作为“置信度”）


