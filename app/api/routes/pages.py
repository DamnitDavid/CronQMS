"""Server-rendered admin pages and their form/action handlers.

The JSON API under /api is the machine interface; these routes are the browser
UI. Mutations here are ordinary form posts that redirect back to the page
(Post/Redirect/Get), and they reuse the same workflow service and permission
dependencies as the API so behavior can't drift between the two.
"""

import hashlib
import os
import uuid
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.routes.setup import admin_exists
from app.config import get_settings
from app.core.auth import get_current_user_optional
from app.core.permissions import Permission, require_permission
from app.core.storage import get_storage
from app.database import get_db
from app.models import Attachment, Comment, Event, EventCustomValue, Site, User
from app.models import EventHistory
from app.models.custom_field import CustomFieldType
from app.models.event import EventStatus, EventType, event_type_label
from app.services.custom_fields import fields_for, save_values, values_for
from app.services.event_workflow import (
    WorkflowError,
    apply_status_transition,
    approve_closure,
    reopen as reopen_workflow,
)

# Valid event-type strings, used to validate the browser form (the JSON API
# validates via Pydantic; the form path did not until now).
EVENT_TYPE_VALUES = {t.value for t in EventType}

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))
# Expose the event-type display label to templates so they can render friendly
# names (e.g. "Defects") instead of the raw stored value.
templates.env.globals["event_type_label"] = event_type_label


# --- helpers ---------------------------------------------------------------
def _event_or_404(db: Session, event_id: int, current_user: User) -> Event:
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )
    if not event or event.organization_id != current_user.organization_id:
        # Render a simple 404 rather than leaking cross-org existence.
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def _permission_flags(user: User) -> dict:
    """Which action buttons the current user may see.

    Reads the DB-resolved ``granted_permissions`` set attached to the user by
    ``get_current_user`` so custom roles are honored (a plain ``Role(...)``
    coercion would raise on non-enum role names).
    """
    granted = getattr(user, "granted_permissions", set())
    checks = {
        "can_edit": Permission.EVENT_UPDATE,
        "can_change_status": Permission.EVENT_CHANGE_STATUS,
        "can_close": Permission.EVENT_APPROVE_CLOSURE,
        "can_reopen": Permission.EVENT_REOPEN,
        "can_comment": Permission.EVENT_COMMENT,
        "can_create_alert": Permission.ALERT_CREATE,
    }
    return {name: perm.value in granted for name, perm in checks.items()}


def _org_user_emails(db: Session, organization_id: int) -> dict[int, str]:
    users = db.query(User).filter(User.organization_id == organization_id).all()
    return {u.id: u.email for u in users}


def _org_groups(db: Session, organization_id: int):
    """Active assignee groups for an organization (for the assignee dropdown)."""
    from app.models import AssigneeGroup

    return (
        db.query(AssigneeGroup)
        .filter(
            AssigneeGroup.organization_id == organization_id,
            AssigneeGroup.is_active.is_(True),
        )
        .order_by(AssigneeGroup.name.asc())
        .all()
    )


def _parse_assignee(value: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Parse a combined assignee value into (assigned_to, assigned_group_id).

    The form encodes a single choice as ``user:<id>`` or ``group:<id>``; an empty
    value means unassigned. At most one of the two ids is set.
    """
    if not value:
        return None, None
    kind, _, raw = value.partition(":")
    if kind == "user" and raw.isdigit():
        return int(raw), None
    if kind == "group" and raw.isdigit():
        return None, int(raw)
    return None, None


def _to_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _to_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _redirect(event_id: int, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/events/{event_id}"
    if error:
        from urllib.parse import quote

        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --- auth pages ------------------------------------------------------------
@router.get("/login")
async def login_page(
    request: Request,
    current_user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if current_user:
        return RedirectResponse("/admin/events", status_code=status.HTTP_303_SEE_OTHER)
    # Before any admin exists, the login page is a dead end — route first-run
    # visitors to the setup wizard instead.
    if not admin_exists(db):
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    # Mirrors the API gate: when public registration is disabled the sign-up
    # form is not reachable — send visitors to the login page instead.
    if not get_settings().allow_public_registration:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("auth/register.html", {"request": request})


# --- custom fields fragment (htmx) -----------------------------------------
@router.get("/admin/events/custom-fields")
async def event_custom_fields_fragment(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
    event_type: str = EventType.DEFECT.value,
    event_id: Optional[str] = None,
):
    """Render the custom-field inputs for an event type (htmx swap target).

    When ``event_id`` is given (edit flow) the inputs are prefilled from the
    event's saved values, scoped to the caller's organization.
    """
    if event_type not in EVENT_TYPE_VALUES:
        event_type = EventType.DEFECT.value
    fields = fields_for(db, current_user.organization_id, event_type)
    values: dict[int, str] = {}
    eid = _to_int(event_id)
    if eid is not None:
        event = (
            db.query(Event)
            .filter(
                Event.id == eid,
                Event.organization_id == current_user.organization_id,
            )
            .first()
        )
        if event is not None:
            values = values_for(db, event.id)
    return templates.TemplateResponse(
        "admin/events/_custom_fields.html",
        {"request": request, "custom_fields": fields, "custom_values": values},
    )


# --- event list & create ---------------------------------------------------
def _assignee_labels(db: Session, events: list[Event], organization_id: int) -> dict[int, str]:
    """Map each event id to a display label for its assignee (user or group)."""
    from app.models import AssigneeGroup

    emails = _org_user_emails(db, organization_id)
    group_names = {
        g.id: g.name
        for g in db.query(AssigneeGroup).filter(
            AssigneeGroup.organization_id == organization_id
        )
    }
    labels: dict[int, str] = {}
    for event in events:
        if event.assigned_group_id:
            labels[event.id] = group_names.get(event.assigned_group_id, "Group")
        elif event.assigned_to:
            labels[event.id] = emails.get(event.assigned_to, str(event.assigned_to))
        else:
            labels[event.id] = "Unassigned"
    return labels


def _apply_custom_field_filters(db: Session, query, organization_id: int, event_type: str, params):
    """Constrain ``query`` by any ``cf_<id>`` filters present in ``params``."""
    if not event_type or event_type not in EVENT_TYPE_VALUES:
        return query
    for field in fields_for(db, organization_id, event_type):
        raw = (params.get(f"cf_{field.id}") or "").strip()
        if not raw:
            continue
        value_col = EventCustomValue.value
        if field.field_type in (CustomFieldType.TEXT.value, CustomFieldType.NUMBER.value):
            condition = value_col.ilike(f"%{raw}%")
        else:
            condition = value_col == raw
        subquery = (
            db.query(EventCustomValue.event_id)
            .filter(EventCustomValue.custom_field_id == field.id, condition)
        )
        query = query.filter(Event.id.in_(subquery))
    return query


@router.get("/admin/events")
async def admin_events_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    event_type: Optional[str] = None,
):
    query = db.query(Event).filter(
        Event.organization_id == current_user.organization_id,
        Event.is_active.is_(True),
    )
    if status_filter:
        query = query.filter(Event.status == status_filter)
    if priority:
        query = query.filter(Event.priority == priority)
    if event_type:
        query = query.filter(Event.event_type == event_type)
    if search:
        query = query.filter(
            or_(Event.title.ilike(f"%{search}%"), Event.description.ilike(f"%{search}%"))
        )
    query = _apply_custom_field_filters(
        db, query, current_user.organization_id, event_type, request.query_params
    )
    events = query.order_by(Event.updated_at.desc()).limit(50).all()
    context = {
        "request": request,
        "current_user": current_user,
        "events": events,
        "assignee_labels": _assignee_labels(db, events, current_user.organization_id),
        "can_create": _permission_flags_create(current_user),
        "event_types": sorted(EVENT_TYPE_VALUES),
        "filters": {
            "search": search or "",
            "status": status_filter or "",
            "priority": priority or "",
            "event_type": event_type or "",
        },
        "filter_fields": (
            fields_for(db, current_user.organization_id, event_type)
            if event_type in EVENT_TYPE_VALUES else []
        ),
        "filter_values": request.query_params,
    }
    # htmx swaps only the table; a direct navigation gets the whole page.
    template = "admin/events/_event_table.html" if "HX-Request" in request.headers else "admin/events/list.html"
    return templates.TemplateResponse(template, context)


@router.get("/admin/events/filter-fields")
async def event_filter_fields_fragment(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
    event_type: Optional[str] = None,
):
    """Custom-field filter controls for an event type (htmx swap target)."""
    fields = (
        fields_for(db, current_user.organization_id, event_type)
        if event_type in EVENT_TYPE_VALUES else []
    )
    return templates.TemplateResponse(
        "admin/events/_filter_fields.html",
        {"request": request, "filter_fields": fields, "filter_values": request.query_params},
    )


def _permission_flags_create(user: User) -> bool:
    return Permission.EVENT_CREATE.value in getattr(user, "granted_permissions", set())


@router.get("/admin/events/create")
async def admin_events_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    sites = db.query(Site).filter(Site.organization_id == current_user.organization_id).all()
    users = db.query(User).filter(User.organization_id == current_user.organization_id).all()
    default_type = EventType.DEFECT.value
    return templates.TemplateResponse(
        "admin/events/create.html",
        {
            "request": request,
            "current_user": current_user,
            "sites": sites,
            "users": users,
            "groups": _org_groups(db, current_user.organization_id),
            "event_types": sorted(EVENT_TYPE_VALUES),
            "selected_type": default_type,
            "custom_fields": fields_for(db, current_user.organization_id, default_type),
            "custom_values": {},
            "error": error,
        },
    )


def _create_redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(
        f"/admin/events/create?error={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/events/create")
async def admin_events_create_submit(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_CREATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    event_type: str = Form("Non_Conformance"),
    priority: str = Form("Medium"),
    assignee: Optional[str] = Form(None),
    site_id: Optional[str] = Form(None),
    target_close_date: Optional[str] = Form(None),
    product_part_number: str = Form(""),
    lot_batch: str = Form(""),
    supplier: str = Form(""),
    work_order: str = Form(""),
    machine: str = Form(""),
):
    if event_type not in EVENT_TYPE_VALUES:
        event_type = EventType.DEFECT.value
    assigned_to, assigned_group_id = _parse_assignee(assignee)
    event = Event(
        title=title,
        description=description or None,
        event_type=event_type,
        status=EventStatus.OPEN.value,
        priority=priority,
        assigned_to=assigned_to,
        assigned_group_id=assigned_group_id,
        site_id=_to_int(site_id),
        organization_id=current_user.organization_id,
        reported_by=current_user.id,
        target_close_date=_to_date(target_close_date) or (date.today() + timedelta(days=30)),
        product_part_number=product_part_number or None,
        lot_batch=lot_batch or None,
        supplier=supplier or None,
        work_order=work_order or None,
        machine=machine or None,
    )
    db.add(event)
    db.flush()  # assign event.id before saving custom values
    fields = fields_for(db, current_user.organization_id, event_type)
    error = save_values(db, event, fields, await request.form())
    if error:
        db.rollback()
        return _create_redirect_error(error)
    db.commit()
    db.refresh(event)
    return _redirect(event.id)


# --- event detail ----------------------------------------------------------
@router.get("/admin/events/{event_id}")
async def event_detail_page(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    event = _event_or_404(db, event_id, current_user)
    comments = (
        db.query(Comment).filter(Comment.event_id == event.id).order_by(Comment.created_at.asc()).all()
    )
    attachments = (
        db.query(Attachment).filter(Attachment.event_id == event.id).order_by(Attachment.created_at.desc()).all()
    )
    history = (
        db.query(EventHistory)
        .filter(EventHistory.entity_type == "event", EventHistory.entity_id == event.id)
        .order_by(EventHistory.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/events/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "event": event,
            "comments": comments,
            "attachments": attachments,
            "history": history,
            "user_emails": _org_user_emails(db, current_user.organization_id),
            "perms": _permission_flags(current_user),
            "custom_fields": fields_for(db, current_user.organization_id, event.event_type),
            "custom_values": values_for(db, event.id),
            "error": error,
        },
    )


# --- event edit ------------------------------------------------------------
@router.get("/admin/events/{event_id}/edit")
async def event_edit_page(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
):
    event = _event_or_404(db, event_id, current_user)
    sites = db.query(Site).filter(Site.organization_id == current_user.organization_id).all()
    users = db.query(User).filter(User.organization_id == current_user.organization_id).all()
    return templates.TemplateResponse(
        "admin/events/edit.html",
        {
            "request": request,
            "current_user": current_user,
            "event": event,
            "sites": sites,
            "users": users,
            "groups": _org_groups(db, current_user.organization_id),
            "event_types": sorted(EVENT_TYPE_VALUES),
            "custom_fields": fields_for(db, current_user.organization_id, event.event_type),
            "custom_values": values_for(db, event.id),
        },
    )


@router.post("/admin/events/{event_id}/edit")
async def event_edit_submit(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    event_type: str = Form(...),
    priority: str = Form(...),
    assignee: Optional[str] = Form(None),
    site_id: Optional[str] = Form(None),
    target_close_date: Optional[str] = Form(None),
    product_part_number: str = Form(""),
    lot_batch: str = Form(""),
    supplier: str = Form(""),
    work_order: str = Form(""),
    machine: str = Form(""),
):
    event = _event_or_404(db, event_id, current_user)
    if event_type not in EVENT_TYPE_VALUES:
        event_type = event.event_type
    assigned_to, assigned_group_id = _parse_assignee(assignee)
    event.title = title
    event.description = description or None
    event.event_type = event_type
    event.priority = priority
    event.assigned_to = assigned_to
    event.assigned_group_id = assigned_group_id
    event.site_id = _to_int(site_id)
    event.target_close_date = _to_date(target_close_date)
    event.product_part_number = product_part_number or None
    event.lot_batch = lot_batch or None
    event.supplier = supplier or None
    event.work_order = work_order or None
    event.machine = machine or None
    db.add(event)
    fields = fields_for(db, current_user.organization_id, event_type)
    error = save_values(db, event, fields, await request.form())
    if error:
        db.rollback()
        return _redirect(event_id, error)
    db.commit()
    return _redirect(event_id)


# --- workflow actions ------------------------------------------------------
@router.post("/admin/events/{event_id}/status")
async def event_status_action(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_CHANGE_STATUS)),
    db: Session = Depends(get_db),
    status_value: str = Form(..., alias="status"),
):
    event = _event_or_404(db, event_id, current_user)
    try:
        apply_status_transition(event, EventStatus(status_value))
    except WorkflowError as exc:
        return _redirect(event_id, exc.message)
    db.add(event)
    db.commit()
    return _redirect(event_id)


@router.post("/admin/events/{event_id}/close")
async def event_close_action(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_APPROVE_CLOSURE)),
    db: Session = Depends(get_db),
):
    event = _event_or_404(db, event_id, current_user)
    try:
        approve_closure(event, current_user)
    except WorkflowError as exc:
        return _redirect(event_id, exc.message)
    db.add(event)
    db.commit()
    return _redirect(event_id)


@router.post("/admin/events/{event_id}/reopen")
async def event_reopen_action(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_REOPEN)),
    db: Session = Depends(get_db),
    reason: str = Form(...),
):
    event = _event_or_404(db, event_id, current_user)
    try:
        reopen_workflow(db, event, reason)
    except WorkflowError as exc:
        return _redirect(event_id, exc.message)
    db.add(event)
    db.commit()
    return _redirect(event_id)


@router.post("/admin/events/{event_id}/comments")
async def event_comment_action(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_COMMENT)),
    db: Session = Depends(get_db),
    body: str = Form(...),
):
    event = _event_or_404(db, event_id, current_user)
    if body.strip():
        db.add(Comment(event_id=event.id, author_id=current_user.id, body=body.strip()))
        db.commit()
    return _redirect(event_id)


@router.post("/admin/events/{event_id}/attachments")
async def event_attachment_action(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
    file: UploadFile = None,
):
    event = _event_or_404(db, event_id, current_user)
    if file is None or not file.filename:
        return _redirect(event_id, "No file selected")
    data = await file.read()
    if not data:
        return _redirect(event_id, "Empty file")
    storage_key = f"{event.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)
    db.add(Attachment(
        event_id=event.id,
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        storage_key=storage_key,
        uploaded_by=current_user.id,
    ))
    db.commit()
    return _redirect(event_id)
