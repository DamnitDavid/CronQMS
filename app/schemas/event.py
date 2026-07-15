"""Pydantic schemas for event-related requests and responses."""

from datetime import date, datetime
from typing import List, Optional

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


class TraceabilityFields(BaseModel):
    """Traceability attributes shared by create/update schemas."""

    product_part_number: Optional[str] = Field(default=None, max_length=100)
    lot_batch: Optional[str] = Field(default=None, max_length=100)
    supplier: Optional[str] = Field(default=None, max_length=255)
    work_order: Optional[str] = Field(default=None, max_length=100)
    machine: Optional[str] = Field(default=None, max_length=100)


class EventCreate(TraceabilityFields):
    """Schema for creating a new quality event."""

    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    event_type: EventType = EventType.NON_CONFORMANCE
    priority: EventPriority = EventPriority.MEDIUM
    assigned_to: Optional[int] = None
    site_id: Optional[int] = None
    # If omitted, derived from priority SLA at creation time.
    target_close_date: Optional[date] = None


class EventUpdate(TraceabilityFields):
    """Partial update schema for quality events."""

    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    event_type: Optional[EventType] = None
    status: Optional[EventStatus] = None
    priority: Optional[EventPriority] = None
    assigned_to: Optional[int] = None
    site_id: Optional[int] = None
    target_close_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Title must not be empty")
        return value


class EventStatusUpdate(BaseModel):
    """Schema for updating event status (non-terminal transitions only)."""

    status: EventStatus


class EventReopen(BaseModel):
    """Schema for reopening a closed event; a reason is mandatory."""

    reason: str = Field(..., min_length=1, max_length=2000)


class EventResponse(BaseModel):
    """Schema for event response data."""

    id: int
    title: str
    description: Optional[str]
    event_type: EventType
    status: EventStatus
    priority: EventPriority
    assigned_to: Optional[int]
    organization_id: int
    site_id: Optional[int]
    reported_by: int
    target_close_date: Optional[date]
    product_part_number: Optional[str]
    lot_batch: Optional[str]
    supplier: Optional[str]
    work_order: Optional[str]
    machine: Optional[str]
    is_overdue: bool
    days_open: int
    closed_by: Optional[int]
    closed_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True


class PaginatedEvents(BaseModel):
    """A page of events plus the total matching count, for UI pagination."""

    total: int
    page: int
    page_size: int
    items: List[EventResponse]
