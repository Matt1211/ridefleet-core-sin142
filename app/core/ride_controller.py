import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_api_key
from app.database import get_db
from app.dtos.ride_request_dto import (
    LockReleaseRequestDTO,
    LockRequestDTO,
    RideRequestDTO,
    RideStatusUpdateDTO,
)
from app.dtos.ride_response_dto import (
    AuditLogDTO,
    AuctionResultDTO,
    LockResponseDTO,
    RideAcceptedDTO,
    RideListDTO,
    RideStatusDTO,
)
from app.models.group import Group
from app.repositories.audit_repository import AuditRepository
from app.repositories.group_repository import GroupRepository
from app.repositories.lock_repository import LockRepository
from app.repositories.proposal_repository import ProposalRepository
from app.repositories.ride_repository import RideRepository
from app.services.ride_service import RideService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rides", tags=["rides"])


# ---------------------------------------------------------------------------
# Dependência: instancia RideService com todas as dependências injetadas
# ---------------------------------------------------------------------------

def get_ride_service(
    db: AsyncSession = Depends(get_db),
) -> RideService:
    return RideService(
        ride_repo=RideRepository(db),
        lock_repo=LockRepository(db),
        audit_repo=AuditRepository(db),
        proposal_repo=ProposalRepository(db),
        group_repo=GroupRepository(db),
    )


# Aliases para injeção limpa nos handlers
AuthGroup = Annotated[Group, Depends(verify_api_key)]
Service = Annotated[RideService, Depends(get_ride_service)]


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=RideAcceptedDTO,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Criar corrida e iniciar leilão",
    description=(
        "Registra uma nova corrida em estado `request`, adquire o lock inicial "
        "em nome do grupo solicitante e publica o evento `auction_request` no "
        "RabbitMQ. Retorna imediatamente — o leilão roda em worker separado."
    ),
)
async def criar_corrida(
    body: RideRequestDTO,
    grupo: AuthGroup,
    service: Service,
) -> RideAcceptedDTO:
    logger.info(
        "POST /rides | group=%s passenger=%s",
        grupo.group_id,
        body.passengerId,
    )
    return await service.criar_corrida(body, grupo)


@router.get(
    "",
    response_model=RideListDTO,
    summary="Listar corridas",
    description="Retorna corridas paginadas com filtros opcionais por grupo e estado.",
)
async def listar_corridas(
    grupo: AuthGroup,
    service: Service,
    origin_service_id: Optional[str] = Query(None, description="Filtrar por grupo de origem"),
    assigned_service_id: Optional[str] = Query(None, description="Filtrar por grupo atribuído"),
    state: Optional[str] = Query(None, description="Filtrar por estado da corrida"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RideListDTO:
    return await service.listar_corridas(
        origin_service_id=origin_service_id,
        assigned_service_id=assigned_service_id,
        state=state,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{rideUuid}",
    response_model=RideStatusDTO,
    summary="Consultar status de uma corrida",
)
async def buscar_status(
    rideUuid: str,
    grupo: AuthGroup,
    service: Service,
) -> RideStatusDTO:
    return await service.buscar_status(rideUuid)


@router.patch(
    "/{rideUuid}/status",
    response_model=RideStatusDTO,
    summary="Atualizar estado da corrida",
    description=(
        "Aplica uma transição de estado validada pela máquina de estados. "
        "Requisições idempotentes (mesmo serviceId + logicalTimestamp) são "
        "ignoradas com segurança."
    ),
)
async def atualizar_status(
    rideUuid: str,
    body: RideStatusUpdateDTO,
    grupo: AuthGroup,
    service: Service,
) -> RideStatusDTO:
    logger.info(
        "PATCH /rides/%s/status | group=%s newState=%s ts=%d",
        rideUuid,
        body.serviceId,
        body.newState,
        body.logicalTimestamp,
    )
    return await service.atualizar_status(rideUuid, body)


@router.get(
    "/{rideUuid}/proposals",
    response_model=AuctionResultDTO,
    summary="Consultar propostas e resultado do leilão",
    description=(
        "Retorna todas as propostas válidas recebidas, o vencedor selecionado "
        "e o status atual do leilão. Disponível para qualquer grupo autenticado."
    ),
)
async def buscar_propostas(
    rideUuid: str,
    grupo: AuthGroup,
    service: Service,
) -> AuctionResultDTO:
    return await service.buscar_propostas(rideUuid)


@router.get(
    "/{rideUuid}/audit",
    response_model=AuditLogDTO,
    summary="Log de auditoria da corrida",
    description="Retorna todos os eventos registrados para a corrida em ordem cronológica.",
)
async def buscar_audit_log(
    rideUuid: str,
    grupo: AuthGroup,
    service: Service,
) -> AuditLogDTO:
    return await service.buscar_audit_log(rideUuid)


@router.post(
    "/{rideUuid}/lock",
    response_model=LockResponseDTO,
    status_code=status.HTTP_200_OK,
    summary="Adquirir lock distribuído",
    description=(
        "Adquire ou renova o lock para uma corrida. "
        "Retorna 409 se o lock já estiver detido por outro grupo."
    ),
)
async def adquirir_lock(
    rideUuid: str,
    body: LockRequestDTO,
    grupo: AuthGroup,
    service: Service,
) -> LockResponseDTO:
    logger.info(
        "POST /rides/%s/lock | group=%s ttl=%ds",
        rideUuid,
        body.serviceId,
        body.ttlSeconds,
    )
    return await service.adquirir_lock(rideUuid, body)


@router.delete(
    "/{rideUuid}/lock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Liberar lock distribuído",
    description="Libera o lock de uma corrida. Apenas o detentor atual pode liberar.",
)
async def liberar_lock(
    rideUuid: str,
    body: LockReleaseRequestDTO,
    grupo: AuthGroup,
    service: Service,
) -> None:
    logger.info(
        "DELETE /rides/%s/lock | group=%s",
        rideUuid,
        body.serviceId,
    )
    await service.liberar_lock(rideUuid, body)