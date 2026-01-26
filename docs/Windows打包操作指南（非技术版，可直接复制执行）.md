# Windows 打包操作指南（非技术版，可直接复制执行）

> 目标：在 Windows 上生成 **安装包**（不包含模型包、不包含 Ollama 包）。  
> 适用目录：`D:\yizhigongfang-main\yizhigongfang-git`  
> 输出文件：`frontend\dist_electron\releases\YizhiStudio-*.exe`

---

## 0. 你需要准备什么（一次性准备）

### 0.1 必备软件

- Windows 10/11
- PowerShell（系统自带）
- Python 3.x（已装即可）
- Node.js（已装即可，用于前端打包）

> 如果你不确定是否安装：先跳过，直接按后面的命令跑；缺什么它会报错。

### 0.2 建议准备的目录（避免写 C 盘）

请确保以下目录存在（没有就创建文件夹即可）：

- `D:\temp\yizhistudio\`
- `D:\cache\`

### 0.3（强烈建议）把这些目录加入 Windows Defender 排除项

在 Defender 的“排除项”里加入：

- `D:\cursor\`（如果你是把 Cursor 装在这里）
- `C:\Users\你的用户名\.cursor\`
- `D:\yizhigongfang-main\`
- `D:\temp\yizhistudio\`
- `D:\cache\`

> 目的：避免打包/更新被“文件占用/锁文件”打断，出现 `Aborted` 或更新失败。

---

## 1. 每次打包前都要做的事（30 秒）

### 1.1 关闭程序，避免文件被占用

请先退出：

- YizhiStudio
- 任何正在跑的安装程序
-（建议）Cursor 里正在跑的终端任务

### 1.2 一键结束可能占用的进程（复制执行）

打开 **系统 PowerShell**（不要用 Cursor 内置终端更稳），执行：

```powershell
taskkill /IM YizhiStudio.exe /T 2> $null
taskkill /F /IM YizhiStudio.exe /T 2> $null
taskkill /IM backend_server.exe /T 2> $null
taskkill /F /IM backend_server.exe /T 2> $null
taskkill /IM quality_worker.exe /T 2> $null
taskkill /F /IM quality_worker.exe /T 2> $null
taskkill /IM ollama.exe /T 2> $null
taskkill /F /IM ollama.exe /T 2> $null
```

---

## 2. 打包（跳过模型包 + 跳过 Ollama 包）

> 这套流程会：
>
> - 重打 `backend_server.exe`
> - 重打 `quality_worker.exe`
> - 重打安装包（`-SkipModelsPack`：不生成/不触碰 `models_pack.zip`）

### 2.1 进入项目目录（复制执行）

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git
```

### 2.2 重打后端 `backend_server.exe`（复制执行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\yizhigongfang-main\yizhigongfang-git\scripts\rebuild_backend_server_exe.ps1" -RepoRoot "D:\yizhigongfang-main\yizhigongfang-git"
```

执行成功后你应该能看到（路径存在即可）：

- `D:\yizhigongfang-main\yizhigongfang-git\dist\backend_server.exe`

### 2.3 重打质量 worker `quality_worker.exe`（复制执行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\yizhigongfang-main\yizhigongfang-git\scripts\rebuild_quality_worker_exe.ps1" -RepoRoot "D:\yizhigongfang-main\yizhigongfang-git"
```

执行成功后你应该能看到：

- `D:\yizhigongfang-main\yizhigongfang-git\dist\quality_worker.exe`

### 2.4 重打安装包（跳过模型包）（复制执行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\yizhigongfang-main\yizhigongfang-git\scripts\build_installer.ps1" -SkipModelsPack
```

执行成功后你应该能看到：

- `D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\releases\YizhiStudio-*.exe`

---

## 3. 打完后的最小自检（可选但推荐）

### 3.1 检查安装包是否带了必要文件（不用安装也能看）

打开目录：

- `D:\yizhigongfang-main\yizhigongfang-git\frontend\dist_electron\win-unpacked\resources\`

确认至少包含：

- `backend_server.exe`
- `quality_worker.exe`
- `bin\ffmpeg.exe`
- `config\`
- `scripts\`

---

## 4. 常见问题与处理（照抄执行）

### 4.1 打包命令报 `Command failed to spawn: Aborted`

含义：命令还没开始跑就被系统/安全软件/进程状态中止了（不是脚本本身报错）。

处理顺序（从快到稳）：

1) 确认你是在 **系统 PowerShell** 里跑（不要在 Cursor 内）
2) 重新执行“1.2 一键结束进程”
3) 确认 Defender 排除项已加（尤其 `D:\yizhigongfang-main\`、`D:\temp\yizhistudio\`、`D:\cache\`）
4) 重启电脑后再跑

### 4.2 安装包安装时报“无法写入 …\Temp\ns*.tmp\modern-wizard.bmp”

含义：安装器解压临时文件时写 C 盘 TEMP 失败（权限/空间不足）。

处理：先清理 C 盘临时残留（用项目脚本，复制执行）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\yizhigongfang-main\yizhigongfang-git\scripts\cleanup_windows_c_drive_residue.ps1" -Force
```

再重新安装（并尽量保证 C 盘有空间）。

### 4.3 C 盘突然变成 0 字节

常见原因：

- 安装器/临时文件大量堆在 `C:\Users\xxx\AppData\Local\Temp\`
- 之前失败的安装/解压残留 `ns*.tmp`

处理：直接跑清理脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\yizhigongfang-main\yizhigongfang-git\scripts\cleanup_windows_c_drive_residue.ps1" -Force
```

---

## 5. 产物交付给别人时给哪些文件

本次“跳过模型包 + 跳过 Ollama 包”的交付物只有：

- **安装包**：`frontend\dist_electron\releases\YizhiStudio-*.exe`

> 注意：不包含 `models_pack.zip` / `ollama_pack.zip` 时，用户需要自己另行准备模型与 Ollama（否则质量模式无法离线完整运行）。

