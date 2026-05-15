"""
Controller de autenticação endpoints públicos e protegidos de grupos.

Responsabilidade: receber a requisição HTTP, montar as dependências
e devolver a resposta. Toda regra de negócio fica no AuthService.
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_api_key
from app.database import get_db
from app.dtos.auth_request_dto import GroupRegistrationDTO
from app.dtos.auth_response_dto import GroupCredentials, GroupInfo
from app.models.group import Group
from app.repositories.group_repository import GroupRepository
from app.services.auth_service import AuthService

router = APIRouter()


def _criar_servico(db: AsyncSession = Depends(get_db)) -> AuthService:
    """Monta o grafo de dependências: repositório => serviço."""
    repositorio = GroupRepository(db)
    return AuthService(repositorio)


@router.post(
    "/groups/register",
    status_code=201,
    response_model=GroupCredentials,
    summary="Registrar grupo e obter API Key de acesso",
    operation_id="registerGroup",
)
async def registrar_grupo(
    dados: GroupRegistrationDTO,
    servico: AuthService = Depends(_criar_servico),
) -> GroupCredentials:
    """
    Endpoint público não requer autenticação.
    Registra o grupo e devolve a API Key gerada.
    """
    return await servico.registrar_grupo(dados)


@router.get(
    "/groups/register",
    status_code=200,
    response_model=List[GroupInfo],
    summary="Listar grupos registrados",
    operation_id="listGroups",
    tags=["auth"],
)
async def listar_grupos(
    servico: AuthService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> List[GroupInfo]:
    """
    Requer autenticação via header X-API-Key.
    A API Key de cada grupo não é exposta na resposta.
    """
    return await servico.listar_grupos()
