"""Quality events endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event
from app.schemas.event import EventCreate, EventResponse
from app.schemas.user import CurrentUser
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/events", tags=["Events"])


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_data: EventCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Create a new quality event.

    Args:
        event_data: Event creation data.
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        Event: Created event object.
    """
    new_event = Event(
        title=event_data.title,
        description=event_data.description,
        event_type=event_data.event_type,
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
) -> list[Event]:
    """List all events for the current user.

    Args:
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        list[Event]: List of events.
    """
    events = db.query(Event).filter(Event.user_id == current_user.id).all()
    return events


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Event:
    """Get a specific event by ID.

    Args:
        event_id: Event ID.
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        Event: Event object.

    Raises:
        HTTPException: If event not found or unauthorized.
    """
    event = db.query(Event).filter(Event.id == event_id).first()

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
