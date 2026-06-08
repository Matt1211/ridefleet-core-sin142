"""
Modelo de proposta de leilão submetida por um grupo parceiro.

Registra cada grupo convidado, sua resposta (ou ausência dela) e os
metadados necessários para reproduzir o critério de seleção do vencedor.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.models.base import Base


def _utcnow_naive() -> datetime:
    """Retorna datetime naive em UTC para colunas TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class RideProposal(Base):
    __tablename__ = "ride_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ride_fk = Column(Integer, ForeignKey("rides.id"), nullable=False, index=True)
    ride_uuid = Column(String, nullable=False, index=True)
    group_id = Column(String, nullable=False)
    service_url = Column(String, nullable=False)

    # Status: accepted | passed | timeout | error
    status = Column(String, nullable=False)

    # Preenchidos apenas quando status == "accepted"
    estimated_eta = Column(Integer, nullable=True)
    estimated_price = Column(Float, nullable=True)
    logical_timestamp = Column(Integer, nullable=True)

    # Observabilidade
    response_time_ms = Column(Integer, nullable=True)
    responded_at = Column(DateTime, nullable=True)

    # Indica se este grupo foi selecionado como vencedor (0 ou 1)
    is_winner = Column(Integer, default=0, nullable=False)

    created_at = Column(
        DateTime,
        default=_utcnow_naive,
        nullable=False,
    )
