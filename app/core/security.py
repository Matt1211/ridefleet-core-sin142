"""
Dependências de segurança reutilizáveis.

verify_api_key pode ser injetada em qualquer endpoint que exija autenticação.
"""

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.database import get_db
from app.repositories.group_repository import GroupRepository
from app.exceptions.handlers import UnauthorizedException
from sqlalchemy.ext.asyncio import AsyncSession

api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    x_api_key: str | None = Depends(api_key_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Valida o header X-API-Key contra o banco de dados.
    Retorna o Group autenticado ou lança 401.
    """
    if not x_api_key:
        raise UnauthorizedException("Header X-API-Key ausente")

    repositorio = GroupRepository(db)
    grupo = await repositorio.buscar_por_api_key(x_api_key)

    if not grupo:
        raise UnauthorizedException("API Key inválida")

    return grupo
