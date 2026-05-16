"""
DTOs de saída para os endpoints de corrida.

Correspondem aos schemas RideAccepted, RideStatus, RideList,
AuctionResult, ProposalSummary, AuditLog, AuditEvent,
LockResponse e LockConflict do contrato OpenAPI.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

class RideAcceptedDTO(BaseModel):
    rideUuid: str
    logicalTimestamp: int
    message: str


class RideStatusDTO(BaseModel):
    rideUuid: str
    state: str
    assignedServiceId: Optional[str] = None
    logicalTimestamp: int
    lockHeldBy: Optional[str] = None
    lockExpiresAt: Optional[datetime] = None
    updatedAt: datetime


class RideListDTO(BaseModel):
    total: int
    limit: int
    offset: int
    rides: List[RideStatusDTO]


# ---------------------------------------------------------------------------
# Leilão / Propostas
# ---------------------------------------------------------------------------

class ProposalSummaryDTO(BaseModel):
    groupId: str
    serviceUrl: str
    status: str                           # accepted | passed | timeout | error
    estimatedEta: Optional[int] = None
    estimatedPrice: Optional[float] = None
    logicalTimestamp: Optional[int] = None
    responseTimeMs: Optional[int] = None
    respondedAt: Optional[datetime] = None


class AuctionResultDTO(BaseModel):
    rideUuid: str
    status: str                           # open | closed | no_proposals
    winner: Optional[str] = None
    auctionOpenedAt: datetime
    auctionClosedAt: Optional[datetime] = None
    proposals: List[ProposalSummaryDTO]


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

class AuditEventDTO(BaseModel):
    eventType: str
    serviceId: str
    logicalTimestamp: int
    wallClockTime: datetime
    payload: Optional[Dict[str, Any]] = None


class AuditLogDTO(BaseModel):
    rideUuid: str
    events: List[AuditEventDTO]


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

class LockResponseDTO(BaseModel):
    rideUuid: str
    serviceId: str
    expiresAt: datetime


class LockConflictDTO(BaseModel):
    rideUuid: str
    heldBy: str
    expiresAt: datetime
