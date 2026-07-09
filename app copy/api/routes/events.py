"""Quality events endpoints."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event
from app.schemas.event import (
    EventCreate,
    EventResponse,
    EventStatus,
    EventStatusUpdate,
    EventType,
    EventPriority,
    EventUpdate,
)
from app.schemas.user import CurrentUser
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/events", tags=["Events"])


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_data: EventCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Create a new quality event."""
    new_event = Event(
        title=event_data.title,
        description=event_data.description,
        event_type=event_data.event_type.value,
        status=EventStatus.OPEN.value,
        priority=event_data.priority.value,
        assigned_to=event_data.assigned_to,
        facility=event_data.facility,
        user_id=current_user.id,
    )

    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    return new_event


@router.get("/", response_model=list[EventResponse])
async def list_events(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[EventStatus] = Query(None),
    priority: Optional[EventPriority] = Query(None),
    event_type: Optional[EventType] = Query(None),
    search: Optional[str] = Query(None, min_length=1),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
) -> list[Event]:
    """List events for the current user with optional filters and pagination."""
    query = db.query(Event).filter(Event.user_id == current_user.id, Event.is_active.is_(True))

    if status:
        query = query.filter(Event.status == status.value)
    if priority:
        query = query.filter(Event.priority == priority.value)
    if event_type:
        query = query.filter(Event.event_type == event_type.value)
    if search:
        query = query.filter(
            or_(
                Event.title.ilike(f"%{search}%"),
                Event.description.ilike(f"%{search}%"),
            )
        )
    if date_from:
        query = query.filter(Event.created_at >= date_from)
    if date_to:
        query = query.filter(Event.created_at <= date_to)

    events = (
        query.order_by(Event.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return events


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Get a specific event by ID."""
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this event",
        )

    return event


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    event_data: EventUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Update an existing event."""
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )

    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this event")

    update_data = event_data.model_dump(exclude_unset=True)
    if "event_type" in update_data:
        update_data["event_type"] = update_data["event_type"].value
    if "priority" in update_data:
        update_data["priority"] = update_data["priority"].value
    if "status" in update_data:
        update_data["status"] = update_data["status"].value

    for key, value in update_data.items():
        setattr(event, key, value)

    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.patch("/{event_id}/status", response_model=EventResponse)
async def patch_event_status(
    event_id: int,
    status_update: EventStatusUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Update event status with workflow validation."""
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )

    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this event")

    current_status = EventStatus(event.status)
    new_status = status_update.status
    allowed_transitions = {
        EventStatus.OPEN: {EventStatus.IN_PROGRESS, EventStatus.RESOLVED, EventStatus.CLOSED},
        EventStatus.IN_PROGRESS: {EventStatus.RESOLVED, EventStatus.CLOSED, EventStatus.OPEN},
        EventStatus.RESOLVED: {EventStatus.CLOSED, EventStatus.IN_PROGRESS},
        EventStatus.CLOSED: {EventStatus.OPEN},
    }

    if new_status not in allowed_transitions[current_status]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status transition from {current_status.value} to {new_status.value}",
        )

    event.status = new_status.value
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}", response_model=dict)
async def delete_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Soft delete an event by marking it inactive."""
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )

    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this event")

    event.is_active = False
    db.add(event)
    db.commit()
    return {"detail": "Event deleted successfully"}
