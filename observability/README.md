# Observabilidade — RideFleet Core

Stack de monitoramento do ecossistema RideFleet.

## Componentes

| Serviço    | Porta | Descrição |
|------------|-------|-----------|
| Prometheus | 9090  | Coleta de métricas |
| Grafana    | 3000  | Dashboards (login: admin / ver `.env`) |

## Acesso

```bash
# Subir o stack completo
docker compose -f infra/docker-compose.core.yml up -d

# Grafana: http://localhost:3000
# Prometheus: http://localhost:9090
```

## Dashboards disponíveis

| Dashboard | UID | Descrição |
|-----------|-----|-----------|
| Overview | `ridefleet-overview` | Visão geral do ecossistema |
| Locks | `ridefleet-locks` | Locks distribuídos ativos/expirados |
| Saga Transitions | `ridefleet-saga` | Transições de estado das corridas |
| Circuit Breakers | `ridefleet-circuit-breakers` | Estado dos circuit breakers por grupo |

## Métricas que cada grupo DEVE expor

Cada serviço deve expor `GET /metrics` no formato Prometheus com pelo menos:

```
# Locks
ridefleet_locks_acquired_total{service="group-X"}
ridefleet_locks_contention_total{service="group-X"}
ridefleet_locks_expired_total{service="group-X"}

# Saga
ridefleet_saga_transitions_total{from="X", to="Y", service="group-X"}
ridefleet_saga_compensations_total{service="group-X"}

# Circuit Breaker
ridefleet_circuit_breaker_state{service="group-X", partner="group-Y"}
# 0=CLOSED, 1=OPEN, 2=HALF_OPEN

# Geral
ridefleet_rides_delegated_total{service="group-X"}
ridefleet_rides_local_total{service="group-X"}
http_request_duration_seconds{handler="/rides", method="POST", service="group-X"}
```
