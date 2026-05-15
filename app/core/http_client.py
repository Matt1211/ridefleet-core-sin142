import httpx

# Instância global reutilizada em toda a aplicação.
# Inicializada com timeout conservador: 8s de connect + 12s de read.
# O código do leilão usa timeouts próprios por chamada quando necessário.
http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
    headers={"Content-Type": "application/json"},
)
