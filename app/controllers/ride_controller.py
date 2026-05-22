from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.circuit_breaker_manager import circuit_breaker_manager, RECOVERY_TIMEOUT
from app.core.security import verify_api_key
from app.database import get_db
from app.dtos.ride_request_dto import LockRequestDTO, LockReleaseRequestDTO, RideRequestDTO, RideStatusUpdateDTO
from app.dtos.ride_response_dto import (
    AuditLogDTO,
    AuctionResultDTO,
    LockConflictDTO,
    LockPunishmentDTO,
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

router = APIRouter()


def _criar_servico(db: AsyncSession = Depends(get_db)) -> RideService:
    """Monta o grafo de dependências: repositórios => serviço."""
    return RideService(
        ride_repo=RideRepository(db),
        lock_repo=LockRepository(db),
        audit_repo=AuditRepository(db),
        proposal_repo=ProposalRepository(db),
        group_repo=GroupRepository(db),
    )

@router.post(
    "/rides",
    status_code=202,
    response_model=RideAcceptedDTO,
    summary="Solicitar nova corrida (ou delegação)",
    operation_id="createRide",
    tags=["rides"],
)
async def criar_corrida(
    dados: RideRequestDTO,
    servico: RideService = Depends(_criar_servico),
    grupo_autenticado: Group = Depends(verify_api_key),
) -> RideAcceptedDTO:
    """
    Registra a corrida em request, adquire lock e dispara o leilão
    em paralelo. Retorna 202 imediatamente.

    O grupo de origem é identificado pela API Key o campo originServiceId
    no body é informativo e não é usado para lookup.
    """
    return await servico.criar_corrida(dados, grupo_autenticado)

@router.get(
    "/rides",
    status_code=200,
    response_model=RideListDTO,
    summary="Listar corridas com filtros opcionais",
    operation_id="listRides",
    tags=["rides"],
)
async def listar_corridas(
    originServiceId: Optional[str] = Query(None, description="Filtra pelo grupo solicitante"),
    assignedServiceId: Optional[str] = Query(None, description="Filtra pelo grupo atribuído"),
    state: Optional[str] = Query(
        None,
        description="Filtra pelo estado da saga",
        enum=["request", "match", "confirm", "in_transit", "complete", "compensating", "cancelled"],
    ),
    limit: int = Query(50, ge=1, le=200, description="Máximo de resultados por página"),
    offset: int = Query(0, ge=0, description="Índice do primeiro resultado (zero-indexed)"),
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> RideListDTO:
    return await servico.listar_corridas(
        origin_service_id=originServiceId,
        assigned_service_id=assignedServiceId,
        state=state,
        limit=limit,
        offset=offset,
    )

@router.get(
    "/rides/{rideUuid}/proposals",
    status_code=200,
    response_model=AuctionResultDTO,
    summary="Consultar resultado do leilão (propostas coletadas pelo core)",
    operation_id="getRideProposals",
    tags=["rides"],
)
async def buscar_propostas(
    rideUuid: str,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> AuctionResultDTO:
    return await servico.buscar_propostas(rideUuid)

@router.get(
    "/rides/{rideUuid}/status",
    status_code=200,
    response_model=RideStatusDTO,
    summary="Consultar estado atual da corrida",
    operation_id="getRideStatus",
    tags=["rides"],
)
async def buscar_status(
    rideUuid: str,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> RideStatusDTO:
    return await servico.buscar_status(rideUuid)

@router.patch(
    "/rides/{rideUuid}/status",
    status_code=200,
    response_model=RideStatusDTO,
    summary="Atualizar estado da corrida (transição de saga)",
    operation_id="updateRideStatus",
    tags=["rides"],
)
async def atualizar_status(
    rideUuid: str,
    dados: RideStatusUpdateDTO,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> RideStatusDTO:
    circuit_breaker = circuit_breaker_manager.get_breaker(dados.serviceId)

    # Verificação do status do circuit breaker
    if not circuit_breaker.check_state():
        punishment = LockPunishmentDTO(
            error = "CIRCUIT_BREAKER_OPEN",
            message = f"O serviço {dados.serviceId} está temporariamente bloqueado devido a multiplos timeouts e/ou falhas.",
            service_id = dados.serviceId,
            recovery_time = RECOVERY_TIMEOUT
        )

        return JSONResponse(
            status_code=503,
            content=punishment.model_dump(mode="json")
        )
    
    resultado = await servico.atualizar_status(rideUuid, dados)
    
    circuit_breaker.success()
    
    return resultado

@router.get(
    "/rides/{rideUuid}/audit",
    status_code=200,
    response_model=AuditLogDTO,
    summary="Log causal completo da corrida (relógios lógicos)",
    operation_id="getRideAuditLog",
    tags=["rides"],
)
async def buscar_audit_log(
    rideUuid: str,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> AuditLogDTO:
    return await servico.buscar_audit_log(rideUuid)

@router.post(
    "/locks/{rideUuid}",
    summary="Adquirir lock distribuído sobre a corrida",
    operation_id="acquireLock",
    tags=["locks"],
)
async def adquirir_lock(
    rideUuid: str,
    dados: LockRequestDTO,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
):
    """
    Adquire ou renova o lock. Se outro serviço já detém o lock ativo,
    retorna 409 com o corpo LockConflict (rideUuid, heldBy, expiresAt).
    """
    lock_atual = await servico.lock_repo.buscar_por_ride(rideUuid)
    circuit_breaker = circuit_breaker_manager.get_breaker(dados.serviceId)

    agora = datetime.utcnow()

    # Verificação do status do circuit breaker
    if not circuit_breaker.check_state():
        punishment = LockPunishmentDTO(
            error = "CIRCUIT_BREAKER_OPEN",
            message = f"O serviço {dados.serviceId} está temporariamente bloqueado devido a multiplos timeouts e/ou falhas.",
            service_id = dados.serviceId,
            recovery_time = RECOVERY_TIMEOUT
        )

        return JSONResponse(
            status_code=503,
            content=punishment.model_dump(mode="json")
        )

    if (
        lock_atual
        and lock_atual.held_by != dados.serviceId
        and lock_atual.expires_at > agora
    ):
        conflito = LockConflictDTO(
            rideUuid=rideUuid,
            heldBy=lock_atual.held_by,
            expiresAt=lock_atual.expires_at,
        )
        return JSONResponse(
            status_code=409,
            content=conflito.model_dump(mode="json"),
        )

    resultado = await servico.adquirir_lock(rideUuid, dados)

    return JSONResponse(
        status_code=200,
        content=resultado.model_dump(mode="json"),
    )

@router.delete(
    "/locks/{rideUuid}",
    status_code=204,
    summary="Liberar lock distribuído",
    operation_id="releaseLock",
    tags=["locks"],
)
async def liberar_lock(
    rideUuid: str,
    dados: LockReleaseRequestDTO,
    servico: RideService = Depends(_criar_servico),
    _grupo_autenticado: Group = Depends(verify_api_key),
) -> Response:
    await servico.liberar_lock(rideUuid, dados)
    return Response(status_code=204)
