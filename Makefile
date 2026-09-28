.PHONY: help lint docker-up docker-down docker-logs docker-rebuild

AI_SERVICE_DIR := ai-service
COMPOSE_FILE := deployments/docker/docker-compose.yaml
COMPOSE_ENV  := --env-file .env

## help: 显示可用目标
help:
	@echo "可用目标:"
	@echo "  lint                 - 对 ai-service 跑 Ruff"
	@echo "  docker-up            - 启动 Compose"
	@echo "  docker-down          - 停止 Compose"
	@echo "  docker-logs          - 跟踪 Compose 日志"
	@echo "  docker-rebuild       - 重建并启动 Compose"

## lint: Ruff
lint:
	@cd $(AI_SERVICE_DIR) && python -m ruff check internal/ pkg/ cmd/

## docker-up: 启动 Compose
docker-up:
	@echo "正在通过 Docker Compose 启动服务..."
	@docker compose -f $(COMPOSE_FILE) $(COMPOSE_ENV) up -d --build
	@echo "健康检查: http://localhost:8084/health/live"

## docker-down: 停止 Compose
docker-down:
	@docker compose -f $(COMPOSE_FILE) $(COMPOSE_ENV) down
	@echo "服务已停止。"

## docker-logs: 查看日志
docker-logs:
	@docker compose -f $(COMPOSE_FILE) $(COMPOSE_ENV) logs -f

## docker-rebuild: 重建并启动
docker-rebuild:
	@docker compose -f $(COMPOSE_FILE) $(COMPOSE_ENV) up -d --build --force-recreate
	@echo "服务已重建。"
