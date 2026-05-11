from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator
import re


class GroupRegistrationDTO(BaseModel):
    groupId: str = Field(..., min_length=1, pattern=r"^[a-z0-9-]+$", examples=["group-a"])
    groupName: str = Field(..., min_length=1, examples=["Grupo A - Sistemas Distribuídos"])
    serviceUrl: str = Field(..., min_length=1, examples=["http://group-a:8081"])
    contactEmail: Optional[EmailStr] = Field(None, examples=["grupo-a@ufv.br"])


# Mantém o alias original para não quebrar código existente
AuthRequestDTO = GroupRegistrationDTO
