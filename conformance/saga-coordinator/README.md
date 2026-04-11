# Saga Coordinator

Conformizador da máquina de estados de corridas do RideFleet Core.

## Responsabilidade

Valida todas as transições de estado contra a máquina de estados formal definida em `spec/saga/state-machine.md`. Garante que compensações sejam executadas corretamente em caso de falha.

## Máquina de estados

```
request → match → confirm → in_transit → complete
   ↓        ↓        ↓           ↓
compensating → cancelled
```

Qualquer estado pode ir para `compensating` (forçado pelo core). `complete` e `cancelled` são terminais.

## Regras aplicadas

| # | Regra |
|---|-------|
| 1 | Transição deve estar na tabela de transições válidas |
| 2 | `logicalTimestamp` deve ser maior que o último registrado |
| 3 | Para `confirm`, `in_transit`, `complete`: solicitante deve deter o lock |
| 4 | Estados terminais (`complete`, `cancelled`) não aceitam transições |

## Implementação atual

`src/saga_coordinator.py` — validação pura em memória. Sem dependências externas.

## Métricas expostas (Prometheus)

| Métrica | Tipo | Descrição |
|---------|------|-----------|
| `ridefleet_saga_transitions_total` | Counter | Total de transições (labels: from, to, result) |
| `ridefleet_saga_compensations_total` | Counter | Total de compensações iniciadas |
| `ridefleet_saga_active_rides` | Gauge | Corridas em estados não-terminais |
