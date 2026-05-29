"""
Orquestrador do ciclo de vida do leilão.

Responsabilidades:
  - Abrir o leilão (registrar deadline)
  - Executar scatter-gather para todos os grupos
  - Aguardar o timeout configurável
  - Selecionar vencedor de forma determinística
  - Persistir resultado e fechar o leilão

NÃO é responsabilidade deste serviço:
  - Receber/persistir propostas (ProposalService)
  - Publicar eventos RabbitMQ (RabbitMQBroker)
  - Monitorar locks (lock_monitor worker)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.settings import get_settings
from app.database import AsyncSessionLocal
from app.models.ride import AuctionStatus, Ride, RideProposal
from app.repositories.group_repository import GroupRepository
from app.repositories.proposal_repository import ProposalRepository
from app.repositories.ride_repository import RideRepository
from app.services.scatter_gather_service import scatter_gather_notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regra determinística de seleção — pura, testável sem banco
# ---------------------------------------------------------------------------

def selecionar_vencedor(proposals: list[RideProposal]) -> RideProposal | None:
    """Seleciona o vencedor do leilão com desempate determinístico.

    Critérios em ordem de prioridade:
        1. Menor preço  (estimated_price)
        2. Menor ETA    (estimated_eta)
        3. group_id em ordem alfabética

    Retorna None se a lista estiver vazia.
    """
    if not proposals:
        return None

    return min(
        proposals,
        key=lambda p: (
            p.estimated_price,
            p.estimated_eta,
            p.group_id,
        ),
    )


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

class AuctionService:

    async def executar(
        self,
        ride_uuid: str,
        auction_timeout_seconds: int,
        excluded_groups: list[str],
    ) -> None:
        """
        Executa o ciclo completo de um leilão.

        Projetado para rodar dentro de asyncio.create_task —
        não bloqueia o event loop durante o sleep do timeout.
        """
        settings = get_settings()

        deadline = datetime.now(tz=timezone.utc) + timedelta(
            seconds=auction_timeout_seconds
        )

        logger.info(
            "Leilão iniciado | ride=%s timeout=%ds deadline=%s",
            ride_uuid, auction_timeout_seconds,
            deadline.strftime("%H:%M:%S"),
        )

        async with AsyncSessionLocal() as session:
            ride_repo = RideRepository(session)
            group_repo = GroupRepository(session)

            ride = await ride_repo.buscar_por_uuid(ride_uuid)
            if not ride:
                logger.error("Corrida não encontrada para leilão | ride=%s", ride_uuid)
                return

            # Registrar deadline no banco antes do scatter-gather
            ride.auction_status = AuctionStatus.OPEN.value
            ride.auction_closed_at = None
            await session.commit()

            # Buscar grupos elegíveis (todos menos os excluídos)
            todos_grupos = await group_repo.listar_ativos()
            grupos_elegiveis = [
                g for g in todos_grupos
                if g.group_id not in excluded_groups
                and g.group_id != ride.origin_group_id
            ]

            logger.info(
                "Grupos elegíveis | ride=%s total=%d excluídos=%s",
                ride_uuid, len(grupos_elegiveis), excluded_groups,
            )

        # Scatter-gather FORA da sessão do banco — operação I/O externa
        await scatter_gather_notify(
            groups=grupos_elegiveis,
            ride_uuid=ride_uuid,
            origin={
                "lat": ride.origin_lat, "lng": ride.origin_lng,
                "street": ride.origin_street, "number": ride.origin_number,
                "city": ride.origin_city, "state": ride.origin_state,
            },
            destination={
                "lat": ride.dest_lat, "lng": ride.dest_lng,
                "street": ride.dest_street, "number": ride.dest_number,
                "city": ride.dest_city, "state": ride.dest_state,
            },
            origin_service_id=ride.origin_group_id,
            passenger_id=ride.passenger_uuid,
            logical_timestamp=ride.core_logical_ts,
            auction_deadline=deadline,
        )

        # Aguardar janela de propostas
        logger.debug("Aguardando propostas | ride=%s sleep=%ds", ride_uuid, auction_timeout_seconds)
        await asyncio.sleep(auction_timeout_seconds)

        # Fechar leilão e selecionar vencedor
        async with AsyncSessionLocal() as session:
            proposal_repo = ProposalRepository(session)
            ride_repo = RideRepository(session)

            propostas = await proposal_repo.listar_validas_por_corrida(
                ride_uuid, deadline
            )
            vencedor = selecionar_vencedor(propostas)

            await self._fechar_leilao(
                session, ride_repo, proposal_repo,
                ride_uuid, vencedor, propostas,
            )

        logger.info(
            "Leilão encerrado | ride=%s propostas=%d vencedor=%s",
            ride_uuid, len(propostas),
            vencedor.group_id if vencedor else "nenhum",
        )

    async def _fechar_leilao(
        self,
        session,
        ride_repo: RideRepository,
        proposal_repo: ProposalRepository,
        ride_uuid: str,
        vencedor: RideProposal | None,
        todas_propostas: list[RideProposal],
    ) -> None:
        """Persiste o resultado do leilão atomicamente."""
        from app.models.ride import RideStatus

        agora = datetime.now(tz=timezone.utc)

        # Marcar vencedor nas propostas
        for p in todas_propostas:
            p.is_winner = 1 if (vencedor and p.group_id == vencedor.group_id) else 0

        # Atualizar estado da corrida
        ride = await ride_repo.buscar_por_uuid(ride_uuid)
        if not ride:
            return

        if vencedor:
            ride.status = RideStatus.MATCH.value
            ride.auction_status = AuctionStatus.CLOSED.value
            ride.recipient_group_id = vencedor.group_id
        else:
            ride.auction_status = AuctionStatus.NO_PROPOSALS.value

        ride.auction_closed_at = agora

        await session.commit()


auction_service = AuctionService()