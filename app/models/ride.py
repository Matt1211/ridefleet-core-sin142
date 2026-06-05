import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.models.base import Base


def _utcnow_naive() -> datetime:
    """Retorna datetime naive em UTC para colunas TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class RideStatus(str, enum.Enum):
    REQUEST = "request"
    MATCH = "match"
    CONFIRM = "confirm"
    IN_TRANSIT = "in_transit"
    COMPLETE = "complete"
    COMPENSATING = "compensating"
    CANCELLED = "cancelled"


class AuctionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    NO_PROPOSALS = "no_proposals"


class Ride(Base):
    __tablename__ = "rides"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    ride_uuid = Column(
        String,
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    registered_at = Column(DateTime, default=_utcnow_naive, nullable=False)

    origin_group_fk = Column(Integer, ForeignKey("groups.id"), nullable=False, index=True)
    recipient_group_fk = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)

    origin_group_id = Column(String, nullable=False, index=True)
    recipient_group_id = Column(String, nullable=True, index=True)
    assigned_at = Column(DateTime, nullable=True)
    passenger_uuid = Column(String, nullable=False, index=True)
    passenger_name = Column(String, nullable=True)
    status = Column(
        String, default=RideStatus.REQUEST.value, nullable=False, index=True
    )

    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    origin_street = Column(String, nullable=True)
    origin_number = Column(String, nullable=True)
    origin_city = Column(String, nullable=True)
    origin_state = Column(String, nullable=True)
    dest_lat = Column(Float, nullable=False)
    dest_lng = Column(Float, nullable=False)
    dest_street = Column(String, nullable=True)
    dest_number = Column(String, nullable=True)
    dest_city = Column(String, nullable=True)
    dest_state = Column(String, nullable=True)
    logicalTimestamp = Column(Integer, default=0, nullable=False)
    auctionTimeoutSeconds = Column(Integer, default=10, nullable=False)
    core_logical_ts = Column(Integer, default=0, nullable=False)
    last_client_ts = Column(Integer, default=0, nullable=False)
    auction_status = Column(String, default=AuctionStatus.OPEN.value, nullable=False)
    auction_closed_at = Column(DateTime, nullable=True)
    excluded_groups = Column(String, nullable=True)
    updated_at = Column(DateTime, default=_utcnow_naive,
                        onupdate=_utcnow_naive, nullable=False)
