# 项目架构与目录说明（v2，面向非技术同学）

> 目的：让第一次接触该项目的人，能快速知道“这个项目做什么、怎么跑、每个文件夹是干什么的、哪些东西可以清理”。

## 一句话概览：这是个“本地译制工坊”

你可以把它理解为一个“把中文视频做成英文字幕 + 英文配音成片”的小工厂：

- **前端（界面）**：在桌面应用里点按钮、看进度、下载结果、审校字幕。
- **后端（引擎）**：真正做计算的地方（识别语音、翻译、生成配音、合成成片、生成质量报告）。
- **模型/工具**：离线跑需要很多“工具箱”（例如 ffmpeg、语音识别模型、翻译模型、配音模型）。
- **输出目录**：所有生成的字幕、音频、成片、报告，都落在 `outputs/`（可能非常大）。

## 快速目录树（v2）

```
译制工坊/
  apps/
    desktop/                桌面端（Electron + React，打包输出在 apps/desktop/dist_electron）
    backend/                后端源码镜像（过渡期；运行仍以根目录 backend/ 为准）
    worker_quality/         质量 worker 说明（过渡期）
  backend/                  后端服务（API + 任务管理；Python/Flask）
  frontend/                 旧桌面端目录（过渡期保留，便于回滚/对照）
  pipelines/                v2 流水线入口（当前为薄封装，委托 scripts/）
    lib/                    公共库（probe/mux/burn 等统一实现）
  scripts/                  旧流水线与研发工具脚本（过渡期保留；打包仍会携带）
  configs/                  v2 配置目录（优先；兼容旧 config/）
  config/                   旧配置目录（过渡期保留）
  packaging/                打包配置与脚本（Windows 稳定入口在 packaging/scripts/windows）
  docker/                   Docker 开发相关（Dockerfile/compose）
  assets/                   资源与模型（模型大多外置，不进安装包）
  bin/                      Windows 运行依赖工具（ffmpeg/whisper-cli 等）
  outputs/                  任务输出（生成物，体积很大，可清理）
  reports/                  评测/质量报告汇总（生成物，可清理）
  docs/                     项目文档（本文件所在）
```

## 总体架构（看懂“流水线”即可）

**你在界面里导入视频 → 点击开始 → 后端按流水线处理 → 结果写入输出目录 → 界面展示/下载。**

- **桌面端**（`apps/desktop/`，旧版在 `frontend/`）
  - 负责 UI、参数编辑、进度展示、产物下载、字幕审校。
  - 通过 HTTP 调用后端（默认 `http://127.0.0.1:5175`）。
- **后端**（`backend/`）
  - 暴露 API：开始任务/查进度/下载/审校等。
  - 根据模式把“参数 + 配置”转为命令行调用流水线脚本，并管理日志与产物。
- **流水线**（`pipelines/` + `scripts/`）
  - lite：`pipelines/asr_translate_tts.py`（过渡期委托 `scripts/asr_translate_tts.py`）
  - quality：`pipelines/quality_pipeline.py`（过渡期委托 `scripts/quality_pipeline.py`）
  - online：`pipelines/online_pipeline.py`（过渡期委托 `scripts/online_pipeline.py`）
  - **公共库**：`pipelines/lib/`（probe/mux/burn 已抽离，避免重复 bug）

## Windows 打包的关键约定（当前实践）

- **安装包不内置模型**：模型通过 `models_pack.zip` 在 App 内导入
- **安装包不内置 Ollama**：通过 `ollama_pack.zip` 在 App 内导入
- **质量模式重依赖独立 exe**：`quality_worker.exe`（与 `backend_server.exe` 分离）
- **唯一对外入口脚本**：`packaging/scripts/windows/build_installer.ps1`

## 重构后的“逐步清理手册”（按步骤删旧文件）

- `docs/refactor/docker_测试-删除-测试_清理手册.md`

## “可清理项”与“不要乱删”

- **基本可安全清理**（不影响代码，会影响历史产物/缓存）
  - `outputs/`、`reports/`
  - `apps/desktop/dist/`、`apps/desktop/dist_electron/`
  - `apps/desktop/node_modules/`（可重装，但很大）
- **不要随便删**
  - `assets/`（包含小资源与模型缓存入口；模型大文件可能外置但目录结构仍要）
  - `bin/`（Windows 打包与运行依赖）
  - `packaging/`（打包入口）

