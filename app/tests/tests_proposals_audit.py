"""
Testes dos endpoints:
  - GET /api/v1/rides/{rideUuid}/proposals
  - GET /api/v1/rides/{rideUuid}/audit

Cobre:
  GET /proposals:
    - Autenticação (401)
    - 200 com campos obrigatórios
    - 404 para UUID inexistente
    - Lista vazia sem propostas
    - auction_status = open após criação
    - winner = null sem vencedor

  GET /audit:
    - Autenticação (401)
    - 200 com campos obrigatórios
    - 404 para UUID inexistente
    - Contém evento ride_created após criação
    - Campos corretos nos eventos (eventType, serviceId, logicalTimestamp, wallClockTime)
    - rideUuid correto na resposta
    - Contém state_transition após transição de status
    - Múltiplos eventos após lock_acquired + state_transition
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride import Ride
from app.rabbitmq import rabbitmq_broker

ENDPOINT_RIDES = "/api/v1/rides"
ENDPOINT_PROPOSALS = "/api/v1/rides/{rideUuid}/proposals"
ENDPOINT_AUDIT = "/api/v1/rides/{rideUuid}/audit"
ENDPOINT_GRUPOS = "/api/v1/groups/register"
UUID_INEXISTENTE = "00000000-0000-0000-0000-000000000000"

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

@pytest_asyncio.fixture
async def api_key(cliente: AsyncClient) -> str:
    resp = await cliente.post(ENDPOINT_GRUPOS, json=GRUPO_ORIGEM)
    assert resp.status_code == 201
    return resp.json()["apiKey"]


@pytest.fixture
def mock_rabbitmq():
    with patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub:
        yield mock_pub

async def _criar_corrida(cliente: AsyncClient, api_key: str) -> str:
    resp = await cliente.post(
        ENDPOINT_RIDES,
        json=CORRIDA_VALIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    return resp.json()["rideUuid"]


# GET /rides/{rideUuid}/proposals — Autenticação
async def test_buscar_propostas_sem_api_key_retorna_401(cliente):
    resp = await cliente.get(ENDPOINT_PROPOSALS.format(rideUuid=UUID_INEXISTENTE))
    assert resp.status_code == 401


async def test_buscar_propostas_api_key_invalida_retorna_401(cliente):
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# GET /rides/{rideUuid}/proposals — Resposta
async def test_buscar_propostas_retorna_200(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_buscar_propostas_retorna_404_para_uuid_inexistente(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


async def test_buscar_propostas_contem_campos_obrigatorios(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["rideUuid"] == ride_uuid
    assert "status" in corpo
    assert "proposals" in corpo
    assert "auctionOpenedAt" in corpo


async def test_buscar_propostas_lista_vazia_sem_propostas(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["proposals"] == []


async def test_buscar_propostas_status_open_apos_criacao(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["status"] == "open"


async def test_buscar_propostas_winner_null_sem_vencedor(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["winner"] is None


async def test_buscar_propostas_auction_closed_at_null_enquanto_aberto(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["auctionClosedAt"] is None


async def test_buscar_propostas_status_no_proposals_apos_leilao_encerrado_vazio(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """Leilão encerrado sem propostas deve refletir no endpoint."""
    ride_uuid = await _criar_corrida(cliente, api_key)

    await db_teste.execute(
        update(Ride)
        .where(Ride.ride_uuid == ride_uuid)
        .values(auction_status="no_proposals", auction_closed_at=datetime.now(tz=timezone.utc))
    )
    await db_teste.commit()

    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_proposals"


# GET /rides/{rideUuid}/audit — Autenticação
async def test_buscar_audit_sem_api_key_retorna_401(cliente):
    resp = await cliente.get(ENDPOINT_AUDIT.format(rideUuid=UUID_INEXISTENTE))
    assert resp.status_code == 401


async def test_buscar_audit_api_key_invalida_retorna_401(cliente):
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# GET /rides/{rideUuid}/audit — Resposta
async def test_buscar_audit_retorna_200(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_buscar_audit_retorna_404_para_uuid_inexistente(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


async def test_buscar_audit_retorna_ride_uuid_correto(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["rideUuid"] == ride_uuid


async def test_buscar_audit_contem_evento_ride_created(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    tipos = [e["eventType"] for e in resp.json()["events"]]
    assert "ride_created" in tipos


async def test_buscar_audit_evento_tem_campos_obrigatorios(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    evento = resp.json()["events"][0]
    assert "eventType" in evento
    assert "serviceId" in evento
    assert "logicalTimestamp" in evento
    assert "wallClockTime" in evento


async def test_buscar_audit_evento_ride_created_tem_service_id_correto(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    evento = next(
        e for e in resp.json()["events"] if e["eventType"] == "ride_created"
    )
    assert evento["serviceId"] == CORRIDA_VALIDA["originServiceId"]


async def test_buscar_audit_evento_ride_created_tem_logical_timestamp_valido(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    evento = next(
        e for e in resp.json()["events"] if e["eventType"] == "ride_created"
    )
    assert isinstance(evento["logicalTimestamp"], int)
    assert evento["logicalTimestamp"] >= CORRIDA_VALIDA["logicalTimestamp"]


async def test_buscar_audit_apos_transicao_contem_state_transition(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        f"/api/v1/rides/{ride_uuid}/status",
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )

    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    tipos = [e["eventType"] for e in resp.json()["events"]]
    assert "state_transition" in tipos


async def test_buscar_audit_apos_lock_acquired_contem_evento_lock(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.post(
        f"/api/v1/locks/{ride_uuid}",
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )

    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    tipos = [e["eventType"] for e in resp.json()["events"]]
    assert "lock_acquired" in tipos


async def test_buscar_audit_sequencia_crescente_de_logical_timestamps(
    cliente, api_key, mock_rabbitmq
):
    """Cada evento deve ter logicalTimestamp >= ao anterior (relógio de Lamport)."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        f"/api/v1/rides/{ride_uuid}/status",
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )

    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    eventos = resp.json()["events"]
    timestamps = [e["logicalTimestamp"] for e in eventos]
    assert timestamps == sorted(timestamps)
