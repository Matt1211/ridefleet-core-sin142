"""
Configuração global dos testes.

Usamos SQLite em memória para não depender de um PostgreSQL rodando.
A cada teste, o banco começa limpo graças ao fixture de sessão.
"""

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

# Renomeamos para evitar conflito com o pacote `app` que é importado logo abaixo
from app.main import app as fastapi_app
from app.database import get_db
from app.models.base import Base
import app.models.group  # noqa: F401 — garante que o model está registrado no Base

engine_teste = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

fabrica_sessao_teste = async_sessionmaker(engine_teste, expire_on_commit=False)


# Fixtures
@pytest_asyncio.fixture(scope="session", autouse=True)
async def criar_tabelas():
    """Cria todas as tabelas uma vez antes da suíte rodar."""
    async with engine_teste.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_teste.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine_teste.dispose()  # fecha o pool e libera o event loop


@pytest_asyncio.fixture(autouse=True)
async def limpar_banco():
    """Limpa os dados entre cada teste para garantir isolamento."""
    yield
    async with engine_teste.begin() as conn:
        for tabela in reversed(Base.metadata.sorted_tables):
            await conn.execute(tabela.delete())


@pytest_asyncio.fixture
async def db_teste() -> AsyncSession:
    """Sessão assíncrona apontando para o banco de teste."""
    async with fabrica_sessao_teste() as sessao:
        yield sessao


@pytest_asyncio.fixture
async def cliente(db_teste: AsyncSession) -> AsyncClient:
    """
    Cliente HTTP que fala diretamente com a app FastAPI,
    usando o banco de teste no lugar do banco real.
    """
    def substituir_db():
        yield db_teste

    fastapi_app.dependency_overrides[get_db] = substituir_db

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://test",
    ) as c:
        yield c

    fastapi_app.dependency_overrides.clear()
