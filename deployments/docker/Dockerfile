# ──────────────────────────────────────────────
# AI Service 镜像构建文件
#
# 构建：docker build -f deployments/docker/Dockerfile -t ai-service .
# 运行：docker run -p 8084:8084 --env-file .env ai-service
# ──────────────────────────────────────────────

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# 元数据
LABEL maintainer="AI Knowledge Base Team"
LABEL description="AI 知识库服务 — 基于 RAG 的智能问答"
LABEL version="0.1.0"

# 安全：非 root 运行
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# 官方 HTTPS 软件源；PyMuPDF 使用锁定的预编译轮子。
# libgl1 / glib 是 RapidOCR 的 OpenCV 运行库；curl 用于健康检查。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/*

# 依赖独立缓存；源码变化只重新安装本项目。
COPY ai-service/pyproject.toml ai-service/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --link-mode copy --locked --no-dev --no-install-project
COPY --chown=appuser:appuser ai-service/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --link-mode copy --locked --no-dev --no-editable

# 创建数据目录（挂载点）
RUN mkdir -p /app/data/uploads && chown appuser:appuser /app /app/data /app/data/uploads

# 切换到非 root 用户
USER appuser

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 暴露端口
EXPOSE 8084

# Health check：利用应用自带的 /health/live 端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8084/health/live || exit 1

# 启动命令
CMD ["python", "-m", "cmd.main"]
