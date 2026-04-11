"""
RideFleet Core — API principal.

Implementa todos os endpoints definidos em spec/api/openapi.yaml v0.1.0.
Estado armazenado in-memory (adequado para v0.1.0 / desenvolvimento).

Endpoints:
  POST   /api/v1/rides
  POST   /api/v1/rides/{rideId}/proposals
  GET    /api/v1/rides/{rideId}/status
  PATCH  /api/v1/rides/{rideId}/status
  GET    /api/v1/rides/{rideId}/audit
  POST   /api/v1/locks/{rideId}
  DELETE /api/v1/locks/{rideId}
  GET    /api/v1/health
  GET    /metrics  (Prometheus)
"""

import sys
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Adiciona o diretório conformance ao path para importar os módulos internos
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "conformance"))

from lock_manager.src.lock_manager import LockManager
from saga_coordinator.src.saga_coordinator import SagaCoordinator, SagaState

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RideFleet Core API",
    version="0.1.0",
    description="Contrato de integração entre os serviços do ecossistema RideFleet.",
)

# ---------------------------------------------------------------------------
# Singletons (in-memory para v0.1.0)
# ---------------------------------------------------------------------------

lock_mgr = LockManager()
saga_coord = SagaCoordinator()

# ride_id -> SagaState
rides: dict[str, SagaState] = {}

# ride_id -> list of proposal dicts
proposals: dict[str, list[dict]] = {}

# ride_id -> list of audit event dicts
audit_logs: dict[str, list[dict]] = {}

# Relógio lógico de Lamport do core
_lamport_clock = 0


def lamport_tick(received: int = 0) -> int:
    global _lamport_clock
    _lamport_clock = max(_lamport_clock, received) + 1
    return _lamport_clock


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(ride_id: str, event_type: str, service_id: str, logical_ts: int, payload: dict):
    if ride_id not in audit_logs:
        audit_logs[ride_id] = []
    audit_logs[ride_id].append({
        "eventType": event_type,
        "serviceId": service_id,
        "logicalTimestamp": logical_ts,
        "wallClockTime": now_iso(),
        "payload": payload,
    })


# ---------------------------------------------------------------------------
# Pydantic models (request / response)
# ---------------------------------------------------------------------------

class Location(BaseModel):
    lat: float
    lng: float
    address: Optional[str] = None


class RideRequest(BaseModel):
    originServiceId: str
    passengerId: str
    origin: Location
    destination: Location
    logicalTimestamp: int


class RideProposal(BaseModel):
    serviceId: str
    estimatedEta: int
    estimatedPrice: float
    logicalTimestamp: int


class RideStatusUpdate(BaseModel):
    newState: str
    serviceId: str
    logicalTimestamp: int


class LockRequest(BaseModel):
    serviceId: str
    ttlSeconds: int = Field(default=30, ge=1, le=300)


class LockReleaseRequest(BaseModel):
    serviceId: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/v1/health")
def health_check():
    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": now_iso(),
    }


@app.post("/api/v1/rides", status_code=202)
def create_ride(body: RideRequest):
    ts = lamport_tick(body.logicalTimestamp)
    ride_id = str(uuid.uuid4())

    # Adquire lock imediatamente em nome do serviço de origem
    acquired = lock_mgr.acquire(ride_id, body.originServiceId, ttl_seconds=60)
    if not acquired:
        raise HTTPException(status_code=409, detail={
            "error": "lock_conflict",
            "detail": "Não foi possível adquirir lock para a corrida",
            "rideId": ride_id,
        })

    rides[ride_id] = SagaState(ride_id=ride_id, logical_timestamp=ts)
    proposals[ride_id] = []

    append_audit(ride_id, "ride_created", body.originServiceId, ts, {
        "passengerId": body.passengerId,
        "origin": body.origin.model_dump(),
        "destination": body.destination.model_dump(),
    })
    append_audit(ride_id, "lock_acquired", body.originServiceId, ts, {
        "ttlSeconds": 60,
    })

    return {
        "rideId": ride_id,
        "logicalTimestamp": ts,
        "message": "Corrida aceita para processamento",
    }


@app.post("/api/v1/rides/{rideId}/proposals")
def submit_proposal(rideId: str = Path(...), body: RideProposal = ...):
    if rideId not in rides:
        raise HTTPException(status_code=404, detail={"error": "not_found", "rideId": rideId})

    saga = rides[rideId]
    if saga.state != "request":
        raise HTTPException(status_code=409, detail={
            "error": "invalid_state",
            "detail": f"Corrida está em estado '{saga.state}', não 'request'",
            "rideId": rideId,
        })

    ts = lamport_tick(body.logicalTimestamp)
    proposal_id = str(uuid.uuid4())
    proposals[rideId].append({
        "proposalId": proposal_id,
        "serviceId": body.serviceId,
        "estimatedEta": body.estimatedEta,
        "estimatedPrice": body.estimatedPrice,
        "logicalTimestamp": body.logicalTimestamp,
    })

    append_audit(rideId, "proposal_submitted", body.serviceId, ts, {
        "proposalId": proposal_id,
        "estimatedEta": body.estimatedEta,
        "estimatedPrice": body.estimatedPrice,
    })

    return {
        "rideId": rideId,
        "proposalId": proposal_id,
        "logicalTimestamp": ts,
    }


@app.get("/api/v1/rides/{rideId}/status")
def get_ride_status(rideId: str = Path(...)):
    if rideId not in rides:
        raise HTTPException(status_code=404, detail={"error": "not_found", "rideId": rideId})

    saga = rides[rideId]
    return {
        "rideId": rideId,
        "state": saga.state,
        "assignedServiceId": saga.assigned_service_id,
        "logicalTimestamp": saga.logical_timestamp,
        "updatedAt": saga.updated_at.isoformat(),
    }


@app.patch("/api/v1/rides/{rideId}/status")
def update_ride_status(rideId: str = Path(...), body: RideStatusUpdate = ...):
    if rideId not in rides:
        raise HTTPException(status_code=404, detail={"error": "not_found", "rideId": rideId})

    saga = rides[rideId]
    lock = lock_mgr.get_lock(rideId)
    lock_holder = lock.service_id if lock else None

    result = saga_coord.apply_transition(
        saga=saga,
        new_state=body.newState,
        service_id=body.serviceId,
        logical_timestamp=body.logicalTimestamp,
        lock_holder=lock_holder,
    )

    if not result.accepted:
        status_code = 409 if "lock" in (result.error or "").lower() else 422
        raise HTTPException(status_code=status_code, detail={
            "error": "transition_rejected",
            "detail": result.error,
            "rideId": rideId,
        })

    ts = lamport_tick(body.logicalTimestamp)

    append_audit(rideId, "state_transition", body.serviceId, ts, {
        "from": saga.state,
        "to": body.newState,
    })

    # Libera lock automaticamente em estados terminais
    if body.newState in ("complete", "cancelled"):
        lock_mgr.release(rideId, lock_holder or body.serviceId)
        append_audit(rideId, "lock_released", "core", ts, {"reason": "terminal_state"})

    return {
        "rideId": rideId,
        "state": saga.state,
        "assignedServiceId": saga.assigned_service_id,
        "logicalTimestamp": saga.logical_timestamp,
        "updatedAt": saga.updated_at.isoformat(),
    }


@app.get("/api/v1/rides/{rideId}/audit")
def get_ride_audit(rideId: str = Path(...)):
    if rideId not in rides:
        raise HTTPException(status_code=404, detail={"error": "not_found", "rideId": rideId})

    events = sorted(
        audit_logs.get(rideId, []),
        key=lambda e: e["logicalTimestamp"],
    )
    return {"rideId": rideId, "events": events}


@app.post("/api/v1/locks/{rideId}")
def acquire_lock(rideId: str = Path(...), body: LockRequest = ...):
    acquired = lock_mgr.acquire(rideId, body.serviceId, body.ttlSeconds)

    if not acquired:
        existing = lock_mgr.get_lock(rideId)
        raise HTTPException(status_code=409, detail={
            "error": "lock_conflict",
            "rideId": rideId,
            "heldBy": existing.service_id if existing else "unknown",
            "expiresAt": datetime.fromtimestamp(
                existing.expires_at, tz=timezone.utc
            ).isoformat() if existing else None,
        })

    lock = lock_mgr.get_lock(rideId)
    ts = lamport_tick()

    if rideId in rides:
        append_audit(rideId, "lock_acquired", body.serviceId, ts, {
            "ttlSeconds": body.ttlSeconds,
        })

    return {
        "rideId": rideId,
        "serviceId": body.serviceId,
        "expiresAt": datetime.fromtimestamp(
            lock.expires_at, tz=timezone.utc
        ).isoformat(),
    }


@app.delete("/api/v1/locks/{rideId}", status_code=204)
def release_lock(rideId: str = Path(...), body: LockReleaseRequest = ...):
    lock = lock_mgr.get_lock(rideId)
    if lock is None:
        raise HTTPException(status_code=404, detail={
            "error": "lock_not_found",
            "detail": f"Nenhum lock ativo para rideId '{rideId}'",
        })

    if lock.service_id != body.serviceId:
        raise HTTPException(status_code=403, detail={
            "error": "forbidden",
            "detail": f"'{body.serviceId}' não detém o lock (detentor: '{lock.service_id}')",
        })

    lock_mgr.release(rideId, body.serviceId)
    ts = lamport_tick()

    if rideId in rides:
        append_audit(rideId, "lock_released", body.serviceId, ts, {"reason": "explicit_release"})


# ---------------------------------------------------------------------------
# Prometheus metrics (texto simples — formato exposition)
# ---------------------------------------------------------------------------

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    stats = lock_mgr.stats()
    active_rides = sum(1 for s in rides.values() if s.state not in ("complete", "cancelled"))
    lines = [
        "# HELP ridefleet_locks_active Locks distribuídos ativos",
        "# TYPE ridefleet_locks_active gauge",
        f"ridefleet_locks_active {stats['active_locks']}",
        "# HELP ridefleet_saga_active_rides Corridas em estados não-terminais",
        "# TYPE ridefleet_saga_active_rides gauge",
        f"ridefleet_saga_active_rides {active_rides}",
        "# HELP ridefleet_rides_total Total de corridas criadas",
        "# TYPE ridefleet_rides_total counter",
        f"ridefleet_rides_total {len(rides)}",
    ]
    return "\n".join(lines) + "\n"
