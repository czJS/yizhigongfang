# 中文 ASR 评测（公开测试集 / CER）

目标：用公开测试集评测“中文识别能力”，产出稳定可回归的 **CER**（Character Error Rate）。

本目录只约定数据格式与运行方式，不绑定某个特定数据集来源（AISHELL/THCHS/CommonVoice 等都可）。

---

## 1) 输入格式（pairs.jsonl）

每行一个样本：

```json
{"id":"utt-0001","ref_zh":"参考转写","pred_zh":"ASR输出","meta":{"source":"aishell1-test","wav":"S0002/BAC009S0002W0122.wav"}}
```

- `id`：唯一 ID
- `ref_zh`：参考转写（来自公开数据集标注）
- `pred_zh`：你的 ASR 输出
- `meta`：可选（保留音频路径、说话人、噪声标记等，便于分桶分析）

建议存放：
- `eval/asr_zh/sets/<name>.jsonl`

---

## 2) 运行 CER 评测

脚本：`scripts/eval_asr_cer.py`

```bash
python3 scripts/eval_asr_cer.py \
  --in-jsonl eval/asr_zh/sets/aishell1_test_pairs.jsonl \
  --out reports/asr_zh/report_aishell1_test.json
```

输出报告包含：
- 总体 CER
- Top-K 最差样例（便于人工定位问题：数字/专名/口音/噪声等）

---

## 3) 推荐公开测试集（中文）

需要“音频 + 转写 + 明确 test 切分”：
- AISHELL-1（优先）
- Common Voice（多口音、噪声）
- THCHS-30（小而经典）
- Primewords Chinese（注意许可）
- WenetSpeech（工程量大，适合后续扩展）

> 如果你确认要用其中某一个（例如 AISHELL-1），我也可以继续加一个“导出 pairs.jsonl”的脚本：  
> - 方案 A：HuggingFace `datasets` 直接下载/导出  
> - 方案 B：下载官方压缩包后解析 `wav.scp`/`trans.txt` 等文件导出


