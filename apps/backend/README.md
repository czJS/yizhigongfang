# 后端（`apps/backend/`）

v2 结构下后端代码已迁移到 `apps/backend/backend/`（物理位置），对外保持 `backend.*` 的 import 路径不变。

- **真实实现位置（源码）**：`apps/backend/backend/`
- **对外 import 路径**：`backend.*`
- **Docker 入口**：容器内仍运行 `python -m backend.app`（将 `apps/backend/backend` 挂载为 `/app/backend`）
- **Windows 打包入口**：`backend_server.exe`（spec：`packaging/windows/pyinstaller/backend_server.spec`）

后续建议（可选）：

- 若未来希望进一步收敛导入路径，可以逐步把内部 `from backend.xxx import ...` 的公共逻辑继续下沉到 `core/` 或 `pipelines/lib/`（保持打包与 Docker 自检通过为前提）。

