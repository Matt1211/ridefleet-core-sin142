"""
Repositório de grupos.

Responsabilidade única: conversar com o banco de dados.
Aqui ficam apenas as queries — sem regra de negócio.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group


class GroupRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def buscar_por_group_id(self, group_id: str) -> Optional[Group]:
        """Retorna o grupo com esse group_id, ou None se não existir."""
        resultado = await self.db.execute(
            select(Group).where(Group.group_id == group_id)
        )
        return resultado.scalar_one_or_none()

    async def buscar_por_api_key(self, api_key: str) -> Optional[Group]:
        """Retorna o grupo dono dessa API Key, ou None se inválida."""
        resultado = await self.db.execute(
            select(Group).where(Group.api_key == api_key)
        )
        return resultado.scalar_one_or_none()

    async def listar_todos(self) -> list[Group]:
        """Retorna todos os grupos ordenados pela data de registro."""
        resultado = await self.db.execute(
            select(Group).order_by(Group.registered_at)
        )
        return list(resultado.scalars().all())

    async def salvar(self, grupo: Group) -> Group:
        """Persiste o grupo e retorna o objeto atualizado."""
        self.db.add(grupo)
        await self.db.commit()
        await self.db.refresh(grupo)
        return grupo
