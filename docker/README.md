# Docker 运行说明

## 详细启动步骤

### 前置条件

- 已安装 **Docker** 与 **Docker Compose**（或 `docker compose` 插件）

---

### 步骤一：拉取镜像并启动

**方式 A：一键脚本（推荐）**

在项目根目录执行：

```bash
./docker/start.sh
```

脚本会自动：拉取 Docker 镜像 → 启动服务并清理孤儿容器。

**方式 B：手动执行**

在项目根目录依次执行：

```bash
# 1. 进入项目根目录（替换为你的实际路径）
cd /Users/chengzheng/Desktop/译制工坊

# 2. 拉取镜像
docker compose -f docker/docker-compose.yml pull

# 3. 启动并清理孤儿容器
docker compose -f docker/docker-compose.yml up -d --remove-orphans
```

若之前跑过旧版，建议先停止再启动：

```bash
docker compose -f docker/docker-compose.yml down
./docker/start.sh
```

---

### 步骤二：确认服务已启动

1. 查看容器状态：
   ```bash
   docker compose -f docker/docker-compose.yml ps
   ```
   应看到 `ollama`、`backend`（以及你启用时的 `frontend`）为 `running`。

2. 服务就绪后，可访问：
   - Ollama：http://localhost:11434
   - 后端：http://localhost:5175
   - 前端（Docker 内）：http://localhost:5176

---

### 步骤三：使用与停止

- **使用**：质量模式默认通过 `llm_endpoint` 连接 Ollama（OpenAI 兼容 `/v1`）。
- **停止所有服务**：
  ```bash
  docker compose -f docker/docker-compose.yml down
  ```

---

## 常用命令速查

| 操作           | 命令 |
|----------------|------|
| 一键拉取并启动 | `./docker/start.sh` |
| 查看运行状态   | `docker compose -f docker/docker-compose.yml ps` |
| 查看后端日志 | `docker compose -f docker/docker-compose.yml logs -f backend` |
| 查看 Ollama 日志 | `docker compose -f docker/docker-compose.yml logs -f ollama` |
| 停止并删除容器 | `docker compose -f docker/docker-compose.yml down` |

