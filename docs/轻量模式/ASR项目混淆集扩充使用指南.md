# ASR项目混淆集扩充使用指南

> 面向对象：研发、测试、运营、后续接手的 AI  
> 目标：让后续“继续扩正式集”这件事不再依赖口头背景，而是按固定流程稳定执行。

---

## 1. 最终要产出的东西

每一轮扩充最终只需要稳定产出三类结果：

1. 终审清单  
   文件形态通常是：`final_clean_core_pairs.accepted.json`
2. 正式集资产  
   固定写入：`assets/zh_phrase/asr_project_confusions.json`
3. 归档与说明  
   固定写入：`docs/轻量模式/ASR项目混淆正式集与人工审核规则.md`

也就是说，前面的挖掘、候选、复核都只是中间过程，最后真正进入产品主链的，只有正式集资产。

注意：

- 终审清单通常按 `wrong -> candidate` pair 记录。
- 正式集资产按运行期格式会按 `wrong` 聚合成唯一项，一个 `wrong` 允许挂多个 `candidates`。
- 因此“终审 pair 数”和“正式集资产条数”不一定相同。

---

## 2. 一轮扩充的标准流程

### 2.1 准备来源视频

准备一个 `source_manifest.jsonl` 或 `source_manifest.json`，至少包含：

- `id`
- `video` 或 `url`
- `platform`
- `category`

推荐优先收这些视频：

- 单人为主的中文口播短视频
- 新闻评论、时政口播
- 法律 / 维权 / 财经 / 产品讲解口播
- 职场经验、面试建议、沟通技巧
- 知识分享、心理、自我提升、人文历史短讲解

不建议大量混入：

- 重 BGM 混剪
- 多人抢话严重素材
- 极强剧情对白碎片
- 缺乏稳定口播结构的视频
- 超过 `5` 分钟的来源视频
- 非中文为主的视频
- 双人 / 多人访谈、播客、圆桌
- 课程、合集、公开课、直播回放

### 2.1.1 视频源如何生成

如果本轮没有现成来源视频，可以直接由 AI 从 B 站补一批新的口播类视频 URL，再整理成 `source_manifest.jsonl`。

推荐生成策略：

1. 先按主题分桶搜：
   - 新闻评论
   - 法律 / 维权
   - 财经 / 商业
   - 职场 / 面试经验
   - 知识分享 / 访谈 / 心理 / 人文
2. 优先挑这些特征的视频：
   - 标题明显是讲解、评论、复盘、分享
   - 单人为主，最好是对镜口播或单人解说
   - 明确是中文视频
   - 来源视频时长不超过 `5` 分钟，避免下载和切片阶段炸掉
   - 页面能正常打开，且不是专栏、图文、合集页
3. 拿到候选 URL 后，不要直接用，先做去重和人工筛一遍。

推荐保留的附加字段：

- `account_type`
- `title`
- `uploader`
- `picked_by`
- `picked_at`
- `note`

这些字段不是脚本必需，但很适合后续复盘“这一轮为什么选了这些源”。

### 2.1.2 如何保证每次不重复

这一部分很关键，手册之前没写，但实际必须做。

固定原则：

1. 新一轮 `source_manifest` 里的 URL，默认不能和历史轮次重复。
2. 历史已用 URL 的主检查范围至少包括：
   - 历史正式跑批目录中的 `prepared/manifest.valid.jsonl`
   - 历史运行产物里的 `transcripts.jsonl`、`events.jsonl`、`runtime_rejected_sources.jsonl`
3. 判断重复时，优先按 `BV` 号去重，而不是只按标题去重。
4. 发现重复后，直接换新视频，不要抱着“上次只抽到一点点片段，应该还能再用”的想法继续混入。

建议每一轮都固定产出三份来源清单：

1. `source_manifest.candidates.jsonl`
2. `source_manifest.roundN.jsonl`
3. `source_manifest.roundN.rejected.json`

其中：

- `candidates`：初始抓到的候选集合
- `roundN`：最终真正投入自动挖掘的去重后清单
- `rejected`：记录本轮被剔除的原因，例如 `duplicate_bv`、`heavy_bgm`、`dialogue_dense`、`multi_speaker`

### 2.1.3 一个可执行的来源生成流程

建议按下面顺序做：

1. 先从 B 站搜一批新的口播视频，凑出 `30` 到 `100` 个候选 URL。
2. 对候选 URL 提取 `BV` 号。
3. 去 `outputs/asr_confusion_mining/` 里比对历史已用 `BV`，删掉重复项。
4. 对剩余项做人工快筛，删掉明显不适合的：
   - 重 BGM
   - 剧情对白
   - 综艺切条
   - 多人抢话
   - 双人或多人访谈 / 播客
   - 非中文视频
   - 超过 `5` 分钟的长视频
   - 纯画面演示、缺乏稳定口播
5. 把通过快筛的结果写入本轮 `source_manifest.roundN.jsonl`
6. 再用 `auto-url-pipeline` 开跑

如果要让 AI 直接做，建议明确要求它：

```text
请先从 B 站生成一批新的中文短口播视频 URL，按 BV 号和 outputs/asr_confusion_mining 下历史实际运行过的来源去重，不能复用旧轮次视频。候选视频必须以单人为主，来源视频时长不超过 5 分钟。优先新闻评论、法律维权、财经商业、产品讲解、职场经验、知识分享、心理、人文历史；剔除重 BGM、多人抢话、剧情对白、双人或多人访谈、播客、非中文视频、课程合集和直播回放。最终输出 source_manifest.roundN.jsonl，并附 rejected 清单。
```

### 2.2 运行无人值守挖掘主链

优先使用一体化入口：

```bash
python pipelines/tools/mine_asr_project_confusions.py auto-url-pipeline \
  --source-manifest "<source_manifest.jsonl>" \
  --work-dir "<outputs/.../run_name>" \
  --target-sources 500 \
  --total-clips 2000 \
  --batch-size 4 \
  --source-profile speech_focused \
  --teacher-b-mode candidate_only
```

这一阶段会自动完成：

1. 来源过滤
2. 采样计划
3. 切片
4. 双 ASR 挖掘
5. 产出候选池和短名单

核心产物通常在：

- `run/candidate_pool.json`
- `run/transcripts.jsonl`
- `promote/pattern_candidates.review.jsonl`
- `promote/asr_project_manual_review_pool.review.jsonl`

### 2.3 进入人工审核

后续人工审核的主输入通常是：

- `asr_project_manual_review_pool.review.jsonl`

人工审核阶段的原则：

1. 入池可以宽，但必须能看懂上下文。
2. 不收裸单字。
3. 双字候选如果没有上下文，不要直接审。
4. 高价值错词优先看，例如：
   - `获原 -> 货源`
   - `所陪 -> 索赔`
   - `战门 -> 站稳`

### 2.4 形成终审清单

人工审核后，整理出最终终审文件，例如：

`outputs/.../final_clean_core_pairs.accepted.json`

这一步一定要做最后一次“正式集口径收口”：

- 删除 `wrong` 本身就是常用词、常用短语的项
- 删除单字、虚词、功能词项
- 删除只能依赖长上下文才成立的项

终审清单是发布正式集的唯一输入。

### 2.4.1 本轮证明更好用的终审稿形态

如果人工审核过程很长、需要你本人逐行删除或保留，推荐先把待终审池整理成“接近正式集结构”的终审稿，再做最后人工收口。

本轮实际采用并验证过的形态是：

- `formal_set.final_review_ready.jsonl`

这一版的特点是：

- 每行只保留一个 `wrong`
- 已经按 `wrong` 聚合好 `candidates`
- 已经带上 `type`、`evidence_count`、`notes`
- 可以直接人工删除整行，最后留下来的行就是最终保留项

如果采用这种方式，发布前必须重新核对：

1. 最终保留行数是否和人工口径一致
2. 文件内 `wrong` 是否仍唯一
3. 多候选项是否已经手动删到最终想保留的 `candidate`

本轮最终就是按这种方式收口，人工终审后实际保留了 `134` 行。

### 2.4.2 如果终审是“删行式审核”，不要再依赖 decision 字段

有两种常见终审方式：

1. 在行内填写 `final_review_decision=accept/reject/hold`
2. 直接删掉不要的行，只留下保留项

如果采用第二种“删行式审核”，后续发布逻辑就应该把“文件中剩余行”视为最终 accepted 集，不要再指望从空白的 `final_review_decision` 字段里反推结果。

### 2.5 发布到正式集

使用新的发布命令：

```bash
python pipelines/tools/mine_asr_project_confusions.py publish-reviewed-final \
  --reviewed-final-json "outputs/.../final_clean_core_pairs.accepted.json" \
  --out-dir "outputs/.../publish" \
  --asset-out "assets/zh_phrase/asr_project_confusions.json" \
  --source-label "bilibili_500_manual_review_v2" \
  --source-run "bili_500_speech_tighter_v2_formal_run" \
  --manual-pool-size 738 \
  --first-pass-accept 534 \
  --first-pass-review 115 \
  --first-pass-reject 89 \
  --core-accept 207 \
  --core-review 44 \
  --core-reject 35 \
  --second-pass-promoted 11 \
  --second-pass-kept-review 22 \
  --second-pass-rejected 11 \
  --removed-examples-json "outputs/.../removed_examples.json" \
  --archive-doc "docs/轻量模式/ASR项目混淆正式集与人工审核规则.md" \
  --archive-title "第二版正式集"
```

执行后会完成：

1. 把终审清单转换为正式集资产
2. 输出一份 `publish_formal_asset.summary.json`
3. 输出一份 `publish_formal_asset.summary.md`
4. 可选地把本轮结果追加到正式归档文档

### 2.5.1 如果是“增量扩充”，不要直接覆盖旧正式集

这一步是本轮最容易踩坑、也最值得补进手册的地方。

如果你这轮做的不是“重建全量正式集”，而是“在现有正式集基础上追加新一轮人工终审结果”，发布时必须做三件事：

1. 先读取当前 `assets/zh_phrase/asr_project_confusions.json`
2. 再把本轮 accepted 项按 `wrong` 做去重合并
3. 对已存在的 `wrong`，合并 `candidates` / `sources` / `notes`，不要重复新增一条

本轮实际就出现了这种情况：

- 本轮终审保留：`134`
- 与旧正式集重叠：`1` 项（`吃啰啰`）
- 实际新增：`133`

因此，增量发布的正确口径不是“accepted 数直接加到资产里”，而是：

- 先按 `wrong` 去重
- 重叠项做合并
- 最后再次验证资产里 `wrong` 全局唯一

### 2.5.2 正式集发布后，`evidence_count` 至少要到主链门槛

本轮还踩到了一个非常关键但之前手册没写清楚的问题：

- 轻量链命中项目混淆时默认要求 `evidence_count >= 2`
- 质量链命中项目混淆时默认也要求 `evidence_count >= 2`

也就是说，某些条目就算已经写进 `assets/zh_phrase/asr_project_confusions.json`，如果 `evidence_count=1`，它在主链里也可能根本不触发。

因此，发布前必须额外检查：

1. 新并入项是否全部满足当前门槛
2. 如果是人工终审保留项，且确认允许进入正式集，通常应把 `evidence_count` 至少补到 `2`
3. 发布后再次确认资产中没有 `evidence_count < 2` 的正式项

---

## 3. 发布后必须做的验证

### 3.1 最小验证

至少跑：

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

当前已经有两类相关测试：

- `tests/test_project_confusion_asset.py`
- `tests/test_project_confusion_publish.py`

本轮实际可用、且更聚焦的跑法是：

```bash
python3 -m unittest discover -s tests -p "test_project_confusion_*.py"
```

### 3.2 你应该重点确认什么

1. 轻量链可以正常读取正式集。
2. 质量链可以正常读取正式集。
3. 已保留项会命中，例如：
   - `获原`
   - `战门`
4. 已剔除的常用词不会回流，例如：
   - `详情`
   - `主张`
   - `干净`
5. 新加入项不是“写进文件但主链不触发”，尤其要看 `evidence_count` 是否已过门槛。

### 3.2.1 建议补一轮“新项专项命中测试”

除了跑单元测试，建议再用几条“本轮新增样例”做 before/after 的专项核验：

1. 旧正式集不命中
2. 新正式集合并后命中
3. 轻量链命中
4. 质量链命中

本轮实际用来做专项验证的新增样例包括：

- `减力 -> 简历`
- `权娱 -> 全局`
- `众揪心 -> 重就轻`
- `硬介身 -> 应届生`

### 3.3 正式集变更前后固定对比

如果你要比较“正式集替换前后到底带来了什么变化”，使用固定对比脚本：

```bash
python pipelines/tools/compare_project_confusion_assets.py \
  --before-asset "assets/zh_phrase/asr_project_confusions.before.json" \
  --after-asset "assets/zh_phrase/asr_project_confusions.json" \
  --corpus "outputs/.../run/transcripts.jsonl" \
  --out-dir "outputs/.../compare_formal_asset"
```

它会固定输出三层对比：

1. `ASR`：直接项目混淆命中变化
2. `Lite`：`lite_asr_stage1` 的项目混淆命中与严重度变化
3. `Quality`：`zh_polish` 规则侧的项目混淆命中与 suspect 变化

默认产物：

- `project_confusion_compare.json`
- `project_confusion_compare.md`

推荐语料优先级：

1. `run/transcripts.jsonl`
2. 终审清单 JSON
3. 人工审核池 JSONL

如果要快速看“当前终审结果比旧正式集多识别了哪些项目混淆”，优先用 `transcripts.jsonl`。

实际执行时，建议先把旧正式集做一份快照，例如：

- `asr_project_confusions.before_merge.json`

这样 compare 的 `before-asset` 和 `after-asset` 都是稳定文件，后续复盘不会丢。

---

## 4. AI 接手时的固定工作清单

如果是让 AI 继续扩充，建议直接要求它按下面顺序执行：

1. 先确认当前正式集文件位置是否仍是 `assets/zh_phrase/asr_project_confusions.json`
2. 先生成新的、按 BV 去重后的 `source_manifest`
3. 基于新的来源视频跑 `auto-url-pipeline`
4. 只把 `manual_review_pool` 作为人工审核输入
5. 审核时严格执行：
   - `wrong` 是常用词就删
   - 单字 / 虚词就删
   - 只能靠长上下文判断就不进正式集
6. 形成新的 `final_clean_core_pairs.accepted.json`
7. 如果是增量发布，先和现有正式集按 `wrong` 去重合并
8. 确保所有新正式项的 `evidence_count >= 2`
9. 跑 `unittest` 和专项命中验证
10. 报告最终条数、代表性保留、代表性删除

可以直接给 AI 这样的任务描述：

```text
请不要直接修改正式集。先跑混淆挖掘，再把人工终审结果整理成 final_clean_core_pairs.accepted.json，最后只用 publish-reviewed-final 命令升级正式集，并补归档文档。正式集口径是：wrong 本身是常用词、常用短语、单字、虚词、强上下文依赖项，一律不收。
```

---

## 5. 常见错误

### 5.1 把人工候选池直接写成正式集

这是最常见错误。  
`manual_review_pool` 只是待审材料，不是正式集。

### 5.2 把“看起来也说得通”的常用词保留下来

例如：

- `详情 -> 相信`
- `主张 -> 手上`
- `干净 -> 赶紧`

这些项会误导主链，不应进入正式集。

### 5.3 只看规则，不做人审

规则适合筛候选，不适合代替终审。  
正式集最终一定要按“人工交付视角”收口。

### 5.4 只更新资产，不更新归档文档

正式集每次变化，都应同步更新：

- `assets/zh_phrase/asr_project_confusions.json`
- `docs/轻量模式/ASR项目混淆正式集与人工审核规则.md`

### 5.5 误以为“写入资产文件”就等于已经生效

这也是本轮真实踩到的坑。

如果新条目 `evidence_count` 不到当前主链门槛，它虽然已经在资产文件里，但轻量链和质量链都可能不会命中。

所以发布完成不应只看：

- 文件是否写成功

还必须看：

- 轻量链是否命中新样例
- 质量链是否命中新样例
- 资产里是否仍有低于门槛的条目

---

## 6. 推荐目录约定

建议每一轮扩充都保留类似结构：

```text
outputs/asr_confusion_mining/<run_name>/
  prepared/
  plan/
  run/
  promote/
  review/
  publish/
```

其中：

- `run/`：双 ASR 原始挖掘结果
- `promote/`：候选、模式、人工审核池
- `review/`：人工审核过程材料
- `publish/`：正式发布摘要

如果本轮采用“先整理成终审稿，再人工删行”的方式，建议在 `review/` 下额外固定一层：

```text
review/
  final_review_asset_ready_v1/
    formal_set.final_review_ready.jsonl
    asr_project_confusions.before_merge.json
    asr_project_confusions.after_merge.json
    merge_into_formal_set.summary.json
```

---

## 7. 一句话口径

后续任何人或 AI 接手时，都应遵守一句话：

> 先把候选挖出来，再人工终审，最后只把终审后的高价值、低误触发项目写进正式集；不要把“有点像对”的项塞进 `asr_project_confusions.json`。

---

## 8. AI 执行模板

下面这两版模板都可以直接给 AI。

### 8.1 短版模板

```text
请按项目现有流程继续扩充 ASR 项目混淆正式集。不要直接手改正式集。先从 B 站生成一批新的中文口播类视频 URL，按 BV 号和 outputs/asr_confusion_mining 下历史已用来源去重，整理成新的 source_manifest；再基于新的 source_manifest 跑混淆挖掘，只用 manual_review_pool 做人工终审，形成 final_clean_core_pairs.accepted.json，最后用 publish-reviewed-final 命令发布到 assets/zh_phrase/asr_project_confusions.json，并补写 docs/轻量模式/ASR项目混淆正式集与人工审核规则.md 的归档段。正式集口径是：wrong 本身是常用词、常用短语、单字、虚词、强上下文依赖项，一律不收。发布后再跑 compare_project_confusion_assets.py 和 unittest，给出最终条数、代表性新增、代表性删除、ASR/Lite/Quality 对比摘要。
```

### 8.2 长版模板

```text
目标：基于新的中文口播视频继续扩充项目 ASR 混淆正式集，并按现有工程规范完成从挖掘、人工终审、正式发布到回归验证的全流程。

必须遵守：
1. 不要直接修改 assets/zh_phrase/asr_project_confusions.json，必须先形成终审清单再发布。
2. 只把已经人工终审通过的高价值项目写进正式集。
3. wrong 本身如果是常用词、常用短语、单字、虚词、功能词，直接排除。
4. 只能靠长上下文判断的项，不进入正式集。
5. 发布后必须补归档文档，并做 before/after 对比和 unittest。

执行顺序：
1. 跑 pipelines/tools/mine_asr_project_confusions.py auto-url-pipeline
2. 读取 promote 目录中的 manual_review_pool
3. 模拟人工终审，形成 final_clean_core_pairs.accepted.json
4. 用 pipelines/tools/mine_asr_project_confusions.py publish-reviewed-final 发布
5. 用 pipelines/tools/compare_project_confusion_assets.py 比较正式集变更前后
6. 跑 python3 -m unittest discover -s tests -p "test_*.py"
7. 输出：
   - 最终终审 pair 数
   - 正式集资产条数
   - 代表性新增
   - 代表性删除
   - ASR/Lite/Quality 对比摘要
   - 是否建议替换上线
```

### 8.3 当前推荐模板

如果后续要把同类任务直接交给 AI，这一版最贴近本项目当前真实流程，也最不容易踩坑：

```text
请按当前项目规范继续处理 ASR 项目混淆正式集任务，并严格按下面流程执行。

必须遵守：
1. 不要直接手改 assets/zh_phrase/asr_project_confusions.json。
2. 如果这轮是继续扩充，而不是重建全量正式集，必须在旧正式集基础上做增量合并，按 wrong 去重，不能重复加入。
3. wrong 本身如果是常用词、常用短语、单字、虚词、功能词，直接删除，不进入正式集。
4. 只能靠长上下文才成立的项，不进入正式集。
5. 最终进入正式集的新增项，必须确保 evidence_count 至少达到当前主链门槛；当前轻量链和质量链默认都要求 evidence_count >= 2。

执行顺序：
1. 先确认当前正式集路径仍是 assets/zh_phrase/asr_project_confusions.json。
2. 如果还没有终审稿，先基于人工审核池整理出按 wrong 聚合的 final_review_asset_ready jsonl，再进行人工终审。
3. 如果终审方式是删行式审核，则把“终审稿中剩余的行”直接视为最终 accepted 集。
4. 发布前先检查 accepted 集内部 wrong 是否唯一，多候选项是否已经删到最终想保留的 candidates。
5. 增量发布时先读取旧正式集，再把 accepted 集按 wrong 合并进去；已有 wrong 只合并 candidates / sources / notes，不重复新增第二条。
6. 发布后检查正式集里 wrong 全局唯一，且没有 evidence_count < 2 的项。
7. 跑 python3 -m unittest discover -s tests -p "test_project_confusion_*.py"。
8. 再做一轮新增样例专项验证：确认旧正式集不命中、新正式集合并后能命中，并且 Lite / Quality 两条主链都能命中。
9. 同步更新 docs/轻量模式/ASR项目混淆正式集与人工审核规则.md，把这轮结果补进归档。
10. 最后报告：
   - 本轮终审保留数
   - 与旧正式集重叠数
   - 实际新增数
   - 合并后正式集总数
   - 代表性新增
   - 代表性删除
   - 测试结果
   - Lite / Quality 专项验证结果

输出口径必须清楚区分：
- 本轮终审保留多少
- 真正新增到正式集多少
- 合并后正式集总数多少

不要把“accepted 数”直接当成“正式集新增数”。
```
