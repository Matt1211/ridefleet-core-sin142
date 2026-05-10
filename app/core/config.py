from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://ridefleet:secret@db:5432/ridefleet_core"
    CORE_ENV: str = "development"

    model_config = {"env_file": ".env"}


settings = Settings()
