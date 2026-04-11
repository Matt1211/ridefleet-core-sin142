# RideFleet Core

Repositório central de padronização e intermediação do ecossistema RideFleet.
SIN 142 — Sistemas Distribuídos — UFV 2026/1

---

## O que é o Core?

O core é o **intermediário ativo** de toda comunicação entre os serviços de transporte. Suas responsabilidades:

- **Gerenciar os tópicos pub/sub** (broker de mensagens): recebe e roteia eventos entre serviços
- **Conformizar o Saga Pattern**: valida as transições de estado das corridas e garante compensações em caso de falha
- **Conformizar os Locks Distribuídos**: arbitra aquisições e liberações de lock sobre IDs de corrida, evitando aceitações duplicadas
- **Definir e versionar a API de integração**: o contrato que todos os grupos devem implementar
- **Orquestrar** todos os serviços via Docker Compose
- **Manter a stack de observabilidade** (Prometheus + Grafana)
- **Executar testes de contrato** no CI para verificar conformidade de cada serviço

---

## Quick Start (para grupos integrando)

```bash
# 1. Clonar o repositório
git clone <url>
cd ridefleet-core

# 2. Configurar variáveis de ambiente
cp infra/.env.example infra/.env

# 3. Subir o core + observabilidade
docker compose -f infra/docker-compose.core.yml up -d

# 4. Verificar health
curl http://localhost:8080/api/v1/health
# → {"status": "ok", "version": "0.1.0"}

# 5. Explorar a API interativamente
# Abra http://localhost:8080/docs no navegador
```

Leia o [Guia de Onboarding completo](docs/onboarding.md) para os próximos passos.

---

## Versão atual da API

- **Spec:** [`spec/api/openapi.yaml`](spec/api/openapi.yaml)
- **Versão:** v0.1.0
- **Status:** Em revisão — aguardando aprovação do Prof. Damaso

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
| Dashboards Grafana | [`observability/grafana/dashboards/`](observability/grafana/dashboards/) |
| Observabilidade | [`observability/README.md`](observability/README.md) |

---

## Estrutura do Repositório

```
ridefleet-core/
├── spec/           ← Contrato de integração (API, schemas, saga)
├── core/           ← Serviço Python/FastAPI
├── conformance/    ← Lock manager, saga coordinator, testes de contrato
├── broker/         ← Configuração de tópicos pub/sub
├── observability/  ← Prometheus + Grafana
├── infra/          ← Docker Compose
└── docs/           ← ADRs, onboarding, documentação
```

---

## Cronograma

| Data-limite | Entregável | Status |
|-------------|-----------|--------|
| **18/04/2026** | Spec da API v0.1.0 aprovada + ADRs | 🔄 Em andamento |
| **16/05/2026** | Testes de contrato no CI | ⏳ Pendente |
| **30/05/2026** | Dashboards Grafana com dados reais | ⏳ Pendente |
| **13/06/2026** | Todos os testes de contrato passando | ⏳ Pendente |

---

## Contribuindo

Leia [`CONTRIBUTING.md`](CONTRIBUTING.md) antes de abrir uma PR.

**Regra crítica:** Qualquer mudança em `spec/api/openapi.yaml` ou `spec/schemas/` usa branch `spec/xxx` e exige aprovação de **todos os representantes do core**.
