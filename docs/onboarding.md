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

## 5. Adicionar seu serviço ao docker-compose completo

Edite `infra/docker-compose.yml` e descomente/preencha o bloco do seu grupo:

```yaml
services:
  group-a:
    image: ${GROUP_A_IMAGE}
    ports:
      - "8081:8080"
    environment:
      - CORE_URL=http://core:8080/api/v1
    networks:
      - ridefleet-net
```

Abra uma PR na branch `feat/add-group-X-compose`.

---

## 6. Comunicação e suporte

- **Dúvidas sobre a spec:** abra uma Issue com template `Proposta de Mudança na API`
- **Bug no core:** abra uma Issue com template `Bug Report`
- **Mudança breaking na API:** prazo mínimo de 48h de comunicação antes de exigir migração
- **Decisão não resolvida no core:** escalar ao Prof. Damaso via issue com label `needs-senior-architect`

---

## 7. Checklist de integração

- [ ] Core sobe localmente sem erros (`docker compose up -d`)
- [ ] `GET /health` retorna `200`
- [ ] Fluxo básico (criar corrida → proposta → match → confirm) funciona
- [ ] Seu serviço expõe `GET /metrics` no formato Prometheus
- [ ] Seu serviço lê `CORE_URL` do ambiente
- [ ] Seu serviço tem `Dockerfile` funcional
- [ ] Imagem adicionada ao `infra/docker-compose.yml`
