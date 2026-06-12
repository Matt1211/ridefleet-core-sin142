"""
Webhook Dispatcher — bridge entre o broker e os grupos.

Consome três filas de saída do core:
  ridefleet.groups.ride_created   → POST {serviceUrl}/rides/incoming
  ridefleet.groups.status         → POST {serviceUrl}/rides/{rideUuid}/status
  ridefleet.compensations         → POST {serviceUrl}/rides/{rideUuid}/compensation

Retry com backoff exponencial: até 5 tentativas, início em 10 s.
Falhas permanentes: RideAuditEvent(event_type=webhook_failed).
Métrica: ridefleet_webhook_deliveries_total{service, status}
"""

import asyncio
import json
import logging

import httpx

from app.core.http_client import http_client
from app.core.lamport_clock import lamport_clock
from app.core.metrics import webhook_deliveries_total
from app.database import AsyncSessionLocal
from app.models.ride_audit_event import RideAuditEvent
from app.rabbitmq import rabbitmq_broker
from app.repositories.group_repository import GroupRepository
from app.repositories.ride_repository import RideRepository

logger = logging.getLogger(__name__)

_MAX_TENTATIVAS = 5
_BACKOFF_INICIAL_S = 10.0

_QUEUE_RIDE_CREATED  = "ridefleet.groups.ride_created"
_QUEUE_STATUS        = "ridefleet.groups.status"
_QUEUE_COMPENSATIONS = "ridefleet.compensations"


def _endpoint_para_evento(service_url: str, event_type: str, ride_uuid: str) -> str:
    if event_type == "ride_created":
        return f"{service_url}/rides/incoming"
    if event_type == "ride_status_changed":
        return f"{service_url}/rides/{ride_uuid}/status"
    return f"{service_url}/rides/{ride_uuid}/compensation"


async def _entregar_webhook(group_id: str, url: str, payload: dict) -> bool:
    """Tenta entregar o webhook com backoff exponencial. Retorna True em sucesso."""
    delay = _BACKOFF_INICIAL_S
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            resp = await http_client.post(url, json=payload, timeout=10.0)
            if resp.status_code < 500:
                webhook_deliveries_total.labels(service=group_id, status="success").inc()
                logger.info(
                    "Webhook entregue: grupo=%s url=%s status=%d tentativa=%d",
                    group_id, url, resp.status_code, tentativa,
                )
                return True
            logger.warning(
                "Webhook 5xx (%d): grupo=%s url=%s tentativa=%d/%d",
                resp.status_code, group_id, url, tentativa, _MAX_TENTATIVAS,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(
                "Webhook erro de rede: grupo=%s url=%s tentativa=%d/%d: %s",
                group_id, url, tentativa, _MAX_TENTATIVAS, exc,
            )
        if tentativa < _MAX_TENTATIVAS:
            await asyncio.sleep(delay)
            delay *= 2

    webhook_deliveries_total.labels(service=group_id, status="failed").inc()
    return False


async def _auditar_falha_permanente(ride_uuid: str, group_id: str, event_type: str) -> None:
    if not ride_uuid:
        return
    try:
        async with AsyncSessionLocal() as db:
            ride = await RideRepository(db).buscar_por_uuid(ride_uuid)
            if not ride:
                return
            ts = await lamport_clock.tick()
            db.add(RideAuditEvent(
                ride_fk=ride.id,
                ride_uuid=ride_uuid,
                event_type="webhook_failed",
                service_id=group_id,
                logical_timestamp=ts,
                payload={"targetEvent": event_type, "groupId": group_id},
            ))
            await db.commit()
    except Exception as exc:
        logger.error("Falha ao registrar webhook_failed no audit: %s", exc)


async def _despachar_para_grupo(
    group_id: str,
    service_url: str,
    event_type: str,
    ride_uuid: str,
    payload: dict,
) -> None:
    url = _endpoint_para_evento(service_url, event_type, ride_uuid)
    ok = await _entregar_webhook(group_id, url, payload)
    if not ok:
        await _auditar_falha_permanente(ride_uuid, group_id, event_type)


async def _processar_mensagem(body: dict) -> None:
    event_type: str = body.get("eventType", "")
    ride_uuid: str  = body.get("rideId") or ""

    async with AsyncSessionLocal() as db:
        grupos = await GroupRepository(db).listar_todos()

    await asyncio.gather(
        *[
            _despachar_para_grupo(g.group_id, g.service_url, event_type, ride_uuid, body)
            for g in grupos
        ],
        return_exceptions=True,
    )


async def _consumir_fila(queue_name: str) -> None:
    channel = await rabbitmq_broker.connection.channel()
    await channel.set_qos(prefetch_count=10)
    queue = await channel.declare_queue(queue_name, durable=True)
    logger.info("Webhook dispatcher escutando: %s", queue_name)

    async with queue.iterator() as messages:
        async for message in messages:
            try:
                body = json.loads(message.body.decode("utf-8"))
                await _processar_mensagem(body)
                await message.ack()
            except Exception as exc:
                logger.error(
                    "Erro ao processar mensagem em %s: %s", queue_name, exc, exc_info=True
                )
                await message.nack(requeue=True)


async def iniciar_dispatcher() -> None:
    if not rabbitmq_broker.connection:
        logger.warning("RabbitMQ não conectado — webhook dispatcher não iniciado.")
        return

    await asyncio.gather(
        _consumir_fila(_QUEUE_RIDE_CREATED),
        _consumir_fila(_QUEUE_STATUS),
        _consumir_fila(_QUEUE_COMPENSATIONS),
    )
