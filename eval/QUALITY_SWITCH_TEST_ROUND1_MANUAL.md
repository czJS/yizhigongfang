# 首轮质量模式增强开关评测手册（Round 1）

面向对象：**产品经理 / 运营 / 交付负责人**（非程序员也能按本文理解与验收）  
范围：**质量模式（Quality Mode）**端到端交付体验评测（ASR → 翻译/提质 → 字幕后处理 → TTS → 封装/门禁）

---

## 1. 这轮评测要解决什么问题？

我们要回答两个“产品决策”问题：

- **哪些开关值得默认开启**（Default ON）  
  以“交付稳定 + 体验提升”为第一优先级，而不是“偶尔更好看”。
- **哪些开关适合做高级选项**（Advanced / On-demand）  
  可能提升大，但更慢、更贵、或对内容依赖强，需要用户按需开启。

本轮（Round 1）只做 **单开关**分档筛选 + 结果记录。  
下一轮（Round 2）再基于结果，给出 **5 组提升巨大的推荐组合**（你审核后再做）。

---

## 2. 指标解释（给非技术同学）

我们主要看三类结论（最终会在报告里同时出现）：

- **交付通过率 `passed_rate`（门禁通过率）**  
  这代表“能不能稳定交付”。越高越好。**通过率下降通常是红线**。
- **交付体验分 `e2e_score_100`（0–100 分）**  
  这是综合体验分：缺产物/截断会重罚；CPS/长行等软问题会扣分。越高越好。
- **提升置信度 `p_improve`（0–1）**  
  用 bootstrap 估计“这个开关比基线更好”的概率。  
  例如 `p_improve=0.92` 可以理解为“**92% 的抽样里它比基线好**”。

---

## 3. 本轮评测数据集（我们用什么样本跑）

### 3.1 默认数据集：`segments_golden_9.docker.jsonl`

当前仓库自带 9 条“金标视频”清单：

- 文件：`eval/e2e_quality/segments_golden_9.docker.jsonl`
- 每条包含：
  - 视频：`/app/golden_videos/<id>.mp4`
  - 中文金标：`/app/golden_videos/<id>.srt`
  - 英文金标：`/app/golden_videos/<id>.srt_en`

> 中文标注：这里的“docker”只是说路径写的是容器内路径；实际视频文件在你电脑上。

### 3.2 你需要准备什么（非技术）

确保你电脑目录 **`/Users/chengzheng/Desktop/金标准`** 下存在上述命名文件（例如 `122701.mp4 / 122701.srt / 122701.srt_en`）。  
因为 Docker 会把它挂载到容器里的 `/app/golden_videos`。

---

## 4. 开关总览：46 个单开关怎么处理（剔除 / 默认开 / 纳入评测）

这一节给你一张“总表”：每个开关都写清楚中文含义、是否剔除、以及（若纳入）测试优先级。

### 4.1 剔除规则（本轮不测）

- **Debug 类开关：剔除**  
  只会“多写调试文件/报告”，不应影响体验分，反而增加磁盘与噪声。
- **流程/对比辅助开关：剔除**  
  例如“跳过翻译复用旧结果”，这不是增强能力，是对比工具。
- **已下线/不建议项：剔除**  
  明确在仓库文档里标注“已下线/回归风险高”的能力，不纳入首轮决策。

### 4.2 “完全不会引入负收益”的开关：改为默认开启（并从实验中移除）

本仓库里符合“低风险、纯净化、不改变语义”的只有：

- **`--asr-normalize-enable`（ASR 文本净化）**  
  中文标注：仅做控制字符/空白/标点等低风险清洗。  
  状态：在 `config/quality.yaml` 里**已经默认开启**，因此**不作为实验项单测**（它属于“基线的一部分”）。

> 重要说明：除上述外，其他开关都可能通过“内容变化/时间轴变化/模型不确定性/资源争用”引入负收益，因此不做“强行默认开”的判断，必须先测。

---

## 5. 46 个单开关清单（含：剔除原因 + 测试优先级）

优先级分 3 档：

- **P0（高）**：最可能提升交付体验（或最关键的提质路径），优先跑
- **P1（中）**：可能提升，但收益更依赖内容/成本更高/效果更不稳定
- **P2（低）**：边缘场景或风险较高/偏展示效果，最后跑

> 英文参数名均来自 CLI；括号内为中文解释。

| 开关（CLI） | 中文名（English → 中文） | 阶段 | 本轮处理 | 优先级 | 说明（白话） | 风险/成本（给产品） | 依赖/注意 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `--asr-normalize-enable` | ASR normalize（ASR 文本净化） | ASR | **默认开启（不测）** | - | 清理乱码/空白/标点等低风险问题 | 几乎无风险 | 已在 `quality.yaml` 默认开 |
| `--asr-merge-save-debug` | ASR merge debug（保存合并调试） | ASR | **剔除：Debug** | - | 只写调试文件 | 无体验收益 | - |
| `--asr-llm-fix-save-debug` | ASR LLM fix debug（保存纠错调试） | ASR | **剔除：Debug** | - | 只写调试文件 | 无体验收益 | - |
| `--meaning-split-save-debug` | Meaning split debug（保存切句调试） | MT | **剔除：Debug** | - | 只写调试文件 | 无体验收益 | - |
| `--tts-fit-save-raw` | Save raw TTS script（保存裁剪前 TTS 稿） | TTS | **剔除：Debug** | - | 只写调试文件 | 无体验收益 | - |
| `--tra-save-debug` | TRA debug（保存 TRA 调试） | MT | **剔除：Debug** | - | 只写调试文件 | 无体验收益 | - |
| `--qe-save-report` | Save QE report（保存 QE 报告） | QE | **剔除：Debug** | - | 只写报告文件 | 无体验收益 | - |
| `--mt-skip-if-present` | MT reuse（复用既有翻译） | MT | **剔除：对比辅助** | - | 只是为了公平对比/复用旧结果 | 容易造成“假提升/假退步” | 不作为增强能力 |
| `--mt-pause-before-translate` | Pause before MT（译前暂停人工改术语） | MT | **剔除：工作流** | - | 给人工编辑术语用 | 需要人工介入，不适合自动回归 | - |
| `--skip-tts` | Skip TTS（跳过配音） | TTS | **剔除：不完整交付** | - | 不做配音就不算完整交付体验 | 会使 E2E 结论不可比 | - |
| `--llm-selfcheck-enable` | LLM self-check（LLM 自检改写） | MT | **剔除：已下线/风险高** | - | 仓库明确标注“已下线”，与 TRA/QE 重叠 | 回归风险高 | 见 `eval/fluency_en/CONFIDENCE_LOG.md` |
| `--qe-backtranslate-enable` | Back-translation audit（回译审计） | QE | **剔除：审计项** | - | 主要用于“查问题”，默认不直接修复 | 成本高、对体验分提升不稳定 | 若要测，放 P2 另开专项 |
| `--diarization` | Diarization（说话人分离） | ASR | **剔除：占位/未实现** | - | 当前版本解析/传参但不产生实际效果（未接入 speaker 标注/多角色链路） | 继续跑只会浪费计算、干扰“覆盖统计”口径 | 待实现后再纳入 P2 专项 |
| `--asr-preprocess-enable` | ASR preprocess（ASR 音频预处理） | ASR | 纳入评测 | P0 | 降噪/滤波/响度归一，提升识别稳定性 | 速度略慢；效果依赖素材 | 常与 loudnorm 一起 |
| `--asr-preprocess-loudnorm` | Loudnorm for ASR（ASR 响度归一） | ASR | 纳入评测 | P0 | ASR 预处理的推荐子项 | 基本低风险，但会耗时 | 依赖 `asr-preprocess-enable` |
| `--asr-merge-short-enable` | Merge short segments（合并极短 ASR 片段） | ASR | 纳入评测 | P0 | 减少“短窗胡猜”，对字幕更稳定 | 可能改变分段，影响对齐 | - |
| `--asr-llm-fix-enable` | ASR LLM fix（LLM 保守纠错） | ASR | 纳入评测 | P0 | 对疑似错别字/同音错字做保守修正 | 速度变慢；极少数会修错 | 依赖本地 LLM 稳定 |
| `--vad-enable` | VAD（静音过滤） | ASR | 纳入评测 | P1 | 在嘈杂/停顿多素材里减少无效片段 | 参数不合适会漏词 | 需配合阈值/最小时长（不是单开关） |
| `--denoise` | Denoise（抽音频时降噪） | ASR | 纳入评测 | P1 | 对底噪明显素材有帮助 | 有时会损伤人声细节 | - |
| `--sentence-unit-enable` | Sentence unit merge（句子单元合并） | MT | 纳入评测 | P0 | 翻译前把短句做“保守合并”，更像完整句翻译 | 可能引入少量错合并 | 依赖断点词/标点策略 |
| `--tra-enable` | TRA（多步翻译：忠实→反思→润色） | MT | 纳入评测 | P0 | 提升英文字幕自然度与一致性 | 成本高、偶发不稳定 | 建议优先测，是否默认开取决于成本 |
| `--tra-json-enable` | TRA JSON（结构化输出） | MT | 纳入评测 | P1 | 提升 TRA 解析稳定性（更少跑飞） | 仍有概率解析失败回退 | 只对 TRA 生效 |
| `--tra-auto-enable` | TRA auto（只对低质行触发 TRA） | MT | 纳入评测 | P1 | 降成本：只修“差的行” | 触发策略不当会漏修 | 常与 QE/阈值联动 |
| `--qe-enable` | QE fix（质量评审→选择性修复） | QE | 纳入评测 | P0 | 找出“可疑翻译行”并修复 | 成本中高；依赖 LLM 稳定 | 建议强关注 passed_rate |
| `--qe-embed-enable` | QE embed recall（向量召回可疑行） | QE | 纳入评测 | P1 | 更容易抓到“语义不对”的行 | 需要 embedding 能力；成本增加 | 可能需要额外模型/依赖 |
| `--entity-protect-enable` | Entity protect（专名保护） | MT | 纳入评测 | P1 | 专名、人名地名更稳定，减少误译 | 可能过度保护导致不自然 | 对专名密集内容收益大 |
| `--meaning-split-enable` | Meaning split（超长句语义切分） | MT | 纳入评测 | P1 | 超长中文一句拆成多句再翻译，更稳 | LLM 参与，成本上升 | 只对“超长句”触发 |
| `--glossary-placeholder-enable` | Glossary placeholder（术语占位保护） | MT | **剔除：字典/数据驱动** | - | 该能力强依赖 glossary 内容（属于“数据配置”，不是通用算法增益） | 结论不可复用；应版本化 glossary 后走交付回归/验收或专项评测 | glossary 版本必须锁定 |
| `--glossary-prompt-enable` | Glossary prompt（术语提示） | MT | **剔除：字典/数据驱动** | - | 同上（提示词效果强依赖 glossary 内容） | 结论不可复用 | glossary 版本必须锁定 |
| `--mt-json-enable` | MT JSON（结构化单步翻译） | MT | 纳入评测 | P2 | 让输出更可解析，减少跑飞 | 对质量提升不稳定 | 更偏工程稳定性 |
| `--mt-topic-auto-enable` | Auto topic（自动生成主题提示） | MT | 纳入评测 | P2 | 自动总结主题，改善语境一致性 | 额外一次 LLM 成本 | 对短视频收益有限 |
| `--subtitle-postprocess-enable` | Subtitle postprocess（字幕后处理总开关） | 字幕 | 纳入评测 | P0 | 软换行 + CPS 修正，提升可读性与门禁通过率 | 可能改变时间轴细节 | 建议与 wrap/cps 一起 |
| `--subtitle-wrap-enable` | Subtitle wrap（英文字幕自动换行） | 字幕 | 纳入评测 | P0 | 长句自动分行，更好读 | 可能影响行数展示 | 依赖 postprocess 体系 |
| `--subtitle-cps-fix-enable` | CPS fix（阅读速度修正） | 字幕 | 纳入评测 | P0 | 拉长字幕时长以降低 CPS（读得过快） | 可能压缩后续空隙 | 依赖 postprocess 体系 |
| `--tts-script-enable` | TTS script（字幕稿/朗读稿分离） | TTS | 纳入评测 | P0 | 朗读稿更像口语，配音更自然 | 可能让字幕与配音文本不完全一致 | 产品侧需确认是否接受“字幕稿≠朗读稿” |
| `--tts-script-strict-clean-enable` | Strict TTS clean（更强 TTS 清洗） | TTS | 纳入评测 | P1 | 去 URL/邮箱/单位等，降低读错 | 可能删掉用户认为重要的信息 | 需定义“可删内容”边界 |
| `--tts-fit-enable` | TTS fit（配音时长裁剪） | TTS | 纳入评测 | P0 | 防止配音超时被迫极限加速/截断 | 可能删词导致信息变少 | 适合“交付优先”策略 |
| `--tts-plan-enable` | TTS plan（每段语速规划/停顿审计） | TTS | 纳入评测 | P0 | 降低“某段超速/不自然”的风险 | 成本增加；规则不当会保守 | - |
| `--bgm-mix-enable` | BGM mix（保留背景音混音） | 混音 | 纳入评测 | P2 | 更像原片：有背景音+旁白配音 | 工程链路更复杂，失败面扩大 | 常与 duck/loudnorm 一起 |
| `--bgm-duck-enable` | BGM duck（背景音自动压低） | 混音 | 纳入评测 | P2 | 旁白说话时背景音自动变小 | 需混音链路稳定 | 依赖 `bgm-mix-enable` |
| `--bgm-loudnorm-enable` | Loudnorm（混音响度归一） | 混音 | 纳入评测 | P2 | 防止混音忽大忽小 | 耗时增加 | 依赖 `bgm-mix-enable` |
| `--display-srt-enable` | Display SRT（展示版字幕） | 展示 | 纳入评测 | P2 | 生成更适合屏幕观看的字幕版本 | 可能改变行号/合并拆分 | 不影响交付主字幕 `eng.srt` |
| `--display-use-for-embed` | Use display for embed（用展示字幕做嵌入） | 展示 | 纳入评测 | P2 | 可能提升“语义召回/对齐”效果 | 效果不稳定 | 依赖 `display-srt-enable` |
| `--display-merge-enable` | Display merge（展示字幕合并） | 展示 | 纳入评测 | P2 | 合并相邻短句更像自然字幕 | 可能合并错 | 依赖 `display-srt-enable` |
| `--display-split-enable` | Display split（展示字幕拆分） | 展示 | 纳入评测 | P2 | 拆分超长句更好读 | 可能拆分不自然 | 依赖 `display-srt-enable` |
| `--erase-subtitle-enable` | Erase hard subs（擦除硬字幕） | 画面 | 纳入评测 | P2 | 针对“视频画面自带字幕”的场景 | 有概率擦除误伤画面 | 需要提前标定区域参数（非单开关） |

---

## 6. 首轮测试顺序（按优先级从高到低）

### 6.1 P0（高优先级：先跑）

建议先跑这些，因为它们最可能带来“可交付体验”的显著提升：

- **ASR 提质**：`asr-preprocess-enable`、`asr-preprocess-loudnorm`、`asr-merge-short-enable`、`asr-llm-fix-enable`
- **翻译/提质主路径**：`sentence-unit-enable`、`tra-enable`、`qe-enable`
- **字幕门禁/可读性**：`subtitle-postprocess-enable`、`subtitle-wrap-enable`、`subtitle-cps-fix-enable`
- **配音交付稳定性**：`tts-script-enable`、`tts-fit-enable`、`tts-plan-enable`

### 6.2 P1（中优先级：第二批跑）

- `vad-enable`、`denoise`
- `entity-protect-enable`
- `meaning-split-enable`
- `glossary-placeholder-enable`
- `qe-embed-enable`
- `tra-json-enable`、`tra-auto-enable`
- `tts-script-strict-clean-enable`

### 6.3 P2（低优先级：最后跑）

（占位/未实现，不跑）`diarization`
- `glossary-prompt-enable`、`mt-json-enable`、`mt-topic-auto-enable`
- `bgm-mix-enable`、`bgm-duck-enable`、`bgm-loudnorm-enable`
- `display-srt-enable`、`display-use-for-embed`、`display-merge-enable`、`display-split-enable`
- `erase-subtitle-enable`

---

## 7. 单开关测试流程（白话版）

### 7.1 你需要关心的“产物”

- 每个开关会生成一个 **run**（一次实验结果集）
- 每个 run 会对 9 条视频分别产出结果与评分

你最终只需要看：

- 一份**汇总报告**（JSON + Markdown）：里面有 passed_rate、e2e_score_100、p_improve
- 一张**对比表**：告诉你“这个开关是默认开/仅高级/默认关”

### 7.2 怎么判断“赢了还是输了”（产品口径）

- **先看硬门槛**：`passed_rate` 不能显著低于 baseline（掉通过率就是不建议默认开）
- **再看收益**：`e2e_score_100` 平均分是否提升，并且 `p_improve` 是否足够高（例如 ≥0.90）
- **最后看成本**：是否明显更慢/更不稳定（这决定“默认开”还是“高级选项”）

### 7.3 怎么监控进度 & 断点续跑（非技术也能理解）

- **进度**：每个开关（run）都会依次处理 9 条视频。你可以把它理解为“9 个小任务”的进度条。  
  - 任何一个视频跑完，会生成该视频的结果文件（可视为“已完成”）。
- **断点续跑**：中途断电/重启/中断后，再次启动同一轮评测时，系统会**自动跳过已完成的视频**，继续跑剩下的。  
  - 中文标注：这就是“断点续传”（Resume）的产品化含义。

> 提示：本轮尚未开始跑测；等你审核通过后，我们会把“怎么看到当前跑到第几个开关/第几个视频”补成一张更直观的截图式说明。

---

## 8. 结果记录（本轮先留模板，跑完后填）

> 本轮尚未开始跑测；先把表格准备好，便于你审核通过后“边跑边填”。

### 8.1 汇总表（每个开关一行）

| 优先级 | 开关（CLI） | 状态（默认开/剔除/已测） | passed_rate（通过率） | e2e_score_100（均分） | 相对 baseline 变化 | p_improve（置信度） | 结论（默认开/仅高级/默认关） | 备注（中文） |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0 | `--qe-enable` | 未测 |  |  |  |  |  |  |

（跑测时继续追加行即可）

### 8.2 “第二轮 5 组推荐组合”占位（基于单开关结果再产出）

| 推荐组合（临时名） | 组合包含哪些开关 | 预期收益（中文） | 风险/成本（中文） | 是否建议默认开 |
| --- | --- | --- | --- | --- |
| combo-1 |  |  |  |  |


