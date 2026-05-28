# ==============================================================================
# RideFleet Core — Makefile
# ==============================================================================
# Uso: make <comando>
# Exemplos:
#   make dev        — sobe o banco e o servidor local com hot-reload
#   make test       — executa a suíte de testes
#   make up         — sobe o stack completo via Docker Compose
# ==============================================================================

PYTHON      := .venv/bin/python
PYTEST      := .venv/bin/pytest
UVICORN     := .venv/bin/uvicorn
PIP         := .venv/bin/pip
COMPOSE      := docker compose -f infra/docker-compose.core.yml
COMPOSE_DEV  := docker compose -f infra/docker-compose.dev.yml
COMPOSE_TEST := docker compose -f infra/docker-compose.test.yml
ENV_FILE    := infra/.env.example
ENV_TARGET  := infra/.env

# DATABASE_URL e RABBITMQ_URL apontam para localhost quando o servidor roda fora do Docker
DATABASE_URL_DEV  := postgresql+asyncpg://ridefleet:secret@localhost:5432/ridefleet_core
RABBITMQ_URL_DEV  := amqp://ridefleet:ridefleet@localhost:5672/

.PHONY: help install dev db-up db-down test test-docker up down build logs health env

# ------------------------------------------------------------------------------
# help — lista todos os comandos disponíveis (padrão ao chamar `make`)
# ------------------------------------------------------------------------------
help:
	@echo ""
	@echo "  RideFleet Core — comandos disponíveis"
	@echo ""
	@echo "  Desenvolvimento local"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make install   Instala as dependências no .venv"
	@echo "  make dev       Sobe o banco + servidor local com hot-reload (porta 8080)"
	@echo "  make db-up     Sobe apenas o PostgreSQL em background"
	@echo "  make db-down   Para e remove o container do banco"
	@echo "  make test      Executa a suíte de testes automatizados (local, requer .venv)"
	@echo "  make test-docker  Executa os testes em container Docker (sem deps locais)"
	@echo ""
	@echo "  Docker (stack completo)"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make build     Constrói a imagem Docker do core"
	@echo "  make up        Sobe o stack (core + observabilidade) em background"
	@echo "  make down      Para e remove os containers do stack"
	@echo "  make logs      Acompanha os logs do container core"
	@echo "  make health    Verifica o endpoint /api/v1/health"
	@echo ""
	@echo "  Configuração"
	@echo "  ─────────────────────────────────────────────────────"
	@echo "  make env       Cria infra/.env a partir do .env.example"
	@echo ""

# ------------------------------------------------------------------------------
# env — copia .env.example para .env (não sobrescreve se já existir)
# ------------------------------------------------------------------------------
env:
	@if [ -f $(ENV_TARGET) ]; then \
		echo "infra/.env já existe — nenhuma alteração feita."; \
	else \
		cp $(ENV_FILE) $(ENV_TARGET); \
		echo "infra/.env criado a partir do .env.example."; \
	fi

# ------------------------------------------------------------------------------
# install — instala dependências Python no virtualenv
# ------------------------------------------------------------------------------
install:
	@if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
	$(PIP) install --upgrade pip
	$(PIP) install -r app/requirements.txt

# ------------------------------------------------------------------------------
# db-up — sobe o PostgreSQL e aguarda o healthcheck passar
# ------------------------------------------------------------------------------
db-up:
	$(COMPOSE_DEV) up -d --wait

# ------------------------------------------------------------------------------
# db-down — para e remove o container do banco
# ------------------------------------------------------------------------------
db-down:
	$(COMPOSE_DEV) down

# ------------------------------------------------------------------------------
# dev — sobe o banco (se necessário) e inicia o servidor local com hot-reload
# ------------------------------------------------------------------------------
dev: db-up
	DATABASE_URL=$(DATABASE_URL_DEV) RABBITMQ_URL=$(RABBITMQ_URL_DEV) $(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8080

# ------------------------------------------------------------------------------
# test — executa os testes com pytest (SQLite em memória, sem banco externo)
# ------------------------------------------------------------------------------
test:
	$(PYTEST) -v

# ------------------------------------------------------------------------------
# test-docker — constrói a imagem de teste e roda pytest em container isolado
# ------------------------------------------------------------------------------
test-docker:
	$(COMPOSE_TEST) up --build --abort-on-container-exit --exit-code-from test

# ------------------------------------------------------------------------------
# build — constrói a imagem Docker do core
# ------------------------------------------------------------------------------
build:
	$(COMPOSE) build core

# ------------------------------------------------------------------------------
# up — sobe o stack em background
# ------------------------------------------------------------------------------
up:
	$(COMPOSE) up -d

# ------------------------------------------------------------------------------
# down — para e remove os containers do stack completo
# ------------------------------------------------------------------------------
down:
	$(COMPOSE) down

# ------------------------------------------------------------------------------
# logs — acompanha os logs do container core em tempo real
# ------------------------------------------------------------------------------
logs:
	$(COMPOSE) logs -f core

# ------------------------------------------------------------------------------
# health — verifica se o core está respondendo
# ------------------------------------------------------------------------------
health:
	curl -sf http://localhost:8080/api/v1/health | python3 -m json.tool
