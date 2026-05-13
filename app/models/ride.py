import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.models.base import Base


class RideStatus(str, enum.Enum):
    REQUESTED = "SOLICITADO"
    ACCEPTED = "ACEITO"
    IN_PROGRESS = "EM_PROGRESSO"
    COMPLETED = "COMPLETO"
    CANCELLED = "CANCELADO"


class Ride(Base):
    __tablename__ = "rides"

    rideId = Column(Integer, primary_key=True, autoincrement=True, index=True)
    ride_uuid = Column(
        String,
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    origin_group_id = Column(String,ForeignKey("groups.group_id"), nullable=False, index=True) #Referenciar Grupo. Foreign key. nao pode ser nulo
    recipient_group_id = Column(String, ForeignKey("groups.group_id"), nullable=True, index=True) #Referenciar Grupo. Foreign key. 
    assigned_at = Column(DateTime, nullable=True)
    passenger_uuid = Column(String, nullable=False, index=True)
    # Uma coluna de STATUS para a corrida. String, voce pode parametrixar com um enum
    status = Column(
        String, default=RideStatus.REQUESTED.value, nullable=False, index=True
    )

    # Campos de Origem
    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    origin_street = Column(String, nullable=False)
    origin_number = Column(String, nullable=False)
    origin_city = Column(String, nullable=False)
    origin_state = Column(String, nullable=False)
    dest_lat = Column(Float, nullable=False)
    dest_lng = Column(Float, nullable=False)
    dest_street = Column(String, nullable=False)
    dest_number = Column(String, nullable=False)
    dest_city = Column(String, nullable=False)
    dest_state = Column(String, nullable=False)

    logicalTimestamps = Column(Integer, default=0, nullable=False)
    auctionTimeoutSeconds = Column(Integer, default=10, nullable=False)