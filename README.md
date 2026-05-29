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

### Escopo do Core — o que está implementado

| Primitiva | Status | Detalhes |
|-----------|--------|----------|
| Relógio lógico de Lamport | Implementado | Singleton thread-safe; aplica `max(local, recebido) + 1` em todos os eventos |
| Locks distribuídos com TTL | Implementado | Por corrida; monitor detecta expiração a cada 5s e dispara compensação |
| Saga pattern + compensação | Implementado | 7 estados; lock monitor + auction worker orquestram o re-leilão automático |
| Circuit breaker por grupo | Implementado | Threshold: 2 falhas; recovery: 20s; retorna 503 quando OPEN |
| Leilão scatter-gather | Implementado | Core chama grupos via HTTP (`POST /rides/incoming`) em paralelo |
| Broker RabbitMQ | Implementado | Exchange topic `ridefleet.core.events`; 8 filas declaradas na inicialização |

> **Fora de escopo nesta versão:** vetor de relógios e algoritmos de consenso (Raft/Paxos). O relógio de Lamport é suficiente para a ordenação causal exigida no cronograma das semanas 1–6.

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

Para rodar seu serviço junto com o core na mesma máquina (demo individual), veja a seção
[Rodando seu serviço junto com o Core](docs/onboarding.md#5-rodando-seu-serviço-junto-com-o-core-demo-individual).

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
- **Versão:** v0.4.1
- **Status:** Em desenvolvimento ativo

### Endpoints implementados

| Método | Rota | Autenticação | Descrição |
|--------|------|-------------|-----------|
| `POST` | `/api/v1/groups/register` | Nenhuma | Registrar grupo e obter API Key (idempotente) |
| `GET` | `/api/v1/groups/register` | `X-API-Key` | Listar grupos registrados |
| `POST` | `/api/v1/rides` | `X-API-Key` | Criar corrida e iniciar leilão (202 Accepted) |
| `GET` | `/api/v1/rides` | `X-API-Key` | Listar corridas com filtros (estado, origem, atribuído) |
| `GET` | `/api/v1/rides/{rideUuid}/status` | `X-API-Key` | Consultar estado atual da saga + lock |
| `PATCH` | `/api/v1/rides/{rideUuid}/status` | `X-API-Key` | Transição de estado (valida saga, idempotente) |
| `GET` | `/api/v1/rides/{rideUuid}/proposals` | `X-API-Key` | Resultado do leilão (propostas + vencedor) |
| `GET` | `/api/v1/rides/{rideUuid}/audit` | `X-API-Key` | Log causal completo (eventos + timestamps Lamport) |
| `POST` | `/api/v1/locks/{rideUuid}` | `X-API-Key` | Adquirir/renovar lock distribuído com TTL |
| `DELETE` | `/api/v1/locks/{rideUuid}` | `X-API-Key` | Liberar lock (somente o detentor) |
| `GET` | `/api/v1/health` | Nenhuma | Health check do core |
| `GET` | `/metrics` | Nenhuma | Métricas Prometheus |

---

## Broker — RabbitMQ

Exchange topic `ridefleet.core.events`. Filas criadas automaticamente na inicialização do core:

| Fila | Routing Key | Assinantes | Descrição |
|------|-------------|-----------|-----------|
| `ridefleet.groups.ride_created` | `ride_created` | Todos os grupos | Nova corrida disponível para leilão |
| `ridefleet.proposals` | `proposal_submitted` | Serviço de origem | Proposta registrada |
| `ridefleet.groups.status` | `ride_status_changed` | Todos os grupos | Transição de estado da saga |
| `ridefleet.locks` | `lock_event` | Interno / observabilidade | Eventos de aquisição/liberação/expiração de lock |
| `ridefleet.compensations` | `compensation_triggered` | Grupo atribuído + origem | Compensação iniciada |
| `ridefleet.auction.requests` | `auction_request` | Auction Worker (interno) | Dispara execução de leilão |
| `ridefleet.audit` | `#` | Auditoria | Captura todos os eventos |
| `ridefleet.observability` | `#` | Observabilidade | Captura todos os eventos |

Ver [`broker/README.md`](broker/README.md) para detalhes de cada fila.

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
├── app/
│   ├── controllers/    ← Rotas FastAPI (rides, locks, auth, health)
│   ├── core/           ← Primitivas SD: LamportClock, CircuitBreaker, métricas
│   ├── models/         ← Modelos SQLAlchemy (Ride, RideLock, Proposal, Group, AuditEvent)
│   ├── repositories/   ← Camada de acesso ao banco de dados
│   ├── services/       ← Lógica de negócio (RideService, StateMachineService)
│   ├── workers/        ← Background tasks: auction_worker, lock_monitor
│   └── tests/          ← Suíte de testes (pytest + httpx)
├── spec/           ← Contrato de integração (OpenAPI, schemas JSON, saga)
├── broker/         ← Configuração de tópicos e exchange RabbitMQ
├── bruno/          ← Coleção de requisições para testes manuais da API
├── observability/  ← Prometheus + Grafana (dashboards provisionados)
├── infra/          ← Docker Compose (core.yml + dev.yml) + .env.example
├── docs/           ← Tutorial de integração, fluxo de delegação, ADRs
└── Makefile        ← Atalhos para desenvolvimento e operação
```

---

## Cronograma

| Data-limite | Entregável | Status |
|-------------|-----------|--------|
| **18/04/2026** | Spec da API v0.1.0 aprovada + ADRs | ✅ Concluído |
| **16/05/2026** | Testes de contrato no CI | ✅ Concluído |
| **28/05/2026** | API v0.4.1 — idempotência no registro de grupos | ✅ Concluído |
| **30/05/2026** | Integração local dos grupos (Semana 3) | 🔄 Em andamento |
| **13/06/2026** | Dashboards Grafana + todos os testes de contrato passando | ⏳ Pendente |

---

## Contribuindo

Leia [`CONTRIBUTING.md`](CONTRIBUTING.md) antes de abrir uma PR.

**Regra crítica:** Qualquer mudança em `spec/api/openapi.yaml` ou `spec/schemas/` usa branch `spec/xxx` e exige aprovação de **todos os representantes do core**.
