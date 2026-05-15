"""
Testes dos endpoints POST e DELETE /api/v1/locks/{rideUuid}.

Cobre:
  POST /locks/{rideUuid}:
    - Autenticação (401)
    - Renovação do próprio lock (200, campos, expiresAt futuro)
    - Conflito quando outro serviço detém lock ativo (409, heldBy no corpo)
    - 404 para corrida inexistente
    - Aquisição após lock removido (200, serviceId correto)
    - Evento de auditoria lock_acquired registrado
    - TTL persiste corretamente
    - Validação de payload

  DELETE /locks/{rideUuid}:
    - Autenticação (401)
    - Liberação bem-sucedida (204)
    - Lock removido do banco
    - Evento de auditoria lock_released registrado
    - 404 quando nenhum lock ativo
    - 403 quando solicitante não é o detentor
    - 404 para corrida inexistente
    - Validação de payload
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_lock import RideLock
from app.rabbitmq import rabbitmq_broker

ENDPOINT_RIDES = "/api/v1/rides"
ENDPOINT_LOCK = "/api/v1/locks/{rideUuid}"
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


# POST /locks/{rideUuid} — Autenticação
async def test_adquirir_lock_sem_api_key_retorna_401(cliente):
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem", "ttlSeconds": 30},
    )
    assert resp.status_code == 401


async def test_adquirir_lock_api_key_invalida_retorna_401(cliente):
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem", "ttlSeconds": 30},
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# POST /locks/{rideUuid} — Renovação do próprio lock
async def test_adquirir_lock_renovacao_proprio_lock_retorna_200(
    cliente, api_key, mock_rabbitmq
):
    """grupo-origem detém o lock; renovar com o mesmo serviceId => 200."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_adquirir_lock_renovacao_retorna_campos_corretos(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["rideUuid"] == ride_uuid
    assert corpo["serviceId"] == "grupo-origem"
    assert "expiresAt" in corpo


async def test_adquirir_lock_expires_at_e_no_futuro(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )
    expires_at_str = resp.json()["expiresAt"]
    # Remove Z e parseia como UTC
    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    assert expires_at > datetime.now(expires_at.tzinfo)


async def test_adquirir_lock_ttl_persistido_no_banco(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """O TTL de 60s deve resultar em expires_at ≈ agora+60s."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    lock = resultado.scalar_one()
    ttl_restante = (lock.expires_at - datetime.utcnow()).total_seconds()
    assert ttl_restante > 55  # margem de 5s para execução do teste


# POST /locks/{rideUuid} — Conflito (409)
async def test_adquirir_lock_conflito_retorna_409(cliente, api_key, mock_rabbitmq):
    """grupo-b tenta adquirir o lock que grupo-origem detém => 409."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-b", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 409


async def test_adquirir_lock_conflito_corpo_contem_held_by(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-b", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["heldBy"] == "grupo-origem"
    assert corpo["rideUuid"] == ride_uuid


# POST /locks/{rideUuid} — Corrida inexistente (404)
async def test_adquirir_lock_corrida_inexistente_retorna_404(cliente, api_key):
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


# POST /locks/{rideUuid} — Aquisição sem lock ativo
async def test_adquirir_lock_sem_lock_ativo_retorna_200(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """Sem lock existente, qualquer serviço pode adquirir."""
    ride_uuid = await _criar_corrida(cliente, api_key)

    # Remove lock criado pela criação da corrida
    await db_teste.execute(delete(RideLock).where(RideLock.ride_uuid == ride_uuid))
    await db_teste.commit()
    db_teste.expunge_all()

    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-b", "ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["serviceId"] == "grupo-b"


# POST /locks/{rideUuid} — Auditoria
async def test_adquirir_lock_registra_evento_lock_acquired(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 60},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "lock_acquired",
        )
    )
    assert resultado.scalar_one_or_none() is not None


# POST /locks/{rideUuid} — Validação de payload
async def test_adquirir_lock_sem_service_id_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"ttlSeconds": 30},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_adquirir_lock_ttl_zero_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 0},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_adquirir_lock_ttl_acima_do_maximo_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 301},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_adquirir_lock_ttl_minimo_aceito(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 1},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_adquirir_lock_ttl_maximo_aceito(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.post(
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "ttlSeconds": 300},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


# DELETE /locks/{rideUuid} — Autenticação
async def test_liberar_lock_sem_api_key_retorna_401(cliente):
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem"},
    )
    assert resp.status_code == 401


async def test_liberar_lock_api_key_invalida_retorna_401(cliente):
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# DELETE /locks/{rideUuid} — Liberação bem-sucedida
async def test_liberar_lock_retorna_204(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 204


async def test_liberar_lock_remove_lock_do_banco(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    assert resultado.scalar_one_or_none() is None


async def test_liberar_lock_registra_evento_lock_released(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "lock_released",
        )
    )
    assert resultado.scalar_one_or_none() is not None


# DELETE /locks/{rideUuid} — Erros
async def test_liberar_lock_sem_lock_ativo_retorna_404(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)

    # Remove o lock
    await db_teste.execute(delete(RideLock).where(RideLock.ride_uuid == ride_uuid))
    await db_teste.commit()
    db_teste.expunge_all()

    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


async def test_liberar_lock_sem_ser_detentor_retorna_403(
    cliente, api_key, mock_rabbitmq
):
    """grupo-b tenta liberar o lock que grupo-origem detém => 403."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-b"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 403


async def test_liberar_lock_corrida_inexistente_retorna_404(cliente, api_key):
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=UUID_INEXISTENTE),
        json={"serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


# DELETE /locks/{rideUuid} — Validação de payload
async def test_liberar_lock_sem_service_id_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.request(
        "DELETE",
        ENDPOINT_LOCK.format(rideUuid=ride_uuid),
        json={},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422
