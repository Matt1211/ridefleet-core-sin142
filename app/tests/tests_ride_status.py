"""
Testes dos endpoints GET e PATCH /api/v1/rides/{rideUuid}/status.

Cobre:
  GET /rides/{rideUuid}/status:
    - Autenticação (401)
    - Resposta 200 com campos corretos
    - 404 para UUID inexistente
    - lockHeldBy preenchido quando lock existe

  PATCH /rides/{rideUuid}/status:
    - Autenticação (401)
    - Transição válida (request => cancelled): 200, estado atualizado, DB, auditoria
    - Estado terminal libera lock automaticamente
    - Transições inválidas: 422 (transição proibida, estado terminal, timestamp stale)
    - 404 para UUID inexistente
    - 409 quando lock obrigatório não detido
    - 200 quando lock detido pelo solicitante
    - Idempotência: mesmo serviceId+logicalTimestamp => 200
    - compensating => publica auction_request e atualiza excluded_groups
    - Validação de payload
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ride import Ride, RideStatus
from app.models.ride_audit_event import RideAuditEvent
from app.models.ride_lock import RideLock
from app.rabbitmq import rabbitmq_broker

ENDPOINT_RIDES = "/api/v1/rides"
ENDPOINT_STATUS = "/api/v1/rides/{rideUuid}/status"
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
    """Cria corrida e retorna rideUuid."""
    resp = await cliente.post(
        ENDPOINT_RIDES,
        json=CORRIDA_VALIDA,
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 202
    return resp.json()["rideUuid"]


async def _forcar_status_match(db: AsyncSession, ride_uuid: str) -> None:
    """
    Força o status do ride para 'match' via update direto no banco.
    Limpa a identity map para garantir que a próxima query leia do DB.
    """
    await db.execute(
        update(Ride)
        .where(Ride.ride_uuid == ride_uuid)
        .values(status="match")
    )
    await db.commit()
    db.expunge_all()


async def _transferir_lock(db: AsyncSession, ride_uuid: str, novo_detentor: str) -> None:
    """Transfere o lock de um ride para outro serviceId."""
    await db.execute(
        update(RideLock)
        .where(RideLock.ride_uuid == ride_uuid)
        .values(
            held_by=novo_detentor,
            expires_at=datetime.utcnow() + timedelta(seconds=60),
        )
    )
    await db.commit()
    db.expunge_all()


# GET /rides/{rideUuid}/status — Autenticação
async def test_buscar_status_sem_api_key_retorna_401(cliente):
    resp = await cliente.get(ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE))
    assert resp.status_code == 401


async def test_buscar_status_api_key_invalida_retorna_401(cliente):
    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# GET /rides/{rideUuid}/status — Resposta
async def test_buscar_status_retorna_200(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_buscar_status_retorna_404_para_uuid_inexistente(cliente, api_key):
    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


async def test_buscar_status_contem_campos_obrigatorios(cliente, api_key, mock_rabbitmq):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["rideUuid"] == ride_uuid
    assert corpo["state"] == "request"
    assert isinstance(corpo["logicalTimestamp"], int)
    assert "updatedAt" in corpo


async def test_buscar_status_retorna_lock_held_by_quando_lock_existe(
    cliente, api_key, mock_rabbitmq
):
    """Lock criado durante POST /rides deve aparecer no status."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["lockHeldBy"] == "grupo-origem"
    assert corpo["lockExpiresAt"] is not None


async def test_buscar_status_lock_null_quando_sem_lock(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """Após liberar o lock, lockHeldBy deve ser null."""
    from sqlalchemy import delete

    ride_uuid = await _criar_corrida(cliente, api_key)
    await db_teste.execute(delete(RideLock).where(RideLock.ride_uuid == ride_uuid))
    await db_teste.commit()
    db_teste.expunge_all()

    resp = await cliente.get(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        headers={"X-API-Key": api_key},
    )
    corpo = resp.json()
    assert corpo["lockHeldBy"] is None
    assert corpo["lockExpiresAt"] is None


# PATCH /rides/{rideUuid}/status — Autenticação
async def test_atualizar_status_sem_api_key_retorna_401(cliente):
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
    )
    assert resp.status_code == 401


async def test_atualizar_status_api_key_invalida_retorna_401(cliente):
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": "rfk_invalida_000"},
    )
    assert resp.status_code == 401


# PATCH /rides/{rideUuid}/status — Transição válida (request => cancelled)
async def test_atualizar_status_request_para_cancelled_retorna_200(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200


async def test_atualizar_status_retorna_novo_estado_na_resposta(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["state"] == "cancelled"


async def test_atualizar_status_persiste_novo_estado_no_banco(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()
    assert ride.status == RideStatus.CANCELLED.value


async def test_atualizar_status_terminal_libera_lock_automaticamente(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideLock).where(RideLock.ride_uuid == ride_uuid)
    )
    assert resultado.scalar_one_or_none() is None


async def test_atualizar_status_registra_evento_state_transition_no_audit(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    db_teste.expunge_all()
    resultado = await db_teste.execute(
        select(RideAuditEvent).where(
            RideAuditEvent.ride_uuid == ride_uuid,
            RideAuditEvent.event_type == "state_transition",
        )
    )
    evento = resultado.scalar_one()
    assert evento.service_id == "grupo-origem"


async def test_atualizar_status_retorna_ride_uuid_correto(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.json()["rideUuid"] == ride_uuid


# PATCH /rides/{rideUuid}/status — Transições inválidas (422)
async def test_atualizar_status_transicao_proibida_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    """request => confirm não é uma transição permitida."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "confirm", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_de_estado_terminal_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    """Após cancelled, qualquer nova transição deve retornar 422."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 3},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_timestamp_igual_ao_ultimo_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    """logicalTimestamp deve ser ESTRITAMENTE maior que o último registrado (1)."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 1},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_timestamp_menor_que_ultimo_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 0},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_uuid_inexistente_retorna_404(cliente, api_key):
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=UUID_INEXISTENTE),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 404


# PATCH /rides/{rideUuid}/status — Lock obrigatório (409)
async def test_atualizar_status_confirm_sem_lock_retorna_409(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """
    match => confirm exige lock. O lock está com grupo-origem,
    mas grupo-b solicita o confirm => 409.
    """
    ride_uuid = await _criar_corrida(cliente, api_key)
    await _forcar_status_match(db_teste, ride_uuid)

    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "confirm", "serviceId": "grupo-b", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 409


async def test_atualizar_status_confirm_com_lock_retorna_200(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """grupo-b detém o lock e solicita confirm => 200."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await _forcar_status_match(db_teste, ride_uuid)
    await _transferir_lock(db_teste, ride_uuid, "grupo-b")

    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "confirm", "serviceId": "grupo-b", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "confirm"


# PATCH /rides/{rideUuid}/status — Idempotência
async def test_atualizar_status_idempotente_retorna_200_na_segunda_chamada(
    cliente, api_key, mock_rabbitmq
):
    """Mesmos serviceId + logicalTimestamp: segundo PATCH é idempotente (200)."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    payload = {"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2}

    resp1 = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json=payload,
        headers={"X-API-Key": api_key},
    )
    resp2 = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json=payload,
        headers={"X-API-Key": api_key},
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200


async def test_atualizar_status_idempotente_retorna_mesmo_estado(
    cliente, api_key, mock_rabbitmq
):
    """Chamada idempotente deve retornar o estado atual (cancelled), não tentar re-transicionar."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    payload = {"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": 2}

    resp1 = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json=payload,
        headers={"X-API-Key": api_key},
    )
    resp2 = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json=payload,
        headers={"X-API-Key": api_key},
    )
    assert resp1.json()["state"] == resp2.json()["state"] == "cancelled"


# PATCH /rides/{rideUuid}/status — Compensação
async def test_atualizar_para_compensating_publica_auction_request(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """Transição para compensating deve publicar auction_request."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await _forcar_status_match(db_teste, ride_uuid)
    mock_rabbitmq.reset_mock()

    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "compensating", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )

    assert mock_rabbitmq.call_count >= 1
    event_type = mock_rabbitmq.call_args.args[0]
    assert event_type == "auction_request"


async def test_atualizar_para_compensating_payload_contem_excluded_groups(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """O grupo que solicitou compensating deve estar em excludedGroups no payload RabbitMQ."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await _forcar_status_match(db_teste, ride_uuid)
    mock_rabbitmq.reset_mock()

    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "compensating", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )

    payload = mock_rabbitmq.call_args.args[4]
    assert "grupo-origem" in payload["excludedGroups"]


async def test_atualizar_para_compensating_persiste_excluded_groups_no_banco(
    cliente, api_key, mock_rabbitmq, db_teste: AsyncSession
):
    """excluded_groups deve ser atualizado no banco após compensating."""
    ride_uuid = await _criar_corrida(cliente, api_key)
    await _forcar_status_match(db_teste, ride_uuid)

    await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "compensating", "serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )

    db_teste.expunge_all()
    resultado = await db_teste.execute(select(Ride).where(Ride.ride_uuid == ride_uuid))
    ride = resultado.scalar_one()
    assert "grupo-origem" in (ride.excluded_groups or "")


# PATCH /rides/{rideUuid}/status — Validação de payload
async def test_atualizar_status_payload_vazio_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_sem_new_state_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"serviceId": "grupo-origem", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_sem_service_id_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "logicalTimestamp": 2},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_sem_logical_timestamp_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422


async def test_atualizar_status_logical_timestamp_negativo_retorna_422(
    cliente, api_key, mock_rabbitmq
):
    ride_uuid = await _criar_corrida(cliente, api_key)
    resp = await cliente.patch(
        ENDPOINT_STATUS.format(rideUuid=ride_uuid),
        json={"newState": "cancelled", "serviceId": "grupo-origem", "logicalTimestamp": -1},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 422
