# 质量模式交付与 Windows 打包

> 本文只覆盖**质量模式第一阶段**的 Windows 打包与交付。  
> macOS 不是当前阶段主线，Docker 也不是产品交付形态。

---

## 1. 最终交付物

当前 Windows 质量模式交付是三件套：

- 安装包：`apps/desktop/dist_electron/YizhiStudio-*.exe`
- 模型包：`apps/desktop/dist_electron/models_pack.zip`
- Ollama 包：`apps/desktop/dist_electron/ollama_pack.zip`

补充说明：

- 安装包不内置模型
- 安装包不内置 Ollama
- 质量模式重依赖拆成独立 `quality_worker.exe`

---

## 2. Windows 打包态的关键组成

质量模式在 Windows 打包态依赖以下部分共同闭环：

- `backend_server.exe`
- `quality_worker.exe`
- `models_pack.zip`
- `ollama_pack.zip`
- 本地 `ollama.exe`
- `configs/*.yaml`
- `bin/ffmpeg.exe`、`bin/whisper-cli.exe` 等运行工具

---

## 3. 打包机最小准备

### 3.1 必装软件

- Git
- Python 3.11
- Node.js 20.x
- 7-Zip

### 3.2 建议准备的目录

- `D:\temp\yizhistudio\`
- `D:\cache\pip\`
- `D:\cache\npm\`
- `D:\cache\electron\`
- `D:\cache\electron-builder\`

### 3.3 打包机上常见硬编码路径

这些路径现在都可以通过脚本参数或环境变量覆盖，不再要求打包机必须与某一台历史机器完全同构。

常见默认值仍然是：

- Node：`D:\tools\node-v20.11.1-win-x64`
- 后端 venv：`D:\tools\venvs\yizhi-backend`
- quality worker venv：`D:\tools\venvs\yizhi-quality`
- Ollama 包来源：`D:\tools\ollama`
- Ollama 模型来源：`D:\tools\ollama_models`

可覆盖方式：

- `YGF_NODE_DIR`
- `YGF_BACKEND_VENV`
- `YGF_QUALITY_VENV`
- `YGF_TEMP_ROOT`
- `YGF_PIP_CACHE_DIR`
- `YGF_NPM_CACHE_DIR`
- `YGF_ELECTRON_CACHE_DIR`
- `YGF_ELECTRON_BUILDER_CACHE_DIR`
- `YGF_BUILD_KEEP_DIR`
- `YGF_OLLAMA_MODELS_DIR`

---

## 4. 打包命令

推荐顺序：

```powershell
cd D:\yizhigongfang-main\yizhigongfang-git

powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\rebuild_backend_server_exe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\rebuild_quality_worker_exe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\scripts\build_installer.ps1
```

唯一 canonical spec 路径：

- `packaging/windows/pyinstaller/backend_server.spec`
- `packaging/windows/pyinstaller/quality_worker.spec`

仓库根目录下的 `backend_server.spec` / `quality_worker.spec` 仅保留为兼容包装层，不再作为维护入口。

常用变体：

- 只重打安装包：`build_installer.ps1 -SkipModelsPack`
- 只重打模型包：`build_installer.ps1 -SkipDist -ForceModelsPack`
- 单独生成 Ollama 包：`build_ollama_pack.ps1`

---

## 5. 质量模式模型约定

质量模式当前默认会用到两类本地 LLM 模型：

- 翻译 / 改写模型：`llm_model`
- 短语识别模型：`zh_phrase_llm_model`

当前默认组合偏向：

- `qwen3.5:9b`

当前建议分工：

- `llm_model`：`qwen3.5:9b`
- `zh_phrase_llm_model`：`qwen3.5:9b`

当前产品口径是统一模型，不再把短语识别拆到单独的 `4b` 支路。
当前产品口径也只保留 Ollama OpenAI 兼容 `/v1` 接口，不再维护 native API 分支。

---

## 6. 打包后最小自检

建议对 `win-unpacked/resources` 做秒级自检：

1. `backend_server.exe --self-check`
2. `quality_worker.exe --self-check`
3. 运行 `verify_win_unpacked_smoke.ps1`

通过标准：

- 后端自检通过
- worker 自检通过
- 输出资源目录完整

---

## 7. 当前最常见失败点

- PowerShell 执行策略阻止脚本运行
- Defender 锁文件或打包产物被占用
- `models_pack.zip` / `ollama_pack.zip` 中途丢失
- 打包机 Node / Python / 7z 路径不一致
- 模型来源目录或 Ollama 模型目录缺失

---

## 8. 发版前最后确认

打包完成后，不应只看“能否产出 exe”，还应确认：

- 质量模式能被前端正确识别为可用
- 模型包导入链路正常
- Ollama 包导入与启动链路正常
- 至少一条真实素材可完整跑通
- `quality_report.passed == true`

更详细的发版判断，见 `质量门禁与测试.md`。
