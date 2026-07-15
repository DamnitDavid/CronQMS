"""Server-rendered admin pages and their form/action handlers.

The JSON API under /api is the machine interface; these routes are the browser
UI. Mutations here are ordinary form posts that redirect back to the page
(Post/Redirect/Get), and they reuse the same workflow service and permission
dependencies as the API so behavior can't drift between the two.
"""

import hashlib
import os
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.routes.setup import admin_exists
from app.core.auth import get_current_user_optional
from app.core.permissions import Permission, require_permission, role_has_permission
from app.core.sla import sla_target
from app.core.storage import get_storage
from app.database import get_db
from app.models import Attachment, Comment, Event, EventHistory, Site, User
from app.models.event import EventStatus
from app.models.user import Role
from app.services.event_workflow import (
    WorkflowError,
    apply_status_transition,
    approve_closure,
    reopen as reopen_workflow,
)

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))


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
    """Which action buttons the current user may see."""
    try:
        role = Role(user.role)
    except ValueError:
        role = None
    checks = {
        "can_edit": Permission.EVENT_UPDATE,
        "can_change_status": Permission.EVENT_CHANGE_STATUS,
        "can_close": Permission.EVENT_APPROVE_CLOSURE,
        "can_reopen": Permission.EVENT_REOPEN,
        "can_comment": Permission.EVENT_COMMENT,
    }
    return {name: bool(role and role_has_permission(role, perm)) for name, perm in checks.items()}


def _org_user_emails(db: Session, organization_id: int) -> dict[int, str]:
    users = db.query(User).filter(User.organization_id == organization_id).all()
    return {u.id: u.email for u in users}


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
        return RedirectResponse("/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    # Before any admin exists, the login page is a dead end — route first-run
    # visitors to the setup wizard instead.
    if not admin_exists(db):
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})


# --- event list & create ---------------------------------------------------
@router.get("/admin/events")
async def admin_events_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
):
    events = (
        db.query(Event)
        .filter(Event.organization_id == current_user.organization_id, Event.is_active.is_(True))
        .order_by(Event.updated_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        "admin/events/list.html",
        {"request": request, "current_user": current_user, "events": events,
         "can_create": _permission_flags_create(current_user)},
    )


def _permission_flags_create(user: User) -> bool:
    try:
        return role_has_permission(Role(user.role), Permission.EVENT_CREATE)
    except ValueError:
        return False


@router.get("/admin/events/create")
async def admin_events_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.EVENT_CREATE)),
    db: Session = Depends(get_db),
):
    sites = db.query(Site).filter(Site.organization_id == current_user.organization_id).all()
    users = db.query(User).filter(User.organization_id == current_user.organization_id).all()
    return templates.TemplateResponse(
        "admin/events/create.html",
        {"request": request, "current_user": current_user, "sites": sites, "users": users},
    )


@router.post("/admin/events/create")
async def admin_events_create_submit(
    current_user: User = Depends(require_permission(Permission.EVENT_CREATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    event_type: str = Form("Non_Conformance"),
    priority: str = Form("Medium"),
    assigned_to: Optional[str] = Form(None),
    site_id: Optional[str] = Form(None),
    target_close_date: Optional[str] = Form(None),
    product_part_number: str = Form(""),
    lot_batch: str = Form(""),
    supplier: str = Form(""),
    work_order: str = Form(""),
    machine: str = Form(""),
):
    event = Event(
        title=title,
        description=description or None,
        event_type=event_type,
        status=EventStatus.OPEN.value,
        priority=priority,
        assigned_to=_to_int(assigned_to),
        site_id=_to_int(site_id),
        organization_id=current_user.organization_id,
        reported_by=current_user.id,
        target_close_date=_to_date(target_close_date) or sla_target(priority, date.today()),
        product_part_number=product_part_number or None,
        lot_batch=lot_batch or None,
        supplier=supplier or None,
        work_order=work_order or None,
        machine=machine or None,
    )
    db.add(event)
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
        {"request": request, "current_user": current_user, "event": event, "sites": sites, "users": users},
    )


@router.post("/admin/events/{event_id}/edit")
async def event_edit_submit(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    event_type: str = Form(...),
    priority: str = Form(...),
    assigned_to: Optional[str] = Form(None),
    site_id: Optional[str] = Form(None),
    target_close_date: Optional[str] = Form(None),
    product_part_number: str = Form(""),
    lot_batch: str = Form(""),
    supplier: str = Form(""),
    work_order: str = Form(""),
    machine: str = Form(""),
):
    event = _event_or_404(db, event_id, current_user)
    event.title = title
    event.description = description or None
    event.event_type = event_type
    event.priority = priority
    event.assigned_to = _to_int(assigned_to)
    event.site_id = _to_int(site_id)
    event.target_close_date = _to_date(target_close_date)
    event.product_part_number = product_part_number or None
    event.lot_batch = lot_batch or None
    event.supplier = supplier or None
    event.work_order = work_order or None
    event.machine = machine or None
    db.add(event)
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
