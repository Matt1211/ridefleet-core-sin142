from datetime import datetime

from pydantic import BaseModel


class GroupCredentials(BaseModel):
    groupId: str
    apiKey: str
    registeredAt: datetime


class GroupInfo(BaseModel):
    groupId: str
    groupName: str
    serviceUrl: str
    registeredAt: datetime
