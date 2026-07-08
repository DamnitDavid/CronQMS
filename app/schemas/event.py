"""Pydantic schemas for event-related requests and responses."""

from datetime import datetime
from pydantic import BaseModel


class EventCreate(BaseModel):
    """Schema for creating a new quality event."""

    title: str
    description: str | None = None
    event_type: str = "defect"


class EventResponse(BaseModel):
    """Schema for event response data."""

    id: int
    title: str
    description: str | None
    event_type: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True
