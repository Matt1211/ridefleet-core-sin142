from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables() -> None:
    from app.models.base import Base
    import app.models.group             # noqa: F401 — registra o modelo no metadata
    import app.models.ride              # noqa: F401 — registra o modelo no metadata
    import app.models.ride_lock         # noqa: F401
    import app.models.ride_proposal     # noqa: F401
    import app.models.ride_audit_event  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
