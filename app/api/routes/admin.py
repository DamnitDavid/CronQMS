"""Admin and dashboard routes for Proins."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, User
from app.core.auth import get_current_admin_user, get_current_user
from app.schemas.event import EventResponse

router = APIRouter(tags=["Admin"])

import os

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))


@router.get("/admin/dashboard")
async def admin_dashboard(
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Render the admin dashboard."""
    stats = {
        "total_events": db.query(func.count(Event.id)).filter(Event.is_active.is_(True)).scalar() or 0,
        "open_events": db.query(func.count(Event.id)).filter(Event.status == "Open", Event.is_active.is_(True)).scalar() or 0,
        "resolved_today": db.query(func.count(Event.id)).filter(
            Event.status == "Resolved",
            func.date(Event.updated_at) == func.current_date(),
            Event.is_active.is_(True),
        ).scalar() or 0,
        "pending_actions": db.query(func.count(Event.id)).filter(Event.status.in_(["Open", "In_Progress"]), Event.is_active.is_(True)).scalar() or 0,
    }

    recent_events = (
        db.query(Event)
        .filter(Event.is_active.is_(True))
        .order_by(Event.updated_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "stats": stats,
            "recent_events": recent_events,
        },
    )


@router.get("/api/stats")
async def api_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return dashboard statistics."""
    return {
        "total_events": db.query(func.count(Event.id)).filter(Event.is_active.is_(True)).scalar() or 0,
        "open_events": db.query(func.count(Event.id)).filter(Event.status == "Open", Event.is_active.is_(True)).scalar() or 0,
        "resolved_today": db.query(func.count(Event.id)).filter(
            Event.status == "Resolved",
            func.date(Event.updated_at) == func.current_date(),
            Event.is_active.is_(True),
        ).scalar() or 0,
        "pending_actions": db.query(func.count(Event.id)).filter(Event.status.in_(["Open", "In_Progress"]), Event.is_active.is_(True)).scalar() or 0,
    }


@router.get("/api/recent-activity")
async def api_recent_activity(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return recent activity event fragments."""
    events = (
        db.query(Event)
        .filter(Event.is_active.is_(True))
        .order_by(Event.updated_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": event.id,
            "title": event.title,
            "status": event.status,
            "priority": event.priority,
            "updated_at": event.updated_at.isoformat(),
        }
        for event in events
    ]
