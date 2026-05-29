import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.circuit_breaker_manager import RECOVERY_TIMEOUT, circuit_breaker_manager
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
    LockConflictDTO,
    LockPunishmentDTO,
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
# Dependências
# ---------------------------------------------------------------------------


def get_ride_service(db: AsyncSession = Depends(get_db)) -> RideService:
    """Monta o grafo de dependências: repositórios => serviço."""
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
# Helpers
# ---------------------------------------------------------------------------


def _verificar_circuit_breaker(service_id: str) -> JSONResponse | None:
    """
    Retorna JSONResponse 503 se o circuit breaker do serviço estiver aberto.
    Retorna None se o serviço estiver disponível.

    Centralizado aqui para evitar duplicação entre atualizar_status
    e adquirir_lock.
    """
    breaker = circuit_breaker_manager.get_breaker(service_id)
    if not breaker.check_state():
        return JSONResponse(
            status_code=503,
            content=LockPunishmentDTO(
                error="CIRCUIT_BREAKER_OPEN",
                message=(
                    f"O serviço {service_id} está temporariamente bloqueado "
                    "devido a múltiplos timeouts e/ou falhas."
                ),
                service_id=service_id,
                recovery_time=RECOVERY_TIMEOUT,
            ).model_dump(mode="json"),
        )
    return None


# ---------------------------------------------------------------------------
# Rotas — Corridas
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=RideAcceptedDTO,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Criar corrida e iniciar leilão",
    operation_id="createRide",
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
    status_code=status.HTTP_200_OK,
    summary="Listar corridas com filtros opcionais",
    operation_id="listRides",
)
async def listar_corridas(
    grupo: AuthGroup,
    service: Service,
    originServiceId: Optional[str] = Query(None, description="Filtrar por grupo de origem"),
    assignedServiceId: Optional[str] = Query(None, description="Filtrar por grupo atribuído"),
    state: Optional[str] = Query(
        None,
        description="Filtrar por estado da corrida",
        enum=["request", "match", "confirm", "in_transit", "complete", "compensating", "cancelled"],
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RideListDTO:
    return await service.listar_corridas(
        origin_service_id=originServiceId,
        assigned_service_id=assignedServiceId,
        state=state,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{rideUuid}",
    response_model=RideStatusDTO,
    status_code=status.HTTP_200_OK,
    summary="Consultar status de uma corrida (alias REST)",
    operation_id="getRideStatusAlias",
    include_in_schema=False,  # evita duplicação no OpenAPI — usa /status como canônico
)
async def buscar_status_alias(
    rideUuid: str,
    grupo: AuthGroup,
    service: Service,
) -> RideStatusDTO:
    return await service.buscar_status(rideUuid)


@router.get(
    "/{rideUuid}/status",
    response_model=RideStatusDTO,
    status_code=status.HTTP_200_OK,
    summary="Consultar estado atual da corrida",
    operation_id="getRideStatus",
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
    status_code=status.HTTP_200_OK,
    summary="Atualizar estado da corrida (transição de saga)",
    operation_id="updateRideStatus",
    responses={503: {"model": LockPunishmentDTO}},
    description=(
        "Aplica uma transição de estado validada pela máquina de estados. "
        "Requisições idempotentes (mesmo serviceId + logicalTimestamp) são "
        "ignoradas com segurança. Retorna 503 se o circuit breaker estiver aberto."
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

    if blocked := _verificar_circuit_breaker(body.serviceId):
        return blocked

    circuit_breaker = circuit_breaker_manager.get_breaker(body.serviceId)
    try:
        resultado = await service.atualizar_status(rideUuid, body)
        circuit_breaker.success()
        return resultado
    except Exception:
        circuit_breaker.failure()
        raise


@router.get(
    "/{rideUuid}/proposals",
    response_model=AuctionResultDTO,
    status_code=status.HTTP_200_OK,
    summary="Consultar resultado do leilão",
    operation_id="getRideProposals",
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
    status_code=status.HTTP_200_OK,
    summary="Log causal completo da corrida",
    operation_id="getRideAuditLog",
    description="Retorna todos os eventos registrados para a corrida em ordem cronológica.",
)
async def buscar_audit_log(
    rideUuid: str,
    grupo: AuthGroup,
    service: Service,
) -> AuditLogDTO:
    return await service.buscar_audit_log(rideUuid)


# ---------------------------------------------------------------------------
# Rotas — Locks
# ---------------------------------------------------------------------------


@router.post(
    "/{rideUuid}/lock",
    status_code=status.HTTP_200_OK,
    summary="Adquirir lock distribuído sobre a corrida",
    operation_id="acquireLock",
    tags=["locks"],
    responses={
        200: {"model": LockResponseDTO},
        409: {"model": LockConflictDTO},
        503: {"model": LockPunishmentDTO},
    },
    description=(
        "Adquire ou renova o lock para uma corrida. "
        "Retorna 409 se o lock já estiver detido por outro grupo. "
        "Retorna 503 se o circuit breaker estiver aberto."
    ),
)
async def adquirir_lock(
    rideUuid: str,
    body: LockRequestDTO,
    grupo: AuthGroup,
    service: Service,
):
    logger.info(
        "POST /rides/%s/lock | group=%s ttl=%ds",
        rideUuid,
        body.serviceId,
        body.ttlSeconds,
    )

    if blocked := _verificar_circuit_breaker(body.serviceId):
        return blocked

    circuit_breaker = circuit_breaker_manager.get_breaker(body.serviceId)
    try:
        resultado = await service.adquirir_lock(rideUuid, body)
        circuit_breaker.success()
        return resultado
    except Exception:
        circuit_breaker.failure()
        raise


@router.delete(
    "/{rideUuid}/lock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Liberar lock distribuído",
    operation_id="releaseLock",
    tags=["locks"],
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