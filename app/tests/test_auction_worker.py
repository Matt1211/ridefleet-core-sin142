"""
Testes de integração para _executar_leilao.

Estratégia após a refatoração assíncrona (issue #24):
  - Propostas são pré-semeadas no banco — simulam grupos que responderam via
    POST /api/v1/rides/{rideUuid}/proposals antes do deadline.
  - asyncio.sleep é patchado para execução instantânea.
  - AsyncSessionLocal é redirecionado para SQLite de testes via _PatchedSessionLocal.
  - rabbitmq_broker.publish_event é mockado com AsyncMock.

Cobre:
  - Publicação de ride_created antes do deadline wait
  - Sem propostas aceitas no banco → corrida cancelada
  - Proposta com status=passed não conta como aceita → corrida cancelada
  - Com proposta aceita → corrida em MATCH, lock transferido, ride_status_changed publicado
  - ride_status_changed com vencedor inclui origin, destination, passengerId e lockExpiresAt
  - Idempotência: leilão já encerrado não reexecuta nem republica eventos
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
from app.tests.conftest import fabrica_sessao_teste


# ---------------------------------------------------------------------------
# Helpers de seed
# ---------------------------------------------------------------------------


async def _seed_grupo(db: AsyncSession, group_id: str) -> Group:
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


async def _seed_ride(db: AsyncSession, grupo_origem: Group) -> Ride:
    ride = Ride(
        origin_group_fk=grupo_origem.id,
        origin_group_id=grupo_origem.group_id,
        passenger_uuid="passageiro-teste",
        status=RideStatus.REQUEST.value,
        auction_status=AuctionStatus.OPEN.value,
        origin_lat=-20.75,
        origin_lng=-42.88,
        origin_street="Av. P.H. Rolfs",
        origin_city="Viçosa",
        origin_state="MG",
        dest_lat=-20.76,
        dest_lng=-42.89,
    )
    db.add(ride)
    await db.commit()
    await db.refresh(ride)
    return ride


async def _seed_proposta(
    db: AsyncSession,
    ride: Ride,
    group_id: str,
    status: str,
    price: float | None = None,
    eta: int | None = None,
) -> RideProposal:
    p = RideProposal(
        ride_fk=ride.id,
        ride_uuid=ride.ride_uuid,
        group_id=group_id,
        service_url=f"http://{group_id}:8080",
        status=status,
        estimated_price=price,
        estimated_eta=eta,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def origem(db_teste: AsyncSession) -> Group:
    return await _seed_grupo(db_teste, "grupo-origem")


@pytest_asyncio.fixture
async def parceiro(db_teste: AsyncSession) -> Group:
    return await _seed_grupo(db_teste, "parceiro-1")


@pytest_asyncio.fixture
async def corrida(db_teste: AsyncSession, origem: Group, parceiro: Group) -> Ride:
    """Corrida cujo origin_group é `origem`; `parceiro` é elegível no leilão."""
    return await _seed_ride(db_teste, origem)


# ---------------------------------------------------------------------------
# Patch de AsyncSessionLocal → SQLite de testes
# ---------------------------------------------------------------------------


class _PatchedSessionLocal:
    """Redireciona AsyncSessionLocal para a factory de testes (SQLite em memória)."""

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
    """ride_created deve ser publicado antes do wait de deadline."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    event_types = [c.args[0] for c in mock_pub.call_args_list]
    assert "ride_created" in event_types


@pytest.mark.asyncio
async def test_leilao_sem_propostas_cancela_corrida(
    corrida: Ride, db_teste: AsyncSession
):
    """Nenhuma proposta no banco após o deadline → corrida cancelada."""
    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock),
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.CANCELLED.value
    assert corrida.auction_status == AuctionStatus.NO_PROPOSALS.value


@pytest.mark.asyncio
async def test_leilao_proposta_passed_cancela_corrida(
    corrida: Ride, db_teste: AsyncSession, parceiro: Group
):
    """Proposta com status='passed' não conta como aceita → corrida cancelada."""
    await _seed_proposta(db_teste, corrida, "parceiro-1", "passed")

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock),
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.CANCELLED.value
    assert corrida.auction_status == AuctionStatus.NO_PROPOSALS.value


@pytest.mark.asyncio
async def test_leilao_com_vencedor_define_match_e_publica_status_changed(
    corrida: Ride, db_teste: AsyncSession, parceiro: Group
):
    """Com proposta aceita no banco → corrida vai para MATCH e ride_status_changed é publicado."""
    await _seed_proposta(db_teste, corrida, "parceiro-1", "accepted", price=50.0, eta=10)

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.status == RideStatus.MATCH.value
    assert corrida.auction_status == AuctionStatus.CLOSED.value
    assert corrida.recipient_group_id == "parceiro-1"

    event_types = [c.args[0] for c in mock_pub.call_args_list]
    assert "ride_status_changed" in event_types


@pytest.mark.asyncio
async def test_status_changed_com_vencedor_inclui_detalhes_completos(
    corrida: Ride, db_teste: AsyncSession, parceiro: Group
):
    """ride_status_changed para leilão com vencedor deve carregar origin, destination e lockExpiresAt."""
    await _seed_proposta(db_teste, corrida, "parceiro-1", "accepted", price=30.0, eta=5)

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    status_call = next(
        c for c in mock_pub.call_args_list if c.args[0] == "ride_status_changed"
    )
    payload = status_call.args[4]
    assert payload["assignedServiceId"] == "parceiro-1"
    assert "origin" in payload
    assert "destination" in payload
    assert "passengerId" in payload
    assert "lockExpiresAt" in payload


@pytest.mark.asyncio
async def test_leilao_desempate_menor_preco(
    corrida: Ride, db_teste: AsyncSession
):
    """Com duas propostas aceitas, vence a de menor preço."""
    await _seed_grupo(db_teste, "parceiro-2")
    await _seed_proposta(db_teste, corrida, "parceiro-1", "accepted", price=80.0, eta=5)
    await _seed_proposta(db_teste, corrida, "parceiro-2", "accepted", price=50.0, eta=10)

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock),
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    await db_teste.refresh(corrida)
    assert corrida.recipient_group_id == "parceiro-2"


@pytest.mark.asyncio
async def test_leilao_idempotente_para_leilao_ja_encerrado(
    corrida: Ride, db_teste: AsyncSession
):
    """Leilão com auction_status != OPEN não deve reexecutar nem publicar eventos."""
    corrida.auction_status = AuctionStatus.CLOSED.value
    await db_teste.commit()

    with (
        patch("app.workers.auction_worker.AsyncSessionLocal", _PatchedSessionLocal),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub,
    ):
        await _executar_leilao(corrida.ride_uuid, auction_timeout=0, excluded_groups=[])

    mock_pub.assert_not_called()
    mock_sleep.assert_not_called()
