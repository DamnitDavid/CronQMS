"""Unified cross-module audit-log feed (external-auditing view).

The append-only ``event_history`` trail already records field-level mutations for
every auditable entity (events, CAPAs, documents, audits, findings, change
requests, training, …). This page surfaces them in one org-scoped, filterable
place so an external auditor can review "all events" across modules.

Org scoping is by the *actor's* organization: users only ever mutate their own
organization's data, so joining ``event_history.actor_id -> users.id`` and
filtering on the actor's ``organization_id`` yields exactly this org's activity
without adding a column to the append-only table. Rows with no actor (system
mutations) are intentionally excluded.
"""

import os
from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import EventHistory, User

router = APIRouter(tags=["Admin"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

# Friendly labels for the raw ``__audit_entity__`` values written to the trail.
ENTITY_TYPE_LABELS: dict[str, str] = {
    "event": "Event",
    "capa": "CAPA",
    "document": "Document",
    "document_version": "Document Version",
    "audit": "Audit",
    "audit_finding": "Audit Finding",
    "alert": "Alert",
    "employee": "Employee",
    "training_course": "Training Course",
    "training_record": "Training Record",
    "change_request": "Change Request",
    "change_action": "Change Action",
}

# Entity types that have a browsable detail page, for optional row links.
ENTITY_LINK_PREFIXES: dict[str, str] = {
    "event": "/admin/events/",
    "document": "/admin/documents/",
    "audit": "/admin/audits/",
    "change_request": "/admin/changes/",
}


def _entity_label(entity_type: str) -> str:
    return ENTITY_TYPE_LABELS.get(entity_type, entity_type)


def _entity_link(entity_type: str, entity_id: int) -> Optional[str]:
    prefix = ENTITY_LINK_PREFIXES.get(entity_type)
    return f"{prefix}{entity_id}" if prefix else None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/admin/audit-log")
async def audit_log_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.AUDIT_LOG_VIEW)),
    db: Session = Depends(get_db),
    entity_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Render the org-scoped audit-log feed (full page, or table partial for htmx)."""
    org_id = current_user.organization_id

    query = (
        db.query(EventHistory)
        .join(User, EventHistory.actor_id == User.id)
        .filter(User.organization_id == org_id)
    )

    if entity_type in ENTITY_TYPE_LABELS:
        query = query.filter(EventHistory.entity_type == entity_type)

    actor_id_int = int(actor_id) if actor_id and actor_id.isdigit() else None
    if actor_id_int is not None:
        query = query.filter(EventHistory.actor_id == actor_id_int)

    df = _parse_date(date_from)
    if df is not None:
        query = query.filter(EventHistory.created_at >= datetime.combine(df, time.min))
    dt = _parse_date(date_to)
    if dt is not None:
        query = query.filter(EventHistory.created_at <= datetime.combine(dt, time.max))

    entries = query.order_by(EventHistory.created_at.desc()).limit(500).all()

    # Distinct entity types actually present for this org (for the filter dropdown).
    present_types = [
        row[0]
        for row in (
            db.query(EventHistory.entity_type)
            .join(User, EventHistory.actor_id == User.id)
            .filter(User.organization_id == org_id)
            .distinct()
            .order_by(EventHistory.entity_type.asc())
            .all()
        )
    ]

    actors = (
        db.query(User)
        .filter(User.organization_id == org_id)
        .order_by(User.email.asc())
        .all()
    )
    actor_emails = {u.id: u.email for u in actors}

    context = {
        "request": request,
        "current_user": current_user,
        "entries": entries,
        "actors": actors,
        "actor_emails": actor_emails,
        "entity_types": present_types,
        "entity_label": _entity_label,
        "entity_link": _entity_link,
        "filters": {
            "entity_type": entity_type or "",
            "actor_id": actor_id or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    }
    template = (
        "admin/audit_log/_audit_log_table.html"
        if "HX-Request" in request.headers
        else "admin/audit_log/list.html"
    )
    return templates.TemplateResponse(template, context)
