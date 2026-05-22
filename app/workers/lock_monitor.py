"""
Monitor de locks expirados — background task que roda a cada 5 segundos.

Responsabilidades:
  1. Detectar locks vencidos (expires_at < agora).
  2. Registrar evento lock_expired no log de auditoria.
  3. Transicionar a corrida para compensating via StateMachineService.
  4. Publicar mensagem auction_request no RabbitMQ para re-leilão.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from app.core.circuit_breaker_manager import circuit_breaker_manager
from app.core.lamport_clock import lamport_clock
from app.database import AsyncSessionLocal
from app.models.ride import AuctionStatus, RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.rabbitmq import rabbitmq_broker
from app.repositories.audit_repository import AuditRepository
from app.repositories.lock_repository import LockRepository
from app.repositories.ride_repository import RideRepository
from app.services.state_machine_service import StateMachineService

logger = logging.getLogger(__name__)
_ESTADOS_TERMINAIS: set[str] = {RideStatus.COMPLETE.value, RideStatus.CANCELLED.value}
_AUCTION_TIMEOUT_COMPENSACAO = 10


def _parse_excluded(raw: Optional[str]) -> List[str]:
    """Converte a string CSV de grupos excluídos em lista."""
    if not raw:
        return []
    return [g for g in raw.split(",") if g]


async def monitorar_locks_expirados() -> None:
    logger.info("Monitor de locks iniciado")
    while True:
        await asyncio.sleep(5)
        try:
            async with AsyncSessionLocal() as db:
                lock_repo = LockRepository(db)
                ride_repo = RideRepository(db)
                audit_repo = AuditRepository(db)
                state_machine = StateMachineService(ride_repo, lock_repo, audit_repo)

                agora = datetime.utcnow()
                locks_expirados = await lock_repo.listar_expirados(agora)

                for lock in locks_expirados:
                    # Incremento do contador de falha do circuit breaker
                    circuit_breaker = circuit_breaker_manager.get_breaker(lock.held_by)
                    circuit_breaker.fail_increment()
                    
                    ride = await ride_repo.buscar_por_uuid(lock.ride_uuid)
                    if not ride:
                        await lock_repo.deletar(lock.ride_uuid)
                        continue


                    if (
                        ride.status in _ESTADOS_TERMINAIS
                        or ride.status == RideStatus.COMPENSATING.value
                    ):
                        await lock_repo.deletar(lock.ride_uuid)
                        continue

                    logger.warning(
                        "Lock expirado: corrida %s (detentor: '%s', expirou: %s)",
                        lock.ride_uuid,
                        lock.held_by,
                        lock.expires_at.strftime("%H:%M:%S"),
                    )

                    ts_expired = await lamport_clock.tick()
                    evento_expirado = RideAuditEvent(
                        ride_fk=ride.id,
                        ride_uuid=lock.ride_uuid,
                        event_type="lock_expired",
                        service_id=lock.held_by,
                        logical_timestamp=ts_expired,
                        payload={"expiredAt": lock.expires_at.isoformat()},
                    )
                    await audit_repo.registrar(evento_expirado)

                    # 2. Remove o lock expirado
                    await lock_repo.deletar(lock.ride_uuid)

                    # 3. Transiciona para compensating via máquina de estados
                    ride = await state_machine.aplicar_transicao_core(
                        ride, RideStatus.COMPENSATING.value
                    )

                    # 4. Atualiza os grupos excluídos e reinicia o status do leilão
                    excluidos = _parse_excluded(ride.excluded_groups)
                    excluidos.append(lock.held_by)
                    ride.excluded_groups = ",".join(excluidos)
                    ride.auction_status = AuctionStatus.OPEN.value
                    ride.auction_closed_at = None
                    await ride_repo.salvar(ride)

                    # 5. Publica no RabbitMQ para acionar o re-leilão
                    ts_pub = await lamport_clock.tick()
                    try:
                        await rabbitmq_broker.publish_event(
                            "auction_request",
                            lock.ride_uuid,
                            "core",
                            ts_pub,
                            {
                                "auctionTimeoutSeconds": _AUCTION_TIMEOUT_COMPENSACAO,
                                "excludedGroups": excluidos,
                            },
                        )
                        logger.info(
                            "Compensação publicada: corrida %s => re-leilão "
                            "(grupos excluídos: %s)",
                            lock.ride_uuid,
                            excluidos,
                        )
                    except Exception as rmq_exc:
                        logger.warning(
                            "Falha ao publicar compensação no RabbitMQ para corrida %s: %s",
                            lock.ride_uuid,
                            rmq_exc,
                        )

        except Exception as exc:
            logger.error("Erro no monitor de locks: %s", exc, exc_info=True)
