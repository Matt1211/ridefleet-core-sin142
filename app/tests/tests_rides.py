"""
Testes do endpoint POST /api/v1/rides — Criação de corrida.

Cobre:
  - Resposta HTTP (status, campos retornados)
  - Persistência no banco: Ride, RideLock, RideAuditEvent
  - Publicação do evento `auction_request` no RabbitMQ (campos e aridade)
  - Degradação graciosa quando o RabbitMQ está indisponível
  - Autenticação (sem key / key inválida => 401)
  - Validação de payload (campos obrigatórios, limites de valor)
  - Valores limítrofes e defaults do DTO
"""

import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride import AuctionStatus, Ride, RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_lock import RideLock
from app.rabbitmq import rabbitmq_broker

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

ENDPOINT = "/api/v1/rides"
ENDPOINT_GRUPOS = "/api/v1/groups/register"

GRUPO_ORIGEM = {
    "groupId": "grupo-origem",
    "groupName": "Grupo Origem - Teste",
    "serviceUrl": "http://grupo-origem:8081",
}

CORRIDA_VALIDA = {
    "originServiceId": "grupo-origem",
    "passengerId": "passageiro-123",
    "origin": {
        "lat": -20.7546,
        "lng": -42.8825,
        "street": "Av. P.H. Rolfs",
        "number": "S/N",
        "city": "Viçosa",
        "state": "MG",
    },
    "destination": {
        "lat": -20.7600,
        "lng": -42.8900,
        "street": "Rua das Flores",
        "number": "100",
        "city": "Viçosa",
        "state": "MG",
    },
    "logicalTimestamp": 1,
    "auctionTimeoutSeconds": 5,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def api_key(cliente: AsyncClient) -> str:
    """Registra o grupo de origem e devolve a API Key para autenticação."""
    resp = await cliente.post(ENDPOINT_GRUPOS, json=GRUPO_ORIGEM)
    assert resp.status_code == 201
    return resp.json()["apiKey"]


@pytest.fixture
def mock_rabbitmq():
    """
    Substitui `rabbitmq_broker.publish_event` por um AsyncMock.
    Impede chamadas reais ao broker e permite inspecionar as invocações.
    """
    with patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub:
        yield mock_pub


# ---------------------------------------------------------------------------
# Helper de conveniência
# ---------------------------------------------------------------------------


async def _post_ride(
    cliente: AsyncClient,
    api_key: str,
    payload: dict | None = None,
):
    """Envia POST /rides e retorna o objeto Response."""
    return await cliente.post(
        ENDPOINT,
        json=payload if payload is not None else CORRIDA_VALIDA,
        headers={"X-API-Key": api_key},
    )


# ===========================================================================
# 1. Resposta HTTP
# ===========================================================================


async def test_criar_corrida_retorna_202(cliente, api_key, mock_rabbitmq):
    resp = await _post_ride(cliente, api_key)
    assert resp.status_code == 202


async def test_criar_corrida_resposta_contem_ride_uuid(cliente, api_key, mock_rabbitmq):
    resp = await _post_ride(cliente, api_key)
    corpo = resp.json()
    assert "rideUuid" in corpo
    assert corpo["rideUuid"]  # não vazio


async def test_criar_corrida_resposta_contem_logical_timestamp(cliente, api_key, mock_rabbitmq):
    """O core devolve seu próprio timestamp lógico, >= ao enviado pelo cliente."""
    resp = await _post_ride(cliente, api_key)
    corpo = resp.json()
    assert "logicalTimestamp" in corpo
    assert isinstance(corpo["logicalTimestamp"], int)
    assert corpo["logicalTimestamp"] >= CORRIDA_VALIDA["logicalTimestamp"]


async def test_criar_corrida_resposta_contem_message(cliente, api_key, mock_rabbitmq):
    resp = await _post_ride(cliente, api_key)
    corpo = resp.json()
    assert "message" in corpo
    assert corpo["message"]


# ===========================================================================
# 2. Persistência no banco — Ride
# ===========================================================================


async def test_criar_corrida_persiste_ride_no_banco(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    assert resultado.scalar_one_or_none() is not None


async def test_criar_corrida_ride_com_status_request(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.status == RideStatus.REQUEST.value


async def test_criar_corrida_ride_com_auction_status_open(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.auction_status == AuctionStatus.OPEN.value


async def test_criar_corrida_ride_persiste_passageiro_e_grupo_de_origem(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.passenger_uuid == CORRIDA_VALIDA["passengerId"]
    assert ride.origin_group_id == CORRIDA_VALIDA["originServiceId"]


async def test_criar_corrida_ride_persiste_coordenadas_de_origem(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.origin_lat == pytest.approx(CORRIDA_VALIDA["origin"]["lat"])
    assert ride.origin_lng == pytest.approx(CORRIDA_VALIDA["origin"]["lng"])


async def test_criar_corrida_ride_persiste_coordenadas_de_destino(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.dest_lat == pytest.approx(CORRIDA_VALIDA["destination"]["lat"])
    assert ride.dest_lng == pytest.approx(CORRIDA_VALIDA["destination"]["lng"])


async def test_criar_corrida_ride_persiste_auction_timeout(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()

    assert ride.auctionTimeoutSeconds == CORRIDA_VALIDA["auctionTimeoutSeconds"]


# ===========================================================================
# 3. Persistência no banco — Lock
# ===========================================================================


async def test_criar_corrida_cria_lock_para_grupo_de_origem(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    lock = resultado.scalar_one_or_none()

    assert lock is not None
    assert lock.held_by == CORRIDA_VALIDA["originServiceId"]


async def test_criar_corrida_lock_expira_no_futuro(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    lock = resultado.scalar_one()

    # SQLite devolve datetime naive (UTC implícito); datetime.utcnow() também é naive
    assert lock.expires_at > datetime.utcnow()


async def test_criar_corrida_lock_ttl_maior_que_auction_timeout(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """
    O TTL do lock deve superar o auctionTimeoutSeconds porque o serviço
    adiciona uma margem de 30 s para compensar o tempo de processamento.
    """
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    lock = resultado.scalar_one()

    ttl_restante = (lock.expires_at - datetime.utcnow()).total_seconds()
    assert ttl_restante > CORRIDA_VALIDA["auctionTimeoutSeconds"]


# ===========================================================================
# 4. Persistência no banco — Auditoria
# ===========================================================================


async def test_criar_corrida_registra_evento_ride_created(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "ride_created",
        )
    )
    assert resultado.scalar_one_or_none() is not None


async def test_criar_corrida_evento_audit_com_service_id_do_grupo_de_origem(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "ride_created",
        )
    )
    evento = resultado.scalar_one()

    assert evento.service_id == CORRIDA_VALIDA["originServiceId"]


async def test_criar_corrida_evento_audit_com_logical_timestamp_valido(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """
    O timestamp lógico do evento deve ser >= ao timestamp enviado pelo cliente
    (regra do relógio de Lamport: max(ts_local, ts_cliente) + 1).
    """
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "ride_created",
        )
    )
    evento = resultado.scalar_one()

    assert evento.logical_timestamp >= CORRIDA_VALIDA["logicalTimestamp"]


# ===========================================================================
# 5. Publicação no RabbitMQ
# ===========================================================================


async def test_criar_corrida_publica_auction_request_exatamente_uma_vez(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)
    assert mock_rabbitmq.call_count == 1


async def test_criar_corrida_publica_com_routing_key_auction_request(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)
    event_type = mock_rabbitmq.call_args.args[0]
    assert event_type == "auction_request"


async def test_criar_corrida_publica_com_ride_uuid_correto(
    cliente, api_key, mock_rabbitmq
):
    resp = await _post_ride(cliente, api_key)
    ride_uuid = resp.json()["rideUuid"]

    published_ride_id = mock_rabbitmq.call_args.args[1]
    assert published_ride_id == ride_uuid


async def test_criar_corrida_publica_com_service_id_core(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)
    service_id = mock_rabbitmq.call_args.args[2]
    assert service_id == "core"


async def test_criar_corrida_payload_rabbitmq_contem_auction_timeout(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)
    payload = mock_rabbitmq.call_args.args[4]
    assert payload["auctionTimeoutSeconds"] == CORRIDA_VALIDA["auctionTimeoutSeconds"]


async def test_criar_corrida_payload_rabbitmq_excluded_groups_vazio_na_primeira_criacao(
    cliente, api_key, mock_rabbitmq
):
    """Corrida nova não possui grupos excluídos."""
    await _post_ride(cliente, api_key)
    payload = mock_rabbitmq.call_args.args[4]
    assert payload["excludedGroups"] == []


async def test_criar_corrida_funciona_sem_rabbitmq(cliente, api_key):
    """Falha no broker não deve impedir a criação — o core retorna 202 mesmo assim."""
    with patch.object(
        rabbitmq_broker,
        "publish_event",
        new_callable=AsyncMock,
        side_effect=Exception("broker indisponível"),
    ):
        resp = await _post_ride(cliente, api_key)

    assert resp.status_code == 202


async def test_criar_corrida_persiste_no_banco_mesmo_sem_rabbitmq(
    cliente, api_key, db_teste: AsyncSession
):
    """O ride é gravado no banco antes da tentativa de publicação no RabbitMQ."""
    with patch.object(
        rabbitmq_broker,
        "publish_event",
        new_callable=AsyncMock,
        side_effect=Exception("broker indisponível"),
    ):
        resp = await _post_ride(cliente, api_key)

    ride_uuid = resp.json()["rideUuid"]
    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    assert resultado.scalar_one_or_none() is not None


# ===========================================================================
# 6. Autenticação
# ===========================================================================


async def test_criar_corrida_sem_api_key_retorna_401(cliente):
    resp = await cliente.post(ENDPOINT, json=CORRIDA_VALIDA)
    assert resp.status_code == 401


async def test_criar_corrida_api_key_invalida_retorna_401(cliente):
    resp = await cliente.post(
        ENDPOINT,
        json=CORRIDA_VALIDA,
        headers={"X-API-Key": "rfk_chave_inexistente_000000000000"},
    )
    assert resp.status_code == 401


# ===========================================================================
# 7. Validação de payload
# ===========================================================================


async def test_criar_corrida_payload_vazio_retorna_422(cliente, api_key):
    resp = await cliente.post(ENDPOINT, json={}, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_sem_origin_service_id_retorna_422(cliente, api_key):
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "originServiceId"}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_sem_passenger_id_retorna_422(cliente, api_key):
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "passengerId"}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_sem_origin_retorna_422(cliente, api_key):
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "origin"}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_sem_destination_retorna_422(cliente, api_key):
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "destination"}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_sem_logical_timestamp_retorna_422(cliente, api_key):
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "logicalTimestamp"}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_origin_lat_acima_de_90_retorna_422(cliente, api_key):
    payload = {**CORRIDA_VALIDA, "origin": {**CORRIDA_VALIDA["origin"], "lat": 91.0}}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_origin_lng_abaixo_de_menos_180_retorna_422(cliente, api_key):
    payload = {**CORRIDA_VALIDA, "origin": {**CORRIDA_VALIDA["origin"], "lng": -181.0}}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_logical_timestamp_negativo_retorna_422(cliente, api_key):
    payload = {**CORRIDA_VALIDA, "logicalTimestamp": -1}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_auction_timeout_abaixo_do_minimo_retorna_422(cliente, api_key):
    """auctionTimeoutSeconds = 4 está abaixo do mínimo de 5."""
    payload = {**CORRIDA_VALIDA, "auctionTimeoutSeconds": 4}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


async def test_criar_corrida_auction_timeout_acima_do_maximo_retorna_422(cliente, api_key):
    """auctionTimeoutSeconds = 61 está acima do máximo de 60."""
    payload = {**CORRIDA_VALIDA, "auctionTimeoutSeconds": 61}
    resp = await cliente.post(ENDPOINT, json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 422


# ===========================================================================
# 8. Valores limítrofes e defaults
# ===========================================================================


async def test_criar_corrida_auction_timeout_default_quando_omitido(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """Sem auctionTimeoutSeconds, o DTO usa o default de 10 s."""
    payload = {k: v for k, v in CORRIDA_VALIDA.items() if k != "auctionTimeoutSeconds"}
    resp = await _post_ride(cliente, api_key, payload)

    assert resp.status_code == 202
    ride_uuid = resp.json()["rideUuid"]

    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()
    assert ride.auctionTimeoutSeconds == 10


async def test_criar_corrida_logical_timestamp_zero_e_aceito(
    cliente, api_key, mock_rabbitmq
):
    """logicalTimestamp = 0 é o valor mínimo permitido pelo DTO."""
    payload = {**CORRIDA_VALIDA, "logicalTimestamp": 0}
    resp = await _post_ride(cliente, api_key, payload)
    assert resp.status_code == 202


async def test_criar_corrida_auction_timeout_no_limite_minimo_e_aceito(
    cliente, api_key, mock_rabbitmq
):
    """auctionTimeoutSeconds = 5 está exatamente no limite mínimo — deve ser aceito."""
    payload = {**CORRIDA_VALIDA, "auctionTimeoutSeconds": 5}
    resp = await _post_ride(cliente, api_key, payload)
    assert resp.status_code == 202


async def test_criar_corrida_auction_timeout_no_limite_maximo_e_aceito(
    cliente, api_key, mock_rabbitmq
):
    """auctionTimeoutSeconds = 60 está exatamente no limite máximo — deve ser aceito."""
    payload = {**CORRIDA_VALIDA, "auctionTimeoutSeconds": 60}
    resp = await _post_ride(cliente, api_key, payload)
    assert resp.status_code == 202


async def test_criar_corrida_uuid_e_unico_por_requisicao(
    cliente, api_key, mock_rabbitmq
):
    """Duas criações distintas devem gerar UUIDs diferentes."""
    resp1 = await _post_ride(cliente, api_key)
    resp2 = await _post_ride(cliente, api_key)

    assert resp1.json()["rideUuid"] != resp2.json()["rideUuid"]
