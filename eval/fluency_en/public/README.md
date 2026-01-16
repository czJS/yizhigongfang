# public-only（公开测试集）

把公开数据整理为 `pairs.jsonl` 放在本目录即可。

文件格式（JSONL，每行一个样本）：

```json
{"id":"iwslt17-test-0001","zh":"……","ref_en":"……","source":"iwslt17","meta":{"split":"test"}}
```

提示：
- `id` 必须唯一
- `zh` 为中文输入
- `ref_en` 为参考英文（母语字幕/口语风格更佳）
- `source/meta` 仅用于追踪来源，可选


