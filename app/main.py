import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI

from app.controllers.auth_controller import router as auth_router
from app.core.metrics import metrics_endpoint, circuit_breaker_metric
from app.controllers.ride_controller import router as ride_router
from app.core.http_client import http_client
from app.database import create_tables
from prometheus_client import make_asgi_app
from app.exceptions import register_exception_handlers
from app.rabbitmq import rabbitmq_broker
from app.workers.auction_worker import iniciar_worker as iniciar_auction_worker
from app.workers.lock_monitor import monitorar_locks_expirados

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("aiormq").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):       
    logger.info("=== RideFleet Core iniciando ===")
    await create_tables()
    logger.info("Banco de dados pronto")

    # Tenta conectar ao RabbitMQ; se falhar (ex.: testes sem broker),
    # apenas loga o aviso e continua sem os workers.
    try:
        await rabbitmq_broker.connect()
        auction_task = asyncio.create_task(iniciar_auction_worker())
        logger.info("Auction worker iniciado")
    except Exception as exc:
        logger.warning("RabbitMQ indisponível na inicialização: %s", exc)
        auction_task = None

    monitor_task = asyncio.create_task(monitorar_locks_expirados())
    logger.info("Lock monitor iniciado")
    logger.info("=== RideFleet Core pronto para receber requisições ===")

    yield

    logger.info("=== RideFleet Core encerrando ===")
    monitor_task.cancel()
    if auction_task:
        auction_task.cancel()

    await http_client.aclose()
    await rabbitmq_broker.close()


# Aplicação
app = FastAPI(
    title="RideFleet Core API",
    version="0.4.0",
    description=(
        "Contrato de integração entre os serviços do ecossistema RideFleet. "
        "Toda comunicação entre grupos DEVE passar pelo core. "
        "SIN 142 - Sistemas Distribuídos - UFV 2026/1\n"
    ),
    servers=[
        {"url": "http://core:8080/api/v1", "description": "Core local (Docker Compose)"},
        {"url": "http://localhost:8080/api/v1", "description": "Core local (desenvolvimento)"},
    ],
    lifespan=lifespan,
)

register_exception_handlers(app)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(ride_router)

app.include_router(api_router)

# Criação e configuração do endpoint /metrics para o prometheus
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# Health check (público)
@app.get("/api/v1/health", tags=["health"])
async def health_check():
    """Verifica se o core está operacional."""
    return {
        "status": "ok",
        "version": "0.4.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }