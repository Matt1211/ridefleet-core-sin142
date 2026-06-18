"""
Webhook dispatcher — único canal de SAÍDA do core para os grupos.

Consome as três filas de grupos e entrega os webhooks aos serviços inscritos,
de modo que o auction_worker não precise mais fazer chamadas HTTP diretas.

Filas consumidas (declaradas/bound em app/rabbitmq.py):
  - ridefleet.groups.ride_created     (routing key: ride_created)
  - ridefleet.groups.status           (routing key: ride_status_changed)
  - ridefleet.compensations           (routing key: compensation_triggered)

Mapeamento evento -> webhook do grupo (contrato em spec/api/openapi.yaml#webhooks):
  - ride_created          -> POST {serviceUrl}/rides/incoming   (todos os grupos elegíveis)
  - ride_status_changed   -> POST {serviceUrl}/rides/{uuid}/assigned   (somente o vencedor,
                             quando status == match). Demais estados não têm webhook de
                             grupo definido no contrato — apenas observabilidade, sem POST.
  - compensation_triggered-> o contrato NÃO define webhook de grupo para compensação
                             (topics.yaml: "grupos devem reverter ações locais", sem rota
                             padronizada). Por ora consumimos e auditamos, sem POST, para
                             não gerar 404 em todos os grupos. Ver tarefa #14.

Resiliência:
  - Retry com backoff exponencial por entrega (padrão: 5 tentativas, base 10s ->
    10, 20, 40, 80s entre as tentativas). 5xx/timeout/erro de conexão são re-tentados;
    respostas < 500 (incl. 4xx) contam como entregues (o grupo recebeu).
  - Falha permanente (todas as tentativas falharam) -> RideAuditEvent
    (event_type="webhook_failed") + métrica ridefleet_webhook_deliveries_total{,status="failed"}.
  - Laço externo de reconexão ao RabbitMQ, espelhando o auction_worker.

ATENÇÃO (dependência da Fase 3): a entrega de /assigned precisa de um payload
enriquecido no evento ride_status_changed (origin, destination, passengerId,
originServiceId, lockExpiresAt). Hoje o evento só carrega {status, assignedServiceId}
— enquanto a tarefa #10 não enriquece o payload, a entrega de /assigned é adiada
com um aviso (não quebra). Este worker NÃO deve ser ligado no lifespan antes da
Fase 3 (tarefa #13), senão duplicaria o convite com o HTTP direto ainda ativo.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.http_client import http_client
from app.core.lamport_clock import lamport_clock
from app.core.metrics import webhook_deliveries_total
from app.database import AsyncSessionLocal
from app.models.ride import RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.rabbitmq import rabbitmq_broker
from app.repositories.group_repository import GroupRepository
from app.repositories.ride_repository import RideRepository

logger = logging.getLogger(__name__)

# Política de retry/entrega — parametrizável via ambiente.
_MAX_TENTATIVAS = int(os.getenv("WEBHOOK_MAX_RETRIES", "5"))
_BACKOFF_BASE_SEGUNDOS = float(os.getenv("WEBHOOK_BACKOFF_BASE", "10"))
_TIMEOUT_POST = float(os.getenv("WEBHOOK_POST_TIMEOUT", "10"))
_PREFETCH = int(os.getenv("WEBHOOK_PREFETCH", "10"))

_FILAS = (
    "ridefleet.groups.ride_created",
    "ridefleet.groups.status",
    "ridefleet.compensations",
)

# Uma "entrega" é uma tripla (group_id, url, corpo) a ser POSTada.
Entrega = Tuple[str, str, Dict[str, Any]]


# ---------------------------------------------------------------------------
# Entrega HTTP com retry/backoff exponencial
# ---------------------------------------------------------------------------


async def _entregar(group_id: str, url: str, corpo: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Faz POST em `url` com retry/backoff exponencial.

    Retorna (True, None) se entregue (resposta < 500), ou (False, motivo) se todas
    as tentativas falharam. Atualiza a métrica deliveries_total em ambos os casos.
    """
    erro: Optional[str] = None
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            resp = await http_client.post(url, json=corpo, timeout=_TIMEOUT_POST)
            if resp.status_code < 500:
                # 2xx/3xx/4xx: o grupo recebeu. 4xx é problema do grupo, não re-entregamos.
                webhook_deliveries_total.labels(service=group_id, status="success").inc()
                if resp.status_code >= 400:
                    logger.info(
                        "Webhook entregue a '%s' (%s) com status %d — não re-entregado",
                        group_id, url, resp.status_code,
                    )
                return True, None
            erro = f"HTTP {resp.status_code}"
        except Exception as exc:  # timeout, conexão, etc.
            erro = f"{type(exc).__name__}: {exc}"

        if tentativa < _MAX_TENTATIVAS:
            atraso = _BACKOFF_BASE_SEGUNDOS * (2 ** (tentativa - 1))
            logger.warning(
                "Falha ao entregar webhook a '%s' (%s) tentativa %d/%d (%s) — "
                "re-tentando em %.0fs",
                group_id, url, tentativa, _MAX_TENTATIVAS, erro, atraso,
            )
            await asyncio.sleep(atraso)

    webhook_deliveries_total.labels(service=group_id, status="failed").inc()
    return False, erro


async def _entregar_todas(ride_uuid: Optional[str], entregas: List[Entrega]) -> None:
    """Executa as entregas em paralelo e audita as que falharam permanentemente."""
    if not entregas:
        return
    resultados = await asyncio.gather(
        *(_entregar(gid, url, corpo) for (gid, url, corpo) in entregas)
    )
    for (gid, url, _), (ok, motivo) in zip(entregas, resultados):
        if not ok:
            await _auditar_falha(ride_uuid, gid, url, motivo)


# ---------------------------------------------------------------------------
# Roteamento de cada tipo de evento para as entregas
# ---------------------------------------------------------------------------


async def _processar_evento(envelope: Dict[str, Any]) -> None:
    event_type = envelope.get("eventType")
    ride_uuid = envelope.get("rideId")
    logical_ts = envelope.get("logicalTimestamp", 0)
    payload = envelope.get("payload") or {}

    if event_type == "ride_created":
        await _processar_ride_created(ride_uuid, logical_ts, payload)
    elif event_type == "ride_status_changed":
        await _processar_status(ride_uuid, logical_ts, payload)
    elif event_type == "compensation_triggered":
        await _processar_compensacao(ride_uuid, payload)
    else:
        logger.debug("Evento ignorado pelo dispatcher: %s (corrida %s)", event_type, ride_uuid)


async def _processar_ride_created(
    ride_uuid: Optional[str], logical_ts: int, payload: Dict[str, Any]
) -> None:
    """Entrega o convite de leilão (POST /rides/incoming) a todos os grupos elegíveis."""
    origin_service = payload.get("originServiceId")
    excluidos = set(payload.get("excludedGroups") or [])

    async with AsyncSessionLocal() as db:
        grupos = await GroupRepository(db).listar_todos()

    elegiveis = [
        g for g in grupos
        if g.group_id != origin_service and g.group_id not in excluidos
    ]
    if not elegiveis:
        logger.info("ride_created %s: nenhum grupo elegível para convite", ride_uuid)
        return

    corpo = {
        "rideUuid": ride_uuid,
        "origin": payload.get("origin"),
        "destination": payload.get("destination"),
        "originServiceId": origin_service,
        "passengerId": payload.get("passengerId"),
        "logicalTimestamp": logical_ts,
        "auctionDeadline": payload.get("auctionDeadline"),
    }
    entregas: List[Entrega] = [
        (g.group_id, f"{g.service_url}/rides/incoming", corpo) for g in elegiveis
    ]
    logger.info(
        "ride_created %s: convidando %d grupo(s): %s",
        ride_uuid, len(elegiveis), [g.group_id for g in elegiveis],
    )
    await _entregar_todas(ride_uuid, entregas)


async def _processar_status(
    ride_uuid: Optional[str], logical_ts: int, payload: Dict[str, Any]
) -> None:
    """Entrega o callback de vitória (POST /rides/{uuid}/assigned) ao grupo vencedor."""
    status_corrida = payload.get("status")
    if status_corrida != RideStatus.MATCH.value:
        # Outros estados não têm webhook de grupo no contrato — só observabilidade.
        logger.debug(
            "ride_status_changed %s status=%s: sem entrega (observabilidade)",
            ride_uuid, status_corrida,
        )
        return

    assigned = payload.get("assignedServiceId")
    if not assigned:
        logger.warning("ride_status_changed match sem assignedServiceId: %s", ride_uuid)
        return

    # Campos enriquecidos que o /assigned exige (preenchidos pela tarefa #10).
    necessarios = ("origin", "destination", "passengerId", "originServiceId", "lockExpiresAt")
    faltando = [k for k in necessarios if payload.get(k) is None]
    if faltando:
        logger.warning(
            "ride_status_changed match para %s sem payload enriquecido (faltam: %s) — "
            "entrega de /assigned adiada até a tarefa #10",
            ride_uuid, faltando,
        )
        return

    async with AsyncSessionLocal() as db:
        grupo = await GroupRepository(db).buscar_por_group_id(assigned)
    if not grupo:
        logger.warning("Grupo vencedor '%s' não encontrado para corrida %s", assigned, ride_uuid)
        return

    corpo = {
        "rideUuid": ride_uuid,
        "origin": payload.get("origin"),
        "destination": payload.get("destination"),
        "passengerId": payload.get("passengerId"),
        "originServiceId": payload.get("originServiceId"),
        "logicalTimestamp": logical_ts,
        "lockExpiresAt": payload.get("lockExpiresAt"),
    }
    url = f"{grupo.service_url}/rides/{ride_uuid}/assigned"
    logger.info("ride_status_changed match %s: notificando vencedor '%s'", ride_uuid, assigned)
    await _entregar_todas(ride_uuid, [(grupo.group_id, url, corpo)])


async def _processar_compensacao(ride_uuid: Optional[str], payload: Dict[str, Any]) -> None:
    """
    Consome compensation_triggered.

    O contrato (openapi.yaml#webhooks) não define um endpoint de grupo para
    compensação, então NÃO fazemos POST (evitaria 404 em todos os grupos). O
    re-leilão já é tratado internamente via auction_request. Quando o endpoint
    for definido (tarefa #14), basta montar as entregas aqui e chamar _entregar_todas.
    """
    logger.info(
        "compensation_triggered consumido para %s (reason=%s) — sem webhook de grupo "
        "definido no contrato; nenhuma entrega feita (ver tarefa #14)",
        ride_uuid, payload.get("reason"),
    )


# ---------------------------------------------------------------------------
# Auditoria de falha permanente
# ---------------------------------------------------------------------------


async def _auditar_falha(
    ride_uuid: Optional[str], group_id: str, endpoint: str, motivo: Optional[str]
) -> None:
    """Registra um RideAuditEvent webhook_failed após esgotar as tentativas."""
    logger.error(
        "webhook_failed: grupo '%s' | corrida %s | %s | após %d tentativas (%s)",
        group_id, ride_uuid, endpoint, _MAX_TENTATIVAS, motivo,
    )
    if not ride_uuid:
        return
    try:
        async with AsyncSessionLocal() as db:
            ride = await RideRepository(db).buscar_por_uuid(ride_uuid)
            if not ride:
                logger.warning("webhook_failed sem corrida %s para auditar", ride_uuid)
                return
            ts = await lamport_clock.tick()
            db.add(RideAuditEvent(
                ride_fk=ride.id,
                ride_uuid=ride_uuid,
                event_type="webhook_failed",
                service_id=group_id,
                logical_timestamp=ts,
                payload={
                    "endpoint": endpoint,
                    "attempts": _MAX_TENTATIVAS,
                    "lastError": motivo,
                },
            ))
            await db.commit()
    except Exception as exc:
        logger.error("Falha ao auditar webhook_failed (corrida %s): %s", ride_uuid, exc)


# ---------------------------------------------------------------------------
# Consumo das filas + laço de reconexão
# ---------------------------------------------------------------------------


async def _consumir_fila(channel, queue_name: str) -> None:
    """Consome uma fila e despacha cada mensagem. Roda como uma task por fila."""
    queue = await channel.declare_queue(queue_name, durable=True)
    logger.info("Webhook dispatcher consumindo '%s'", queue_name)
    async with queue.iterator() as mensagens:
        async for mensagem in mensagens:
            try:
                envelope = json.loads(mensagem.body.decode("utf-8"))
                await _processar_evento(envelope)
                await mensagem.ack()
            except Exception as exc:
                # O retry de entrega é interno; um erro aqui é inesperado (ex.: JSON
                # inválido, banco fora). requeue=False evita poison-message em loop.
                logger.error(
                    "Erro ao processar mensagem de '%s': %s", queue_name, exc, exc_info=True
                )
                await mensagem.nack(requeue=False)


async def iniciar_dispatcher() -> None:
    """
    Sobe o webhook dispatcher: consome as três filas de grupos em paralelo.

    Mesma estratégia de resiliência do auction_worker: laço externo ilimitado que
    reconecta ao RabbitMQ com backoff exponencial limitado; o orçamento de backoff
    é zerado a cada reconexão bem-sucedida.
    """
    base_delay = 5.0
    max_delay = 60.0
    consecutive_failures = 0

    while True:
        try:
            if not rabbitmq_broker.connection:
                logger.warning("RabbitMQ não conectado — tentando conectar...")
                await rabbitmq_broker.connect()

            channel = await rabbitmq_broker.connection.channel()
            await channel.set_qos(prefetch_count=_PREFETCH)

            logger.info(
                "Webhook dispatcher iniciado (retries=%d, base=%.0fs) — aguardando eventos",
                _MAX_TENTATIVAS, _BACKOFF_BASE_SEGUNDOS,
            )
            consecutive_failures = 0

            await asyncio.gather(*(_consumir_fila(channel, fila) for fila in _FILAS))

        except asyncio.CancelledError:
            logger.info("Webhook dispatcher cancelado — encerrando.")
            raise

        except Exception as exc:
            consecutive_failures += 1
            delay = min(base_delay * (2 ** (consecutive_failures - 1)), max_delay)
            logger.error(
                "Erro crítico no webhook dispatcher (falha consecutiva #%d): %s — "
                "reconectando em %.1fs",
                consecutive_failures, exc, delay, exc_info=True,
            )
            rabbitmq_broker.connection = None
            rabbitmq_broker.channel = None
            rabbitmq_broker.exchange = None
            await asyncio.sleep(delay)
