from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/v1/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert "version" in data


def test_groups_register_endpoint_exists():
    """
    Verifica se a rota de registro está mapeada.
    Pode retornar 422 porque faltou body.
    """
    response = client.post("/api/v1/groups/register")

    assert response.status_code in [200, 201, 422]


def test_groups_list_requires_auth():
    """
    Deve exigir API Key.
    """
    response = client.get("/api/v1/groups/register")

    assert response.status_code in [401, 403]


def test_rides_create_requires_auth():
    response = client.post("/api/v1/rides")

    assert response.status_code in [401, 403]


def test_rides_list_requires_auth():
    response = client.get("/api/v1/rides")

    assert response.status_code in [401, 403]


def test_rides_status_requires_auth():
    response = client.get("/api/v1/rides/test/status")

    assert response.status_code in [401, 403]


def test_rides_proposals_requires_auth():
    response = client.get("/api/v1/rides/test/proposals")

    assert response.status_code in [401, 403]


def test_rides_audit_requires_auth():
    response = client.get("/api/v1/rides/test/audit")

    assert response.status_code in [401, 403]


def test_lock_acquire_requires_auth():
    response = client.post("/api/v1/locks/test")

    assert response.status_code in [401, 403]


def test_lock_release_requires_auth():
    response = client.delete("/api/v1/locks/test")

    assert response.status_code in [401, 403]