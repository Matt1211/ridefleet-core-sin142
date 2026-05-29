# Observabilidade — RideFleet Core

Stack de monitoramento do ecossistema RideFleet.

## Componentes

| Serviço | Porta | Descrição |
|---------|-------|-----------|
| Prometheus | 9090 | Coleta de métricas |
| Grafana | 3000 | Dashboards (login: admin / ver `.env`) |

## Acesso

```bash
# Subir o stack completo
docker compose -f infra/docker-compose.core.yml up -d

# Grafana:    http://localhost:3000
# Prometheus: http://localhost:9090
```

## Dashboards disponíveis

| Dashboard | UID | Descrição |
|-----------|-----|-----------|
| Overview | `ridefleet-overview` | Visão geral do ecossistema |
| Locks | `ridefleet-locks` | Locks distribuídos ativos/expirados |
| Saga Transitions | `ridefleet-saga` | Transições de estado das corridas |
| Circuit Breakers | `ridefleet-circuit-breakers` | Estado dos circuit breakers por grupo |

---

## Métricas expostas pelo Core

O Core expõe `GET /metrics` (porta 8080). Métricas implementadas em `app/core/metrics.py`:

```
# Relógio lógico de Lamport
ridefleet_logical_timestamp          (Gauge)

# Locks
ridefleet_locks_acquired_total{service="core"}    (Counter)
ridefleet_locks_expired_total{service="core"}     (Counter)

# Circuit Breakers (0=CLOSED, 1=OPEN, 2=HALF_OPEN)
ridefleet_circuit_breaker_state{service="<group-id>"}   (Gauge)

# Saga
ridefleet_saga_transitions_total{from_state="X", to_state="Y", service="core"}  (Counter)

# Delegação
ridefleet_rides_delegated_total{service="core"}   (Counter)
ridefleet_rides_local_total{service="core"}       (Counter)
```

---

## Métricas que cada grupo DEVE expor

Cada serviço deve expor `GET /metrics` no formato Prometheus com pelo menos:

```
# Locks
ridefleet_locks_acquired_total{service="meu-grupo"}
ridefleet_locks_expired_total{service="meu-grupo"}

# Saga (use os mesmos nomes de label do Core: from_state, to_state)
ridefleet_saga_transitions_total{from_state="confirm", to_state="in_transit", service="meu-grupo"}
ridefleet_saga_compensations_total{service="meu-grupo"}

# Circuit Breaker (0=CLOSED, 1=OPEN, 2=HALF_OPEN)
ridefleet_circuit_breaker_state{service="meu-grupo"}

# Corridas
ridefleet_rides_delegated_total{service="meu-grupo"}
ridefleet_rides_local_total{service="meu-grupo"}

# HTTP
http_request_duration_seconds{handler="/rides", method="POST", service="meu-grupo"}
```

> Use os mesmos nomes de métrica e labels listados acima para que os dashboards do Grafana funcionem sem configuração adicional.
