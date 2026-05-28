# Guia de Onboarding para Grupos — RideFleet Core

Bem-vindo ao ecossistema RideFleet! Este guia mostra o que seu grupo precisa fazer para integrar com o core.

---

## 1. O que é o core?

O core é o intermediário ativo entre todos os serviços. Ele:
- Gerencia a máquina de estados de cada corrida (saga)
- Arbitra locks distribuídos (evita duplicação de aceites)
- Define e versiona o contrato de API que todos devem implementar
- Mantém o log causal de auditoria com relógios de Lamport

**Toda comunicação entre grupos passa pelo core.**

---

## 2. Quick Start

### 2.1 Clonar e subir o core localmente

```bash
git clone <url-do-repositório-core>
cd ridefleet-core

# Copiar variáveis de ambiente
cp infra/.env.example infra/.env

# Subir core + Redis + Prometheus + Grafana
docker compose -f infra/docker-compose.core.yml up -d

# Verificar que o core está saudável
curl http://localhost:8080/api/v1/health
# Esperado: {"status": "ok", "version": "0.1.0", ...}
```

### 2.2 Explorar a API interativamente

Acesse `http://localhost:8080/docs` para o Swagger UI com todos os endpoints.

---

## 3. Fluxo de delegação — passo a passo

### Cenário: Grupo A delega corrida para Grupo B

```bash
# 1. Criar corrida (Grupo A solicita)
curl -X POST http://localhost:8080/api/v1/rides \
  -H "Content-Type: application/json" \
  -d '{
    "originServiceId": "group-a",
    "passengerId": "passenger-1",
    "origin": {"lat": -20.75, "lng": -42.88},
    "destination": {"lat": -20.80, "lng": -42.90},
    "logicalTimestamp": 1
  }'
# → {"rideId": "<uuid>", "logicalTimestamp": 2, "message": "..."}

RIDE_ID="<uuid do passo anterior>"

# 2. Grupo B submete proposta (leilão)
curl -X POST http://localhost:8080/api/v1/rides/$RIDE_ID/proposals \
  -H "Content-Type: application/json" \
  -d '{
    "serviceId": "group-b",
    "estimatedEta": 180,
    "estimatedPrice": 15.50,
    "logicalTimestamp": 3
  }'

# 3. Core seleciona vencedor → transição para "match"
curl -X PATCH http://localhost:8080/api/v1/rides/$RIDE_ID/status \
  -H "Content-Type: application/json" \
  -d '{
    "newState": "match",
    "serviceId": "group-a",
    "logicalTimestamp": 5
  }'

# 4. Grupo B adquire lock para confirmar
curl -X POST http://localhost:8080/api/v1/locks/$RIDE_ID \
  -H "Content-Type: application/json" \
  -d '{"serviceId": "group-b", "ttlSeconds": 30}'

# 5. Grupo B confirma → transição para "confirm"
curl -X PATCH http://localhost:8080/api/v1/rides/$RIDE_ID/status \
  -H "Content-Type: application/json" \
  -d '{
    "newState": "confirm",
    "serviceId": "group-b",
    "logicalTimestamp": 7
  }'

# 6. Consultar log causal
curl http://localhost:8080/api/v1/rides/$RIDE_ID/audit
```

---

## 4. Requisitos que seu serviço deve implementar

### 4.1 Expor endpoint de métricas Prometheus

```
GET /metrics
Content-Type: text/plain

# Métricas mínimas obrigatórias:
ridefleet_locks_acquired_total{service="group-X"}
ridefleet_circuit_breaker_state{service="group-X", partner="group-Y"}
ridefleet_rides_delegated_total{service="group-X"}
ridefleet_rides_local_total{service="group-X"}
```

### 4.2 Implementar relógio lógico de Lamport

Todo evento significativo deve incluir `logicalTimestamp` seguindo as regras:
- Ao enviar: inclua seu clock atual
- Ao receber: `clock = max(local_clock, received_timestamp) + 1`

### 4.3 Containerizar com Docker

Seu serviço deve ter um `Dockerfile` funcional. O core usará sua imagem no `docker-compose.yml` completo.

### 4.4 Variável de ambiente `CORE_URL`

Seu serviço deve ler a URL do core de `CORE_URL` (padrão: `http://core:8080/api/v1`).

---

## 5. Rodando seu serviço junto com o Core (demo individual)

Esta seção guia o grupo que quer rodar o Core + o próprio serviço na **mesma máquina**, integrados pela rede Docker, para a demo individual.

### Pré-requisitos

- Docker Desktop em execução.
- Repo `ridefleet-core` clonado localmente.
- Seu serviço tem um `Dockerfile` funcional e expõe:
  - `GET /health` — health check
  - `POST /rides/incoming` — recebe oferta de leilão do core
  - `POST /rides/{rideUuid}/assigned` — recebe notificação de vitória no leilão

### Passo 1 — Configurar o bloco do seu serviço no compose

Edite `infra/docker-compose.yml` e descomente o bloco `meu-servico`:

```yaml
services:

  meu-servico:
    build:
      context: ../meu-servico   # path relativo ao repo do seu grupo
      dockerfile: Dockerfile
    container_name: ridefleet-meu-servico
    ports:
      - "8081:8080"
    environment:
      - CORE_URL=http://core:8080/api/v1
      - GROUP_ID=meu-grupo
    depends_on:
      core:
        condition: service_healthy
    networks:
      - ridefleet-net
```

> O nome `meu-servico` (container_name) é o hostname que o core usará para callbacks — garanta que seu `serviceUrl` no registro bata com esse nome.

### Passo 2 — Subir tudo

```bash
docker compose -f infra/docker-compose.yml up -d
```

Aguarde o health check do core:

```bash
curl http://localhost:8080/api/v1/health
# → {"status": "ok", "version": "..."}
```

### Passo 3 — Registrar seu serviço no core (auto-registro no boot)

O endpoint `/groups/register` é **idempotente**: pode ser chamado a cada reinício do container sem erro. Inclua este registro no script de startup do seu serviço:

```bash
curl -s -X POST http://core:8080/api/v1/groups/register \
  -H "Content-Type: application/json" \
  -d '{
    "groupId":   "meu-grupo",
    "groupName": "Meu Grupo — SIN 142",
    "serviceUrl": "http://meu-servico:8080"
  }'
# Primeiro boot  → 201 + apiKey
# Boots seguintes → 200 + mesma apiKey (serviceUrl atualizado)
```

> Guarde a `apiKey` retornada — use-a no header `X-API-Key` em todas as chamadas ao core.

### Passo 4 — Disparar uma corrida e validar o fluxo

```bash
API_KEY="rfk_<sua chave>"

# 1. Solicitar corrida (delegação de saída)
RIDE=$(curl -s -X POST http://localhost:8080/api/v1/rides \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "originServiceId": "meu-grupo",
    "passengerId": "passageiro-1",
    "origin":      {"lat": -20.75, "lng": -42.88, "street": "Rua A", "number": "1", "city": "Viçosa", "state": "MG"},
    "destination": {"lat": -20.80, "lng": -42.90, "street": "Rua B", "number": "2", "city": "Viçosa", "state": "MG"},
    "logicalTimestamp": 1,
    "auctionTimeoutSeconds": 10
  }')
RIDE_UUID=$(echo $RIDE | python3 -c "import sys,json; print(json.load(sys.stdin)['rideUuid'])")
echo "Corrida criada: $RIDE_UUID"

# 2. Aguardar o leilão (10s) e consultar status
sleep 12
curl -s http://localhost:8080/api/v1/rides/$RIDE_UUID/status \
  -H "X-API-Key: $API_KEY"

# 3. Consultar o log causal
curl -s http://localhost:8080/api/v1/rides/$RIDE_UUID/audit \
  -H "X-API-Key: $API_KEY"
```

Seu serviço deve ter recebido `POST /rides/incoming` durante o leilão e, se vencedor, `POST /rides/{uuid}/assigned`.

### Passo 5 — Troubleshooting

| Sintoma | Causa provável | Solução |
|---------|---------------|---------|
| Core não chama `/rides/incoming` | `serviceUrl` registrado não bate com hostname na `ridefleet-net` | Registre novamente com `serviceUrl: "http://meu-servico:8080"` |
| `docker compose up` falha no build | Path do `context` incorreto | Verifique o path relativo em `infra/docker-compose.yml` |
| Re-registro retorna 500 | Container do core ainda inicializando | Aguarde o health check do core antes de chamar `/groups/register` |
| Leilão fecha sem propostas — corrida cancelada | Nenhum outro grupo registrado ou todos passaram | Normal na demo individual; o próprio grupo pode ser o único participante |

### Adicionar ao compose do core (integração multi-grupo — Semana 6)

Quando o professor subir o ambiente centralizado, abra uma PR com o bloco do seu grupo em `infra/docker-compose.yml` na branch `feat/add-group-X-compose`.

---

## 6. Comunicação e suporte

- **Dúvidas sobre a spec:** abra uma Issue com template `Proposta de Mudança na API`
- **Bug no core:** abra uma Issue com template `Bug Report`
- **Mudança breaking na API:** prazo mínimo de 48h de comunicação antes de exigir migração
- **Decisão não resolvida no core:** escalar ao Prof. Damaso via issue com label `needs-senior-architect`

---

## 8. Checklist de integração

- [ ] Core sobe localmente sem erros (`docker compose up -d`)
- [ ] `GET /health` retorna `200`
- [ ] Seu serviço sobe junto via `infra/docker-compose.yml` (bloco `meu-servico` configurado)
- [ ] Auto-registro idempotente funciona no boot (200 ou 201 sem erro)
- [ ] Core chama `POST /rides/incoming` no seu serviço durante o leilão
- [ ] Fluxo básico (criar corrida → leilão → match → confirm) funciona end-to-end
- [ ] Seu serviço expõe `GET /metrics` no formato Prometheus
- [ ] Seu serviço lê `CORE_URL` do ambiente
- [ ] Seu serviço tem `Dockerfile` funcional
