# Windows 打包待办清单（非开发友好）

> 目标：生成可分发的 Windows 安装包（`.exe`），用户安装后可直接使用（质量模式离线可跑）。

---

## 你需要做的事情（一页式总览）
1. **准备打包机**（一台 Windows 电脑）  
2. **整理资源**（模型、工具、配置、脚本）  
3. **生成后端可执行程序**（`backend_server.exe`）  
4. **准备离线 LLM**（`ollama.exe + ollama_models/`）  
5. **打包成安装包**（Electron Builder）  
6. **验收**（新账号环境测试）

---

## 1. 打包机准备（Windows 电脑）
**最低建议：**
- 内存：16GB（更稳 32GB）
- 磁盘：至少 80–120GB
- 网络：可下载依赖与模型（完成后可断网验收）

**只要确认：**
- Windows 能正常运行
- 有管理员权限（可安装依赖）

---

## 2. 资源清单（必须准备）
**必须有：**
- `bin/ffmpeg.exe`
- `bin/whisper-cli.exe`（或项目内实际 whisper 可执行文件）
- `assets/models/whisperx/`
- `assets/models/tts/`
- `assets/models/mt/`（若离线翻译）
- `config/`（`defaults.yaml` / `quality.yaml`）
- `scripts/`（质量模式脚本）

**质量模式离线 LLM：**
- `ollama.exe`
- `ollama_models/`（模型目录）

**资源获取表（人话版）**
| 资源 | 从哪里拿 | 放到哪里 | 一句话说明 |
| --- | --- | --- | --- |
| `bin/ffmpeg.exe` | 项目内 `bin/`，或下载 Windows 版 ffmpeg | `bin/` | 成片合成必需工具 |
| `bin/whisper-cli.exe` | 项目内 `bin/`，或 whisper.cpp Windows 可执行文件 | `bin/` | 识别引擎必需工具 |
| `assets/models/whisperx/` | 在 Windows 上跑一次质量模式自动下载，或从已可跑机器拷贝 | `assets/models/whisperx/` | 质量模式识别模型 |
| `assets/models/tts/` | 从已可跑机器拷贝（Piper/Coqui） | `assets/models/tts/` | 配音模型 |
| `assets/models/mt/` | 需要离线翻译时下载/拷贝 | `assets/models/mt/` | 离线翻译模型 |
| `config/defaults.yaml` / `config/quality.yaml` | 项目自带 | `config/` | 基线配置与质量配置 |
| `scripts/` | 项目自带 | `scripts/` | 质量模式流水线脚本 |
| `ollama.exe` | Windows 版 Ollama | 安装包资源目录 | 质量模式 LLM 服务 |
| `ollama_models/` | 用 `ollama pull` 下载后拷贝 | 安装包资源目录 | 离线模型文件夹 |

---

## 3. 生成后端可执行程序（backend_server.exe）

**目标：**用户电脑不需要安装 Python 也能跑后端。  
**产物：**`backend_server.exe`

**要做的事：**
- 在 Windows 打包机上安装 Python
- 安装后端依赖（含 WhisperX）
- 用 PyInstaller 打包 `backend/app.py`

**验收方式：**
- 双击运行 `backend_server.exe`
- 浏览器访问 `http://127.0.0.1:5175/api/health`
- 看到 `ok` 即成功

---

## 4. 准备离线 LLM（质量模式必须）
**目标：**断网也能跑质量模式。

**要准备：**
- `ollama.exe`
- `ollama_models/`（模型目录）

**关键点：**
- 运行时设置 `OLLAMA_MODELS=<安装包内模型目录>`
- App 启动时自动运行 `ollama serve`

---

## 5. 打包成 Windows 安装包
**你要打进安装包的内容：**
- 前端构建产物
- `backend_server.exe`
- `scripts/`、`config/`、`assets/`、`bin/`
- `ollama.exe` + `ollama_models/`

**最终产物：**
- `*.exe` 安装包

---

## 6. 验收清单（非开发也能做）
- 能安装、能打开
- 系统页显示“后端可用”
- 导入 20~40 秒视频能跑通
- 产物目录至少有：`chs.srt`、`eng.srt`、`output_en_sub.mp4`
- 断网后仍能跑质量模式

---

## 7. 不需要打包的内容（减少体积）
- `outputs/`（运行产物）
- `reports/`、`eval/`（评测）
- `docs/`（文档）
- `frontend/src/`、`frontend/node_modules/`（源码/依赖）
- `backend/` Python 源码（已被 exe 替代）

---

## 你只需要记住的三句话
1. **后端必须变成 exe**  
2. **模型和工具必须随包带走**  
3. **前端启动时必须自动拉起后端和 LLM**

