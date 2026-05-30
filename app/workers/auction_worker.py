"""
Worker de leilão — consome auction_request da fila RabbitMQ e executa
o ciclo completo de um leilão para a corrida indicada.

Fluxo por mensagem:
  1. Deserializa e valida o payload.
  2. Publica `ride_created` no exchange fanout (notificação a todos os grupos).
  3. Executa scatter-gather HTTP para coleta de propostas.
  4. Seleciona o vencedor de forma determinística.
  5. Persiste resultado, transfere lock e notifica o vencedor.
  6. Publica `ride_status_changed`.

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

import httpx

from app.core.http_client import http_client
from app.core.lamport_clock import lamport_clock
from app.database import AsyncSessionLocal
from app.dtos.ride_request_dto import RideIncomingNotificationDTO
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
      1. Publica `ride_created` no exchange fanout.
      2. Notifica todos os grupos elegíveis via HTTP em paralelo.
      3. Coleta propostas e seleciona o vencedor.
      4. Transfere o lock ao vencedor e notifica-o via callback.
      5. Persiste o resultado e publica `ride_status_changed`.
    """
    async with AsyncSessionLocal() as db:
        ride_repo = RideRepository(db)
        lock_repo = LockRepository(db)
        proposal_repo = ProposalRepository(db)
        group_repo = GroupRepository(db)

        ride = await ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            logger.error("Leilão: corrida %s não encontrada no banco", ride_uuid)
            return

        # Idempotência — leilão já finalizado: nada a fazer
        if ride.auction_status != AuctionStatus.OPEN.value:
            logger.info(
                "Leilão da corrida %s já encerrado (status: '%s') — mensagem ignorada",
                ride_uuid,
                ride.auction_status,
            )
            return

        todos_grupos = await group_repo.listar_todos()
        grupos_participantes = [
            g for g in todos_grupos
            if g.group_id != ride.origin_group_id
            and g.group_id not in excluded_groups
        ]

        nomes_participantes = [g.group_id for g in grupos_participantes]
        logger.info(
            "Leilão iniciado: corrida %s | timeout: %ds | %d grupo(s): %s",
            ride_uuid,
            auction_timeout,
            len(grupos_participantes),
            nomes_participantes or "(nenhum)",
        )

        deadline = _utcnow() + timedelta(seconds=auction_timeout)

        # ------------------------------------------------------------------
        # 1. Publicar ride_created (topics.yaml: publisher=core, subscribers=all_groups)
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
            logger.info("ride_created publicado | ride=%s", ride_uuid)
        except Exception as exc:
            logger.warning(
                "Falha ao publicar ride_created para corrida %s: %s — continuando",
                ride_uuid,
                exc,
            )

        # ------------------------------------------------------------------
        # 2. Scatter-gather HTTP
        # ------------------------------------------------------------------
        ts_notif = await lamport_clock.tick()
        notificacao = RideIncomingNotificationDTO(
            rideUuid=ride_uuid,
            origin=dict(
                lat=ride.origin_lat,
                lng=ride.origin_lng,
                street=ride.origin_street,
                number=ride.origin_number,
                city=ride.origin_city,
                state=ride.origin_state,
            ),
            destination=dict(
                lat=ride.dest_lat,
                lng=ride.dest_lng,
                street=ride.dest_street,
                number=ride.dest_number,
                city=ride.dest_city,
                state=ride.dest_state,
            ),
            originServiceId=ride.origin_group_id,
            passengerId=ride.passenger_uuid,
            logicalTimestamp=ts_notif,
            auctionDeadline=deadline,
        )

        tarefas = [
            _chamar_grupo(g.service_url, g.group_id, notificacao, auction_timeout)
            for g in grupos_participantes
        ]
        resultados = await asyncio.gather(*tarefas, return_exceptions=True)

        # ------------------------------------------------------------------
        # 3. Classificar propostas
        # ------------------------------------------------------------------
        propostas_aceitas: List[RideProposal] = []
        todas_propostas: List[RideProposal] = []

        for grupo, resultado in zip(grupos_participantes, resultados):
            if isinstance(resultado, Exception):
                logger.warning(
                    "Erro ao contatar grupo '%s' para corrida %s: %s",
                    grupo.group_id,
                    ride_uuid,
                    resultado,
                )
                prop = RideProposal(
                    ride_fk=ride.id,
                    ride_uuid=ride_uuid,
                    group_id=grupo.group_id,
                    service_url=grupo.service_url,
                    status="error",
                )
            else:
                prop = resultado
                prop.ride_fk = ride.id
                prop.ride_uuid = ride_uuid
                prop.group_id = grupo.group_id
                prop.service_url = grupo.service_url
                if prop.status == "accepted":
                    propostas_aceitas.append(prop)
                    logger.info(
                        "Proposta aceita: grupo '%s' | corrida %s | preço: %s | ETA: %s",
                        grupo.group_id,
                        ride_uuid,
                        prop.estimated_price,
                        prop.estimated_eta,
                    )
                else:
                    logger.info(
                        "Proposta de '%s' para corrida %s: status '%s'",
                        grupo.group_id,
                        ride_uuid,
                        prop.status,
                    )
            todas_propostas.append(prop)

        logger.info(
            "Coleta encerrada: corrida %s | %d total, %d aceita(s)",
            ride_uuid,
            len(todas_propostas),
            len(propostas_aceitas),
        )

        # ------------------------------------------------------------------
        # 4. Selecionar vencedor — critérios do escopo:
        #    1. menor preço  2. menor ETA  3. group_id alfabético
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

        await proposal_repo.criar_varios(todas_propostas)

        # ------------------------------------------------------------------
        # 5. Fechar leilão no banco
        # ------------------------------------------------------------------
        ts_fechamento = await lamport_clock.tick()
        agora_fechamento = _utcnow()

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
        # 6. Publicar ride_status_changed
        # ------------------------------------------------------------------
        try:
            await rabbitmq_broker.publish_event(
                "ride_status_changed",
                ride_uuid,
                "core",
                ts_fechamento,
                {
                    "status": ride.status,
                    "assignedServiceId": ride.recipient_group_id,
                },
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
        lock_expires = agora_fechamento + timedelta(seconds=_LOCK_TTL_VENCEDOR)
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

    await _notificar_vencedor(
        service_url=vencedor.service_url,
        ride=ride,
        lock_expires_at=lock_expires,
        logical_timestamp=ts_fechamento,
    )


async def _chamar_grupo(
    service_url: str,
    group_id: str,
    notificacao: RideIncomingNotificationDTO,
    timeout_segundos: int,
) -> RideProposal:
    """
    Envia POST {serviceUrl}/rides/incoming e retorna um RideProposal.
    Nunca lança exceções — erros ficam encapsulados no status da proposta.
    """
    inicio = _utcnow()
    try:
        resp = await http_client.post(
            f"{service_url}/rides/incoming",
            json=notificacao.model_dump(mode="json"),
            timeout=httpx.Timeout(timeout_segundos + 2.0),
        )
        elapsed_ms = int((_utcnow() - inicio).total_seconds() * 1000)

        if resp.status_code == 204:
            return RideProposal(
                group_id=group_id,
                service_url=service_url,
                status="passed",
                response_time_ms=elapsed_ms,
                responded_at=_utcnow(),
            )

        if resp.status_code == 200:
            dados = resp.json()
            return RideProposal(
                group_id=group_id,
                service_url=service_url,
                status="accepted",
                estimated_eta=dados.get("estimatedEta"),
                estimated_price=dados.get("estimatedPrice"),
                logical_timestamp=dados.get("logicalTimestamp"),
                response_time_ms=elapsed_ms,
                responded_at=_utcnow(),
            )

        return RideProposal(
            group_id=group_id,
            service_url=service_url,
            status="error",
            response_time_ms=elapsed_ms,
            responded_at=_utcnow(),
        )

    except httpx.TimeoutException:
        elapsed_ms = int((_utcnow() - inicio).total_seconds() * 1000)
        logger.warning(
            "Timeout ao chamar grupo '%s' após %dms", group_id, elapsed_ms
        )
        return RideProposal(
            group_id=group_id,
            service_url=service_url,
            status="timeout",
            response_time_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Erro ao chamar grupo '%s': %s", group_id, exc)
        return RideProposal(
            group_id=group_id,
            service_url=service_url,
            status="error",
        )


async def _notificar_vencedor(
    service_url: str,
    ride: Ride,
    lock_expires_at: datetime,
    logical_timestamp: int,
) -> None:
    """Envia POST {serviceUrl}/rides/{rideUuid}/assigned ao grupo vencedor."""
    payload = {
        "rideUuid": ride.ride_uuid,
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
        "logicalTimestamp": logical_timestamp,
        "lockExpiresAt": lock_expires_at.isoformat(),
    }
    url = f"{service_url}/rides/{ride.ride_uuid}/assigned"
    try:
        await http_client.post(url, json=payload, timeout=10.0)
        logger.info(
            "Vencedor '%s' notificado | corrida %s | POST %s",
            ride.recipient_group_id,
            ride.ride_uuid,
            url,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao notificar vencedor em %s | corrida %s: %s",
            url,
            ride.ride_uuid,
            exc,
        )


async def iniciar_worker() -> None:
    """
    Inicia o consumer RabbitMQ para a fila ridefleet.auction.requests.

    Usa prefetch_count=1 para garantir que apenas uma mensagem seja
    processada por vez — evita condição de corrida em leilões simultâneos.
    ACK somente após processamento completo; NACK + requeue em caso de erro.
    """
    if not rabbitmq_broker.connection:
        logger.warning("RabbitMQ não conectado — auction worker não iniciado.")
        return

    channel = await rabbitmq_broker.connection.channel()
    await channel.set_qos(prefetch_count=1)

    queue = await channel.declare_queue("ridefleet.auction.requests", durable=True)

    logger.info("Auction worker aguardando mensagens em ridefleet.auction.requests")

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