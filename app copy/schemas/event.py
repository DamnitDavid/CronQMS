"""Pydantic schemas for event-related requests and responses."""

from datetime import datetime, date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, validator


class EventStatus(str, Enum):
    OPEN = "Open"
    IN_PROGRESS = "In_Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class EventPriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class EventType(str, Enum):
    NON_CONFORMANCE = "Non_Conformance"
    CAPA = "CAPA"
    AUDIT_FINDING = "Audit_Finding"
    OTHER = "Other"


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

    @validator("title")
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
