"""
Testes de contrato do Core.

Propósito: verificar que cada endpoint retorna o shape documentado
(status code + campos obrigatórios + tipos). Outros grupos podem
executar `pytest app/tests/test_core_contract.py` para confirmar
compatibilidade antes da integração real.

Não contém assertions de lógica de negócio — apenas forma.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.rabbitmq import rabbitmq_broker

ENDPOINT_GRUPOS = "/api/v1/groups/register"
ENDPOINT_RIDES = "/api/v1/rides"
UUID_INEXISTENTE = "00000000-0000-0000-0000-000000000000"

GRUPO = {
    "groupId": "contrato-grupo",
    "groupName": "Grupo Contrato",
    "serviceUrl": "http://contrato-grupo:8082",
}

CORRIDA = {
    "originServiceId": "contrato-grupo",
    "passengerId": "passageiro-contrato",
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
    resp = await cliente.post(ENDPOINT_GRUPOS, json=GRUPO)
    assert resp.status_code == 201
    return resp.json()["apiKey"]


@pytest.fixture
def mock_rabbitmq():
    with patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as m:
        yield m


async def _criar_corrida(cliente: AsyncClient, api_key: str, mock_rabbitmq) -> str:
    resp = await cliente.post(
        ENDPOINT_RIDES,
        json=CORRIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    return resp.json()["rideUuid"]


# ---------------------------------------------------------------------------
# POST /api/v1/rides  →  202 RideAcceptedDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_post_rides(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    resp = await cliente.post(
        ENDPOINT_RIDES,
        json=CORRIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["logicalTimestamp"], int)
    assert isinstance(body["message"], str)


# ---------------------------------------------------------------------------
# GET /api/v1/rides  →  200 RideListDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_get_rides(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.get(ENDPOINT_RIDES, headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["total"], int)
    assert isinstance(body["limit"], int)
    assert isinstance(body["offset"], int)
    assert isinstance(body["rides"], list)


# ---------------------------------------------------------------------------
# GET /api/v1/rides/{rideUuid}/status  →  200 RideStatusDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_get_ride_status(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.get(
        f"{ENDPOINT_RIDES}/{ride_uuid}/status",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["state"], str)
    assert isinstance(body["logicalTimestamp"], int)
    assert isinstance(body["updatedAt"], str)


# ---------------------------------------------------------------------------
# PATCH /api/v1/rides/{rideUuid}/status  →  200 RideStatusDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_patch_ride_status(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.patch(
        f"{ENDPOINT_RIDES}/{ride_uuid}/status",
        json={"newState": "cancelled", "serviceId": "contrato-grupo", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["state"], str)
    assert isinstance(body["logicalTimestamp"], int)
    assert isinstance(body["updatedAt"], str)


# ---------------------------------------------------------------------------
# GET /api/v1/rides/{rideUuid}/proposals  →  200 AuctionResultDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_get_proposals(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.get(
        f"{ENDPOINT_RIDES}/{ride_uuid}/proposals",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["status"], str)
    assert isinstance(body["proposals"], list)


# ---------------------------------------------------------------------------
# GET /api/v1/rides/{rideUuid}/audit  →  200 AuditLogDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_get_audit(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.get(
        f"{ENDPOINT_RIDES}/{ride_uuid}/audit",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["events"], list)


# ---------------------------------------------------------------------------
# POST /api/v1/locks/{rideUuid}  →  200 LockResponseDTO
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_post_lock(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    resp = await cliente.post(
        f"/api/v1/locks/{ride_uuid}",
        json={"serviceId": "contrato-grupo", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rideUuid"], str)
    assert isinstance(body["serviceId"], str)
    assert isinstance(body["expiresAt"], str)


# ---------------------------------------------------------------------------
# DELETE /api/v1/locks/{rideUuid}  →  204
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_delete_lock(cliente: AsyncClient, api_key: str, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key, mock_rabbitmq)
    await cliente.post(
        f"/api/v1/locks/{ride_uuid}",
        json={"serviceId": "contrato-grupo", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    resp = await cliente.request(
        "DELETE",
        f"/api/v1/locks/{ride_uuid}",
        json={"serviceId": "contrato-grupo"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# GET /metrics  →  200 text/plain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_metrics(cliente: AsyncClient):
    resp = await cliente.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
