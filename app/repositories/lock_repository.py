"""
Repositório de locks distribuídos.

Responsabilidade única: queries no banco para a entidade RideLock.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride_lock import RideLock


class LockRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def buscar_por_ride(self, ride_uuid: str) -> Optional[RideLock]:
        """Retorna o lock ativo para a corrida, ou None."""
        resultado = await self.db.execute(
            select(RideLock).where(RideLock.ride_uuid == ride_uuid)
        )
        return resultado.scalar_one_or_none()

    async def criar_ou_renovar(
        self,
        ride_uuid: str,
        service_id: str,
        expires_at: datetime,
        ride_id: int,
    ) -> RideLock:
        """
        Cria um novo lock ou renova o existente (se o detentor for o mesmo).
        Não verifica conflitos — a verificação de titularidade é feita no serviço.
        """
        lock = await self.buscar_por_ride(ride_uuid)
        if lock:
            lock.held_by = service_id
            lock.expires_at = expires_at
            lock.acquired_at = datetime.utcnow()
        else:
            lock = RideLock(
                ride_fk=ride_id,
                ride_uuid=ride_uuid,
                held_by=service_id,
                expires_at=expires_at,
            )
            self.db.add(lock)
        await self.db.commit()
        await self.db.refresh(lock)
        return lock

    async def deletar(self, ride_uuid: str) -> bool:
        """Remove o lock da corrida. Retorna True se existia."""
        lock = await self.buscar_por_ride(ride_uuid)
        if not lock:
            return False
        await self.db.delete(lock)
        await self.db.commit()
        return True

    async def listar_expirados(self, agora: datetime) -> List[RideLock]:
        """Retorna todos os locks cujo expires_at <= agora."""
        resultado = await self.db.execute(
            select(RideLock).where(RideLock.expires_at <= agora)
        )
        return list(resultado.scalars().all())
