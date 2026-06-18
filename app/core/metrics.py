from prometheus_client import Gauge, Counter
from fastapi import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

circuit_breaker_metric = Gauge(
    "ridefleet_circuit_breaker_state",
    "Estado do circuit breaker por parceiro (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
    ["service"],
)

lamport_clock_metric = Gauge(
    "ridefleet_logical_timestamp",
    "Valor atual do relógio lógico de Lamport do core distribuído",
)

rides_local_total = Counter(
    "ridefleet_rides_local_total",
    "Quantidade de corridas locais",
    ["service"]
)

# novas metricas para locks e delegações

locks_acquired_total = Counter(
    "ridefleet_locks_acquired_total",
    "Quantidade de locks adquiridos",
    ["service"]
)

locks_expired_total = Counter(
    "ridefleet_locks_expired_total",
    "Quantidade de locks expirados",
    ["service"]
)

rides_delegated_total = Counter(
    "ridefleet_rides_delegated_total",
    "Quantidade de corridas delegadas",
    ["service"]
)

saga_transitions_total = Counter(
    "ridefleet_saga_transitions_total",
    "Quantidade de transições da saga",
    ["from_state", "to_state", "service"]
)

# Entregas de webhook do core para os grupos (via webhook_dispatcher)
webhook_deliveries_total = Counter(
    "ridefleet_webhook_deliveries_total",
    "Total de entregas de webhook do core para os grupos, por serviço e resultado",
    ["service", "status"]
)

def metrics_endpoint():
    from app.core.circuit_breaker_manager import circuit_breaker_manager

    for breaker in circuit_breaker_manager._breakers.values():
        if breaker.state.name == "OPEN":
            breaker.check_state()

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
