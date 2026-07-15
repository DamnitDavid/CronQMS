"""Quality events endpoints."""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, User
from app.schemas.event import (
    EventCreate,
    EventReopen,
    EventResponse,
    EventStatus,
    EventStatusUpdate,
    EventType,
    EventPriority,
    EventUpdate,
)
from app.core.audit import set_audit_reason
from app.core.permissions import Permission, require_permission
from app.core.sla import sla_target

router = APIRouter(prefix="/api/events", tags=["Events"])


def _require_organization(current_user: User) -> int:
    """Return the user's organization id, or 400 if they aren't scoped to one."""
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_event_in_org(db: Session, event_id: int, current_user: User) -> Event:
    """Fetch an active event that belongs to the caller's organization.

    Raises 404 both when the event does not exist and when it belongs to another
    organization, so cross-org existence is never leaked.
    """
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )
    if not event or event.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    return event


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_data: EventCreate,
    current_user: User = Depends(require_permission(Permission.EVENT_CREATE)),
    db: Session = Depends(get_db),
) -> Event:
    """Create a new quality event in the caller's organization."""
    organization_id = _require_organization(current_user)
    # Derive the target close date from priority SLA unless one is supplied.
    target_close_date = event_data.target_close_date or sla_target(
        event_data.priority.value, date.today()
    )
    new_event = Event(
        title=event_data.title,
        description=event_data.description,
        event_type=event_data.event_type.value,
        status=EventStatus.OPEN.value,
        priority=event_data.priority.value,
        assigned_to=event_data.assigned_to,
        site_id=event_data.site_id,
        organization_id=organization_id,
        reported_by=current_user.id,
        target_close_date=target_close_date,
        product_part_number=event_data.product_part_number,
        lot_batch=event_data.lot_batch,
        supplier=event_data.supplier,
        work_order=event_data.work_order,
        machine=event_data.machine,
    )

    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    return new_event


@router.get("/", response_model=list[EventResponse])
async def list_events(
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
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
    """List every active event in the caller's organization, with filters."""
    query = db.query(Event).filter(
        Event.organization_id == current_user.organization_id,
        Event.is_active.is_(True),
    )

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
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
) -> Event:
    """Get a specific event by ID within the caller's organization."""
    return _get_event_in_org(db, event_id, current_user)


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    event_data: EventUpdate,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
) -> Event:
    """Update an existing event within the caller's organization."""
    event = _get_event_in_org(db, event_id, current_user)

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


# Non-terminal transitions available via the ordinary status endpoint. Closure
# (-> Closed) and reopening (Closed -> Open) are deliberately excluded here and
# handled by their own privileged endpoints below.
_ALLOWED_TRANSITIONS = {
    EventStatus.OPEN: {EventStatus.IN_PROGRESS},
    EventStatus.IN_PROGRESS: {EventStatus.RESOLVED, EventStatus.OPEN},
    EventStatus.RESOLVED: {EventStatus.IN_PROGRESS},
    EventStatus.CLOSED: set(),
}


@router.patch("/{event_id}/status", response_model=EventResponse)
async def patch_event_status(
    event_id: int,
    status_update: EventStatusUpdate,
    current_user: User = Depends(require_permission(Permission.EVENT_CHANGE_STATUS)),
    db: Session = Depends(get_db),
) -> Event:
    """Advance an event through the investigation workflow.

    Only non-terminal transitions are permitted here; closing and reopening are
    privileged actions with their own endpoints. Notably an event cannot jump
    straight from Open to Closed — it must pass through investigation.
    """
    event = _get_event_in_org(db, event_id, current_user)

    current_status = EventStatus(event.status)
    new_status = status_update.status

    if new_status not in _ALLOWED_TRANSITIONS[current_status]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid status transition from {current_status.value} to "
                f"{new_status.value}. Use /close or /reopen for closure."
            ),
        )

    event.status = new_status.value
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/close", response_model=EventResponse)
async def close_event(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_APPROVE_CLOSURE)),
    db: Session = Depends(get_db),
) -> Event:
    """Approve and close a resolved event.

    Closure requires an independent approver: the closer may be neither the
    reporter nor the assigned investigator. The event must already be Resolved.
    """
    event = _get_event_in_org(db, event_id, current_user)

    if event.status != EventStatus.RESOLVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a Resolved event can be closed",
        )
    if current_user.id in (event.reported_by, event.assigned_to):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Closure must be approved by someone other than the reporter or investigator",
        )

    event.status = EventStatus.CLOSED.value
    event.closed_by = current_user.id
    event.closed_at = datetime.utcnow()
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/reopen", response_model=EventResponse)
async def reopen_event(
    event_id: int,
    reopen: EventReopen,
    current_user: User = Depends(require_permission(Permission.EVENT_REOPEN)),
    db: Session = Depends(get_db),
) -> Event:
    """Reopen a closed event. Privileged, and audit-logged with a reason."""
    event = _get_event_in_org(db, event_id, current_user)

    if event.status != EventStatus.CLOSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a Closed event can be reopened",
        )

    set_audit_reason(db, reopen.reason)
    event.status = EventStatus.OPEN.value
    event.closed_by = None
    event.closed_at = None
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}", response_model=dict)
async def delete_event(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_DELETE)),
    db: Session = Depends(get_db),
) -> dict:
    """Soft delete an event by marking it inactive."""
    event = _get_event_in_org(db, event_id, current_user)

    event.is_active = False
    db.add(event)
    db.commit()
    return {"detail": "Event deleted successfully"}
