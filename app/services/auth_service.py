"""
Serviço de autenticação e registro de grupos.

Responsabilidade: regras de negócio do fluxo de registro.
O serviço não sabe nada de HTTP — só recebe dados e devolve dados.
"""

import logging
import secrets

from app.dtos.auth_request_dto import GroupRegistrationDTO
from app.dtos.auth_response_dto import GroupCredentials, GroupInfo
from app.models.group import Group
from app.repositories.group_repository import GroupRepository

logger = logging.getLogger(__name__)


def _gerar_api_key() -> str:
    """Gera uma API Key única no formato rfk_<32 caracteres hex>."""
    return f"rfk_{secrets.token_hex(16)}"


class AuthService:

    def __init__(self, repositorio: GroupRepository):
        self.repositorio = repositorio

    async def registrar_grupo(self, dados: GroupRegistrationDTO) -> tuple["GroupCredentials", bool]:
        """
        Registra ou re-registra um grupo (upsert).

        - Primeiro registro: persiste com nova API Key, retorna (creds, True).
        - Re-registro (mesmo groupId): atualiza serviceUrl/groupName/contactEmail,
          mantém a mesma API Key, retorna (creds, False). Só pra testes isso aqui é inseguro eu sei
        """
        grupo_existente = await self.repositorio.buscar_por_group_id(dados.groupId)

        if grupo_existente:
            grupo_existente.service_url = dados.serviceUrl
            grupo_existente.group_name = dados.groupName
            if dados.contactEmail is not None:
                grupo_existente.contact_email = dados.contactEmail
            grupo_salvo = await self.repositorio.salvar(grupo_existente)
            logger.info(
                "Grupo re-registrado: '%s' | URL atualizada: %s",
                grupo_salvo.group_id,
                grupo_salvo.service_url,
            )
            return GroupCredentials(
                groupId=grupo_salvo.group_id,
                apiKey=grupo_salvo.api_key,
                registeredAt=grupo_salvo.registered_at,
            ), False

        novo_grupo = Group(
            group_id=dados.groupId,
            group_name=dados.groupName,
            service_url=dados.serviceUrl,
            contact_email=dados.contactEmail,
            api_key=_gerar_api_key(),
        )

        grupo_salvo = await self.repositorio.salvar(novo_grupo)
        logger.info(
            "Grupo registrado: '%s' | nome: '%s' | URL: %s",
            grupo_salvo.group_id,
            grupo_salvo.group_name,
            grupo_salvo.service_url,
        )

        return GroupCredentials(
            groupId=grupo_salvo.group_id,
            apiKey=grupo_salvo.api_key,
            registeredAt=grupo_salvo.registered_at,
        ), True

    async def listar_grupos(self) -> list[GroupInfo]:
        """
        Retorna a lista pública de todos os grupos registrados.
        A API Key nunca é exposta nessa listagem.
        """
        grupos = await self.repositorio.listar_todos()

        return [
            GroupInfo(
                groupId=g.group_id,
                groupName=g.group_name,
                serviceUrl=g.service_url,
                registeredAt=g.registered_at,
            )
            for g in grupos
        ]
