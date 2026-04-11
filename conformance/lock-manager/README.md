# Lock Manager

Árbitro de locks distribuídos do RideFleet Core.

## Responsabilidade

Impede que dois ou mais serviços aceitem ou manipulem a mesma corrida simultaneamente. Implementa aquisição/liberação de lock sobre o `rideId` com TTL configurável.

## Como funciona

1. Serviço chama `POST /locks/{rideId}` com seu `serviceId` e TTL desejado.
2. O core responde `200` (lock adquirido) ou `409` (lock já detido).
3. Somente o detentor do lock pode transitar a corrida para `confirm`, `in_transit` ou `complete`.
4. O serviço chama `DELETE /locks/{rideId}` ao terminar.
5. Locks não liberados expiram automaticamente após o TTL — evita deadlock em caso de falha.

## Implementação atual

`src/lock_manager.py` — armazenamento in-memory, thread-safe, com reaper daemon para limpeza de locks expirados.

**Limitação:** in-memory não persiste reinicios do container. Adequado para v0.1.0 (desenvolvimento/testes). Substituir pelo backend definido no ADR-002 para produção.

## Métricas expostas (Prometheus)

| Métrica | Tipo | Descrição |
|---------|------|-----------|
| `ridefleet_locks_active` | Gauge | Locks ativos no momento |
| `ridefleet_locks_acquired_total` | Counter | Total de locks adquiridos |
| `ridefleet_locks_contention_total` | Counter | Tentativas bloqueadas por contenção |
| `ridefleet_locks_expired_total` | Counter | Locks expirados por TTL |
