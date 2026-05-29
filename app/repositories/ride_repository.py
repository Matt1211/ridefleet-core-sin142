"""
Repositório de corridas.

Responsabilidade única: queries no banco de dados para a entidade Ride.
Sem regras de negócio aqui.
"""

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride import Ride


class RideRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def criar(self, ride: Ride) -> Ride:
        """Persiste uma nova corrida e retorna o objeto atualizado."""
        self.db.add(ride)
        await self.db.commit()
        await self.db.refresh(ride)
        return ride

    async def buscar_por_uuid(self, ride_uuid: str) -> Optional[Ride]:
        """Retorna a corrida com esse UUID, ou None se não existir."""
        resultado = await self.db.execute(
            select(Ride).where(Ride.ride_uuid == ride_uuid)
        )
        return resultado.scalar_one_or_none()

    async def listar(
        self,
        origin_service_id: Optional[str] = None,
        assigned_service_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Ride], int]:
        """
        Retorna (rides, total) com filtros opcionais, paginação e ordem
        cronológica decrescente.
        """
        query = select(Ride)
        count_query = select(func.count()).select_from(Ride)

        if origin_service_id:
            query = query.where(Ride.origin_group_id == origin_service_id)
            count_query = count_query.where(Ride.origin_group_id == origin_service_id)

        if assigned_service_id:
            query = query.where(Ride.recipient_group_id == assigned_service_id)
            count_query = count_query.where(Ride.recipient_group_id == assigned_service_id)

        if state:
            query = query.where(Ride.status == state)
            count_query = count_query.where(Ride.status == state)

        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = query.order_by(Ride.registered_at.desc()).limit(limit).offset(offset)
        resultado = await self.db.execute(query)
        rides = list(resultado.scalars().all())

        return rides, total

    async def salvar(self, ride: Ride) -> Ride:
        """Persiste alterações numa corrida já existente."""
        self.db.add(ride)
        await self.db.commit()
        await self.db.refresh(ride)
        return ride
