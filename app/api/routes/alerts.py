"""Server-rendered Quality/Safety Alert pages, the per-user inbox, and the
acknowledgement upload/download endpoints.

Mirrors the browser-page style of ``app/api/routes/pages.py``: form posts that
redirect back (Post/Redirect/Get), reusing the same permission dependencies and
storage backend as the rest of the app so behaviour can't drift.
"""

import hashlib
import os
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Permission, require_permission, role_has_permission
from app.core.storage import get_storage
from app.database import get_db
from app.models import (
    Alert,
    AlertAcknowledgement,
    AssigneeGroup,
    Event,
    Notification,
    User,
)
from app.models.alert import AlertSeverity, AlertStatus, AlertType
from app.models.user import Role

router = APIRouter(tags=["Alerts"])

settings = get_settings()

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

ALERT_TYPE_VALUES = [t.value for t in AlertType]
ALERT_SEVERITY_VALUES = [s.value for s in AlertSeverity]


# --- helpers ---------------------------------------------------------------
def _event_or_404(db: Session, event_id: int, current_user: User) -> Event:
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )
    if not event or event.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def _alert_or_404(db: Session, alert_id: int, current_user: User) -> Alert:
    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id, Alert.is_active.is_(True))
        .first()
    )
    if not alert or alert.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return alert


def _org_groups(db: Session, organization_id: int):
    return (
        db.query(AssigneeGroup)
        .filter(
            AssigneeGroup.organization_id == organization_id,
            AssigneeGroup.is_active.is_(True),
        )
        .order_by(AssigneeGroup.name.asc())
        .all()
    )


def _org_user_emails(db: Session, organization_id: int) -> dict[int, str]:
    users = db.query(User).filter(User.organization_id == organization_id).all()
    return {u.id: u.email for u in users}


def _alert_permission_flags(user: User) -> dict:
    """Which alert action controls the current user may see."""
    try:
        role = Role(user.role)
    except ValueError:
        role = None
    checks = {
        "can_create": Permission.ALERT_CREATE,
        "can_acknowledge": Permission.ALERT_ACKNOWLEDGE,
        "can_close": Permission.ALERT_CLOSE,
    }
    return {name: bool(role and role_has_permission(role, perm)) for name, perm in checks.items()}


def _to_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _alert_redirect(alert_id: int, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/alerts/{alert_id}"
    if error:
        from urllib.parse import quote

        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _unread_count(db: Session, user_id: int) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read.is_(False))
        .count()
    )


# --- create ----------------------------------------------------------------
@router.get("/admin/events/{event_id}/alerts/new")
async def alert_create_page(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    event = _event_or_404(db, event_id, current_user)
    return templates.TemplateResponse(
        "admin/alerts/create.html",
        {
            "request": request,
            "current_user": current_user,
            "event": event,
            "groups": _org_groups(db, current_user.organization_id),
            "alert_types": ALERT_TYPE_VALUES,
            "severities": ALERT_SEVERITY_VALUES,
            "unread_count": _unread_count(db, current_user.id),
            "error": error,
        },
    )


@router.post("/admin/events/{event_id}/alerts")
async def alert_create_submit(
    event_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_CREATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    alert_type: str = Form(...),
    severity: str = Form(...),
    affected_product: str = Form(""),
    affected_lot_batch: str = Form(""),
    description: str = Form(""),
    containment_actions: str = Form(""),
    required_actions: str = Form(""),
    response_due_date: Optional[str] = Form(None),
    recipient_group_ids: list[int] = Form(default=[]),
):
    event = _event_or_404(db, event_id, current_user)

    def _redirect_new(message: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(
            f"/admin/events/{event_id}/alerts/new?error={quote(message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # A group of recipients is required before an alert can be issued.
    groups = (
        db.query(AssigneeGroup)
        .filter(
            AssigneeGroup.id.in_(recipient_group_ids),
            AssigneeGroup.organization_id == current_user.organization_id,
            AssigneeGroup.is_active.is_(True),
        )
        .all()
        if recipient_group_ids
        else []
    )
    if not groups:
        return _redirect_new("Select at least one recipient group.")

    if alert_type not in ALERT_TYPE_VALUES:
        alert_type = AlertType.QUALITY.value
    if severity not in ALERT_SEVERITY_VALUES:
        severity = AlertSeverity.MEDIUM.value

    alert = Alert(
        organization_id=current_user.organization_id,
        event_id=event.id,
        title=title,
        alert_type=alert_type,
        severity=severity,
        status=AlertStatus.OPEN.value,
        affected_product=affected_product or None,
        affected_lot_batch=affected_lot_batch or None,
        description=description or None,
        containment_actions=containment_actions or None,
        required_actions=required_actions or None,
        response_due_date=_to_date(response_due_date),
        issued_by=current_user.id,
    )
    alert.recipient_groups = groups
    db.add(alert)
    db.flush()  # assign alert.id for notifications

    # Fan out an in-app notification to each unique member across the groups.
    recipient_ids = {member.id for group in groups for member in group.members}
    for user_id in recipient_ids:
        db.add(
            Notification(
                organization_id=current_user.organization_id,
                user_id=user_id,
                alert_id=alert.id,
                subject=f"{alert.severity} {alert.alert_type} Alert: {alert.title}",
                body=(alert.required_actions or alert.description or ""),
            )
        )

    db.commit()
    db.refresh(alert)
    return _alert_redirect(alert.id)


# --- list ------------------------------------------------------------------
@router.get("/admin/alerts")
async def alerts_list_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    alerts = (
        db.query(Alert)
        .filter(
            Alert.organization_id == current_user.organization_id,
            Alert.is_active.is_(True),
        )
        .order_by(Alert.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/alerts/list.html",
        {
            "request": request,
            "current_user": current_user,
            "alerts": alerts,
            "unread_count": _unread_count(db, current_user.id),
        },
    )


# --- detail ----------------------------------------------------------------
@router.get("/admin/alerts/{alert_id}")
async def alert_detail_page(
    alert_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    alert = _alert_or_404(db, alert_id, current_user)
    return templates.TemplateResponse(
        "admin/alerts/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "alert": alert,
            "user_emails": _org_user_emails(db, current_user.organization_id),
            "perms": _alert_permission_flags(current_user),
            "unread_count": _unread_count(db, current_user.id),
            "error": error,
        },
    )


# --- print (standalone, browser print-to-PDF) ------------------------------
@router.get("/admin/alerts/{alert_id}/print")
async def alert_print_page(
    alert_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    alert = _alert_or_404(db, alert_id, current_user)
    return templates.TemplateResponse(
        "admin/alerts/print.html",
        {
            "request": request,
            "alert": alert,
            "user_emails": _org_user_emails(db, current_user.organization_id),
        },
    )


# --- acknowledgement upload ------------------------------------------------
@router.post("/admin/alerts/{alert_id}/acknowledgements")
async def alert_acknowledge_submit(
    alert_id: int,
    file: UploadFile,
    current_user: User = Depends(require_permission(Permission.ALERT_ACKNOWLEDGE)),
    db: Session = Depends(get_db),
    group_id: Optional[str] = Form(None),
    note: str = Form(""),
):
    alert = _alert_or_404(db, alert_id, current_user)

    data = await file.read()
    if not data:
        return _alert_redirect(alert_id, "Empty file")
    if len(data) > settings.attachment_max_bytes:
        return _alert_redirect(alert_id, "File exceeds maximum allowed size")

    checksum = hashlib.sha256(data).hexdigest()
    storage_key = f"alert/{alert.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)

    db.add(
        AlertAcknowledgement(
            alert_id=alert.id,
            filename=file.filename or "upload",
            content_type=file.content_type,
            size_bytes=len(data),
            checksum=checksum,
            storage_key=storage_key,
            submitted_by=current_user.id,
            group_id=int(group_id) if group_id not in (None, "") else None,
            note=note or None,
        )
    )
    db.flush()

    # When every recipient group has returned at least one signed document, the
    # open alert is considered acknowledged. Query the table directly rather than
    # the (already-loaded, now-stale) alert.acknowledgements collection.
    responded_groups = {
        gid
        for (gid,) in db.query(AlertAcknowledgement.group_id)
        .filter(
            AlertAcknowledgement.alert_id == alert.id,
            AlertAcknowledgement.group_id.isnot(None),
        )
        .all()
    }
    required_groups = {g.id for g in alert.recipient_groups}
    if alert.status == AlertStatus.OPEN.value and required_groups and required_groups <= responded_groups:
        alert.status = AlertStatus.ACKNOWLEDGED.value
        db.add(alert)

    db.commit()
    return _alert_redirect(alert_id)


@router.get("/api/alert-acknowledgements/{ack_id}/download")
async def alert_acknowledgement_download(
    ack_id: int,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
) -> Response:
    ack = db.query(AlertAcknowledgement).filter(AlertAcknowledgement.id == ack_id).first()
    if not ack:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acknowledgement not found")
    # Enforce org scope via the parent alert.
    _alert_or_404(db, ack.alert_id, current_user)

    data = get_storage().load(ack.storage_key)
    return Response(
        content=data,
        media_type=ack.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{ack.filename}"'},
    )


# --- close -----------------------------------------------------------------
@router.post("/admin/alerts/{alert_id}/close")
async def alert_close_action(
    alert_id: int,
    current_user: User = Depends(require_permission(Permission.ALERT_CLOSE)),
    db: Session = Depends(get_db),
):
    alert = _alert_or_404(db, alert_id, current_user)
    alert.status = AlertStatus.CLOSED.value
    db.add(alert)
    db.commit()
    return _alert_redirect(alert_id)


# --- inbox -----------------------------------------------------------------
@router.get("/admin/inbox")
async def inbox_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    notifications = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/alerts/inbox.html",
        {
            "request": request,
            "current_user": current_user,
            "notifications": notifications,
            "unread_count": _unread_count(db, current_user.id),
        },
    )


@router.post("/admin/inbox/{notification_id}/read")
async def inbox_mark_read(
    notification_id: int,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    notification.is_read = True
    db.add(notification)
    db.commit()
    # Send the user to the linked alert if there is one, else back to the inbox.
    if notification.alert_id:
        return RedirectResponse(
            f"/admin/alerts/{notification.alert_id}", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse("/admin/inbox", status_code=status.HTTP_303_SEE_OTHER)
