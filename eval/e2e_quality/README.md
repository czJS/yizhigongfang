# 质量模式全链路 E2E 评测（20 段起步）

目标：对 **质量模式**（ASR → 翻译/提质 → 字幕后处理 → TTS → 混音/封装）中每个增强开关与关键组合，给出：
- **质量效果**：端到端综合分（100 分制）+ 门禁通过率 + 关键问题数
- **置信度**：bootstrap 下 `p_improve = P(Δscore > 0)`
- **成本**：耗时、失败率（缺产物/异常）

本评测 **不依赖参考译文**（因为 E2E 重点是“交付体验”），而是复用现有 `quality_report.json` 的门禁与检查项，构造一个稳定可回归的端到端主指标 `e2e_score_100`。

---

## 1) 数据集（20 段）

你需要准备 20 个短视频片段（建议每段 30–120s，覆盖噪声、多人、快语速、专名数字等）。

数据集清单文件：`eval/e2e_quality/segments_20.jsonl`（你按示例填写）

每行示例：

```json
{"id":"seg-0001","video":"/abs/path/to/clip1.mp4","meta":{"tag":["noisy","fast"],"note":"室外嘈杂"}}
```

---

## 2) 实验定义（单开关 + 组合）

实验文件：`eval/e2e_quality/experiments.yaml`

- `baseline`：基线（通常就是质量模式默认配置）
- `experiments`：每个实验只写“相对 baseline 的 overrides”
- 支持 `reuse`：用于公平对比（例如只评估翻译开关时固定 ASR 输出）

示例见：`eval/e2e_quality/experiments.example.yaml`

---

## 3) 一键跑（生成每段产物 + quality_report）

```bash
docker compose -p yzh up -d ollama backend

docker compose -p yzh exec -T backend bash -lc '
python /app/scripts/run_quality_e2e.py \
  --segments /app/eval/e2e_quality/segments_20.jsonl \
  --experiments /app/eval/e2e_quality/experiments.yaml \
  --base-config /app/config/quality.yaml \
  --out-root /app/outputs/eval/e2e_quality \
  --jobs 1
'
```

产物结构（示例）：

```
outputs/eval/e2e_quality/
  baseline/seg-0001/... (quality_pipeline outputs)
  tra_on/seg-0001/...
  ...
```

---

## 4) 汇总报告（端到端主指标 + 置信度）

```bash
docker compose -p yzh exec -T backend bash -lc '
python /app/scripts/eval_quality_e2e_suite.py \
  --segments /app/eval/e2e_quality/segments_20.jsonl \
  --baseline /app/outputs/eval/e2e_quality/baseline \
  --runs tra_on=/app/outputs/eval/e2e_quality/tra_on \
        tts_fit=/app/outputs/eval/e2e_quality/tts_fit \
  --bootstrap-iters 2000 \
  --out /app/reports/e2e_quality/report_e2e20.json
'
```

---

## 5) 主指标选择（推荐实践）

我们采用 **“门禁优先 + 综合分”** 的业内常用做法：
- **硬失败（missing artifacts / 视频截断）**：直接重罚，防止“分数好但不能交付”
- **软门禁（CPS/长行/重叠/tts_risk 等）**：按命中数线性扣分（稳定、可回归）
- 输出同时给 **passed_rate**（通过率）与 **e2e_score_100**（综合体验分）

这样可以稳定回答：某个开关/组合是否值得默认开启（质量提升的概率 + 成本）。





