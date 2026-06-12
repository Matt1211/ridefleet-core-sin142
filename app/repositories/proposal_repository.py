"""
Repositório de propostas de leilão.

Responsabilidade única: persistir e consultar RideProposal.
"""

from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride_proposal import RideProposal


class ProposalRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def criar(self, proposta: RideProposal) -> RideProposal:
        """Persiste uma proposta de leilão."""
        self.db.add(proposta)
        await self.db.commit()
        await self.db.refresh(proposta)
        return proposta

    async def criar_varios(self, propostas: List[RideProposal]) -> List[RideProposal]:
        """Persiste múltiplas propostas em batch."""
        for p in propostas:
            self.db.add(p)
        await self.db.commit()
        for p in propostas:
            await self.db.refresh(p)
        return propostas

    async def upsert_por_ride_e_grupo(self, proposta: RideProposal) -> RideProposal:
        """Cria ou atualiza a proposta de um grupo para uma corrida (idempotente)."""
        resultado = await self.db.execute(
            select(RideProposal)
            .where(RideProposal.ride_uuid == proposta.ride_uuid)
            .where(RideProposal.group_id == proposta.group_id)
        )
        existente = resultado.scalar_one_or_none()
        if existente:
            existente.status = proposta.status
            existente.estimated_eta = proposta.estimated_eta
            existente.estimated_price = proposta.estimated_price
            existente.logical_timestamp = proposta.logical_timestamp
            existente.responded_at = proposta.responded_at
            await self.db.commit()
            await self.db.refresh(existente)
            return existente
        self.db.add(proposta)
        await self.db.commit()
        await self.db.refresh(proposta)
        return proposta

    async def listar_por_corrida(self, ride_uuid: str) -> List[RideProposal]:
        """Retorna todas as propostas de uma corrida."""
        resultado = await self.db.execute(
            select(RideProposal).where(RideProposal.ride_uuid == ride_uuid)
        )
        return list(resultado.scalars().all())
