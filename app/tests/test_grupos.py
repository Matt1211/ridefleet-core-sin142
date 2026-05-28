"""
Testes de registro e listagem de grupos.

Cobre os dois endpoints:
  POST /api/v1/groups/register  — público, registra o grupo
  GET  /api/v1/groups/register  — protegido, lista todos os grupos
"""

from httpx import AsyncClient

ENDPOINT = "/api/v1/groups/register"

# Payload padrão reutilizado nos testes
GRUPO_A = {
    "groupId": "grupo-a",
    "groupName": "Grupo A - Sistemas Distribuídos",
    "serviceUrl": "http://grupo-a:8081",
    "contactEmail": "grupo-a@ufv.br",
}

GRUPO_B = {
    "groupId": "grupo-b",
    "groupName": "Grupo B - Sistemas Distribuídos",
    "serviceUrl": "http://grupo-b:8082",
}


# POST /api/v1/groups/register
async def test_registrar_grupo_retorna_201_e_credenciais(cliente: AsyncClient):
    """Registro com dados válidos deve devolver 201 com groupId e apiKey."""
    resposta = await cliente.post(ENDPOINT, json=GRUPO_A)

    assert resposta.status_code == 201

    corpo = resposta.json()
    assert corpo["groupId"] == GRUPO_A["groupId"]
    assert "apiKey" in corpo
    assert "registeredAt" in corpo


async def test_api_key_gerada_tem_formato_rfk(cliente: AsyncClient):
    """A API Key deve começar com o prefixo 'rfk_'."""
    resposta = await cliente.post(ENDPOINT, json=GRUPO_A)

    api_key = resposta.json()["apiKey"]
    assert api_key.startswith("rfk_"), f"Formato inesperado: {api_key}"
    assert len(api_key) == 36, f"Tamanho inesperado: {len(api_key)}"  # "rfk_" + 32 hex


async def test_registrar_grupo_sem_email_opcional(cliente: AsyncClient):
    """contactEmail é opcional — deve aceitar o registro sem ele."""
    resposta = await cliente.post(ENDPOINT, json=GRUPO_B)

    assert resposta.status_code == 201
    assert resposta.json()["groupId"] == GRUPO_B["groupId"]


async def test_re_registrar_grupo_retorna_200_mesma_api_key(cliente: AsyncClient):
    """Re-registro do mesmo groupId deve retornar 200 com a mesma API Key."""
    primeira = await cliente.post(ENDPOINT, json=GRUPO_A)
    assert primeira.status_code == 201
    api_key_original = primeira.json()["apiKey"]

    segunda = await cliente.post(ENDPOINT, json=GRUPO_A)
    assert segunda.status_code == 200
    assert segunda.json()["apiKey"] == api_key_original


async def test_re_registrar_grupo_atualiza_service_url(cliente: AsyncClient):
    """Re-registro com serviceUrl diferente deve atualizar a URL mantendo a mesma chave."""
    await cliente.post(ENDPOINT, json=GRUPO_A)
    api_key = (await cliente.post(ENDPOINT, json=GRUPO_A)).json()["apiKey"]

    nova_url = "http://grupo-a-novo:9000"
    payload_atualizado = {**GRUPO_A, "serviceUrl": nova_url}
    resposta = await cliente.post(ENDPOINT, json=payload_atualizado)

    assert resposta.status_code == 200
    assert resposta.json()["apiKey"] == api_key

    grupos = (await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})).json()
    grupo_a = next(g for g in grupos if g["groupId"] == GRUPO_A["groupId"])
    assert grupo_a["serviceUrl"] == nova_url


async def test_registrar_grupo_sem_group_id_retorna_422(cliente: AsyncClient):
    """Payload sem groupId deve ser rejeitado com 422."""
    payload_invalido = {
        "groupName": "Sem ID",
        "serviceUrl": "http://sem-id:9000",
    }
    resposta = await cliente.post(ENDPOINT, json=payload_invalido)

    assert resposta.status_code == 422


async def test_registrar_grupo_com_group_id_invalido_retorna_422(cliente: AsyncClient):
    """groupId com letras maiúsculas ou espaços deve ser rejeitado com 422."""
    payload_invalido = {**GRUPO_A, "groupId": "Grupo Com Espaço"}
    resposta = await cliente.post(ENDPOINT, json=payload_invalido)

    assert resposta.status_code == 422


async def test_registrar_grupo_sem_service_url_retorna_422(cliente: AsyncClient):
    """Payload sem serviceUrl deve ser rejeitado com 422."""
    payload_invalido = {
        "groupId": "grupo-sem-url",
        "groupName": "Grupo sem URL",
    }
    resposta = await cliente.post(ENDPOINT, json=payload_invalido)

    assert resposta.status_code == 422


async def test_registrar_grupo_corpo_vazio_retorna_422(cliente: AsyncClient):
    """Corpo vazio deve retornar 422."""
    resposta = await cliente.post(ENDPOINT, json={})

    assert resposta.status_code == 422


# GET /api/v1/groups/register
async def _registrar_e_obter_api_key(cliente: AsyncClient, payload: dict) -> str:
    """Auxiliar: registra um grupo e devolve a API Key gerada."""
    resposta = await cliente.post(ENDPOINT, json=payload)
    assert resposta.status_code == 201
    return resposta.json()["apiKey"]


async def test_listar_grupos_sem_autenticacao_retorna_401(cliente: AsyncClient):
    """GET sem X-API-Key deve retornar 401."""
    resposta = await cliente.get(ENDPOINT)

    assert resposta.status_code == 401

    corpo = resposta.json()
    assert "error" in corpo


async def test_listar_grupos_com_api_key_invalida_retorna_401(cliente: AsyncClient):
    """GET com X-API-Key inexistente deve retornar 401."""
    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": "rfk_chave_invalida"})

    assert resposta.status_code == 401


async def test_listar_grupos_autenticado_retorna_200(cliente: AsyncClient):
    """GET com X-API-Key válida deve retornar 200 e a lista de grupos."""
    api_key = await _registrar_e_obter_api_key(cliente, GRUPO_A)

    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})

    assert resposta.status_code == 200
    assert isinstance(resposta.json(), list)


async def test_listar_grupos_retorna_grupo_registrado(cliente: AsyncClient):
    """O grupo recém-registrado deve aparecer na listagem."""
    api_key = await _registrar_e_obter_api_key(cliente, GRUPO_A)

    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})

    grupos = resposta.json()
    ids_registrados = [g["groupId"] for g in grupos]
    assert GRUPO_A["groupId"] in ids_registrados


async def test_listar_grupos_nao_expoe_api_key(cliente: AsyncClient):
    """A listagem nunca deve expor a API Key dos grupos."""
    api_key = await _registrar_e_obter_api_key(cliente, GRUPO_A)

    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})

    for grupo in resposta.json():
        assert "apiKey" not in grupo


async def test_listar_grupos_multiplos(cliente: AsyncClient):
    """Todos os grupos registrados devem aparecer na listagem."""
    api_key_a = await _registrar_e_obter_api_key(cliente, GRUPO_A)
    await _registrar_e_obter_api_key(cliente, GRUPO_B)

    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key_a})

    grupos = resposta.json()
    ids_registrados = [g["groupId"] for g in grupos]

    assert GRUPO_A["groupId"] in ids_registrados
    assert GRUPO_B["groupId"] in ids_registrados
    assert len(grupos) == 2


async def test_listar_grupos_retorna_campos_esperados(cliente: AsyncClient):
    """Cada item da lista deve ter groupId, groupName, serviceUrl e registeredAt."""
    api_key = await _registrar_e_obter_api_key(cliente, GRUPO_A)

    resposta = await cliente.get(ENDPOINT, headers={"X-API-Key": api_key})

    grupo = resposta.json()[0]
    assert "groupId" in grupo
    assert "groupName" in grupo
    assert "serviceUrl" in grupo
    assert "registeredAt" in grupo
