"""
DTOs de entrada para os endpoints de corrida e notificações de saída.

Correspondem aos schemas RideRequest, RideStatusUpdate, LockRequest,
LockReleaseRequest, RideIncomingNotification e ProposalSubmission do
contrato OpenAPI.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LocationDTO(BaseModel):
    lat: float = Field(..., ge=-90, le=90, examples=[-20.7546])
    lng: float = Field(..., ge=-180, le=180, examples=[-42.8825])
    street: Optional[str] = Field(None, examples=["Av. P.H. Rolfs"])
    number: Optional[str] = Field(None, examples=["S/N"])
    city: Optional[str] = Field(None, examples=["Viçosa"])
    state: Optional[str] = Field(None, examples=["MG"])


class RideRequestDTO(BaseModel):
    originServiceId: str = Field(..., min_length=1, examples=["group-a"])
    passengerId: str = Field(..., min_length=1, examples=["passenger-42"])
    origin: LocationDTO
    destination: LocationDTO
    logicalTimestamp: int = Field(..., ge=0, examples=[17])
    auctionTimeoutSeconds: int = Field(default=10, ge=5, le=60, examples=[10])


class RideStatusUpdateDTO(BaseModel):
    newState: str = Field(
        ...,
        examples=["confirm"],
        description=(
            "Estado alvo da transição. Grupos podem enviar: "
            "confirm, in_transit, complete, compensating, cancelled."
        ),
    )
    serviceId: str = Field(..., min_length=1, examples=["group-b"])
    logicalTimestamp: int = Field(..., ge=0, examples=[25])


class LockRequestDTO(BaseModel):
    serviceId: str = Field(..., min_length=1, examples=["group-a"])
    ttlSeconds: int = Field(default=30, ge=1, le=300, examples=[30])


class LockReleaseRequestDTO(BaseModel):
    serviceId: str = Field(..., min_length=1, examples=["group-b"])


class RideIncomingNotificationDTO(BaseModel):
    """
    Payload enviado pelo core aos grupos via POST {serviceUrl}/rides/incoming
    durante o leilão. Anuncia uma nova corrida disponível para proposta.
    """

    rideUuid: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    origin: LocationDTO
    destination: LocationDTO
    originServiceId: str = Field(..., examples=["group-a"])
    passengerId: str = Field(..., examples=["passenger-42"])
    logicalTimestamp: int = Field(..., ge=0, examples=[17])
    auctionDeadline: datetime = Field(
        ..., description="Prazo limite para envio de proposta (UTC ISO-8601)."
    )


class ProposalSubmissionDTO(BaseModel):
    """
    Proposta enviada por um grupo ao core via POST {core}/rides/{rideUuid}/proposals.

    Substitui a antiga resposta síncrona 200 do convite POST {serviceUrl}/rides/incoming:
    no fluxo assíncrono o grupo responde aqui, até o auctionDeadline. O grupo
    proponente é determinado pela API Key autenticada — não pelo corpo.

    Regras herdadas do contrato (IncomingProposal):
        estimatedEta:   obrigatório, mínimo 1
        estimatedPrice: obrigatório, mínimo 0
    """

    estimatedEta: int = Field(
        ..., ge=1, examples=[300], description="ETA estimado em segundos (mínimo 1)."
    )
    estimatedPrice: float = Field(
        ..., ge=0, examples=[18.5], description="Preço estimado (mínimo 0)."
    )
    logicalTimestamp: int = Field(..., ge=0, examples=[21])
