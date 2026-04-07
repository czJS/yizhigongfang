# 目录清单（不含 VideoLingo）

> 目的：用“非技术友好”的方式说明仓库里每个顶层目录/关键文件是干什么的，以及通常能不能删。
>
> 重要说明：`.git/` 是 Git 的内部数据库目录（用于版本管理）。为避免干扰阅读，本文**不展开 `.git/`**；并且**不要手动删除 `.git/`**（否则仓库会损坏）。

## 根目录文件（关键）

说明：这部分通常是**项目规则/打包配置/全局配置**等“总开关”。多数情况下不建议删除。

- **`.dockerignore`**：Docker 构建时的“忽略清单”（避免把缓存/大文件打进镜像）
- **`.gitignore`**：Git 的“忽略清单”（避免把输出/缓存误提交）
- **`backend_server.spec`**：打包（PyInstaller）配置：后端服务
- **`quality_worker.spec`**：打包（PyInstaller）配置：质量 worker
- **`测试视频.mp4`**：示例/测试素材
- **`译制工坊.code-workspace`**：Cursor/VSCode 工作区配置

## 根目录文件夹（总览）

说明：下面每个目录都对应一块“功能模块”。这里先给一句话解释，后面重点目录会再细分。

- **`apps/`**：应用本体（桌面端 + 后端真实实现 + 质量 worker）
- **`assets/`**：资源与模型入口（术语/字典/本地模型/离线包等）
- **`bin/`**：工具入口（可执行文件/启动器等；运行链路可能依赖）
- **`configs/`**：配置（v2 唯一口径：defaults/quality/online 等）
- **`docker/`**：Docker 开发环境（Dockerfile/compose）
- **`docs/`**：项目文档
- **`eval/`**：评测与回归验证相关材料
- **`outputs/`**：任务输出与中间产物（体积大，通常可清理）
- **`packaging/`**：打包脚本与配置（尤其是 Windows 发布）
- **`pipelines/`**：流水线入口与实现（ASR/翻译/TTS/质量等核心流程）
- **`reports/`**：评测/质量报告汇总（多为生成物，通常可清理）
- **`resources/`**：运行资源说明/约定（偏文档性质）
- **`scripts/`**：运维/打包/验证脚本（当前以 PowerShell 为主）

---

## `assets/`（资源与模型）

- **一句话解释**：术语、字典、模型、离线包等“材料库”。
- **是否可以删除**：⚠️不建议整目录删除（但可按类别清理缓存）。

### 大分类（不逐文件展开）

- **`assets/glossary/`**：术语表（影响翻译一致性）
- **`assets/asr_normalize/`**：ASR 文本归一化字典/规则（影响转写清洗）
- **`assets/models/`**：模型与离线包入口（可能很大）
  - **`assets/models/build_incoming/`**：离线安装包/源码/压缩包（构建/离线依赖会用）
  - **`assets/models/common_cache_hf/`**：HuggingFace 缓存（WhisperX/对齐模型等离线快照）
  - **`assets/models/quality_tts_coqui/`**：Coqui TTS 本地模型与 vocoder（质量模式用）
  - **`assets/models/quality_asr_whisperx/`**：WhisperX / faster-whisper / 对齐模型缓存
  - **`assets/models/*.onnx` / `*.bin`**：推理模型文件（例如 rnnoise / whisper.cpp ggml 等）

### 最核心文件（本仓库当前已存在的）

- **`assets/models/模型文件清单.md`**：模型清单说明（中文）
- **`assets/models/lite_tts_kokoro_onnx/`**：Kokoro ONNX 的模型与 voices 文件（轻量模式当前默认 TTS）
- **`assets/models/lite_tts_piper/en_US-amy-low.onnx`**（及 `.json`）：旧 Piper 基线模型（保留作历史对照/回退）
- **`assets/models/lite_asr_whispercpp/ggml-*.bin`**：whisper.cpp 的 ggml 模型（轻量模式识别用）

### 常见关键文件（如果你后续准备/放入）

- **`assets/glossary/glossary.json`**：术语表主文件（流水线默认会用这个路径）
- **`assets/asr_normalize/asr_zh_dict.json`**：ASR 归一化字典（可选启用）

---

## `apps/`（应用本体）

- **一句话解释**：你真正运行/打包/交付的“程序代码”都在这里（桌面端、后端实现、worker）。
- **是否可以删除**：❌别删（核心代码）。

### 大分类

- **`apps/desktop/`**：桌面端（Electron + React）
- **`apps/backend/backend/`**：后端真实实现目录（v2 的“真后端”）
- **`apps/worker_quality/`**：质量 worker（独立进程）

### 最核心文件（建议关注）

- **桌面端（`apps/desktop/`）**
  - **`apps/desktop/package.json`**：前端依赖与启动命令
  - **`apps/desktop/electron/main.js`**：Electron 主进程入口（启动桌面壳）
  - **`apps/desktop/electron/preload.js`**：预加载脚本（桥接前端与系统能力）
  - **`apps/desktop/src/App.tsx`**：主界面与核心交互
  - **`apps/desktop/src/api.ts`**：调用后端 API 的封装

- **后端（`apps/backend/backend/`）**
  - **必需（后端 API 正常启动就会用到）**
    - **`apps/backend/backend/__init__.py`**：把目录声明为 Python 包（供 `import backend.*` 使用）
    - **`apps/backend/backend/app.py`**：后端 API 服务入口（被 `python -m backend.app` 启动）
    - **`apps/backend/backend/config.py`**：读取/合并 `configs/*.yaml`（defaults/quality 等）
    - **`core/runtime_paths.py`**：运行路径解析（仓库根、configs、pipelines 等位置；v2 的唯一“真实现”）
    - **`apps/backend/backend/hardware.py`**：硬件探测与推荐预设（给 UI/任务调度使用）
    - **`apps/backend/backend/task_manager.py`**：任务管理与调度（创建任务、拼命令、管理 outputs）
    - **`apps/backend/backend/glossary_store.py`**：术语表读写（给前端维护 glossary 用）
    - **`apps/backend/backend/review_workflow.py`**：审校/差异对比/封装等流程（给 UI 按钮调用）
    - **`apps/backend/backend/quality_report.py`**：质量报告的 API 封装（具体“评分/检查规则”实现下沉在 `pipelines/lib/quality/quality_report.py`，便于复用与测试）
  - **按功能启用（不是每条流程都会走；有些可能当前未启用）**
    - （已物理下沉清理）ASR/翻译/TTS/术语等“算法/规则”模块已从 `apps/backend/backend/` 移出，统一归档到 `pipelines/lib/`（例如 `pipelines/lib/media/subtitle_display.py`、`pipelines/lib/tts/lite_tts.py` 等）
    - （已清理）旧的“backend 内 pipelines”兼容目录已删除；统一以仓库根 `pipelines/` 为唯一真入口

- **质量 worker（`apps/worker_quality/`）**
  - **`apps/worker_quality/quality_worker_entry.py`**：worker 入口（独立进程运行质量流水线；主要用于 Windows 打包的 `quality_worker.exe`，用于隔离 WhisperX/PyTorch 等重依赖）
- **`docs/质量模式/运行架构.md`**：质量 worker、运行闭环与打包关系说明

---

## `bin/`

- **一句话解释**：工具入口（可执行文件/启动器等），运行链路可能会调用这里的工具。
- **是否可以删除**：⚠️不建议删（可能导致某些流程缺工具）。

### 大分类

- **工具入口/启动器**：提供统一的工具调用入口（例如某些环境下会用 `bin/main`）

### 最核心文件（当前存在）

- **`bin/main`**：工具入口（具体行为取决于你的运行链路/打包方式）

## `configs/`

- **一句话解释**：v2 配置的唯一口径（决定默认参数、模式开关、路径等）。
- **是否可以删除**：❌别删（运行必需）。

### 大分类

- **默认配置**：通用默认值（被其它模式继承/覆盖）
- **模式配置**：online / quality 等模式的默认参数集合
- **实验/对照配置**：用于评测或 ablation 的参数组合

### 最核心文件（当前存在）

- **`configs/defaults.yaml`**：默认配置基线
- **`configs/quality.yaml`**：质量模式默认配置（Docker 默认 `CONFIG_PATH` 指向它）
- **`configs/online.yaml`**：在线模式默认配置

## `docker/`

- **一句话解释**：Docker 开发环境入口（构建镜像 + 一键启动服务）。
- **是否可以删除**：⚠️仅在你明确不再使用 Docker 时考虑（你当前的“可验证清理手册”依赖它）。

### 大分类

- **镜像构建**：把后端运行环境固化为镜像
- **开发编排**：一键启动/停止后端、LLM（Ollama）、前端等服务，并挂载本地目录

### 最核心文件（当前存在）

- **`docker/Dockerfile`**：后端镜像构建入口
- **`docker/docker-compose.yml`**：本地开发/验证的统一启动入口

## `docs/`

- **一句话解释**：项目文档（架构、打包、重构、评测/质量说明等）。
- **是否可以删除**：⚠️可以删但不建议（会显著增加维护/交接成本）。

### 大分类

- **架构说明**：给非技术同学看懂“这是什么、怎么跑、目录怎么理解”
- **打包发布**：Windows 打包与打包机准备
- **重构手册**：按“测试→删除→测试→删除”的节奏推进 v2
- **质量/评测文档**：质量模式配置、门禁、打分体系与测试流程
- **前端方案**：界面改版/交互方案类文档

### 最核心文件（建议关注）

- **`docs/质量模式/README.md`**：质量模式最终文档总入口
- **`docs/架构/目录清单（不含VideoLingo）.md`**：目录清单（本文）
- **`docs/质量模式/README.md`**：质量模式最终文档总入口
- **`docs/质量模式/运行架构.md`**：质量模式运行与目录架构说明
- **`docs/质量模式/配置说明.md`**：质量模式配置长期口径说明

## `eval/`

- **一句话解释**：评测与回归验证材料（实验配置、样例集、评测说明）。
- **是否可以删除**：⚠️可选（不做评测可删；但会失去可复现的质量回归能力）。

### 大分类

- **guides（指南）**：怎么跑测试、怎么解释结果（给人看的）
- **suites（题库/实验定义）**：数据集 + 实验配置（给机器跑的）
- **reports（成绩单）**：评测汇总报告（json/md，可归档）

### 最核心文件（建议关注）

- **`docs/质量模式/实验评测.md`**：质量模式实验评测总入口
- **`eval/suites/e2e_quality/experiments/round1_registry.yaml`**：Round1 suites 的唯一入口（去冗余后的注册表）
- **`eval/reports/e2e_quality/`**：E2E 质量评测报告（json/md，便于归档与追溯）

## `outputs/`

- **一句话解释**：任务输出与中间产物（体积大）。
- **是否可以删除**：✅通常可删除（删了只是丢结果；需要时可重跑生成）。

### 大分类（常见内容）

- **上传素材**：待处理的视频/音频
- **任务目录**：每次处理的中间文件（字幕、分段、音频、日志等）
- **最终产物**：成片/字幕/音频等导出结果

### 最核心“约定”（比具体文件更重要）

- **`outputs/<任务ID>/...`**：每个任务独立目录（便于清理与回溯）

## `packaging/`

- **一句话解释**：打包脚本与配置（主要面向 Windows 交付）。
- **是否可以删除**：⚠️不打包可删；要发布/交付建议保留。

### 大分类

- **PyInstaller 配置**：定义如何把后端/worker 打成 exe
- **发布脚本（Windows）**：一键构建安装包与离线包

### 最核心文件（建议关注）

- **必需（要做 Windows 交付/打包就离不开）**
  - **`packaging/windows/scripts/build_installer.ps1`**：Windows 一键出安装包主入口（驱动 Electron `npm run dist`，并联动 models_pack/ollama_pack 的产物管理与可选冒烟）
  - **`packaging/windows/pyinstaller/backend_server.spec`**：`backend_server.exe` 的 PyInstaller 打包入口（后端主进程）
  - **`packaging/windows/pyinstaller/quality_worker.spec`**：`quality_worker.exe` 的 PyInstaller 打包入口（质量 worker；隔离 WhisperX/PyTorch 等重依赖）
  - **`packaging/windows/scripts/rebuild_backend_server_exe.ps1`**：构建/刷新 `dist/backend_server.exe`（使用独立 venv 安装 `apps/backend/requirements.txt`）
  - **`packaging/windows/scripts/rebuild_quality_worker_exe.ps1`**：构建/刷新 `dist/quality_worker.exe`（使用独立 venv 安装 `apps/backend/requirements_quality.txt` + Coqui TTS 兼容安装策略）
  - **`packaging/windows/scripts/verify_win_unpacked_smoke.ps1`**：对 `win-unpacked/resources` 做冒烟自检（`backend_server.exe --self-check` + `quality_worker.exe --self-check`）
  - **`packaging/windows/scripts/build_ollama_pack.ps1`**：生成单独分发的 `ollama_pack.zip`（不进安装包，供 App 内导入）

- **可选/可删（仅当你明确不做 Windows 交付）**
  - `packaging/` 整目录：如果你永远只跑 Docker/本机开发、不做 exe/安装包交付，可删除；否则建议保留。

## `pipelines/`

- **一句话解释**：流水线入口与实现（真正做 ASR/翻译/TTS/合成/评测 的核心流程）。
- **是否可以删除**：❌别删（核心代码）。

### 大分类

- **入口脚本**：lite/quality/online 三种模式的可执行入口
- **实现主体**：复杂模式的主实现文件（通常是 `*_impl.py`）
- **公共库**：可复用的底层能力（字幕 IO、TTS、实体保护、分句等）
- **工具脚本**：评测、数据集构建、批处理、扫参等（位于 `pipelines/tools/`）

### 最核心文件（建议关注）

- **`pipelines/quality_pipeline.py`**：质量模式入口
- **`pipelines/quality_pipeline_impl.py`**：质量模式主要实现
- **`pipelines/lite_pipeline.py`**：轻量流程入口
- **`pipelines/online_pipeline.py`**：在线流程入口
- **（已删除）`pipelines/lite.py`**：旧的兼容 facade 已完成迁移并删除；如需复用能力请直接使用 `pipelines/lib/*`
- **`pipelines/lib/lite_tts.py`**：TTS 相关实现（包含 Kokoro / Piper / Coqui 等）


## `resources/`

- **一句话解释**：运行资源说明/约定（偏文档）。
- **是否可以删除**：⚠️**不建议删**（不仅是“文档”，还与打包/运行路径约定有关）。

### 大分类

- **README/约定**：说明资源准备方式、目录约定等

### 最核心文件（当前存在）

- **`resources/README.md`**：资源说明入口

### 为什么它看起来像“只有文档”，却仍建议保留？

- **打包（Windows/Mac）时**：很多“随安装包一起带走的文件”会落到 `resources/`（或等价资源目录）；代码也会优先从资源目录读取配置/流水线等，避免写入安装目录。
- **开发（Docker/本机）时**：`resources/` 可能很“空”，因为开发模式多数直接用仓库里的 `pipelines/`、`configs/`、`assets/`；但保留目录能让“开发 ↔ 打包”路径约定一致，减少打包后路径找不到的问题。

## `scripts/`

- **一句话解释**：✅已删除（v2 重构后不再需要）。
- **替代入口**：
  - Windows 打包/交付：`packaging/windows/scripts/`
  - 评测/批处理：`pipelines/tools/` + `eval/`

