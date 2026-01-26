# Windows 打包机准备清单（clone 后还缺什么）

这份文档回答一个问题：**把仓库 clone 到一台新的 Windows 电脑后，还缺哪些东西，应该放到哪里，才能成功打包？**

> 适用仓库：`D:\yizhigongfang-main\yizhigongfang-git`（脚本里默认使用此路径；你也可以改脚本参数/常量来适配你的路径）

---

## 0. 必须理解的交付形态（否则会找错东西）

当前 Windows 交付是 3 件套：

- **安装包**：`frontend/dist_electron/YizhiStudio-*.exe`（不包含模型、不包含 Ollama）
- **模型包**：`frontend/dist_electron/models_pack.zip`（App 内导入）
- **Ollama 包**：`frontend/dist_electron/ollama_pack.zip`（App 内导入，质量模式需要）

---

## 1. 需要安装的软件（新机器必须）

- **Git**：用于 clone 仓库
- **Python 3.11（推荐）**：用于 PyInstaller 打包（后端/quality worker）
  - 要求：`python` 在命令行可用（`python -V` 有输出）
- **Node.js 20.x**：用于 Electron 前端打包
- **7-Zip**：用于生成大体积 `models_pack.zip`（没有也许能跑，但很容易失败/慢）
- （可选但推荐）**Windows Defender 排除项**：见第 3 节

---

## 2. 必须存在的目录（脚本默认写死在 D 盘）

这些目录不存在会自动创建一部分，但建议你提前准备，避免权限/磁盘写入问题：

- `D:\temp\yizhistudio\`（脚本把 TEMP/TMP 指向这里，避免写爆 C 盘）
- `D:\cache\pip\`
- `D:\cache\npm\`
- `D:\cache\electron\`
- `D:\cache\electron-builder\`

---

## 3. 强烈建议：Defender 排除项（不然容易 Aborted/锁文件）

建议添加排除项（至少这些）：

- `D:\yizhigongfang-main\`
- `D:\temp\yizhistudio\`
- `D:\cache\`
- `C:\Users\<你的用户名>\.cursor\`（如果你用 Cursor 打包）

常见现象：

- 打包/复制 exe 时提示 `Aborted`、`Permission denied`、资源文件被占用

---

## 4. 关键“硬编码路径”检查（新机器最常缺这一步）

项目打包脚本里有一些默认路径（你可以二选一：**按默认路径准备**，或**修改脚本参数/常量**）。

### 4.1 Node 路径（前端打包）

脚本：`scripts/build_installer.ps1` 默认：

- `D:\tools\node-v20.11.1-win-x64`

你需要做到其一：

- 把 Node 解压到上面路径；或
- 修改 `scripts/build_installer.ps1` 里的 `$nodeDir` 为你机器上的 Node 路径；或
- 确保系统 PATH 已包含 node/npm，并把脚本里 `$nodeDir` 逻辑改成“可选”。

### 4.2 Python venv 路径（后端打包）

脚本默认 venv：

- 后端：`D:\tools\venvs\yizhi-backend`（`scripts/rebuild_backend_server_exe.ps1`）
- 质量 worker：`D:\tools\venvs\yizhi-quality`（`scripts/rebuild_quality_worker_exe.ps1`）

如果你不想用 D 盘路径，可以直接在执行时传参（脚本支持 `-VenvPath` / `-RepoRoot`），或把脚本头部默认值改掉。

### 4.3 7z 路径（模型包）

脚本会按顺序找：

- `7z`（PATH 里）
- `D:\7-Zip\7z.exe`
- `C:\Program Files\7-Zip\7z.exe`
- `C:\Program Files (x86)\7-Zip\7z.exe`

保证其中一个存在即可。

---

## 5. “资源文件”必须在仓库里的哪个位置

### 5.1 安装包会带走（resources/ 下）

必须存在于仓库根目录：

- `dist/backend_server.exe`（由 `rebuild_backend_server_exe.ps1` 生成）
- `dist/quality_worker.exe`（由 `rebuild_quality_worker_exe.ps1` 生成）
- `bin/ffmpeg.exe`
- `bin/whisper-cli.exe`（或项目当前使用的 whisper 可执行文件）
- `config/*.yaml`
- `scripts/*.py`（质量流水线脚本）

> 注意：安装包不会带 `assets/models/**`，模型走 models_pack.zip。

### 5.2 模型包（models_pack.zip）的来源

打包机需要有可用模型缓存（脚本会从仓库的 `assets/models/` 组装模型包）：

- `assets/models/whisperx/`
- `assets/models/tts/`
- （可选）`assets/models/mt/`

如果新机器没有这些模型目录，你需要从“已可跑机器”复制到对应路径，再生成 `models_pack.zip`。

### 5.3 Ollama 包（ollama_pack.zip）的来源

脚本会从以下目录打包（默认约定）：

- `D:\tools\ollama` → 生成 `frontend/dist_electron/ollama_pack.zip`

如果新机器没有这个目录：

- 你需要安装/解压 Windows 版 Ollama 到此目录，或修改 `scripts/build_ollama_pack.ps1` 里的源路径。

---

## 6. 最小验证（新机器上，先验证环境再打包）

在仓库根目录执行：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git

# 1) 后端 exe
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rebuild_backend_server_exe.ps1

# 2) 质量 worker exe
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\rebuild_quality_worker_exe.ps1

# 3) 安装包（可先跳过模型包）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 -SkipModelsPack
```

如果 3 步都能跑通，说明“打包机环境”基本齐全。

