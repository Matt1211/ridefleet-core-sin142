# Broker — RideFleet Core

Configuração dos tópicos pub/sub do ecossistema RideFleet.

## Exchange

| Nome | Tipo | Descrição |
|------|------|-----------|
| `ridefleet.core.events` | topic | Exchange principal para todos os eventos do Core |

O Core declara a exchange e todas as filas automaticamente na inicialização (`app/rabbitmq.py`).

## Filas e Routing Keys

| Fila | Routing Key | Publisher | Assinantes | Descrição |
|------|-------------|-----------|-----------|-----------|
| `ridefleet.groups.ride_created` | `ride_created` | Core | Todos os grupos | Nova corrida disponível; disparada pelo auction worker no início do leilão |
| `ridefleet.proposals` | `proposal_submitted` | Core | Serviço de origem | Proposta registrada no leilão |
| `ridefleet.groups.status` | `ride_status_changed` | Core | Todos os grupos | Transição de estado da saga (match, confirm, in_transit, complete, compensating) |
| `ridefleet.locks` | `lock_event` | Core | Interno / observabilidade | Aquisição, liberação e expiração de locks |
| `ridefleet.compensations` | `compensation_triggered` | Core | Grupo atribuído + grupo de origem | Compensação iniciada (lock expirado) |
| `ridefleet.auction.requests` | `auction_request` | Core | Auction Worker (interno) | Dispara execução de leilão; consumido exclusivamente pelo worker interno |
| `ridefleet.audit` | `#` | — | Auditoria | Captura todos os eventos (wildcard) |
| `ridefleet.observability` | `#` | — | Observabilidade | Captura todos os eventos (wildcard) |

## Formato das Mensagens

Todas as mensagens publicadas pelo Core seguem o envelope padrão:

```json
{
  "eventType": "ride_created",
  "rideId": "uuid-da-corrida",
  "serviceId": "core",
  "logicalTimestamp": 7,
  "wallClockTime": "2026-05-29T14:00:00.000Z",
  "payload": { ... }
}
```

## O que os grupos devem consumir

Para integrar com o leilão, os grupos devem **implementar endpoints HTTP** — não consumir filas diretamente. O Core chama seu serviço via HTTP durante o leilão:

- `POST /rides/incoming` — recebe oferta de leilão (Core chama durante o scatter)
- `POST /rides/{uuid}/assigned` — recebe notificação de vitória

A fila `ridefleet.groups.ride_created` é útil para observabilidade e logging, mas não substitui o callback HTTP para participar do leilão.

Ver [`docs/onboarding.md`](../docs/onboarding.md) para o tutorial completo de integração.
