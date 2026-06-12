"""
Worker de leilão — consome auction_request da fila RabbitMQ e executa
o ciclo completo de um leilão para a corrida indicada.

Fluxo por mensagem:
  1. Deserializa e valida o payload.
  2. Publica `ride_created` no exchange (notificação entregue pelo webhook_dispatcher).
  3. Aguarda assincronamente até o auctionDeadline.
  4. Lê propostas submetidas pelos grupos via POST /rides/{rideUuid}/proposals.
  5. Seleciona o vencedor de forma determinística.
  6. Persiste resultado e transfere lock.
  7. Publica `ride_status_changed` (entregue pelo webhook_dispatcher ao vencedor).

Critérios de seleção do vencedor (conforme escopo):
  1. Menor preço
  2. Menor ETA
  3. group_id em ordem alfabética
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.core.lamport_clock import lamport_clock
from app.database import AsyncSessionLocal
from app.models.ride import AuctionStatus, Ride, RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_proposal import RideProposal
from app.rabbitmq import rabbitmq_broker
from app.repositories.group_repository import GroupRepository
from app.repositories.lock_repository import LockRepository
from app.repositories.proposal_repository import ProposalRepository
from app.repositories.ride_repository import RideRepository

logger = logging.getLogger(__name__)

_LOCK_TTL_VENCEDOR = 60


def _utcnow() -> datetime:
    """Retorna datetime naive em UTC — consistente com colunas TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def selecionar_vencedor(propostas: List[RideProposal]) -> Optional[RideProposal]:
    """Seleciona o vencedor do leilão com desempate determinístico.

    Critérios em ordem de prioridade:
        1. Menor preço  (estimated_price)
        2. Menor ETA    (estimated_eta)
        3. group_id em ordem alfabética

    Caller deve passar apenas propostas com status == "accepted".
    Retorna None se a lista estiver vazia.
    """
    if not propostas:
        return None
    return min(
        propostas,
        key=lambda p: (
            p.estimated_price if p.estimated_price is not None else float("inf"),
            p.estimated_eta   if p.estimated_eta   is not None else float("inf"),
            p.group_id,
        ),
    )


async def _executar_leilao(
    ride_uuid: str,
    auction_timeout: int,
    excluded_groups: List[str],
) -> None:
    """
    Executa um leilão completo para a corrida indicada:
      1. Publica `ride_created` no exchange (webhook_dispatcher notifica os grupos).
      2. Aguarda até o auctionDeadline para coletar propostas assíncronas.
      3. Lê as propostas submetidas via POST /rides/{rideUuid}/proposals.
      4. Seleciona o vencedor e persiste o resultado.
      5. Transfere o lock ao vencedor.
      6. Publica `ride_status_changed` (webhook_dispatcher notifica o vencedor).
    """
    async with AsyncSessionLocal() as db:
        ride_repo = RideRepository(db)
        group_repo = GroupRepository(db)

        ride = await ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            logger.error("Leilão: corrida %s não encontrada no banco", ride_uuid)
            return

        if ride.auction_status != AuctionStatus.OPEN.value:
            logger.info(
                "Leilão da corrida %s já encerrado (status: '%s') — mensagem ignorada",
                ride_uuid,
                ride.auction_status,
            )
            return

        todos_grupos = await group_repo.listar_todos()
        nomes_participantes = [
            g.group_id for g in todos_grupos
            if g.group_id != ride.origin_group_id
            and g.group_id not in excluded_groups
        ]

        deadline = _utcnow() + timedelta(seconds=auction_timeout)

        logger.info(
            "Leilão iniciado: corrida %s | timeout: %ds | %d grupo(s) elegível(is): %s",
            ride_uuid,
            auction_timeout,
            len(nomes_participantes),
            nomes_participantes or "(nenhum)",
        )

        # ------------------------------------------------------------------
        # 1. Publicar ride_created — webhook_dispatcher entrega aos grupos
        # ------------------------------------------------------------------
        ts_criacao = await lamport_clock.tick()
        try:
            await rabbitmq_broker.publish_event(
                "ride_created",
                ride_uuid,
                "core",
                ts_criacao,
                {
                    "origin": {
                        "lat": ride.origin_lat,
                        "lng": ride.origin_lng,
                        "street": ride.origin_street,
                        "number": ride.origin_number,
                        "city": ride.origin_city,
                        "state": ride.origin_state,
                    },
                    "destination": {
                        "lat": ride.dest_lat,
                        "lng": ride.dest_lng,
                        "street": ride.dest_street,
                        "number": ride.dest_number,
                        "city": ride.dest_city,
                        "state": ride.dest_state,
                    },
                    "passengerId": ride.passenger_uuid,
                    "originServiceId": ride.origin_group_id,
                    "auctionDeadline": deadline.isoformat(),
                    "excludedGroups": excluded_groups,
                },
            )
            logger.info("ride_created publicado | ride=%s | deadline=%s", ride_uuid, deadline.isoformat())
        except Exception as exc:
            logger.warning(
                "Falha ao publicar ride_created para corrida %s: %s — continuando",
                ride_uuid,
                exc,
            )

    # ------------------------------------------------------------------
    # 2. Aguardar até o deadline (propostas chegam via POST /proposals)
    # ------------------------------------------------------------------
    tempo_restante = (deadline - _utcnow()).total_seconds()
    if tempo_restante > 0:
        logger.info(
            "Aguardando propostas: corrida %s | %.1fs até o deadline",
            ride_uuid,
            tempo_restante,
        )
        await asyncio.sleep(tempo_restante)

    # ------------------------------------------------------------------
    # 3. Ler propostas submetidas assincronamente e selecionar vencedor
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        ride_repo = RideRepository(db)
        lock_repo = LockRepository(db)
        proposal_repo = ProposalRepository(db)
        group_repo = GroupRepository(db)

        ride = await ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            logger.error("Leilão: corrida %s sumiu do banco após deadline", ride_uuid)
            return

        if ride.auction_status != AuctionStatus.OPEN.value:
            logger.info(
                "Leilão da corrida %s já fechado antes do processamento pós-deadline — ignorado",
                ride_uuid,
            )
            return

        todas_propostas = await proposal_repo.listar_por_corrida(ride_uuid)
        propostas_aceitas = [p for p in todas_propostas if p.status == "accepted"]
        todos_grupos = await group_repo.listar_todos()

        logger.info(
            "Deadline atingido: corrida %s | %d proposta(s) recebida(s), %d aceita(s)",
            ride_uuid,
            len(todas_propostas),
            len(propostas_aceitas),
        )

        # ------------------------------------------------------------------
        # 4. Selecionar vencedor
        # ------------------------------------------------------------------
        vencedor: Optional[RideProposal] = selecionar_vencedor(propostas_aceitas)
        if vencedor:
            vencedor.is_winner = 1
            logger.info(
                "Vencedor selecionado: corrida %s => '%s' (preço: %s, ETA: %s)",
                ride_uuid,
                vencedor.group_id,
                vencedor.estimated_price,
                vencedor.estimated_eta,
            )

        # ------------------------------------------------------------------
        # 5. Fechar leilão no banco
        # ------------------------------------------------------------------
        ts_fechamento = await lamport_clock.tick()
        agora_fechamento = _utcnow()
        lock_expires = agora_fechamento + timedelta(seconds=_LOCK_TTL_VENCEDOR)

        if vencedor:
            grupo_vencedor = next(
                (g for g in todos_grupos if g.group_id == vencedor.group_id), None
            )
            ride.status = RideStatus.MATCH.value
            ride.recipient_group_id = vencedor.group_id
            ride.recipient_group_fk = grupo_vencedor.id if grupo_vencedor else None
            ride.assigned_at = agora_fechamento
            ride.auction_status = AuctionStatus.CLOSED.value
        else:
            ride.status = RideStatus.CANCELLED.value
            ride.auction_status = AuctionStatus.NO_PROPOSALS.value

        ride.auction_closed_at = agora_fechamento
        ride.core_logical_ts = ts_fechamento
        await db.commit()
        await db.refresh(ride)

        # ------------------------------------------------------------------
        # 6. Publicar ride_status_changed — webhook_dispatcher notifica grupos
        # ------------------------------------------------------------------
        status_payload: dict = {
            "status": ride.status,
            "assignedServiceId": ride.recipient_group_id,
        }
        if vencedor:
            # Inclui detalhes completos para que o grupo vencedor possa despachar
            # um motorista sem precisar fazer uma chamada adicional ao core.
            status_payload.update({
                "origin": {
                    "lat": ride.origin_lat,
                    "lng": ride.origin_lng,
                    "street": ride.origin_street,
                    "number": ride.origin_number,
                    "city": ride.origin_city,
                    "state": ride.origin_state,
                },
                "destination": {
                    "lat": ride.dest_lat,
                    "lng": ride.dest_lng,
                    "street": ride.dest_street,
                    "number": ride.dest_number,
                    "city": ride.dest_city,
                    "state": ride.dest_state,
                },
                "passengerId": ride.passenger_uuid,
                "originServiceId": ride.origin_group_id,
                "lockExpiresAt": lock_expires.isoformat(),
            })
        try:
            await rabbitmq_broker.publish_event(
                "ride_status_changed",
                ride_uuid,
                "core",
                ts_fechamento,
                status_payload,
            )
        except Exception as exc:
            logger.warning(
                "Falha ao publicar ride_status_changed para corrida %s: %s",
                ride_uuid,
                exc,
            )

        evento_leilao = RideAuditEvent(
            ride_fk=ride.id,
            ride_uuid=ride_uuid,
            event_type="auction_closed",
            service_id="core",
            logical_timestamp=ts_fechamento,
            payload={
                "winner": vencedor.group_id if vencedor else None,
                "proposalsCount": len(propostas_aceitas),
                "auctionStatus": ride.auction_status,
            },
        )
        db.add(evento_leilao)
        await db.commit()

        if not vencedor:
            await lock_repo.deletar(ride_uuid)
            logger.info(
                "Leilão encerrado SEM propostas: corrida %s => cancelada",
                ride_uuid,
            )
            return

        # ------------------------------------------------------------------
        # 7. Transferir lock ao vencedor
        # ------------------------------------------------------------------
        await lock_repo.criar_ou_renovar(
            ride_uuid, vencedor.group_id, lock_expires, ride.id
        )
        logger.info(
            "Lock transferido para '%s' | corrida %s | expira: %s",
            vencedor.group_id,
            ride_uuid,
            lock_expires.strftime("%H:%M:%S"),
        )

        ts_lock = await lamport_clock.tick()
        db.add(RideAuditEvent(
            ride_fk=ride.id,
            ride_uuid=ride_uuid,
            event_type="lock_acquired",
            service_id=vencedor.group_id,
            logical_timestamp=ts_lock,
            payload={"reason": "auction_winner", "ttlSeconds": _LOCK_TTL_VENCEDOR},
        ))
        await db.commit()

        logger.info(
            "Leilão encerrado: corrida %s => vencedor '%s' notificado via broker",
            ride_uuid,
            vencedor.group_id,
        )


async def iniciar_worker() -> None:
    """
    Inicia o consumer RabbitMQ para a fila ridefleet.auction.requests.

    Usa prefetch_count=1 para garantir que apenas uma mensagem seja
    processada por vez — evita condição de corrida em leilões simultâneos.
    ACK somente após processamento completo; NACK + requeue em caso de erro.
    Reconecta automaticamente se o RabbitMQ cair durante o processamento.

    Estratégia de resiliência:
      - Laço externo ilimitado: o worker nunca encerra silenciosamente — em
        caso de falha crítica reconecta indefinidamente com backoff exponencial
        limitado (max_delay). Evita o "death" silencioso que deixaria a API no
        ar (health 200) sem processar nenhum leilão.
      - O orçamento de backoff é zerado a cada reconexão bem-sucedida.
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
            await channel.set_qos(prefetch_count=1)

            queue = await channel.declare_queue("ridefleet.auction.requests", durable=True)

            logger.info("Auction worker aguardando mensagens em ridefleet.auction.requests")

            # Conexão + canal + fila saudáveis → zera o orçamento de falhas/backoff.
            consecutive_failures = 0

            # Escopo local — sem estado global mutável
            last_processed_timestamp = 0

            async with queue.iterator() as messages:
                async for message in messages:
                    try:
                        body = json.loads(message.body.decode("utf-8"))
                        logical_timestamp: int = body.get("logicalTimestamp", 0)
                        ride_uuid: str = body.get("rideId") or ""
                        payload: dict = body.get("payload", {})
                        auction_timeout: int = payload.get("auctionTimeoutSeconds", 10)
                        excluded_groups: List[str] = payload.get("excludedGroups", [])

                        if not ride_uuid:
                            logger.error("Mensagem sem rideId descartada: %s", body)
                            await message.ack()
                            continue

                        if logical_timestamp < last_processed_timestamp:
                            logger.warning(
                                "Mensagem fora de ordem descartada: "
                                "received=%d last=%d ride=%s",
                                logical_timestamp,
                                last_processed_timestamp,
                                ride_uuid,
                            )
                            await message.ack()
                            continue

                        last_processed_timestamp = logical_timestamp
                        await lamport_clock.update(logical_timestamp)

                        await _executar_leilao(ride_uuid, auction_timeout, excluded_groups)
                        await message.ack()

                    except Exception as exc:
                        logger.error(
                            "Erro no auction worker ao processar mensagem: %s",
                            exc,
                            exc_info=True,
                        )
                        await message.nack(requeue=True)

        except asyncio.CancelledError:
            logger.info("Auction worker cancelado — encerrando.")
            raise

        except Exception as exc:
            consecutive_failures += 1
            delay = min(base_delay * (2 ** (consecutive_failures - 1)), max_delay)
            logger.error(
                "Erro crítico no auction worker (falha consecutiva #%d): %s — "
                "reconectando em %.1fs",
                consecutive_failures,
                exc,
                delay,
                exc_info=True,
            )
            rabbitmq_broker.connection = None
            rabbitmq_broker.channel = None
            rabbitmq_broker.exchange = None

            await asyncio.sleep(delay)
