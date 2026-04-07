#!/usr/bin/env bash
# 一键启动开发 Docker（Apple Silicon 自动切 arm64 后端）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

HOST_ARCH="$(uname -m)"
COMPOSE_ARGS=(-f docker/docker-compose.yml)
BACKEND_IMAGE="yzh-backend:quality"
BACKEND_DOCKERFILE="docker/Dockerfile"

if [[ "$HOST_ARCH" == "arm64" || "$HOST_ARCH" == "aarch64" ]]; then
  COMPOSE_ARGS+=(-f docker/docker-compose.arm64.yml)
  BACKEND_IMAGE="yzh-backend:quality-arm64"
  BACKEND_DOCKERFILE="docker/Dockerfile.arm64"
fi

echo "[info] host_arch=$HOST_ARCH"
echo "[info] backend_image=$BACKEND_IMAGE"
echo "[info] compose_files=${COMPOSE_ARGS[*]}"

if ! docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1; then
  echo "[1/3] 本地缺少后端镜像，开始构建：$BACKEND_IMAGE"
  docker build -t "$BACKEND_IMAGE" -f "$BACKEND_DOCKERFILE" .
else
  echo "[1/3] 后端镜像已存在，跳过构建：$BACKEND_IMAGE"
fi

echo "[2/3] 拉取基础 Docker 镜像..."
docker compose "${COMPOSE_ARGS[@]}" pull

echo "[3/3] 启动服务（--remove-orphans 清理孤儿容器）..."
docker compose "${COMPOSE_ARGS[@]}" up -d --remove-orphans

echo ""
echo "已启动。常用地址："
echo "  - Ollama: http://localhost:11434"
echo "  - 后端:   http://localhost:5175"
