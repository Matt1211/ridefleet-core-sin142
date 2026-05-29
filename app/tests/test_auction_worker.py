"""
Testes de integração para _executar_leilao.

Estratégia:
  - Seed de dados via db_teste (SQLite em memória compartilhado).
  - AsyncSessionLocal patchado para usar a mesma factory de testes.
  - _chamar_grupo patchado para retornar propostas sintéticas.
  - _notificar_vencedor patchado para evitar chamadas HTTP externas.
  - rabbitmq_broker.publish_event patchado com AsyncMock.

Cobre:
  - Publicação de ride_created antes do scatter-gather
  - Leilão sem propostas aceitas → corrida cancelada, sem lock
  - Todos os parceiros com timeout → corrida cancelada (late == ignorado por construção)
  - Leilão com vencedor → corrida em match, lock transferido, ride_status_changed publicado
  - Idempotência: leilão já encerrado não reexecuta
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.ride import AuctionStatus, Ride, RideStatus
from app.models.ride_proposal import RideProposal
from app.rabbitmq import rabbitmq_broker
from app.workers.auction_worker import _executar_leilao

# Importa a factory do conftest indiretamente via módulo
from app.tests.conftest import fabrica_sessao_teste


# ---------------------------------------------------------------------------
# Helpers de seed
# ---------------------------------------------------------------------------


async def _seed_grupo(db: AsyncSession, group_id: str = "grupo-parceiro") -> Group:
    grupo = Group(
        group_id=group_id,
        group_name=f"Grupo {group_id}",
        service_url=f"http://{group_id}:8080",
        api_key=f"rfk_{group_id}_key",
    )
    db.add(grupo)
    await db.commit()
    await db.refresh(grupo)
    return grupo


async def _seed_ride(db: AsyncSession, grupo: Group) -> Ride:
    ride = Ride(
        origin_group_fk=grupo.id,
        origin_group_id=grupo.group_id,
        passenger_uuid="passageiro-teste",
        status=RideStatus.REQUEST.value,
        auction_status=AuctionStatus.OPEN.value,
        origin_lat=-20.75,
        origin_lng=-42.88,
        dest_lat=-20.76,
        dest_lng=-42.89,
    )
    db.add(ride)
    await db.commit()
    await db.refresh(ride)
    return ride


def _proposta_aceita(group_id: str, price: float, eta: int) -> RideProposal:
    return RideProposal(
        group_id=group_id,
        service_url=f"http://{group_id}:8080",
        status="accepted",
        estimated_price=price,
        estimated_eta=eta,
    )


def _proposta_timeout(group_id: str) -> RideProposal:
    return RideProposal(
        group_id=group_id,
        service_url=f"http://{group_id}:8080",
        status="timeout",
    )


def _proposta_passed(group_id: str) -> RideProposal:
    return RideProposal(
        group_id=group_id,
        service_url=f"http://{group_id}:8080",
        status="passed",
    )


# ---------------------------------------------------------------------------
# Fixtures: grupo de origem + parceiro elegível separado
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def origem(db_teste: AsyncSession) -> Group:
    """Grupo que solicita a corrida — excluído do leilão pelo worker."""
    return await _seed_grupo(db_teste, "grupo-origem")


@pytest_asyncio.fixture
async def parceiro(db_teste: AsyncSession) -> Group:
    """Grupo elegível para participar do leilão."""
    return await _seed_grupo(db_teste, "parceiro-1")


@pytest_asyncio.fixture
async def corrida(db_teste: AsyncSession, origem: Group, parceiro: Group) -> Ride:
    """Corrida cujo origin_group é `origem`; `parceiro` é elegível no leilão."""
    return await _seed_ride(db_teste, origem)


# ---------------------------------------------------------------------------
# Contexto: patch de AsyncSessionLocal para apontar para SQLite de testes
# ---------------------------------------------------------------------------


class _PatchedSessionLocal:
    """Substitui AsyncSessionLocal: retorna sessões da factory de testes."""
    def __aenter__(self):
        self._session = fabrica_sessao_teste()
        return self._session.__aenter__()

    def __aexit__(self, *args):
        return self._session.__aexit__(*args)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leilao_publica_ride_created(corrida: Ride):
    """ride_created deve ser publicado antes do scatter-gather."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("app.workers.auction_worker._chamar_grupo", new_callable=AsyncMock) as mock_chamar,
        patch("app.workers.auction_worker._notificar_vencedor", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        mock_chamar.return_value = _proposta_passed("parceiro-1")

        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    event_types = [c.args[0] for c in mock_pub.call_args_list]
    assert "ride_created" in event_types


@pytest.mark.asyncio
async def test_leilao_sem_propostas_aceitas_cancela_corrida(
    corrida: Ride, db_teste: AsyncSession
):
    """Todos os parceiros passando → corrida deve ser cancelada."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("app.workers.auction_worker._chamar_grupo", new_callable=AsyncMock) as mock_chamar,
        patch("app.workers.auction_worker._notificar_vencedor", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock),
    ):
        mock_chamar.return_value = _proposta_passed("parceiro-1")

        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.CANCELLED.value
    assert corrida.auction_status == AuctionStatus.NO_PROPOSALS.value


@pytest.mark.asyncio
async def test_leilao_todos_timeout_cancela_corrida(
    corrida: Ride, db_teste: AsyncSession
):
    """Propostas atrasadas (timeout) são ignoradas por construção — corrida cancelada."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("app.workers.auction_worker._chamar_grupo", new_callable=AsyncMock) as mock_chamar,
        patch("app.workers.auction_worker._notificar_vencedor", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock),
    ):
        mock_chamar.return_value = _proposta_timeout("parceiro-1")

        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.CANCELLED.value
    assert corrida.auction_status == AuctionStatus.NO_PROPOSALS.value


@pytest.mark.asyncio
async def test_leilao_com_vencedor_define_status_match_e_publica_status_changed(
    corrida: Ride, db_teste: AsyncSession
):
    """Com proposta aceita, corrida vai para MATCH e ride_status_changed é publicado."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("app.workers.auction_worker._chamar_grupo", new_callable=AsyncMock) as mock_chamar,
        patch("app.workers.auction_worker._notificar_vencedor", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        mock_chamar.return_value = _proposta_aceita("parceiro-1", price=50.0, eta=10)

        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.MATCH.value
    assert corrida.auction_status == AuctionStatus.CLOSED.value
    assert corrida.recipient_group_id == "parceiro-1"

    event_types = [c.args[0] for c in mock_pub.call_args_list]
    assert "ride_status_changed" in event_types


@pytest.mark.asyncio
async def test_leilao_idempotente_para_leilao_ja_encerrado(
    corrida: Ride, db_teste: AsyncSession
):
    """Leilão com auction_status != OPEN não deve reexecutar."""
    corrida.auction_status = AuctionStatus.CLOSED.value
    await db_teste.commit()

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("app.workers.auction_worker._chamar_grupo", new_callable=AsyncMock) as mock_chamar,
        patch("app.workers.auction_worker._notificar_vencedor", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    mock_chamar.assert_not_called()
    mock_pub.assert_not_called()
