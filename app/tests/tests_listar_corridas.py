"""
Testes do endpoint GET /api/v1/rides — Listagem de corridas.

Cobre:
  - Autenticação (sem key / key inválida => 401)
  - Resposta básica (200, estrutura, lista vazia)
  - Corridas criadas aparecem na listagem
  - Filtros: originServiceId, state
  - Paginação: limit, offset, total
  - Validação de query params inválidos
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from app.rabbitmq import rabbitmq_broker

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

@pytest_asyncio.fixture
async def api_key(cliente: AsyncClient) -> str:
    """Registra o grupo de origem e devolve a API Key."""
    resp = await cliente.post(ENDPOINT_GRUPOS, json=GRUPO_ORIGEM)
    assert resp.status_code == 201
    return resp.json()["apiKey"]


@pytest.fixture
def mock_rabbitmq():
    with patch.object(rabbitmq_broker, "publish_event", new_callable=AsyncMock) as mock_pub:
        yield mock_pub


# Helper
async def _post_ride(cliente: AsyncClient, api_key: str) -> str:
    """Cria uma corrida e retorna o rideUuid."""
    resp = await cliente.post(
        ENDPOINT,
        json=CORRIDA_VALIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    return resp.json()["rideUuid"]


# 1. Autenticação
async def test_listar_corridas_sem_api_key_retorna_401(cliente):
    resp = await cliente.get(ENDPOINT)
    assert resp.status_code == 401


async def test_listar_corridas_api_key_invalida_retorna_401(cliente):
    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": "rfk_chave_inexistente_000"})
    assert resp.status_code == 401


# 2. Resposta básica
async def test_listar_corridas_retorna_200(cliente, api_key, mock_rabbitmq):
    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    assert resp.status_code == 200


async def test_listar_corridas_vazia_quando_nenhuma_corrida(cliente, api_key, mock_rabbitmq):
    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    corpo = resp.json()
    assert corpo["total"] == 0
    assert corpo["rides"] == []


async def test_listar_corridas_contem_campos_de_paginacao(cliente, api_key, mock_rabbitmq):
    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    corpo = resp.json()
    assert "total" in corpo
    assert "limit" in corpo
    assert "offset" in corpo
    assert "rides" in corpo


async def test_listar_corridas_defaults_de_paginacao(cliente, api_key, mock_rabbitmq):
    """Sem parâmetros, limit padrão = 50 e offset = 0."""
    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    corpo = resp.json()
    assert corpo["limit"] == 50
    assert corpo["offset"] == 0


# 3. Corridas criadas aparecem na listagem
async def test_listar_corridas_retorna_corrida_criada(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _post_ride(cliente, api_key)

    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    uuids = [r["rideUuid"] for r in resp.json()["rides"]]
    assert ride_uuid in uuids


async def test_listar_corridas_total_reflete_quantidade_criada(cliente, api_key, mock_rabbitmq):
    await _post_ride(cliente, api_key)
    await _post_ride(cliente, api_key)

    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    assert resp.json()["total"] == 2


async def test_listar_corridas_cada_ride_tem_campos_obrigatorios(cliente, api_key, mock_rabbitmq):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    ride = resp.json()["rides"][0]

    assert "rideUuid" in ride
    assert "state" in ride
    assert "logicalTimestamp" in ride
    assert "updatedAt" in ride


async def test_listar_corridas_estado_request_apos_criacao(cliente, api_key, mock_rabbitmq):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    ride = resp.json()["rides"][0]
    assert ride["state"] == "request"


async def test_listar_corridas_lock_held_by_preenchido(cliente, api_key, mock_rabbitmq):
    """Após criar a corrida, o lock inicial pertence ao grupo de origem."""
    await _post_ride(cliente, api_key)

    resp = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})
    ride = resp.json()["rides"][0]
    assert ride["lockHeldBy"] == CORRIDA_VALIDA["originServiceId"]


# 4. Filtros
async def test_listar_corridas_filtro_por_origin_service_id_retorna_resultado(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"originServiceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_listar_corridas_filtro_origin_service_id_sem_resultado(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"originServiceId": "grupo-inexistente"},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["total"] == 0
    assert resp.json()["rides"] == []


async def test_listar_corridas_filtro_por_state_request_retorna_corrida(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"state": "request"},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["total"] == 1


async def test_listar_corridas_filtro_por_state_sem_match_retorna_vazio(
    cliente, api_key, mock_rabbitmq
):
    await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"state": "match"},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["total"] == 0
    assert resp.json()["rides"] == []


# 5. Paginação
async def test_listar_corridas_limit_restringe_rides_retornados(cliente, api_key, mock_rabbitmq):
    for _ in range(3):
        await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"limit": 2},
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert len(corpo["rides"]) == 2
    assert corpo["total"] == 3  # total reflete o real, não o limit


async def test_listar_corridas_offset_pula_primeiros_registros(cliente, api_key, mock_rabbitmq):
    for _ in range(3):
        await _post_ride(cliente, api_key)

    resp = await cliente.get(
        ENDPOINT,
        params={"limit": 50, "offset": 2},
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert len(corpo["rides"]) == 1
    assert corpo["offset"] == 2


async def test_listar_corridas_offset_refletido_na_resposta(cliente, api_key, mock_rabbitmq):
    resp = await cliente.get(
        ENDPOINT,
        params={"offset": 5},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["offset"] == 5


async def test_listar_corridas_limit_refletido_na_resposta(cliente, api_key, mock_rabbitmq):
    resp = await cliente.get(
        ENDPOINT,
        params={"limit": 10},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["limit"] == 10

# 6. Validação de query params
async def test_listar_corridas_limit_zero_retorna_422(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT,
        params={"limit": 0},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_listar_corridas_limit_acima_de_200_retorna_422(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT,
        params={"limit": 201},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_listar_corridas_offset_negativo_retorna_422(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT,
        params={"offset": -1},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422
