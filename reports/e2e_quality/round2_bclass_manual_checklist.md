# Round 2 - B类（需要人工验收）3段样本清单与验收模板

目的：对 **B 类（体验/视觉/听感）** 能力做人工验收，补齐 E2E 指标无法覆盖的“隐性质量”。

> 中文说明：B类通常包含“展示字幕、混音、硬字幕擦除”等能力，自动评分（passed_rate/e2e_score_100）只能保证链路跑通与产物齐全，**无法替代人眼/人耳体验判断**。

---

## 1) 使用的 3 段样本（short3）

文件：`eval/e2e_quality/segments_short3.docker.jsonl`

- 122704
- 122723
- 122726

---

## 2) 产物在哪里（你要看的视频文件）

> 提示：以下路径是仓库相对路径（宿主机），对应容器内 `/app/...`。

### 2.1 展示字幕（Display）

- 输出根目录：`outputs/eval/e2e_quality_short3_display_suite/`
- 每个 run / 每段视频会有：
  - `output_en_sub.mp4`（带字幕的交付视频）
  - `eng.srt`（主英文字幕，尽量 1:1 可审计）
  - `eng_display.srt`（展示版字幕）
  - `quality_report.json`（门禁与体验评分）

Runs（对应开关）：
- `display_base`：只开 `display_srt_enable`
- `display_use_for_embed_on`：额外开 `display_use_for_embed`
- `display_merge_on`：额外开 `display_merge_enable`
- `display_split_on`：额外开 `display_split_enable`

### 2.2 混音（BGM Mix）

- 输出根目录：`outputs/eval/e2e_quality_short3_bgm_suite/`
- 重点看：
  - `output_en.mp4`（通常是带配音的视频）
  - `output_en_sub.mp4`（带字幕）

Runs：
- `bgm_base`：只开 `bgm_mix_enable`
- `bgm_duck_on`：额外开 `bgm_duck_enable`
- `bgm_loudnorm_on`：额外开 `bgm_loudnorm_enable`

### 2.3 硬字幕擦除（Erase hard subs）

- 输出根目录：`outputs/eval/e2e_quality_short3_erase_suite/`
- 重点看：
  - `output_en_sub.mp4`：是否出现误擦、残影、画面破坏

Runs：
- `baseline`
- `erase_subtitle_on`：开 `erase_subtitle_enable`

---

## 3) 人工验收维度（打分口径）

建议 0/1/2 三档：
- 0 = 不可接受（明显影响交付/严重违和）
- 1 = 可接受（有瑕疵但不影响交付）
- 2 = 很好（明显提升体验）

### 3.1 展示字幕（Display）

- **可读性（Readability / 可读性）**：是否更容易读（断行、每行长度、两行以内）
- **节奏（Rhythm / 节奏）**：字幕出现/消失是否更舒服，不闪、不压
- **语义一致（Meaning / 语义一致）**：展示版与主字幕语义是否一致（不应改变意思）
- **副作用（Side effects / 副作用）**：是否出现错合并、错拆分导致理解困难

### 3.2 混音（BGM）

- **人声清晰度（Voice clarity / 人声清晰）**：配音是否被背景音盖住
- **背景自然（Background natural / 背景自然）**：背景音是否保留得自然，不突兀
- **响度一致（Loudness stability / 响度一致）**：忽大忽小、爆音、底噪
- **口型/节奏（Sync / 同步）**：配音是否明显不同步

### 3.3 硬字幕擦除（Erase）

- **误擦/漏擦（Over/Under erase / 误擦漏擦）**
- **残影/糊块（Artifacts / 视觉伪影）**
- **画面破坏（Visual damage / 画面破坏）**
- **适用性（Applicability / 适用性）**：是否需要每个视频手工调参（若需要，则更像“高级功能”）

---

## 4) 记录模板（每段每 run 一行）

| 类别 | run | 视频ID | 评分(0/1/2) | 结论（默认开/高级/隐藏） | 备注（中文） |
| --- | --- | --- | --- | --- | --- |
| Display |  | 122704 |  |  |  |
| Display |  | 122723 |  |  |  |
| Display |  | 122726 |  |  |  |
| BGM |  | 122704 |  |  |  |
| BGM |  | 122723 |  |  |  |
| BGM |  | 122726 |  |  |  |
| Erase |  | 122704 |  |  |  |
| Erase |  | 122723 |  |  |  |
| Erase |  | 122726 |  |  |  |



