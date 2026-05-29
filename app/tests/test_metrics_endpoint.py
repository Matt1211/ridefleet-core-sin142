import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_retorna_200_com_content_type_prometheus(cliente: AsyncClient):
    resp = await cliente.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
