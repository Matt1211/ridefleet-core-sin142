"""
Serviço de corridas — orquestração do ciclo de vida de uma corrida.

Responsabilidades:
  - Criar corrida, publicar evento auction_request no RabbitMQ
  - Delegar validação e persistência de transições ao StateMachineService
  - Publicar auction_request para re-leilão quando o estado for compensating
  - Orquestrar lock distribuído (adquirir / liberar)
  - Consultar propostas e log de auditoria

O que NÃO é responsabilidade deste serviço:
  - Executar o leilão (auction_worker)
  - Monitorar locks expirados (lock_monitor)
  - Transições internas sem validação de grupo (StateMachineService.aplicar_transicao_core)
"""

import logging
from datetime import timedelta
from typing import List, Optional

from app.core.lamport_clock import lamport_clock
from app.dtos.ride_request_dto import LockRequestDTO, LockReleaseRequestDTO, RideRequestDTO, RideStatusUpdateDTO
from app.dtos.ride_response_dto import (
    AuditEventDTO,
    AuditLogDTO,
    AuctionResultDTO,
    LockConflictDTO,
    LockResponseDTO,
    ProposalSummaryDTO,
    RideAcceptedDTO,
    RideListDTO,
    RideStatusDTO,
)
from app.exceptions import ConflictException, ForbiddenException, NotFoundException, UnprocessableEntityException
from app.models.group import Group
from app.models.ride import AuctionStatus, Ride, RideStatus, _utcnow_naive
from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_lock import RideLock
from app.rabbitmq import rabbitmq_broker
from app.repositories.audit_repository import AuditRepository
from app.repositories.group_repository import GroupRepository
from app.repositories.lock_repository import LockRepository
from app.repositories.proposal_repository import ProposalRepository
from app.repositories.ride_repository import RideRepository
from app.services.state_machine_service import StateMachineService
from app.core.metrics import rides_delegated_total, rides_local_total

logger = logging.getLogger(__name__)

_LOCK_TTL_LEILAO_EXTRA = 30
_AUCTION_TIMEOUT_COMPENSACAO = 10
_ESTADOS_TERMINAIS: set[str] = {RideStatus.COMPLETE.value, RideStatus.CANCELLED.value}

def _ride_para_status_dto(ride: Ride, lock: Optional[RideLock] = None) -> RideStatusDTO:
    return RideStatusDTO(
        rideUuid=ride.ride_uuid,
        state=ride.status,
        assignedServiceId=ride.recipient_group_id,
        logicalTimestamp=ride.core_logical_ts,
        lockHeldBy=lock.held_by if lock else None,
        lockExpiresAt=lock.expires_at if lock else None,
        updatedAt=ride.updated_at,
    )


def _parse_excluded(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [g for g in raw.split(",") if g]

class RideService:

    def __init__(
        self,
        ride_repo: RideRepository,
        lock_repo: LockRepository,
        audit_repo: AuditRepository,
        proposal_repo: ProposalRepository,
        group_repo: GroupRepository,
    ):
        self.ride_repo = ride_repo
        self.lock_repo = lock_repo
        self.audit_repo = audit_repo
        self.proposal_repo = proposal_repo
        self.group_repo = group_repo
        self.state_machine = StateMachineService(ride_repo, lock_repo, audit_repo)

    async def criar_corrida(self, dados: RideRequestDTO, grupo_origem: Group) -> RideAcceptedDTO:
        """
        Registra a corrida em estado request, adquire o lock em nome do
        grupo solicitante e publica o evento auction_request no RabbitMQ.
        Retorna 202 imediatamente; o leilão roda em worker separado.

        O grupo de origem é determinado pela API Key autenticada, o campo
        originServiceId no body é tratado apenas como dado informativo.
        """
        logger.info(
            "Corrida recebida de '%s' (passageiro: %s, timeout leilão: %ds)",
            grupo_origem.group_id,
            dados.passengerId,
            dados.auctionTimeoutSeconds,
        )

        ts_core = await lamport_clock.update(dados.logicalTimestamp)

        ride = Ride(
            origin_group_fk=grupo_origem.id,
            origin_group_id=grupo_origem.group_id,
            passenger_uuid=dados.passengerId,
            origin_lat=dados.origin.lat,
            origin_lng=dados.origin.lng,
            origin_street=dados.origin.street,
            origin_number=dados.origin.number,
            origin_city=dados.origin.city,
            origin_state=dados.origin.state,
            dest_lat=dados.destination.lat,
            dest_lng=dados.destination.lng,
            dest_street=dados.destination.street,
            dest_number=dados.destination.number,
            dest_city=dados.destination.city,
            dest_state=dados.destination.state,
            logicalTimestamp=dados.logicalTimestamp,
            auctionTimeoutSeconds=dados.auctionTimeoutSeconds,
            core_logical_ts=ts_core,
            last_client_ts=dados.logicalTimestamp,
            status=RideStatus.REQUEST.value,
            auction_status=AuctionStatus.OPEN.value,
        )
        ride = await self.ride_repo.criar(ride)
        logger.info("Corrida %s salva no banco (grupo: '%s')", ride.ride_uuid, grupo_origem.group_id)

        expires = _utcnow_naive() + timedelta(
            seconds=dados.auctionTimeoutSeconds + _LOCK_TTL_LEILAO_EXTRA
        )
        await self.lock_repo.criar_ou_renovar(ride.ride_uuid, grupo_origem.group_id, expires, ride.id)
        logger.info(
            "Lock inicial adquirido: corrida %s => detentor '%s', expira %s",
            ride.ride_uuid,
            grupo_origem.group_id,
            expires.strftime("%H:%M:%S"),
        )

        await self._registrar_evento(
            ride.ride_uuid,
            ride.id,
            "ride_created",
            grupo_origem.group_id,
            ts_core,
            {
                "passengerId": dados.passengerId,
                "origin": {"lat": dados.origin.lat, "lng": dados.origin.lng},
                "destination": {"lat": dados.destination.lat, "lng": dados.destination.lng},
                "auctionTimeoutSeconds": dados.auctionTimeoutSeconds,
            },
        )

        # Publica o evento de leilão no RabbitMQ
        ts_auction = await lamport_clock.tick()
        try:
            await rabbitmq_broker.publish_event(
                "auction_request",
                ride.ride_uuid,
                "core",
                ts_auction,
                {
                    "auctionTimeoutSeconds": dados.auctionTimeoutSeconds,
                    "excludedGroups": [],
                },
            )
            logger.info(
                "Corrida %s enviada para a fila de leilão (timeout: %ds)",
                ride.ride_uuid,
                dados.auctionTimeoutSeconds,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao publicar auction_request para corrida %s: %s",
                ride.ride_uuid,
                exc,
            )

        rides_local_total.labels(service="core").inc()

        return RideAcceptedDTO(
            rideUuid=ride.ride_uuid,
            logicalTimestamp=ts_core,
            message="Corrida aceita para processamento. Leilão iniciado.",
        )


    async def listar_corridas(
        self,
        origin_service_id: Optional[str],
        assigned_service_id: Optional[str],
        state: Optional[str],
        limit: int,
        offset: int,
    ) -> RideListDTO:
        rides, total = await self.ride_repo.listar(
            origin_service_id=origin_service_id,
            assigned_service_id=assigned_service_id,
            state=state,
            limit=limit,
            offset=offset,
        )
        dtos = []
        for r in rides:
            lock = await self.lock_repo.buscar_por_ride(r.ride_uuid)
            dtos.append(_ride_para_status_dto(r, lock))

        return RideListDTO(total=total, limit=limit, offset=offset, rides=dtos)

    async def buscar_status(self, ride_uuid: str) -> RideStatusDTO:
        ride = await self._exigir_corrida(ride_uuid)
        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        return _ride_para_status_dto(ride, lock)

    async def atualizar_status(
        self, ride_uuid: str, dados: RideStatusUpdateDTO
    ) -> RideStatusDTO:
        """
        Valida e aplica uma transição de estado solicitada por um grupo.

        Fluxo:
          1. Pré-verifica idempotência — se já aplicado, retorna estado atual.
          2. Delega validação e persistência ao StateMachineService.
          3. Se o novo estado for `compensating`, atualiza grupos excluídos e
             publica `auction_request` no RabbitMQ para re-leilão.
        """
        logger.info(
            "Transição solicitada: corrida %s | => '%s' (serviceId: '%s', ts: %d)",
            ride_uuid,
            dados.newState,
            dados.serviceId,
            dados.logicalTimestamp,
        )

        ja_aplicado = await self.audit_repo.existe_idempotente(
            ride_uuid, dados.serviceId, dados.logicalTimestamp
        )
        if ja_aplicado:
            logger.info(
                "Transição idempotente ignorada: corrida %s (serviceId: '%s', ts: %d)",
                ride_uuid,
                dados.serviceId,
                dados.logicalTimestamp,
            )
            ride = await self._exigir_corrida(ride_uuid)
            lock = await self.lock_repo.buscar_por_ride(ride_uuid)
            return _ride_para_status_dto(ride, lock)

        try:
            ride, lock = await self.state_machine.aplicar_transicao_grupo(ride_uuid, dados)
        except (UnprocessableEntityException, ConflictException) as exc:
            ts_comp = await lamport_clock.tick()
            try:
                await rabbitmq_broker.publish_event(
                    "compensation_triggered",
                    ride_uuid,
                    "core",
                    ts_comp,
                    {"reason": str(exc), "failedState": dados.newState},
                )
            except Exception as publish_exc:
                logger.warning(
                    "Falha ao publicar compensation_triggered para corrida %s: %s",
                    ride_uuid,
                    publish_exc,
                )
            raise exc

        ts_pub = await lamport_clock.tick()
        try:
            await rabbitmq_broker.publish_event(
                "ride_status_changed",
                ride_uuid,
                dados.serviceId,
                ts_pub,
                {
                    "status": ride.status,
                    "assignedServiceId": ride.recipient_group_id,
                },
            )
        except Exception as publish_exc:
            logger.warning(
                "Falha ao publicar ride_status_changed para corrida %s: %s",
                ride_uuid,
                publish_exc,
            )

        if dados.newState == RideStatus.COMPENSATING.value:
            excluidos = _parse_excluded(ride.excluded_groups)
            excluidos.append(dados.serviceId)
            ride.excluded_groups = ",".join(excluidos)
            ride.auction_status = AuctionStatus.OPEN.value
            ride.auction_closed_at = None
            ride = await self.ride_repo.salvar(ride)

            logger.info(
                "Compensação acionada: corrida %s => re-leilão (grupos excluídos: %s)",
                ride_uuid,
                excluidos,
            )

            ts_comp = await lamport_clock.tick()
            try:
                await rabbitmq_broker.publish_event(
                    "auction_request",
                    ride_uuid,
                    "core",
                    ts_comp,
                    {
                        "auctionTimeoutSeconds": _AUCTION_TIMEOUT_COMPENSACAO,
                        "excludedGroups": excluidos,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "Falha ao publicar auction_request (compensação) para corrida %s: %s",
                    ride_uuid,
                    exc,
                )

            rides_delegated_total.labels(service="core").inc()

        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        return _ride_para_status_dto(ride, lock)

    async def buscar_propostas(self, ride_uuid: str) -> AuctionResultDTO:
        ride = await self._exigir_corrida(ride_uuid)
        propostas = await self.proposal_repo.listar_por_corrida(ride_uuid)

        vencedor = next(
            (p.group_id for p in propostas if p.is_winner == 1), None
        )

        return AuctionResultDTO(
            rideUuid=ride_uuid,
            status=ride.auction_status,
            winner=vencedor,
            auctionOpenedAt=ride.registered_at,
            auctionClosedAt=ride.auction_closed_at,
            proposals=[
                ProposalSummaryDTO(
                    groupId=p.group_id,
                    serviceUrl=p.service_url,
                    status=p.status,
                    estimatedEta=p.estimated_eta,
                    estimatedPrice=p.estimated_price,
                    logicalTimestamp=p.logical_timestamp,
                    responseTimeMs=p.response_time_ms,
                    respondedAt=p.responded_at,
                )
                for p in propostas
            ],
        )

    async def buscar_audit_log(self, ride_uuid: str) -> AuditLogDTO:
        await self._exigir_corrida(ride_uuid)
        eventos = await self.audit_repo.listar_por_corrida(ride_uuid)
        return AuditLogDTO(
            rideUuid=ride_uuid,
            events=[
                AuditEventDTO(
                    eventType=e.event_type,
                    serviceId=e.service_id,
                    logicalTimestamp=e.logical_timestamp,
                    wallClockTime=e.wall_clock_time,
                    payload=e.payload,
                )
                for e in eventos
            ],
        )

    async def adquirir_lock(
        self, ride_uuid: str, dados: LockRequestDTO
    ) -> LockResponseDTO:
        ride = await self._exigir_corrida(ride_uuid)

        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        agora = _utcnow_naive()

        if lock and lock.held_by != dados.serviceId and lock.expires_at > agora:
            raise ConflictException(
                f"Lock já detido por '{lock.held_by}' até {lock.expires_at.isoformat()}."
            )

        expires_at = agora + timedelta(seconds=dados.ttlSeconds)
        lock = await self.lock_repo.criar_ou_renovar(ride_uuid, dados.serviceId, expires_at, ride.id)
        logger.info(
            "Lock adquirido: corrida %s => detentor '%s' (TTL: %ds, expira: %s)",
            ride_uuid,
            dados.serviceId,
            dados.ttlSeconds,
            expires_at.strftime("%H:%M:%S"),
        )

        ts_core = await lamport_clock.tick()
        await self._registrar_evento(
            ride_uuid,
            ride.id,
            "lock_acquired",
            dados.serviceId,
            ts_core,
            {"ttlSeconds": dados.ttlSeconds, "expiresAt": expires_at.isoformat()},
        )

        return LockResponseDTO(
            rideUuid=ride_uuid,
            serviceId=dados.serviceId,
            expiresAt=lock.expires_at,
        )

    def _lock_conflict_response(self, ride_uuid: str, lock: RideLock) -> LockConflictDTO:
        return LockConflictDTO(
            rideUuid=ride_uuid,
            heldBy=lock.held_by,
            expiresAt=lock.expires_at,
        )

    async def liberar_lock(
        self, ride_uuid: str, dados: LockReleaseRequestDTO
    ) -> None:
        ride = await self._exigir_corrida(ride_uuid)

        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        if not lock:
            raise NotFoundException(f"Não há lock ativo para a corrida '{ride_uuid}'.")

        if lock.held_by != dados.serviceId:
            raise ForbiddenException(
                f"Serviço '{dados.serviceId}' não detém o lock. "
                f"Detentor: '{lock.held_by}'."
            )

        await self.lock_repo.deletar(ride_uuid)
        logger.info("Lock liberado: corrida %s (por '%s')", ride_uuid, dados.serviceId)

        ts_core = await lamport_clock.tick()
        await self._registrar_evento(
            ride_uuid,
            ride.id,
            "lock_released",
            dados.serviceId,
            ts_core,
            {"reason": "explicit_release"},
        )

    async def _exigir_corrida(self, ride_uuid: str) -> Ride:
        ride = await self.ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            raise NotFoundException(f"Corrida '{ride_uuid}' não encontrada.")
        return ride

    async def _registrar_evento(
        self,
        ride_uuid: str,
        ride_id: int,
        event_type: str,
        service_id: str,
        logical_timestamp: int,
        payload: dict,
    ) -> None:
        evento = RideAuditEvent(
            ride_fk=ride_id,
            ride_uuid=ride_uuid,
            event_type=event_type,
            service_id=service_id,
            logical_timestamp=logical_timestamp,
            payload=payload,
        )
        await self.audit_repo.registrar(evento)
