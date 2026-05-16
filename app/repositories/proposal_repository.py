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

    async def listar_por_corrida(self, ride_uuid: str) -> List[RideProposal]:
        """Retorna todas as propostas de uma corrida."""
        resultado = await self.db.execute(
            select(RideProposal).where(RideProposal.ride_uuid == ride_uuid)
        )
        return list(resultado.scalars().all())
