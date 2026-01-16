# Marian 翻译优化速览（以 Helsinki-NLP/opus-mt-zh-en 为例）

## 可控配置项（基于 HuggingFace Transformers）
- `model` / `tokenizer`：`Helsinki-NLP/opus-mt-zh-en`
- `device`：`0`/`cpu`（脚本中 `--mt-device auto/cpu/cuda`）
- `max_length`：默认 512，可按句长调整，避免截断或过长生成
- `truncation`：默认开启（防止超长输入报错）
- `forced_bos_token`：对 opus-mt 模型通常已设为目标语；若自建 pipeline，可显式传 `forced_bos_token_id`
- 源语言标签：opus-mt 多数已固定方向，无需 `src_lang`/前缀（不同于 M2M/NLLB）
- 批大小：本脚本逐句调用，如需加速可自行包装 batch（注意显存/内存）

## 提示/工程化手段（对 Marian 的可行度）
- 轻量 “风格”/“领域” 前缀：如在翻译前附加短标签（`[tech] ...`），效果有限但有时有用
- 保留/保护：数字、专名占位后还原，减少误译
- 合并翻译再回填：按标点/段落合并 → 翻译 → 按比例拆回原时间戳，减少碎句直译
- 后处理：口语化规则、标点/空格清理、大小写修正；避免让 Marian 自由发挥

## 性能与资源
- 体积：~300MB；CPU 可跑，GPU 有利于速度
- 典型调用耗时：在 8GB CPU 场景，1 分钟视频翻译耗时数分钟以内（取决于 ASR 段数与合并策略）

## 优化方案（推荐）
1) 预处理：合并短段、数字占位、专名保护（如需），再交给 Marian
2) 翻译调用：保持 `max_length=512`，逐句（或小批量）推理
3) 回填：按源段长度比例拆分，避免断句不均
4) 规则后处理：口语化替换、去冗余空格标点；可选轻量 GEC/Grammar（如有 Java 环境可尝试 LanguageTool；或保持规则即可）

## 何时考虑换模型
- 如果直译感仍重、口语/流畅度欠佳，可评估 NLLB 600M/1.3B（需更多资源）或搭配小型英文润色（风险：可能改写语义）  
- 若需多语方向或更强上下文，可试 M2M/NLLB；但在口语短句上，未加润色时未必优于 Marian

