"""Management-review reporting and CSV export.

All figures are scoped to the caller's organization. Endpoints return JSON by
default; pass ``?format=csv`` for a downloadable CSV of the same data. Month
bucketing and cycle-time math are done in Python to stay dialect-portable
(SQLite in tests, Postgres in production).
"""

import csv
import io
from collections import defaultdict
from datetime import date
from typing import Iterable

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import Capa, CapaStatus, Event, User
from app.models.event import EventStatus

router = APIRouter(prefix="/api/reports", tags=["Reports"])


def _csv_response(filename: str, fieldnames: list[str], rows: Iterable[dict]) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render(rows: list[dict], fmt: str, filename: str, fieldnames: list[str]):
    """Return rows as JSON or CSV depending on ``fmt``."""
    if fmt == "csv":
        return _csv_response(filename, fieldnames, rows)
    return rows


@router.get("/pareto-root-cause")
async def pareto_root_cause(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """CAPA counts by root-cause category, descending (Pareto ordering)."""
    rows = (
        db.query(Capa.root_cause_category, func.count(Capa.id))
        .filter(
            Capa.organization_id == current_user.organization_id,
            Capa.is_active.is_(True),
            Capa.root_cause_category.isnot(None),
        )
        .group_by(Capa.root_cause_category)
        .order_by(func.count(Capa.id).desc())
        .all()
    )
    data = [{"category": category, "count": count} for category, count in rows]
    return _render(data, format, "pareto_root_cause.csv", ["category", "count"])


@router.get("/events-by-month")
async def events_by_month(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Events opened vs. closed per calendar month."""
    events = (
        db.query(Event)
        .filter(Event.organization_id == current_user.organization_id, Event.is_active.is_(True))
        .all()
    )
    buckets: dict[str, dict] = defaultdict(lambda: {"opened": 0, "closed": 0})
    for event in events:
        buckets[event.created_at.strftime("%Y-%m")]["opened"] += 1
        if event.closed_at is not None:
            buckets[event.closed_at.strftime("%Y-%m")]["closed"] += 1
    data = [
        {"month": month, "opened": counts["opened"], "closed": counts["closed"]}
        for month, counts in sorted(buckets.items())
    ]
    return _render(data, format, "events_by_month.csv", ["month", "opened", "closed"])


@router.get("/capa-cycle-time")
async def capa_cycle_time(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Cycle time (open -> verified) for closed CAPAs."""
    capas = (
        db.query(Capa)
        .filter(
            Capa.organization_id == current_user.organization_id,
            Capa.is_active.is_(True),
            Capa.status == CapaStatus.CLOSED.value,
            Capa.verification_date.isnot(None),
        )
        .all()
    )
    durations = [(capa.verification_date - capa.created_at.date()).days for capa in capas]
    average = round(sum(durations) / len(durations), 1) if durations else 0.0
    data = [{
        "closed_capas": len(durations),
        "average_cycle_days": average,
        "min_cycle_days": min(durations) if durations else 0,
        "max_cycle_days": max(durations) if durations else 0,
    }]
    return _render(
        data, format, "capa_cycle_time.csv",
        ["closed_capas", "average_cycle_days", "min_cycle_days", "max_cycle_days"],
    )


@router.get("/overdue-by-owner")
async def overdue_by_owner(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Count of overdue (past target, not closed) events grouped by assignee."""
    rows = (
        db.query(Event.assigned_to, func.count(Event.id))
        .filter(
            Event.organization_id == current_user.organization_id,
            Event.is_active.is_(True),
            Event.target_close_date.isnot(None),
            Event.target_close_date < date.today(),
            Event.status != EventStatus.CLOSED.value,
        )
        .group_by(Event.assigned_to)
        .order_by(func.count(Event.id).desc())
        .all()
    )
    data = [{"owner_id": owner_id, "count": count} for owner_id, count in rows]
    return _render(data, format, "overdue_by_owner.csv", ["owner_id", "count"])


@router.get("/events.csv")
async def export_events_csv(
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Full CSV export of the organization's active events."""
    events = (
        db.query(Event)
        .filter(Event.organization_id == current_user.organization_id, Event.is_active.is_(True))
        .order_by(Event.created_at.desc())
        .all()
    )
    fieldnames = [
        "id", "title", "event_type", "status", "priority", "reported_by",
        "assigned_to", "site_id", "target_close_date", "is_overdue", "days_open",
        "product_part_number", "lot_batch", "supplier", "work_order", "machine",
        "closed_by", "closed_at", "created_at",
    ]
    rows = [
        {
            "id": e.id,
            "title": e.title,
            "event_type": e.event_type,
            "status": e.status,
            "priority": e.priority,
            "reported_by": e.reported_by,
            "assigned_to": e.assigned_to,
            "site_id": e.site_id,
            "target_close_date": e.target_close_date.isoformat() if e.target_close_date else "",
            "is_overdue": e.is_overdue,
            "days_open": e.days_open,
            "product_part_number": e.product_part_number or "",
            "lot_batch": e.lot_batch or "",
            "supplier": e.supplier or "",
            "work_order": e.work_order or "",
            "machine": e.machine or "",
            "closed_by": e.closed_by or "",
            "closed_at": e.closed_at.isoformat() if e.closed_at else "",
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]
    return _csv_response("events.csv", fieldnames, rows)
