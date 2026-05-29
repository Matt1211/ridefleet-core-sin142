"""
Serviço de máquina de estados — responsável EXCLUSIVAMENTE por:
  - Validar transições de estado
  - Persistir a transição no banco de dados
  - Registrar o evento de auditoria correspondente

Não publica eventos no RabbitMQ, não faz chamadas HTTP e não conhece
contexto de leilão. Essa separação mantém o serviço testável e coeso.
"""

import logging
from typing import Optional

from app.core.lamport_clock import lamport_clock
from app.dtos.ride_request_dto import RideStatusUpdateDTO
from app.exceptions import ConflictException, NotFoundException, UnprocessableEntityException
from app.models.ride import Ride, RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_lock import RideLock
from app.repositories.audit_repository import AuditRepository
from app.repositories.lock_repository import LockRepository
from app.repositories.ride_repository import RideRepository
from app.core.metrics import saga_transitions_total

logger = logging.getLogger(__name__)

_TRANSICOES_VALIDAS: dict[str, set[str]] = {
    RideStatus.REQUEST.value: {RideStatus.CANCELLED.value},
    RideStatus.MATCH.value: {
        RideStatus.CONFIRM.value,
        RideStatus.COMPENSATING.value,
        RideStatus.CANCELLED.value,
    },
    RideStatus.CONFIRM.value: {
        RideStatus.IN_TRANSIT.value,
        RideStatus.COMPENSATING.value,
        RideStatus.CANCELLED.value,
    },
    RideStatus.IN_TRANSIT.value: {
        RideStatus.COMPLETE.value,
        RideStatus.COMPENSATING.value,
        RideStatus.CANCELLED.value,
    },
    RideStatus.COMPLETE.value: set(),
    RideStatus.COMPENSATING.value: set(),
    RideStatus.CANCELLED.value: set(),
}

_REQUER_LOCK: set[str] = {
    RideStatus.CONFIRM.value,
    RideStatus.IN_TRANSIT.value,
    RideStatus.COMPLETE.value,
}

_ESTADOS_TERMINAIS: set[str] = {RideStatus.COMPLETE.value, RideStatus.CANCELLED.value}


class StateMachineService:
    """
    Máquina de estados distribuída para o ciclo de vida de corridas.

    Dois pontos de entrada:
      - aplicar_transicao_grupo: validação completa (lock, ordem causal,
        transição permitida) para o endpoint PATCH /rides/{rideUuid}/status.
      - aplicar_transicao_core: transição interna sem checagens de grupo
        (usada pelo lock monitor e pelo worker de compensação).
    """

    def __init__(
        self,
        ride_repo: RideRepository,
        lock_repo: LockRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self.ride_repo = ride_repo
        self.lock_repo = lock_repo
        self.audit_repo = audit_repo

    async def aplicar_transicao_grupo(
        self,
        ride_uuid: str,
        dados: RideStatusUpdateDTO,
    ) -> tuple[Ride, Optional[RideLock]]:
        """
        Valida e aplica uma transição de estado solicitada por um grupo.

        Pré-condição: a idempotência já foi verificada pelo chamador
        (audit_repo.existe_idempotente). Esta função assume que a transição
        ainda não foi registrada.

        Retorna (ride_atualizado, lock_atual).
        """
        ride = await self.ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            raise NotFoundException(f"Corrida '{ride_uuid}' não encontrada.")

        if not _TRANSICOES_VALIDAS.get(ride.status):
            raise UnprocessableEntityException(
                f"Corrida em estado terminal '{ride.status}' — nenhuma transição permitida."
            )

        if dados.newState not in _TRANSICOES_VALIDAS.get(ride.status, set()):
            raise UnprocessableEntityException(
                f"Transição '{ride.status}' => '{dados.newState}' não é permitida."
            )

        if dados.logicalTimestamp <= ride.last_client_ts:
            raise UnprocessableEntityException(
                f"logicalTimestamp {dados.logicalTimestamp} não é maior que o "
                f"último registrado ({ride.last_client_ts}). Evento atrasado ou duplicado."
            )

        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        if dados.newState in _REQUER_LOCK:
            if not lock or lock.held_by != dados.serviceId:
                detentor = lock.held_by if lock else "nenhum"
                raise ConflictException(
                    f"Serviço '{dados.serviceId}' não detém o lock desta corrida. "
                    f"Detentor atual: '{detentor}'."
                )

        old_state = ride.status
        ts_core = await lamport_clock.update(dados.logicalTimestamp)

        logger.info(
            "Transição de estado: corrida %s | '%s' => '%s' (serviceId: '%s', ts: %d)",
            ride_uuid,
            old_state,
            dados.newState,
            dados.serviceId,
            ts_core,
        )

        ride.status = dados.newState
        saga_transitions_total.labels(
             from_state=old_state,
            to_state=dados.newState,
            service="core"
        ).inc()

        ride.core_logical_ts = ts_core
        ride.last_client_ts = dados.logicalTimestamp

        # Estados terminais: core libera o lock automaticamente
        if dados.newState in _ESTADOS_TERMINAIS:
            await self.lock_repo.deletar(ride_uuid)
            lock = None

        ride = await self.ride_repo.salvar(ride)

        await self._registrar_evento(
            ride_uuid,
            ride.id,
            "state_transition",
            dados.serviceId,
            ts_core,
            {
                "fromState": old_state,
                "toState": dados.newState,
                "clientLogicalTimestamp": dados.logicalTimestamp,
            },
        )

        lock = await self.lock_repo.buscar_por_ride(ride_uuid)
        return ride, lock

    async def aplicar_transicao_core(
        self,
        ride: Ride,
        new_state: str,
    ) -> Ride:
        """
        Aplica uma transição de estado iniciada internamente pelo core,
        sem validações de grupo, lock ou ordem causal.

        Registra um evento 'state_transition' com serviceId='core'.
        Retorna o ride persistido com os novos valores.
        """
        old_state = ride.status
        ts_core = await lamport_clock.tick()

        logger.info(
            "Transição interna (core): corrida %s | '%s' => '%s'",
            ride.ride_uuid,
            old_state,
            new_state,
        )

        ride.status = new_state

        saga_transitions_total.labels(
             from_state=old_state,
            to_state=new_state,
            service="core"
        ).inc()

        ride.core_logical_ts = ts_core

        ride = await self.ride_repo.salvar(ride)

        await self._registrar_evento(
            ride.ride_uuid,
            ride.id,
            "state_transition",
            "core",
            ts_core,
            {"fromState": old_state, "toState": new_state},
        )

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
