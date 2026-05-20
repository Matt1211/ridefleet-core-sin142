from prometheus_client import Gauge
from fastapi import Response

circuit_breaker_metric = Gauge(
    "ridefleet_circuit_breaker_state",
    "Estado do circuit breaker por parceiro (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
    ["service"],
)

def metrics_endpoint():
    circuit_breaker_metric.set(1000)

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)