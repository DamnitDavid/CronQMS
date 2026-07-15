"""Server-rendered Quality/Safety Alert pages, the per-user inbox, and the
acknowledgement upload/download endpoints.

Mirrors the browser-page style of ``app/api/routes/pages.py``: form posts that
redirect back (Post/Redirect/Get), reusing the same permission dependencies and
storage backend as the rest of the app so behaviour can't drift.
"""

import hashlib
import os
import uuid
from datetime import date, timedelta
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
    AlertImage,
    AssigneeGroup,
    Event,
    Notification,
    User,
)
from app.models.alert import AlertSeverity, AlertStatus, AlertType
from app.models.user import Role
from app.services import org_settings

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


def _default_expiry(db: Session, organization_id: int) -> date:
    """The pre-filled/default expiry date: today + the org's configured days."""
    return date.today() + timedelta(days=org_settings.default_expiry_days(db, organization_id))


def _issue_alert(
    db: Session,
    current_user: User,
    *,
    event: Optional[Event],
    title: str,
    alert_type: str,
    severity: str,
    affected_product: str,
    affected_lot_batch: str,
    description: str,
    containment_actions: str,
    required_actions: str,
    response_due_date: Optional[str],
    expires_at: Optional[str],
    recipient_group_ids: list[int],
) -> tuple[Optional[Alert], Optional[str]]:
    """Validate + create an alert and fan out notifications.

    Returns ``(alert, None)`` on success or ``(None, error_message)`` when no
    valid recipient group was selected. Does not commit.
    """
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
        return None, "Select at least one recipient group."

    if alert_type not in ALERT_TYPE_VALUES:
        alert_type = AlertType.QUALITY.value
    if severity not in ALERT_SEVERITY_VALUES:
        severity = AlertSeverity.MEDIUM.value

    alert = Alert(
        organization_id=current_user.organization_id,
        event_id=event.id if event is not None else None,
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
        expires_at=_to_date(expires_at) or _default_expiry(db, current_user.organization_id),
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
    return alert, None


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


# --- create (from an event) ------------------------------------------------
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
            "form_action": f"/admin/events/{event.id}/alerts",
            "cancel_url": f"/admin/events/{event.id}",
            "groups": _org_groups(db, current_user.organization_id),
            "alert_types": ALERT_TYPE_VALUES,
            "severities": ALERT_SEVERITY_VALUES,
            "default_expiry": _default_expiry(db, current_user.organization_id).isoformat(),
            "unread_count": _unread_count(db, current_user.id),
            "error": error,
        },
    )


@router.post("/admin/events/{event_id}/alerts")
async def alert_create_submit(
    event_id: int,
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
    expires_at: Optional[str] = Form(None),
    recipient_group_ids: list[int] = Form(default=[]),
):
    event = _event_or_404(db, event_id, current_user)
    alert, error = _issue_alert(
        db, current_user, event=event, title=title, alert_type=alert_type,
        severity=severity, affected_product=affected_product,
        affected_lot_batch=affected_lot_batch, description=description,
        containment_actions=containment_actions, required_actions=required_actions,
        response_due_date=response_due_date, expires_at=expires_at,
        recipient_group_ids=recipient_group_ids,
    )
    if error:
        from urllib.parse import quote

        return RedirectResponse(
            f"/admin/events/{event_id}/alerts/new?error={quote(error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    db.commit()
    db.refresh(alert)
    return _alert_redirect(alert.id)


# --- create (standalone, no source event) ----------------------------------
def _require_standalone_enabled(db: Session, current_user: User) -> None:
    """404 unless the org has enabled alerts without a CAPA/source event."""
    if not org_settings.standalone_alerts_enabled(db, current_user.organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Standalone alerts are not enabled for this organization",
        )


@router.get("/admin/alerts/new")
async def standalone_alert_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    _require_standalone_enabled(db, current_user)
    return templates.TemplateResponse(
        "admin/alerts/create.html",
        {
            "request": request,
            "current_user": current_user,
            "event": None,
            "form_action": "/admin/alerts/new",
            "cancel_url": "/admin/alerts",
            "groups": _org_groups(db, current_user.organization_id),
            "alert_types": ALERT_TYPE_VALUES,
            "severities": ALERT_SEVERITY_VALUES,
            "default_expiry": _default_expiry(db, current_user.organization_id).isoformat(),
            "unread_count": _unread_count(db, current_user.id),
            "error": error,
        },
    )


@router.post("/admin/alerts/new")
async def standalone_alert_create_submit(
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
    expires_at: Optional[str] = Form(None),
    recipient_group_ids: list[int] = Form(default=[]),
):
    _require_standalone_enabled(db, current_user)
    alert, error = _issue_alert(
        db, current_user, event=None, title=title, alert_type=alert_type,
        severity=severity, affected_product=affected_product,
        affected_lot_batch=affected_lot_batch, description=description,
        containment_actions=containment_actions, required_actions=required_actions,
        response_due_date=response_due_date, expires_at=expires_at,
        recipient_group_ids=recipient_group_ids,
    )
    if error:
        from urllib.parse import quote

        return RedirectResponse(
            f"/admin/alerts/new?error={quote(error)}",
            status_code=status.HTTP_303_SEE_OTHER,
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
    perms = _alert_permission_flags(current_user)
    return templates.TemplateResponse(
        "admin/alerts/list.html",
        {
            "request": request,
            "current_user": current_user,
            "alerts": alerts,
            "perms": perms,
            "standalone_enabled": org_settings.standalone_alerts_enabled(
                db, current_user.organization_id
            ),
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


# --- print (browser print-to-PDF) ------------------------------------------
@router.get("/admin/alerts/{alert_id}/print")
async def alert_print_page(
    alert_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    """The alert as a clean signage poster (fields + photos, no signatures)."""
    alert = _alert_or_404(db, alert_id, current_user)
    return templates.TemplateResponse(
        "admin/alerts/print.html",
        {
            "request": request,
            "alert": alert,
            "user_emails": _org_user_emails(db, current_user.organization_id),
        },
    )


@router.get("/admin/alerts/{alert_id}/signoff")
async def alert_signoff_page(
    alert_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
):
    """A full page of blank Name/Signature/Date rows for operators to hand-sign."""
    alert = _alert_or_404(db, alert_id, current_user)
    return templates.TemplateResponse(
        "admin/alerts/signoff.html",
        {
            "request": request,
            "alert": alert,
            "blank_rows": range(20),
        },
    )


# --- images (attach + edit, then view) -------------------------------------
@router.post("/admin/alerts/{alert_id}/images")
async def alert_image_upload(
    alert_id: int,
    file: UploadFile,
    current_user: User = Depends(require_permission(Permission.ALERT_CREATE)),
    db: Session = Depends(get_db),
    position: int = Form(...),
):
    alert = _alert_or_404(db, alert_id, current_user)
    if position not in (1, 2):
        return _alert_redirect(alert_id, "Invalid image slot")

    data = await file.read()
    if not data:
        return _alert_redirect(alert_id, "Empty file")
    if len(data) > settings.attachment_max_bytes:
        return _alert_redirect(alert_id, "File exceeds maximum allowed size")

    checksum = hashlib.sha256(data).hexdigest()
    storage_key = f"alert-image/{alert.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)

    # Upsert the slot: replace any existing image at this position.
    existing = (
        db.query(AlertImage)
        .filter(AlertImage.alert_id == alert.id, AlertImage.position == position)
        .first()
    )
    if existing is not None:
        get_storage().delete(existing.storage_key)
        existing.filename = file.filename or "image.png"
        existing.content_type = file.content_type or "image/png"
        existing.size_bytes = len(data)
        existing.checksum = checksum
        existing.storage_key = storage_key
        existing.uploaded_by = current_user.id
        db.add(existing)
    else:
        db.add(
            AlertImage(
                alert_id=alert.id,
                position=position,
                filename=file.filename or "image.png",
                content_type=file.content_type or "image/png",
                size_bytes=len(data),
                checksum=checksum,
                storage_key=storage_key,
                uploaded_by=current_user.id,
            )
        )
    db.commit()
    return _alert_redirect(alert_id)


@router.post("/admin/alerts/{alert_id}/images/{image_id}/delete")
async def alert_image_delete(
    alert_id: int,
    image_id: int,
    current_user: User = Depends(require_permission(Permission.ALERT_CREATE)),
    db: Session = Depends(get_db),
):
    alert = _alert_or_404(db, alert_id, current_user)
    image = (
        db.query(AlertImage)
        .filter(AlertImage.id == image_id, AlertImage.alert_id == alert.id)
        .first()
    )
    if image is not None:
        get_storage().delete(image.storage_key)
        db.delete(image)
        db.commit()
    return _alert_redirect(alert_id)


@router.get("/api/alert-images/{image_id}")
async def alert_image_view(
    image_id: int,
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
    db: Session = Depends(get_db),
) -> Response:
    image = db.query(AlertImage).filter(AlertImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    # Enforce org scope via the parent alert.
    _alert_or_404(db, image.alert_id, current_user)

    data = get_storage().load(image.storage_key)
    return Response(
        content=data,
        media_type=image.content_type or "image/png",
        headers={"Content-Disposition": f'inline; filename="{image.filename}"'},
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
