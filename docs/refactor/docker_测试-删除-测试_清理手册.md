# Docker 驱动的“测试 → 删除 → 测试 → 删除”清理手册（逐步完成 v2 重构）

> 目标：在 **保留 Docker 开发能力** 的前提下，逐步删除旧目录/旧入口，并且每一步都有可复现的验证手段与回滚方式。

## 总原则（一定要遵守）

- **一次只做一类删除**：删完立刻测，测不过立刻回滚，不要叠加多步。
- **先“禁止兜底”再物理删除**：凡是存在 `legacy fallback` 的地方，先让系统在兜底发生时直接报错或打印明显告警，确认不会走兜底，再删旧目录。
- **Docker 验证优先**：你要求保留 Docker，因此每一步以 `docker/docker-compose.yml` 的验证作为硬标准。

## 0. 基线：确认你用的是 v2 Docker 入口（必须先做）

### 0.1 构建镜像

在仓库根目录执行：

```bash
docker build -t yzh-backend:quality -f docker/Dockerfile .
```

### 0.2 启动服务

```bash
docker compose -f docker/docker-compose.yml up -d ollama backend
```

### 0.3 基线验证（必须全部通过）

- **健康检查**（宿主机）：

```bash
curl http://localhost:5175/api/health
```

期待：返回 `{"status":"ok"}` 或类似 ok。

- **后端自检**（容器内）：

```bash
docker compose -f docker/docker-compose.yml exec backend python -m backend.app --self-check
```

期待：输出 JSON 且 `"ok": true`，并列出缺失项为空或可接受。

> 如果这里不过，先不要进入“删除”阶段。

---

## 1. 第 1 轮（低风险）：清理旧文档入口

### 1.1 删除目标

- `docs/Windows打包指南.md`
- `docs/Windows打包机准备清单（clone后还缺什么）.md`

> 新入口分别为：`docs/packaging/windows.md` 与 `docs/packaging/windows_prereqs.md`

### 1.2 验证

此轮不涉及运行时；验证方式：

- 确认新文档存在且内容完整
- 在团队常用入口（README/群公告/收藏）里把链接替换为新路径

### 1.3 回滚

从 git 历史恢复两份文档即可（无运行时副作用）。

---

## 2. 第 2 轮（中风险）：把 Docker 前端开发入口切到 `apps/desktop`

> 目的：最终允许你删除旧的 `frontend/` 目录（但**不是这一轮就删**）。

### 2.1 当前状态

- Docker 前端服务现在应使用：`docker/docker-compose.yml` 中的 `frontend` service
- 其 `working_dir` 应为：`/app/apps/desktop`

### 2.2 验证

启动前端服务：

```bash
docker compose -f docker/docker-compose.yml up -d frontend
```

宿主机打开：

- 前端：`http://localhost:5176`
- 后端：`http://localhost:5175/api/health`

期待：

- 前端页面可打开
- 能正常调用后端（至少能看到健康状态/配置或能启动任务）

### 2.3 删除目标（本轮不删 `frontend/`）

本轮不做物理删除，只确认 docker 前端不再依赖 `frontend/`。

---

## 3. 第 3 轮（高价值）：删除根目录旧 Docker 入口（可选）

### 3.1 删除目标（可选）

- 根目录 `docker-compose.yml`
- 根目录 `Dockerfile`

### 3.2 先做“禁用旧入口”的测试（推荐）

在团队内统一约定只使用：

- `docker/docker-compose.yml`
- `docker/Dockerfile`

并用以下命令确保流程可用：

```bash
docker build -t yzh-backend:quality -f docker/Dockerfile .
docker compose -f docker/docker-compose.yml up -d --remove-orphans
docker compose -f docker/docker-compose.yml exec backend python -m backend.app --self-check
```

通过后再删根目录两个文件。

### 3.3 回滚

从 git 恢复根目录 `Dockerfile/docker-compose.yml`。

---

## 4. 第 4 轮（高风险）：去掉 `config/`（只保留 `configs/`）

> 注意：Docker 目前同时挂载了 `./configs` 和 `./config`，并且镜像也同时 `COPY config` 与 `COPY configs`。  
> 因此删除 `config/` **必须** 先做“禁用兜底”验证与 Dockerfile/compose 的同步修改。

### 4.1 本轮目标

- Docker compose 不再挂载 `./config:/app/config`
- 镜像构建不再 `COPY config /app/config`
- 代码仅使用 `configs/`（`config/` 不再作为兜底）

### 4.2 验证步骤（删之前）

1) 修改 `docker/docker-compose.yml`：
   - 移除 `./config:/app/config:ro` 与任何 `/app/config` 的引用
2) 修改 `docker/Dockerfile`：
   - 移除 `COPY config /app/config`
3) 启动并验证（同 0.2、0.3）

### 4.3 物理删除

验证通过后删除：

- `config/`

### 4.4 回滚

恢复 `config/` 目录，并把 compose/Dockerfile 的改动回滚即可。

---

## 5. 第 5 轮（暂不可删）：删除 `scripts/`（需要先把 pipelines 从“薄封装”变成“真入口”）

### 5.1 为什么现在不能删

当前 `pipelines/*.py` 仍是“薄封装”，会委托执行 `scripts/*.py`（通过 `runpy.run_path`）。  
Docker 评测/跑分脚本也直接从 `/app/scripts/*.py` 执行。

### 5.2 要达到可删 `scripts/`，你需要先完成的迁移

- 把 `scripts/asr_translate_tts.py` 的真实实现迁移到 `pipelines/`（或 `pipelines/lib/` + 小入口），做到：
  - `python /app/pipelines/asr_translate_tts.py ...` 不再依赖 `scripts/`
- 同理迁移 `quality_pipeline.py`、`online_pipeline.py`
- Docker compose 中把 `onejob/round2` 的命令从 `/app/scripts/...` 改到 `/app/pipelines/...`

完成后再进入“scripts 删除轮次”。

---

## 6. 建议的执行节奏（最稳）

- 第 1 天：完成 0 + 1 + 2（全是低风险/收益高）
- 第 2 天：做第 3（可选）与第 4（要谨慎）
- 第 3 天起：开始真正的 pipelines 迁移（为第 5 轮做准备）

