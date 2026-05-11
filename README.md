# RideFleet Core

Repositório central de padronização e intermediação do ecossistema RideFleet.
SIN 142 — Sistemas Distribuídos — UFV 2026/1

---

## O que é o Core?

O core é o **intermediário ativo** de toda comunicação entre os serviços de transporte. Suas responsabilidades:

- **Gerenciar o broker de mensagens (RabbitMQ)**: publica e roteia eventos entre serviços via exchange topic `ridefleet.core.events`
- **Conformizar o Saga Pattern**: valida as transições de estado das corridas e garante compensações em caso de falha
- **Conformizar os Locks Distribuídos**: arbitra aquisições e liberações de lock sobre IDs de corrida, evitando aceitações duplicadas
- **Autenticar grupos**: registro de grupos parceiros e emissão de API Keys (`X-API-Key`)
- **Definir e versionar a API de integração**: o contrato que todos os grupos devem implementar
- **Orquestrar** todos os serviços via Docker Compose
- **Manter a stack de observabilidade** (Prometheus + Grafana)
- **Executar testes de contrato** no CI para verificar conformidade de cada serviço

---

## Stack implementada

| Componente | Tecnologia | Porta |
|------------|-----------|-------|
| API / Core | Python 3.12 + FastAPI | `8080` |
| Banco de dados | PostgreSQL 16 | `5432` |
| Broker de mensagens | RabbitMQ 3.13 (management) | `5672` / `15672` |
| Métricas | Prometheus 2.53 | `9090` |
| Dashboards | Grafana 11.2 | `3000` |

---

## Quick Start (para grupos integrando)

```bash
# 1. Clonar o repositório
git clone <url>
cd ridefleet-core

# 2. Configurar variáveis de ambiente
cp infra/.env.example infra/.env

# 3. Subir o stack completo (core + RabbitMQ + banco + observabilidade)
make up
# ou: docker compose -f infra/docker-compose.core.yml up -d

# 4. Verificar health
curl http://localhost:8080/api/v1/health
# → {"status": "ok", "version": "0.4.0", "timestamp": "..."}

# 5. Explorar a API interativamente
# Abra http://localhost:8080/docs no navegador

# 6. Painel de gerenciamento do RabbitMQ
# Abra http://localhost:15672  (usuário/senha padrão: ridefleet/ridefleet)
```

Leia o [Guia de Onboarding completo](docs/onboarding.md) para os próximos passos.

### Comandos úteis (Makefile)

```bash
make help       # Lista todos os comandos disponíveis
make install    # Instala dependências Python em .venv
make dev        # Sobe banco + servidor local com hot-reload
make test       # Executa a suíte de testes
make up         # Sobe o stack completo via Docker
make down       # Para e remove os containers
make logs       # Acompanha logs do container core
make health     # Verifica o endpoint /api/v1/health
```

---

## Versão atual da API

- **Spec:** [`spec/api/openapi.yaml`](spec/api/openapi.yaml)
- **Versão:** v0.4.0
- **Status:** Em desenvolvimento ativo

### Endpoints implementados

| Método | Rota | Autenticação | Descrição |
|--------|------|-------------|-----------|
| `POST` | `/api/v1/groups/register` | Nenhuma | Registrar grupo e obter API Key |
| `GET` | `/api/v1/groups/register` | `X-API-Key` | Listar grupos registrados |
| `GET` | `/api/v1/health` | Nenhuma | Health check do core |

---

## Broker — RabbitMQ

Exchange topic `ridefleet.core.events`. Filas criadas automaticamente na inicialização do core:

| Fila | Routing Key | Descrição |
|------|-------------|-----------|
| `ridefleet.groups.ride_created` | `ride_created` | Nova corrida disponível para leilão |
| `ridefleet.proposals` | `proposal_submitted` | Proposta registrada |
| `ridefleet.groups.status` | `ride_status_changed` | Transição de estado da saga |
| `ridefleet.locks` | `lock_event` | Eventos de aquisição/liberação de lock |
| `ridefleet.compensations` | `compensation_triggered` | Compensação iniciada |
| `ridefleet.audit` | `#` | Auditoria (todos os eventos) |
| `ridefleet.observability` | `#` | Observabilidade (todos os eventos) |

Ver [`broker/config/topics.yaml`](broker/config/topics.yaml) para o contrato completo de cada tópico.

---

## Links Rápidos

| Recurso | Caminho |
|---------|---------|
| Especificação da API (OpenAPI) | [`spec/api/openapi.yaml`](spec/api/openapi.yaml) |
| Histórico de versões da API | [`spec/api/CHANGELOG.md`](spec/api/CHANGELOG.md) |
| Schemas de Mensagens | [`spec/schemas/`](spec/schemas/) |
| Máquina de Estados (Saga) | [`spec/saga/state-machine.md`](spec/saga/state-machine.md) |
| Guia de Onboarding para Grupos | [`docs/onboarding.md`](docs/onboarding.md) |
| Fluxo de Delegação | [`docs/fluxo-delegacao.md`](docs/fluxo-delegacao.md) |
| Decisões Arquiteturais (ADRs) | [`docs/adr/`](docs/adr/) |
| Tópicos Pub/Sub | [`broker/config/topics.yaml`](broker/config/topics.yaml) |
| Coleção Bruno (API client) | [`bruno/`](bruno/) |
| Dashboards Grafana | [`observability/grafana/dashboards/`](observability/grafana/dashboards/) |
| Observabilidade | [`observability/README.md`](observability/README.md) |

---

## Estrutura do Repositório

```
ridefleet-core/
├── app/            ← Serviço Python/FastAPI (core, controllers, models, services)
├── spec/           ← Contrato de integração (API, schemas, saga)
├── conformance/    ← Lock manager, saga coordinator, testes de contrato
├── broker/         ← Configuração de tópicos e exchange RabbitMQ
├── bruno/          ← Coleção de requisições para testes manuais da API
├── observability/  ← Prometheus + Grafana
├── infra/          ← Docker Compose (core.yml + dev.yml)
├── docs/           ← ADRs, onboarding, documentação
└── Makefile        ← Atalhos para desenvolvimento e operação
```

---

## Cronograma

| Data-limite | Entregável | Status |
|-------------|-----------|--------|
| **18/04/2026** | Spec da API v0.1.0 aprovada + ADRs | ✅ Concluído |
| **16/05/2026** | Testes de contrato no CI | 🔄 Em andamento |
| **30/05/2026** | Dashboards Grafana com dados reais | ⏳ Pendente |
| **13/06/2026** | Todos os testes de contrato passando | ⏳ Pendente |

---

## Contribuindo

Leia [`CONTRIBUTING.md`](CONTRIBUTING.md) antes de abrir uma PR.

**Regra crítica:** Qualquer mudança em `spec/api/openapi.yaml` ou `spec/schemas/` usa branch `spec/xxx` e exige aprovação de **todos os representantes do core**.
