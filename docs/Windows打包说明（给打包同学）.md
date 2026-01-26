# Windows 打包说明（给打包同学）

> 目标：产出可分发的 Windows 安装包（`YizhiStudio-*.win.exe`）+ 模型包（`models_pack.zip`）+ Ollama 包（`ollama_pack.zip`）。  
> 交付给用户后：用户无需 Python，可运行后端 exe；模型通过 `models_pack.zip` 导入；质量模式需要额外导入 `ollama_pack.zip`（独立入口）。

---

## 0. 最终交付物（必须一起给）

- **安装包**：`frontend/dist_electron/YizhiStudio-*.win.exe`
- **模型包**：`frontend/dist_electron/models_pack.zip`
- **Ollama 包（质量模式用）**：`frontend/dist_electron/ollama_pack.zip`

> 注意：
> - `electron-builder.yml` 会排除 `assets/models/**`，因此模型不会进安装包，必须单独交付模型包。
> - 为避免安装器“解压超大 GPU DLL 导致卡安装”，**安装包不再内置 Ollama**，改为独立导入包。
> - 质量模式的重依赖（WhisperX / torch / pyannote 等）已从主后端拆分为独立可执行文件：`quality_worker.exe`，会随安装包进入 `resources/`（无需单独交付）。

---

## 1. 打包前检查清单（必须满足）

### 1.1 后端 exe

- 需要存在：`dist/backend_server.exe`
- 且能在 Windows 上运行并通过健康检查：
  - `http://127.0.0.1:5175/api/health`

### 1.1.1 质量模式 worker exe（必须）

- 需要存在：`dist/quality_worker.exe`
- 用于质量模式任务执行（依赖隔离，避免与主后端依赖冲突）

### 1.2 工具（会随安装包进入 resources）

- `bin/ffmpeg.exe`
- `bin/whisper-cli.exe`（或实际使用的 whisper 可执行文件）

### 1.3 Ollama（不再随安装包进入 resources）

- 现在改为 **独立交付 `ollama_pack.zip`**，由用户在 App 的“系统页 → Ollama 管理”导入。
- 打包侧不再需要 `pack/ollama/**` 进入安装包。

### 1.4 质量模式模型（会进入 models_pack.zip）

确保 `assets/models/` 下已经有可用的离线缓存（至少包括）：

- `assets/models/whisperx/`（WhisperX 模型缓存）
- `assets/models/tts/`（Coqui/Piper 模型）

> 若你的模型来自某台“已可跑机器”，建议直接拷贝其模型目录到 `assets/models/` 对应位置后再打包。

### 1.5 离线 LLM 模型（强烈建议）

脚本会尝试把 `D:\tools\ollama_models` 打进 `models_pack.zip`：

- 若 **不存在**：模型包里不会带 LLM 模型，用户离线质量模式会失败（只能在线 pull）
- 若 **存在**：会把 `ollama_models/` 目录打进模型包

---

## 2. 关键配置（避免“装完不能跑质量模式”）

### 2.1 Windows 安装包场景的 LLM Endpoint

质量模式在 Docker 场景常用：

- `http://ollama:11434/v1`

但 Windows 本机安装包场景应使用：

- `http://127.0.0.1:11434/v1`

安装包场景已在 Electron 主进程里注入 `YGF_LLM_ENDPOINT=http://127.0.0.1:11434/v1`，后端会优先使用该环境变量，因此**无需修改** `config/quality.yaml`（保留 Docker 开发默认值即可）。

---

## 3. 打包命令（官方脚本）

在 Windows 打包机 PowerShell 执行：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
.\scripts\rebuild_backend_server_exe.ps1
.\scripts\rebuild_quality_worker_exe.ps1
.\scripts\build_installer.ps1
```

单独生成 Ollama 包（支持增量；若源文件没变则自动跳过）：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
.\scripts\build_ollama_pack.ps1
```

只重打模型包（不重打安装包）：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
.\scripts\build_installer.ps1 -SkipDist -ForceModelsPack
```

只重打安装包并跳过模型包（不触碰 `models_pack.zip`）：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
.\scripts\build_installer.ps1 -SkipModelsPack
```

脚本关键逻辑：

- `npm run dist` → 生成 `frontend/dist_electron/YizhiStudio-*.win.exe`
- `robocopy assets/models -> frontend/dist_electron/models_pack` → 再压缩为 `models_pack.zip`
- 若存在 `D:\tools\ollama_models`，会额外复制到 `models_pack/ollama_models`
 - `build_ollama_pack.ps1`：把 `D:\tools\ollama` 打成 `frontend/dist_electron/ollama_pack.zip`（独立导入）
 - `rebuild_quality_worker_exe.ps1`：用独立 venv 打出 `dist/quality_worker.exe`（质量模式任务会优先调用它）

---

## 4. 产物位置（打完后你应该看到）

- 安装包：`frontend/dist_electron/YizhiStudio-*.win.exe`
- 模型包：`frontend/dist_electron/models_pack.zip`
- Ollama 包：`frontend/dist_electron/ollama_pack.zip`
- 解包目录（调试用）：`frontend/dist_electron/win-unpacked/`

> `win-unpacked/resources/` 下应能看到：`backend_server.exe`、`quality_worker.exe`、`bin/`、`config/`、`scripts/`、`assets/（不含 models）`  
> **不应再包含** `ollama/`（否则安装包会变大且安装变慢）。

---

## 5. 交付验收（推荐最小验收）

### 5.1 “新环境验收”原则

尽量用：

- 新 Windows 用户账号（或新机器）
- 避免本机已有模型缓存/已有服务导致“假通过”

### 5.2 验收步骤

1) 安装 `YizhiStudio-*.win.exe`
2) 打开 App，确认后端可用（或访问 `http://127.0.0.1:5175/api/health`）
3) 在 App 内导入 `models_pack.zip`
4) 在 App 内导入 `ollama_pack.zip`（系统页 → Ollama 管理）
5) 跑一个 15s 质量模式任务（推荐用项目脚本 `scripts/verify_quality_15s_local.ps1`）

### 5.3 打包后“秒级自检”（强烈建议）

为降低“打包一次发现一个问题”的低效循环，建议在 **不运行安装器** 的情况下先对 `win-unpacked` 做快速自检：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
.\scripts\verify_win_unpacked_smoke.ps1
```

它会在 `frontend/dist_electron/win-unpacked/resources` 下直接运行：

- `backend_server.exe --self-check`
- `quality_worker.exe --self-check`

可快速发现：

- `resources/scripts` / `resources/config` / `quality_worker.exe` 是否缺失
- 质量 worker 依赖是否可导入（torch/whisperx/pyannote/omegaconf 等）
- PyTorch 版本升级导致的安全反序列化兼容问题（weights_only）

---

## 6. 常见失败点（高频）

### 6.1 质量模式报 “LLM 连接失败”

原因：

- 未导入 `ollama_pack.zip`（本机没有 Ollama 可执行文件/服务起不来）
- 或模型包里没有 `ollama_models/`（离线时拉不起模型）
- 或 11434 端口被占用

处理：

- 导入 `ollama_pack.zip` 后重启 App
- 确保 `D:\tools\ollama_models` 存在且包含目标模型，再重打模型包
 - 检查 11434 端口占用并释放

### 6.2 模型包导入后仍提示缺模型

原因：

- `models_pack.zip` 目录层级不符合预期（zip 里可能包含多层前缀目录）

处理：

- Electron 端有“自动扁平化”逻辑，但仍建议检查 zip 内的顶层是否能直接看到 `whisperx/tts/ollama_models/`

### 6.3 打包机缺 7-Zip 导致 models_pack.zip 生成失败

脚本依赖 7z（更可靠处理大 zip）。确保：

- `D:\7-Zip\7z.exe` 或 `C:\Program Files\7-Zip\7z.exe` 存在

---

## 7. 建议的交付包命名规范（方便沟通）

- `YizhiStudio-<version>-win.exe`
- `models_pack-<version>-<date>.zip`

