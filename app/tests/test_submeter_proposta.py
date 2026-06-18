"""
Testes do endpoint POST /api/v1/rides/{rideUuid}/proposals (envio assíncrono de proposta).

Cobre:
    - Autenticação (401 sem API Key)
    - 404 para corrida inexistente
    - 202 + shape do ProposalSummary no sucesso
    - Persistência (a proposta aparece no GET /proposals)
    - Evento de auditoria proposal_submitted
    - 409 quando o leilão já está encerrado (auction_status != open)
    - 422 para proposta inválida (estimatedEta < 1, estimatedPrice < 0)

SQLite in-memory; o publish do RabbitMQ é mockado ao criar a corrida.
"""

import pytest
import pytest_asyncio
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

GRUPO_PROPONENTE = {
    "groupId": "grupo-proponente",
    "groupName": "Grupo Proponente - Teste",
    "serviceUrl": "http://grupo-proponente:8082",
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

PROPOSTA_VALIDA = {
    "estimatedEta": 300,
    "estimatedPrice": 18.5,
    "logicalTimestamp": 5,
}


@pytest.fixture
def mock_rabbitmq():
    with patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub:
        yield mock_pub


async def _registrar_grupo(cliente: AsyncClient, grupo: dict) -> str:
    resp = await cliente.post(ENDPOINT_GRUPOS, json=grupo)
    assert resp.status_code == 201
    return resp.json()["apiKey"]


@pytest_asyncio.fixture
async def api_key_origem(cliente: AsyncClient) -> str:
    """API Key do grupo que cria a corrida."""
    return await _registrar_grupo(cliente, GRUPO_ORIGEM)


@pytest_asyncio.fixture
async def api_key_proponente(cliente: AsyncClient) -> str:
    """API Key do grupo que envia a proposta (identifica o proponente)."""
    return await _registrar_grupo(cliente, GRUPO_PROPONENTE)


async def _criar_corrida(cliente: AsyncClient, api_key: str) -> str:
    resp = await cliente.post(
        ENDPOINT_RIDES,
        json=CORRIDA_VALIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    return resp.json()["rideUuid"]


# ---------------------------------------------------------------------------
# Autenticação / corrida inexistente
# ---------------------------------------------------------------------------

async def test_enviar_proposta_sem_api_key_retorna_401(cliente):
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=UUID_INEXISTENTE),
        json=PROPOSTA_VALIDA,
    )
    assert resp.status_code == 401


async def test_enviar_proposta_corrida_inexistente_retorna_404(cliente, api_key_proponente):
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=UUID_INEXISTENTE),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sucesso
# ---------------------------------------------------------------------------

async def test_enviar_proposta_retorna_202(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    assert resp.status_code == 202


async def test_enviar_proposta_retorna_shape_correto(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    corpo = resp.json()
    assert corpo["groupId"] == GRUPO_PROPONENTE["groupId"]
    assert corpo["serviceUrl"] == GRUPO_PROPONENTE["serviceUrl"]
    assert corpo["status"] == "accepted"
    assert corpo["estimatedEta"] == PROPOSTA_VALIDA["estimatedEta"]
    assert corpo["estimatedPrice"] == PROPOSTA_VALIDA["estimatedPrice"]


async def test_enviar_proposta_persiste_e_aparece_no_get(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    resp = await cliente.get(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key_origem},
    )
    assert resp.status_code == 200
    propostas = resp.json()["proposals"]
    assert len(propostas) == 1
    assert propostas[0]["groupId"] == GRUPO_PROPONENTE["groupId"]
    assert propostas[0]["status"] == "accepted"


async def test_enviar_proposta_registra_audit_proposal_submitted(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    resp = await cliente.get(
        ENDPOINT_AUDIT.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key_origem},
    )
    tipos = [e["eventType"] for e in resp.json()["events"]]
    assert "proposal_submitted" in tipos


# ---------------------------------------------------------------------------
# Leilão encerrado -> 409
# ---------------------------------------------------------------------------

async def test_enviar_proposta_leilao_encerrado_retorna_409(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)

    await db_teste.execute(
        update(Ride).where(Ride.ride_uuid == ride_uuid).values(auction_status="closed")
    )
    await db_teste.commit()

    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json=PROPOSTA_VALIDA,
        headers={"X-API-Key": api_key_proponente},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Validação do corpo -> 422
# ---------------------------------------------------------------------------

async def test_enviar_proposta_eta_menor_que_1_retorna_422(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json={"estimatedEta": 0, "estimatedPrice": 10.0, "logicalTimestamp": 5},
        headers={"X-API-Key": api_key_proponente},
    )
    assert resp.status_code == 422


async def test_enviar_proposta_preco_negativo_retorna_422(
    cliente, api_key_origem, api_key_proponente, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key_origem)
    resp = await cliente.post(
        ENDPOINT_PROPOSALS.format(rideUuid=ride_uuid),
        json={"estimatedEta": 100, "estimatedPrice": -5.0, "logicalTimestamp": 5},
        headers={"X-API-Key": api_key_proponente},
    )
    assert resp.status_code == 422
