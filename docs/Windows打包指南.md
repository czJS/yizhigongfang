# Windows 打包指南（非开发友好）

## 这份文档给谁
给需要在 **Windows 电脑**上打包、分发“译制工坊”的同学。  
目标是：**用户不装 Python、不装模型、不装工具，直接安装即可用**。

---

## 你将得到什么
- 一个 `*.exe` 安装包  
- 安装后双击即可使用（质量模式可离线跑）

---

## 先说明白的现实
- **包会很大**：常见 20GB～100GB+（取决于模型）
- **必须打包后端引擎**：否则用户电脑无法运行
- **必须把模型和工具带进安装包**：否则会报“缺模型/缺 ffmpeg”

---

## 打包前准备（Windows 机器）

### 必备清单（只检查“是否存在”）
- `bin/ffmpeg.exe`
- `bin/whisper-cli.exe`（或项目内实际 whisper 可执行文件）
- `assets/models/whisperx/`（质量模式必需）
- `assets/models/tts/`（Piper/Coqui 模型）
- `assets/models/mt/` 或 HF 缓存（如果要离线翻译）
- `config/defaults.yaml`、`config/quality.yaml`
- `scripts/`（质量模式脚本在此）

---

## 哪些必须打包 / 哪些不需要

### 必须打包（缺一就跑不起来）
- **前端产物**：Electron 构建后的界面（打包时自动带）
- **后端可执行程序**：`backend_server.exe`
- **脚本**：`scripts/`（质量模式运行脚本）
- **配置**：`config/`（模式默认参数）
- **工具**：`bin/`（ffmpeg/whisper 等）
- **模型**：`assets/models/`（WhisperX、TTS、翻译模型）
- **离线 LLM（质量模式）**：`ollama.exe` + `ollama_models/`

### 不需要打包（打了只会变大）
- `outputs/`：运行产物目录
- `reports/`、`eval/`：评测与报告
- `docs/`：文档
- `frontend/src/`：前端源码
- `frontend/node_modules/`：编译已包含
- `backend/` 源码：已被 `backend_server.exe` 替代

**原因（人话版）**  
- “必须打包”的是**运行引擎 + 必需模型 + 运行配置**  
- “不需要打包”的是**源码、文档、评测、历史产物**

---

## 步骤 1：把后端打成可执行程序（Windows）
目标：生成 `backend_server.exe`，用户电脑不需要 Python。

**你要做的事情：**
1. 用 Windows 机器安装 Python（只在打包机上）
2. 安装后端依赖（含 WhisperX）
3. 用 PyInstaller 打包 `backend/app.py`
4. 验证能访问 `/api/health`

**验证方法（不懂原理也能做）：**
- 启动 `backend_server.exe`
- 打开浏览器访问：`http://127.0.0.1:5175/api/health`
- 看到 `ok` 即成功

---

## 步骤 2：让桌面 App 自动启动后端
用户只打开一个应用，不需要手动开后端。

**必须做的动作：**
- App 启动时自动拉起 `backend_server.exe`
- 健康检查：`/api/health` 返回 ok
- 端口被占用时给出提示或换端口
- 后端日志落盘（便于排错）

---

## 步骤 3：内置 LLM（质量模式离线）
质量模式依赖本地 LLM，必须随包带走。

**需要准备：**
- `ollama.exe`
- `ollama_models/`（模型文件夹）

**运行时要求：**
- 设置 `OLLAMA_MODELS=<安装包内模型目录>`
- 自动启动 `ollama serve`

---

## 步骤 4：用 Electron Builder 打包 Windows 安装包

**你要打进去的内容：**
- 前端构建产物
- `backend_server.exe`
- `scripts/`、`config/`、`assets/`、`bin/`
- `ollama.exe` + `ollama_models/`

**打包完成后你会得到：**
- `*.exe` 安装包

---

## 步骤 5：验收（非开发也能做）
请用“新电脑/新账号”验收，避免本机环境干扰。

**验收清单：**
- 能安装、能打开
- 系统页显示“后端可用”
- 导入 20~40 秒视频能跑通
- 产物目录至少有：`chs.srt`、`eng.srt`、`output_en_sub.mp4`
- 断网后仍能跑质量模式

---

## 常见问题（最常见的三类）

### 1) 提示缺模型 / 缺工具
- 多数是 `bin/`、`assets/` 没打进包  
- 或路径注入失败（资源路径没传给后端）

### 2) 质量模式无法启动
- `ollama.exe` 未打进包  
- 或 `OLLAMA_MODELS` 没设置到安装包内目录

### 3) 打包能装但无法运行
- 后端没打成 `backend_server.exe`
- 或后端没有随 App 自动启动

---

## 你只需要记住的三句话
1. **后端必须变成 exe**  
2. **模型和工具必须随包带走**  
3. **前端启动时必须自动拉起后端和 LLM**

