from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI

from app.controllers.auth_controller import router as auth_router
from app.database import create_tables
from app.exceptions import register_exception_handlers
from app.rabbitmq import rabbitmq_broker

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    await rabbitmq_broker.connect()
    yield
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

# Exception handlers
register_exception_handlers(app)

# Routers — todos agrupados sob /api/v1

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)

app.include_router(api_router)


# Health check (público) - A MELHORAR
@app.get("/api/v1/health", tags=["health"])
async def health_check():
    """Verifica se o core está operacional."""
    return {
        "status": "ok",
        "version": "0.4.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
