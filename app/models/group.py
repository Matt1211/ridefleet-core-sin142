import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class Group(Base):
    __tablename__ = "groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, nullable=False, unique=True, index=True)
    group_name = Column(String, nullable=False)
    service_url = Column(String, nullable=False)
    contact_email = Column(String, nullable=True)
    api_key = Column(String, nullable=False, unique=True, index=True)
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
