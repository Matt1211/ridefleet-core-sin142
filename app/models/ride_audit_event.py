"""
Modelo de evento de auditoria da corrida (relógio lógico de Lamport).

Cada evento registra o serviceId responsável, o instante wall-clock
e o valor do relógio lógico do core no momento do evento, permitindo
reconstruir a relação happened-before entre serviços distintos.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.models.base import Base


class RideAuditEvent(Base):
    __tablename__ = "ride_audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ride_fk = Column(Integer, ForeignKey("rides.id"), nullable=False, index=True)
    ride_uuid = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    service_id = Column(String, nullable=False)
    logical_timestamp = Column(Integer, nullable=False)
    wall_clock_time = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    payload = Column(JSON, nullable=True)
