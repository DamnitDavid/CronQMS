"""Admin stats endpoints for CronQMS.

The dashboard page was removed; these JSON endpoints remain as the machine
interface for organization-scoped event statistics and recent activity.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from datetime import date

from app.database import get_db
from app.models import Event, User
from app.core.permissions import Permission, require_permission

router = APIRouter(tags=["Admin"])


def _dashboard_stats(db: Session, organization_id: int | None) -> dict:
    """Compute event counters scoped to a single organization."""
    org_filter = Event.organization_id == organization_id
    return {
        "total_events": db.query(func.count(Event.id)).filter(org_filter, Event.is_active.is_(True)).scalar() or 0,
        "open_events": db.query(func.count(Event.id)).filter(org_filter, Event.status == "Open", Event.is_active.is_(True)).scalar() or 0,
        "resolved_today": db.query(func.count(Event.id)).filter(
            org_filter,
            Event.status == "Resolved",
            func.date(Event.updated_at) == func.current_date(),
            Event.is_active.is_(True),
        ).scalar() or 0,
        # Events still open past their target close date — the meaningful
        # "needs attention now" figure.
        "overdue_events": db.query(func.count(Event.id)).filter(
            org_filter,
            Event.target_close_date.isnot(None),
            Event.target_close_date < date.today(),
            Event.status != "Closed",
            Event.is_active.is_(True),
        ).scalar() or 0,
        "pending_actions": db.query(func.count(Event.id)).filter(org_filter, Event.status.in_(["Open", "In_Progress"]), Event.is_active.is_(True)).scalar() or 0,
    }


@router.get("/api/stats")
async def api_stats(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    """Return dashboard statistics for the caller's organization."""
    return _dashboard_stats(db, current_user.organization_id)


@router.get("/api/recent-activity")
async def api_recent_activity(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return recent activity as JSON (API clients)."""
    events = (
        db.query(Event)
        .filter(Event.organization_id == current_user.organization_id, Event.is_active.is_(True))
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
