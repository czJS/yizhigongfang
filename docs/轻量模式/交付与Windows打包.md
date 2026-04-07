# 轻量模式交付与 Windows 打包

> 本文只写一件事：让切到 Windows 打包机后的 Cursor，按项目实际情况判断当前仓库**能不能**打出轻量模式一体化安装包；如果能，就一步步打；如果不能，就准确报出阻塞点。  
> 这里的产品目标已经固定：**lite 面向用户只交付一个安装包，安装后即可直接跑，不再要求用户额外下载或导入 `models_pack.zip`。**

---

## 1. 先说结论

轻量模式 Windows 交付的目标形态应当是：

- 只交付 `apps/desktop/dist_electron/YizhiStudio-*.exe`
- 安装包内置 lite 主链运行所需模型
- 安装后可直接运行 lite 主链
- 不要求用户再手动导入 `models_pack.zip`
- 不要求用户再单独处理 `ollama_pack.zip`

但要先说清楚一件事：

**当前仓库默认打包实现并不自动等于这个目标。**

原因已经体现在现有文件里：

- `packaging/windows/scripts/build_installer.ps1` 仍会单独准备 `models_pack.zip`
- `apps/desktop/electron-builder.yml` 仍带有明显的质量模式打包口径
- `apps/desktop/electron-builder.yml` 当前仍排除了 `lite_pipeline.py` / `lite_pipeline_impl.py`
- `apps/desktop/electron-builder.yml` 当前也没有把 `assets/models/**` 打进安装包资源

所以：

- 如果当前分支已经把这些问题改掉了，本文可以直接指导打包
- 如果当前分支还没改掉，本文会要求 Cursor **停止打包并明确报阻塞**

---

## 2. lite 一体化安装包的最低定义

要把当前打包结果称为“轻量模式一体化安装包”，至少要满足下面 6 条：

1. 安装包里包含 `backend_server.exe`
2. 安装包里包含 `configs/defaults.yaml`
3. 安装包里包含 `pipelines/lite_pipeline.py`
4. 安装包里包含 `pipelines/lite_pipeline_impl.py`
5. 安装包里包含 lite 所需模型目录，即至少覆盖：
   - `assets/models/lite_asr_whispercpp`
   - `assets/models/lite_mt_marian_opus_mt_zh_en`
   - `assets/models/lite_tts_kokoro_onnx`
6. 安装后首启时，不再提示用户导入 `models_pack.zip`

如果缺任意一条，就不能对外声称这次产物是 lite 一体化安装包。

---

## 3. 当前项目里与 lite 一体化直接相关的真实文件

切到打包机后，Cursor 先只看下面 3 处：

- `packaging/windows/scripts/build_installer.ps1`
- `apps/desktop/electron-builder.yml`
- `configs/defaults.yaml`

这 3 处分别代表：

- `build_installer.ps1`：当前 Windows 正式打包入口
- `electron-builder.yml`：安装包实际会带哪些文件
- `configs/defaults.yaml`：lite 主链到底依赖哪些模型和资源

基于当前仓库现状，必须知道这些事实：

### 3.1 `build_installer.ps1` 的当前现实

当前脚本里仍然存在下面行为：

- 打包前强检查 `dist/backend_server.exe`
- 打包前强检查 `dist/quality_worker.exe`
- 正常流程会单独产出 `models_pack.zip`
- 默认还会读取 `D:\tools\ollama_models`
- 默认 smoke 仍调用质量模式的 `verify_win_unpacked_smoke.ps1`

这意味着：

- 即使你要打 lite 包，当前脚本也可能还要求先构建 `quality_worker.exe`
- 如果不显式跳过，当前 smoke 会按质量模式口径误判 lite 包
- 如果不先确认分支实现，单靠这套脚本默认行为，**不能证明**最终产物是 lite 一体化安装包

### 3.2 `electron-builder.yml` 的当前现实

当前文件里最关键的问题有 3 个：

- 注释里仍写着 `quality-only packaging`
- `files` / `extraResources` 里显式排除了 `lite_pipeline.py` 与 `lite_pipeline_impl.py`
- `assets` 资源当前排除了 `models/**`

只要这 3 点还存在，就说明这次分支**还不是** lite 一体化打包口径。

### 3.3 `configs/defaults.yaml` 的当前现实

当前 lite 主链默认依赖的关键资源已经写在配置里，例如：

- `assets/models/lite_asr_whispercpp/...`
- `assets/models/lite_mt_marian_opus_mt_zh_en/...`
- `assets/models/lite_tts_kokoro_onnx/...`
- `assets/asr_normalize/...`
- `assets/zh_phrase/...`

这说明 lite 想做成一体化安装包时，至少要保证这些资源在安装后可直接找到。

---

## 4. 打包前阻断检查

这一节不是建议，而是**硬门槛**。  
Cursor 到打包机后，必须先做下面检查；任一不通过，就停止，不要继续执行正式打包命令。

### 4.1 检查 `electron-builder.yml`

必须确认下面 4 条全部成立：

- 不再出现 `quality-only packaging` 这种质量模式专用口径
- 不再排除 `lite_pipeline.py`
- 不再排除 `lite_pipeline_impl.py`
- lite 运行所需 `assets/models/**` 已进入安装包资源

如果任一不成立，直接判定：

> 当前分支还不具备 lite 一体化安装包打包条件。

### 4.2 检查 `build_installer.ps1`

必须确认下面两类问题已经被处理：

- 默认 smoke 不会再拿质量模式脚本来验 lite 包
- 产物口径已经允许“只交付安装包”，而不是继续把 `models_pack.zip` 当成用户安装闭环的一部分

如果脚本仍保留旧行为，也可以继续构建安装包，但必须把结果视为：

> 仅完成“构建动作”，还不能宣称完成 lite 一体化交付。

### 4.3 检查 lite 模型目录

至少确认以下目录在当前仓库真实存在：

- `assets/models/lite_asr_whispercpp`
- `assets/models/lite_mt_marian_opus_mt_zh_en`
- `assets/models/lite_tts_kokoro_onnx`

如果这些目录本身都不完整，那么即使打包配置正确，也不能得到可运行的一体化安装包。

---

## 5. Windows 打包机最小准备

### 5.1 必装软件

- Git
- Python 3.11
- Node.js 20.x
- 7-Zip

### 5.2 建议准备目录

- `D:\temp\yizhistudio\`
- `D:\cache\pip\`
- `D:\cache\npm\`
- `D:\cache\electron\`
- `D:\cache\electron-builder\`

### 5.3 当前脚本常见环境变量

- `YGF_NODE_DIR`
- `YGF_BACKEND_VENV`
- `YGF_TEMP_ROOT`
- `YGF_PIP_CACHE_DIR`
- `YGF_NPM_CACHE_DIR`
- `YGF_ELECTRON_CACHE_DIR`
- `YGF_ELECTRON_BUILDER_CACHE_DIR`
- `YGF_BUILD_KEEP_DIR`

仅当当前分支里的 `build_installer.ps1` 仍强检查 `dist/quality_worker.exe` 时，才额外需要：

- `YGF_QUALITY_VENV`

---

## 6. 真正给 Cursor 用的执行顺序

这一节按“先判断，再执行”的方式写。  
不要跳步。

### 6.1 第一步：先判定当前分支是否具备 lite 一体化条件

Cursor 应先阅读并检查：

- `apps/desktop/electron-builder.yml`
- `packaging/windows/scripts/build_installer.ps1`
- `configs/defaults.yaml`

判定标准：

- 若 `electron-builder.yml` 仍排除 `lite_pipeline.py` / `lite_pipeline_impl.py`，停止
- 若 `electron-builder.yml` 仍排除 `assets/models/**`，停止
- 若安装后仍设计成提示用户导入 `models_pack.zip`，停止

停止时应直接向用户报告：

1. 哪个文件阻塞
2. 哪一行逻辑仍是质量模式口径
3. 为什么这会导致 lite 不能成为一体化安装包

### 6.2 第二步：只有在通过第 6.1 步后，才执行构建

推荐顺序：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git

powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\rebuild_backend_server_exe.ps1

# 仅当当前 build_installer.ps1 仍要求 dist\quality_worker.exe 时执行
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\rebuild_quality_worker_exe.ps1

# lite 一体化目标下，只打安装包，不再把 models_pack.zip 作为用户交付物
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\build_installer.ps1 -SkipSmokeCheck -SkipModelsPack
```

这里每一步的含义是：

- `rebuild_backend_server_exe.ps1`：生成桌面端实际依赖的 `dist/backend_server.exe`
- `rebuild_quality_worker_exe.ps1`：仅用于兼容当前共用脚本的前置检查，不代表 lite 需要把它作为对外交付主件
- `build_installer.ps1 -SkipModelsPack`：只打安装包，不再单独准备用户要导入的模型包

### 6.3 为什么这里仍然保留 `-SkipSmokeCheck`

因为当前仓库里默认 smoke 仍是质量模式口径：

- `verify_win_unpacked_smoke.ps1`
- `backend_server.exe --self-check`

它们默认检查的是：

- `quality_worker.exe`
- `configs/quality.yaml`

所以在 lite 打包流程里，当前仍然应该：

- 不复用这套质量模式 smoke
- 改用第 7 节的一体化安装包检查方式

---

## 7. 打包完成后的最小验收

### 7.1 先查构建产物

至少确认：

- `apps/desktop/dist_electron/YizhiStudio-*.exe` 已生成
- `apps/desktop/dist_electron/win-unpacked/resources/backend_server.exe` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/configs/defaults.yaml` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/pipelines/lite_pipeline.py` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/pipelines/lite_pipeline_impl.py` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/assets/models/lite_asr_whispercpp` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/assets/models/lite_mt_marian_opus_mt_zh_en` 存在
- `apps/desktop/dist_electron/win-unpacked/resources/assets/models/lite_tts_kokoro_onnx` 存在

如果这里还看不到安装包内置模型目录，就说明这次产物仍不是 lite 一体化安装包。

### 7.2 再做安装后人工 smoke

建议至少验证：

1. 安装包可正常安装
2. 首次启动可进入主界面，不空白、不闪退
3. 首次启动后不会提示用户去导入 `models_pack.zip`
4. 系统页不会把“缺少模型包导入”当成正常首启路径
5. 至少 1 条真实视频可直接跑通 lite 主链
6. 最终输出目录里至少有：
   - `eng.srt`
   - `output_en_sub.mp4`
   - `quality_report.json`
7. `quality_report.passed == true`

如果出现“请手动选择并导入模型包（models_pack.zip）”之类提示，本次安装包就不满足 lite 一体化目标。

---

## 8. Cursor 在打包机上的汇报模板

Cursor 完成后，应至少向用户汇报下面 5 项：

1. 本次是否满足 lite 一体化前提检查
2. 如果不满足，阻塞文件与阻塞原因分别是什么
3. 如果满足，安装包路径是什么
4. 安装包内是否已实际带上 lite 模型目录
5. 最小人工 smoke 是否通过

推荐汇报口径只有两种：

- `通过`：当前分支已满足 lite 一体化打包前提，已生成安装包并完成最小验收
- `阻塞`：当前分支仍是质量模式打包口径，无法仅靠打包机执行得到 lite 一体化安装包

### 8.1 通过时的标准汇报模板

可直接按下面格式回报：

```text
lite 一体化安装包已完成。

- 前提检查：通过
- 安装包路径：<填写实际 exe 路径>
- lite 模型内置情况：已内置 / 未确认
- 关键内置目录：
  - assets/models/lite_asr_whispercpp
  - assets/models/lite_mt_marian_opus_mt_zh_en
  - assets/models/lite_tts_kokoro_onnx
- 最小人工 smoke：通过 / 未完成
- 补充说明：<例如当前 build_installer.ps1 仍兼容性要求 quality_worker.exe，但未作为 lite 对外交付物>
```

### 8.2 阻塞时的标准汇报模板

可直接按下面格式回报：

```text
当前无法确认或产出 lite 一体化安装包，已在打包前阻断。

- 前提检查：未通过
- 阻塞文件：<填写文件路径>
- 阻塞点：<填写具体配置或脚本逻辑>
- 为什么阻塞：<解释为什么这会导致 lite 仍不是一体化安装包>
- 当前结论：本次最多只能完成构建动作，不能对外宣称已完成 lite 一体化交付
- 建议下一步：先修正上述阻塞点，再重新执行打包
```

### 8.3 阻塞汇报必须写到的最小信息

如果 Cursor 在打包前就停下，至少要写清楚：

- 是 `electron-builder.yml` 阻塞，还是 `build_installer.ps1` 阻塞
- 是 `lite_pipeline.py` 被排除，还是 `assets/models/**` 没有被打入安装包
- 阻塞发生在“打包前检查”还是“构建后验收”

不要只说：

- “当前不能打”
- “配置不对”
- “建议先修”

这种说法太虚，不能帮助你下一步判断是该切分支、改脚本，还是继续打包。

---

## 9. 当前最常见失败点

- `electron-builder.yml` 仍排除了 `lite_pipeline.py`
- `electron-builder.yml` 仍排除了 `assets/models/**`
- 打包后首启仍提示导入 `models_pack.zip`
- `build_installer.ps1` 仍把 `models_pack.zip` 当成默认交付链路的一部分
- `build_installer.ps1` 仍要求 `dist/quality_worker.exe`
- 默认 smoke 仍沿用质量模式脚本
- 打包机上 Node / Python / 7-Zip / cache 路径与脚本默认值不一致

---

## 10. 这份文档的使用方式

如果你是在 Windows 打包机上让 Cursor 干活，正确做法不是上来就运行脚本，而是：

1. 先按第 4 节做阻断检查
2. 通过后再按第 6 节执行构建
3. 构建完成后按第 7 节验收
4. 不通过就按第 8 节汇报阻塞

这就是 lite 当前最务实的打包文档口径：

> 目标明确是一体化安装包，但执行必须忠于仓库现状；现状没满足，就明确说没满足，而不是用一份“看起来完整”的文档假装已经可以直接打出来。

