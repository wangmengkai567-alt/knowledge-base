# ──────────────────────────────────────────────
# AI Service 镜像构建文件
#
# 构建：docker build -f deployments/docker/Dockerfile -t ai-service .
# 运行：docker run -p 8084:8084 --env-file .env ai-service
# ──────────────────────────────────────────────

FROM python:3.11-slim

# 元数据
LABEL maintainer="AI Knowledge Base Team"
LABEL description="AI 知识库服务 — 基于 RAG 的智能问答"
LABEL version="0.1.0"

# 安全：非 root 运行
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# 系统依赖：build-essential 编译 Python C 扩展，libmupdf 给 PyMuPDF 用，curl 给 HEALTHCHECK 用
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libmupdf-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖声明 + 源码，安装 Python 依赖
COPY ai-service/ ./
RUN pip install --no-cache-dir .

# 创建数据目录（挂载点）
RUN mkdir -p /app/data/uploads && chown -R appuser:appuser /app

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
