# golden（自有金标准）

如果你们有人工审校后的“母语自然字幕”，建议放入本目录：

- `eng.srt`（必选）：英文金标（允许改写、更像母语字幕）
- `chs.srt`（可选）：对应中文（用于额外检查/抽样分层；英文自然度评测不强依赖）

构建评测集合：

```bash
python3 scripts/build_fluency_eval_set.py \
  --out eval/fluency_en/sets/golden_200.jsonl \
  --n 200 \
  --golden-dir eval/fluency_en/golden
```


