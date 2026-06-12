# Webhook Dispatcher — Eliminação do Scatter-Gather Síncrono (Issue #24)

## Contexto

Antes desta mudança, o `auction_worker` fazia chamadas HTTP diretas aos grupos para notificá-los (`_chamar_grupo`) e para avisar o vencedor (`_notificar_vencedor`). O RabbitMQ existia apenas para receber a mensagem de leilão do core — a entrega real aos grupos era síncrona via `httpx`, tornando o broker "ornamental" para o scatter-gather.

## O que foi alterado

### 1. `app/workers/webhook_dispatcher.py` — arquivo novo

Worker assíncrono que consome três filas RabbitMQ e faz HTTP POST aos grupos registrados:

| Fila | Endpoint destino |
|---|---|
| `ridefleet.groups.ride_created` | `{serviceUrl}/rides/incoming` |
| `ridefleet.groups.status` | `{serviceUrl}/rides/{rideUuid}/status` |
| `ridefleet.compensations` | `{serviceUrl}/rides/{rideUuid}/compensation` |

Funcionalidades do dispatcher:
- **Retry com backoff exponencial**: até 5 tentativas, iniciando em 10 s, duplicando a cada falha.
- **Falha permanente auditada**: após 5 tentativas sem sucesso, persiste `RideAuditEvent(event_type="webhook_failed")` no banco.
- **Métrica Prometheus**: `ridefleet_webhook_deliveries_total{service, status}` incrementada a cada tentativa.
- Iniciado no lifespan da aplicação (`app/main.py`) junto ao `auction_worker`.

### 2. `app/workers/auction_worker.py` — refatorado

- **Removido**: `_chamar_grupo`, `_notificar_vencedor`, `_validar_proposta`, import de `httpx` e `RideIncomingNotificationDTO`.
- **Novo fluxo assíncrono em `_executar_leilao`**:
  1. Publica `ride_created` no exchange — o `webhook_dispatcher` entrega aos grupos.
  2. Faz `asyncio.sleep(tempo_restante)` até o `auctionDeadline`.
  3. Lê as propostas do banco (submetidas pelos grupos via `POST /proposals`).
  4. Seleciona o vencedor e persiste o resultado.
  5. Publica `ride_status_changed` com detalhes completos da corrida para o vencedor.
- **`iniciar_worker`** mantém a lógica de reconexão automática do develop: laço ilimitado com backoff exponencial (`base_delay=5s`, `max_delay=60s`) e `CancelledError` tratado corretamente.
- **`_utcnow()`** retorna `datetime` naive (sem tzinfo), consistente com a convenção do branch `develop`.

### 3. `app/core/metrics.py`

Novo counter adicionado:

```python
webhook_deliveries_total = Counter(
    "ridefleet_webhook_deliveries_total",
    "Total de entregas de webhook para grupos",
    ["service", "status"],   # status: "success" | "failed"
)
```

### 4. Endpoint `POST /api/v1/rides/{rideUuid}/proposals` — novo

Permite que grupos submetam propostas de forma assíncrona durante o leilão.

**`app/dtos/ride_request_dto.py`** — `ProposalSubmitDTO`:
```json
{
  "status": "accepted | passed",
  "estimatedEta": 5,
  "estimatedPrice": 15.50,
  "logicalTimestamp": 42
}
```

**`app/dtos/ride_response_dto.py`** — `ProposalAcceptedDTO`:
```json
{
  "rideUuid": "...",
  "groupId": "...",
  "status": "accepted",
  "logicalTimestamp": 42
}
```

**`app/repositories/proposal_repository.py`** — método `upsert_por_ride_e_grupo`: cria ou atualiza a proposta de um grupo para uma corrida (idempotente).

**`app/services/ride_service.py`** — método `submeter_proposta`: valida que o leilão está aberto (`OPEN`), faz o upsert, registra `RideAuditEvent(event_type="proposal_received")`.

**`app/controllers/ride_controller.py`** — endpoint `POST /rides/{rideUuid}/proposals` com `status_code=201`.

### 5. Testes

**`app/tests/test_auction_worker.py`** — completamente reescrito:
- Elimina todos os patches de `_chamar_grupo` / `_notificar_vencedor`.
- Semeia propostas diretamente no banco SQLite de testes via `_seed_proposta`.
- Patcha `asyncio.sleep` para execução instantânea.
- Redireciona `AsyncSessionLocal` para SQLite via `_PatchedSessionLocal`.
- Cobre: publicação de `ride_created`, sem propostas → cancelado, `passed` → cancelado, aceito → MATCH, payload do vencedor com detalhes completos, desempate por menor preço, idempotência.

**`app/tests/test_core_contract.py`** — dois novos testes de contrato:
- `test_contract_post_proposal` — 201 + shape de `ProposalAcceptedDTO`.
- `test_contract_post_proposal_passed` — 201 + `status == "passed"`.

## Diagrama do novo fluxo

```
Passenger
   │  POST /rides
   ▼
[Core API] ──────────────────────────────────────────┐
   │  publica auction_request                          │
   ▼                                                   │
[auction_worker]                                       │
   │  1. publica ride_created                          │
   │  2. asyncio.sleep(deadline)                       │
   ▼                                                   │
[RabbitMQ exchange: ridefleet.core.events]             │
   │  ridefleet.groups.ride_created queue              │
   ▼                                                   │
[webhook_dispatcher] ──► POST {serviceUrl}/rides/incoming ──► [Grupo A]
                     └──► POST {serviceUrl}/rides/incoming ──► [Grupo B]
                                                       │
[Grupo A] ──► POST /api/v1/rides/{uuid}/proposals ────┘
[Grupo B] ──► POST /api/v1/rides/{uuid}/proposals

   [auction_worker após deadline]
   │  lê propostas do banco
   │  seleciona vencedor (menor preço > menor ETA > group_id)
   │  publica ride_status_changed
   ▼
[webhook_dispatcher] ──► POST {serviceUrl}/rides/{uuid}/status ──► [Vencedor]
```

## Impacto nos grupos

Os grupos precisam:
1. Consumir `POST {serviceUrl}/rides/incoming` para receber novas corridas.
2. Submeter `POST /api/v1/rides/{rideUuid}/proposals` antes do `auctionDeadline`.
3. Consumir `POST {serviceUrl}/rides/{uuid}/status` para saber o resultado.

O campo `auctionDeadline` (ISO 8601) é incluído no payload de `ride_created`.

**Não há breaking change nos endpoints existentes.** O endpoint `POST /proposals` é adição pura.
