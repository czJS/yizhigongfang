## 功能置信度记录（英文自然度 / public-only）

目的：记录“增强开关”在**固定公开集**上的量化结果，便于后续优化后做回归对比。

说明：
- 数据集：`outputs/eval/fluency_en/public/pairs.jsonl`（IWSLT2017 zh-en test）
- 评测集合：`outputs/eval/fluency_en/sets/public_200.jsonl`（抽样 200 条）
- 模型：Ollama `qwen2.5:7b`
- 评测脚本：`scripts/eval_fluency_suite.py`
- 置信度：`p_improve`（bootstrap，主指标默认 chrF），表示“增强优于 baseline 的概率”
- **quality_score**：一个用于长期回归的可复现标量（见 `scripts/eval_fluency_suite.py::quality_score`），用于补足“横向对比 + 绝对质量结果”

---

### 2025-12-26（public-only / 200条）

#### llm_selfcheck_enable（已下线）
- **结论**：稳定负收益，建议删除以降低链路复杂度与回归风险
- **原因（链路功能重复）**：
  - selfcheck 是“翻译后再次改写”的 LLM 路径
  - TRA 是“翻译过程内的多步反思/改写”
  - QE 是“评审后选择性修复”的改写
  - 三者都会改写英文，叠加会导致：成本叠加、回归面扩大、难定位“谁引入的改写”，因此属于功能重复

指标（主指标 chrF）：
- baseline: bleu=0.242266, chrf=0.480136, len_ratio=0.945440, rep3=0.000093
- selfcheck: bleu=0.224172, chrf=0.465902, len_ratio=0.915402, rep3=0.001000
- bootstrap(chrF): p_improve=0.008, delta_mean=-0.014483, ci95=[-0.026833, -0.002695]

产物：
- `reports/fluency_en/report_public_200_selfcheck.json`


