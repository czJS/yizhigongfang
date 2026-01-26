# Windows 打包指南（v2，唯一入口）

> 目标：在 Windows 上产出可分发的 **安装包**（`YizhiStudio-*.exe`），并配套产出/复用 **模型包**（`models_pack.zip`）与 **Ollama 包**（`ollama_pack.zip`）。

## 0. 交付物（你最终要给别人什么）

- **安装包**：`apps/desktop/dist_electron/YizhiStudio-*.exe`
- **模型包**：`apps/desktop/dist_electron/models_pack.zip`（App 内导入）
- **Ollama 包（质量模式用）**：`apps/desktop/dist_electron/ollama_pack.zip`（App 内导入）

重要约定（当前实践）：

- **安装包不内置模型**（体积太大），模型走 `models_pack.zip`
- **安装包不内置 Ollama**（避免安装极慢），Ollama 走 `ollama_pack.zip`
- 质量模式重依赖已拆分为独立 exe：`quality_worker.exe`（会随安装包进 `resources/`）

## 1. 打包机准备（新机器请先看）

新机器 clone 后缺什么、放哪儿：请看 `docs/packaging/windows_prereqs.md`。

## 2. 打包命令（唯一对外入口）

在 **系统 PowerShell**（更稳）执行：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git

powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\rebuild_backend_server_exe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\rebuild_quality_worker_exe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\build_installer.ps1
```

常用变体：

- **只重打安装包**（推荐日常迭代，跳过模型包）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\build_installer.ps1 -SkipModelsPack
```

- **只重打模型包**（不重打安装包）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\build_installer.ps1 -SkipDist -ForceModelsPack
```

- **单独生成 Ollama 包**（质量模式用，独立交付）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\build_ollama_pack.ps1
```

## 3. 产物位置（打完后你应该看到）

- 安装包：`apps/desktop/dist_electron/YizhiStudio-*.exe`
- 模型包：`apps/desktop/dist_electron/models_pack.zip`
- Ollama 包：`apps/desktop/dist_electron/ollama_pack.zip`
- 解包目录（验收用）：`apps/desktop/dist_electron/win-unpacked/`

## 4. 打包后“秒级自检”（强烈建议）

不运行安装器，直接对 `win-unpacked/resources` 做快速自检：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\scripts\windows\verify_win_unpacked_smoke.ps1
```

通过标准：

- `backend_server.exe --self-check` 通过
- `quality_worker.exe --self-check` 通过（尤其 `TTS/jamo.data` 等依赖项）
- 最后输出 `[smoke] OK`

## 5. 常见失败点（按频率）

- **PowerShell 禁止运行脚本（ExecutionPolicy）**
  - 用 `powershell -ExecutionPolicy Bypass -File <script.ps1>` 执行
- **`models_pack.zip / ollama_pack.zip` “消失”**
  - `build_installer.ps1` 会先备份到 `D:\temp\yizhistudio\build_keep`；中途失败会导致未恢复；从该目录拷回即可
- **打包报 `Aborted` / 文件被占用**
  - 关掉正在运行的 App/安装器/后端进程，并把仓库与缓存目录加入 Defender 排除项

