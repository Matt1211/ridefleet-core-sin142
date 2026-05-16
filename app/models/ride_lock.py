"""
Modelo de lock distribuído por corrida.

Cada corrida pode ter no máximo um lock ativo. O lock é criado quando
o core aceita a corrida (em nome do originServiceId) e transferido ao
grupo vencedor após o leilão. O TTL é monitorado por um background task.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.models.base import Base


class RideLock(Base):
    __tablename__ = "ride_locks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ride_fk = Column(Integer, ForeignKey("rides.id"), nullable=False, unique=True, index=True)
    ride_uuid = Column(String, nullable=False, unique=True, index=True)
    held_by = Column(String, nullable=False)          # group_id do detentor
    expires_at = Column(DateTime, nullable=False)     # prazo de expiração (UTC)
    acquired_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
