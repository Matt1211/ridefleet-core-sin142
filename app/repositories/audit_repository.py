"""
Repositório de eventos de auditoria.

Responsabilidade única: persistir e consultar RideAuditEvent.
"""

from typing import List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride_audit_event import RideAuditEvent


class AuditRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def registrar(self, evento: RideAuditEvent) -> RideAuditEvent:
        """Persiste um evento de auditoria."""
        self.db.add(evento)
        await self.db.commit()
        await self.db.refresh(evento)
        return evento

    async def listar_por_corrida(self, ride_uuid: str) -> List[RideAuditEvent]:
        """
        Retorna todos os eventos de uma corrida ordenados
        pelo relógio lógico de Lamport (crescente).
        """
        resultado = await self.db.execute(
            select(RideAuditEvent)
            .where(RideAuditEvent.ride_uuid == ride_uuid)
            .order_by(RideAuditEvent.logical_timestamp)
        )
        return list(resultado.scalars().all())

    async def existe_idempotente(
        self, ride_uuid: str, service_id: str, logical_timestamp: int
    ) -> bool:
        """
        Verifica se já existe um evento state_transition com o mesmo
        serviceId + logicalTimestamp (para idempotência do PATCH).
        """
        resultado = await self.db.execute(
            select(RideAuditEvent).where(
                RideAuditEvent.ride_uuid == ride_uuid,
                RideAuditEvent.event_type == "state_transition",
                RideAuditEvent.service_id == service_id,
                func.json_extract(RideAuditEvent.payload, "$.clientLogicalTimestamp") == logical_timestamp,
            )
        )
        return resultado.scalar_one_or_none() is not None
