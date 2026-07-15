"""Pydantic schemas for event-related requests and responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Enums are defined once on the model layer and re-exported here so schema
# consumers keep importing them from ``app.schemas.event`` unchanged.
from app.models.event import EventPriority, EventStatus, EventType

__all__ = [
    "EventStatus",
    "EventPriority",
    "EventType",
    "EventCreate",
    "EventUpdate",
    "EventStatusUpdate",
    "EventResponse",
]


class EventCreate(BaseModel):
    """Schema for creating a new quality event."""

    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    event_type: EventType = EventType.NON_CONFORMANCE
    priority: EventPriority = EventPriority.MEDIUM
    assigned_to: Optional[int] = None
    facility: Optional[str] = Field(default=None, max_length=255)


class EventUpdate(BaseModel):
    """Partial update schema for quality events."""

    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    event_type: Optional[EventType] = None
    status: Optional[EventStatus] = None
    priority: Optional[EventPriority] = None
    assigned_to: Optional[int] = None
    facility: Optional[str] = Field(default=None, max_length=255)

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Title must not be empty")
        return value


class EventStatusUpdate(BaseModel):
    """Schema for updating event status."""

    status: EventStatus


class EventResponse(BaseModel):
    """Schema for event response data."""

    id: int
    title: str
    description: Optional[str]
    event_type: EventType
    status: EventStatus
    priority: EventPriority
    assigned_to: Optional[int]
    facility: Optional[str]
    user_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True
