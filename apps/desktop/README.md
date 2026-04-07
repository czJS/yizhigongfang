# 桌面端（`apps/desktop/`）

这里是桌面应用（Electron + React）的源码目录，也是 v2 结构下的**前端真入口**。

- **当前实现位置**：桌面端源码位于 `apps/desktop/`（旧目录 `frontend/` 已删除）。
- **开发联调**：通过 HTTP 调用后端（默认 `http://127.0.0.1:5175`）。
- **云端认证服务地址**：默认读取 `apps/desktop/src/authApi.ts` 中的 `DEFAULT_AUTH_API_BASE`，当前默认值已切到 `https://auth.miaoyichuhai.com`，也支持通过 `VITE_AUTH_API_BASE` 或本地存储 `ygf_auth_api_base` 覆盖。
- **Docker 前端开发**：见 `docker/docker-compose.yml` 的 `frontend` service（容器内只跑 Vite，不运行 Electron）。
- **Windows 打包入口**：`packaging/windows/scripts/build_installer.ps1`（安装包产物在 `apps/desktop/dist_electron/`）。

> 说明：仓库仍处于“目录迁移收敛期”，但 v2 运行与打包已以 `apps/desktop/` 为准。

