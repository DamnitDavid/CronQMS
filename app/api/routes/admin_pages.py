"""Server-rendered admin sections: Settings (custom fields), Users, Reports, CAPA.

These are the browser UIs for capabilities that previously existed only as JSON
APIs (or, for Settings, not at all). They mirror the Post/Redirect/Get pattern in
``pages.py`` and reuse the same permission dependencies so behavior can't drift
from the API.
"""

import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.core.security import hash_password
from app.database import get_db
from app.models import Capa, CustomField, User
from app.models.custom_field import CustomFieldType
from app.models.event import EventType
from app.models.user import Role
from app.services.custom_fields import fields_for, unique_key

router = APIRouter(tags=["Admin"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

EVENT_TYPES = [t.value for t in EventType]
FIELD_TYPES = [t.value for t in CustomFieldType]
ROLES = [r.value for r in Role]

# Default password applied by "reset password" (matches the JSON API).
RESET_PASSWORD = "ChangeMe123!"


# --- Settings: custom fields -----------------------------------------------
def _settings_redirect(event_type: str, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/settings/custom-fields?event_type={quote(event_type)}"
    if error:
        url += f"&error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/settings")
async def settings_home(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
):
    return RedirectResponse("/admin/settings/custom-fields", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/settings/custom-fields")
async def custom_fields_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    event_type: str = EventType.NON_CONFORMANCE.value,
    error: Optional[str] = None,
):
    if event_type not in EVENT_TYPES:
        event_type = EventType.NON_CONFORMANCE.value
    fields = fields_for(db, current_user.organization_id, event_type)
    return templates.TemplateResponse(
        "admin/settings/custom_fields.html",
        {
            "request": request,
            "current_user": current_user,
            "event_types": EVENT_TYPES,
            "field_types": FIELD_TYPES,
            "selected_type": event_type,
            "fields": fields,
            "error": error,
        },
    )


@router.post("/admin/settings/custom-fields")
async def custom_field_create(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    event_type: str = Form(...),
    label: str = Form(...),
    field_type: str = Form(...),
):
    if event_type not in EVENT_TYPES:
        return _settings_redirect(EventType.NON_CONFORMANCE.value, "Unknown event type.")
    label = label.strip()
    if not label:
        return _settings_redirect(event_type, "Field label is required.")
    if field_type not in FIELD_TYPES:
        return _settings_redirect(event_type, "Unknown field type.")

    order = len(fields_for(db, current_user.organization_id, event_type))
    db.add(
        CustomField(
            organization_id=current_user.organization_id,
            event_type=event_type,
            label=label,
            key=unique_key(db, current_user.organization_id, event_type, label),
            field_type=field_type,
            display_order=order,
            is_active=True,
        )
    )
    db.commit()
    return _settings_redirect(event_type)


@router.post("/admin/settings/custom-fields/{field_id}/delete")
async def custom_field_delete(
    field_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    field = (
        db.query(CustomField)
        .filter(
            CustomField.id == field_id,
            CustomField.organization_id == current_user.organization_id,
        )
        .first()
    )
    event_type = field.event_type if field else EventType.NON_CONFORMANCE.value
    if field is not None:
        field.is_active = False
        db.add(field)
        db.commit()
    return _settings_redirect(event_type)


# --- Users -----------------------------------------------------------------
def _org_users(db: Session, organization_id: int) -> list[User]:
    return (
        db.query(User)
        .filter(User.organization_id == organization_id)
        .order_by(User.created_at.asc())
        .all()
    )


def _users_redirect(error: Optional[str] = None) -> RedirectResponse:
    url = "/admin/users"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/users")
async def users_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
    notice: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/users/list.html",
        {
            "request": request,
            "current_user": current_user,
            "users": _org_users(db, current_user.organization_id),
            "roles": ROLES,
            "error": error,
            "notice": notice,
        },
    )


@router.post("/admin/users")
async def user_create(
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(Role.VIEWER.value),
):
    email = email.strip().lower()
    if role not in ROLES:
        return _users_redirect("Unknown role.")
    if len(password) < 8:
        return _users_redirect("Password must be at least 8 characters.")
    if db.query(User).filter(User.email == email).first() is not None:
        return _users_redirect("That email is already registered.")
    db.add(
        User(
            email=email,
            hashed_password=hash_password(password),
            role=role,
            organization_id=current_user.organization_id,
            is_active=True,
        )
    )
    db.commit()
    return _users_redirect()


def _user_in_org(db: Session, user_id: int, organization_id: int) -> Optional[User]:
    return (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == organization_id)
        .first()
    )


@router.post("/admin/users/{user_id}/role")
async def user_set_role(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
    role: str = Form(...),
):
    user = _user_in_org(db, user_id, current_user.organization_id)
    if user is None or role not in ROLES:
        return _users_redirect("Could not update role.")
    user.role = role
    db.add(user)
    db.commit()
    return _users_redirect()


@router.post("/admin/users/{user_id}/deactivate")
async def user_deactivate(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    user = _user_in_org(db, user_id, current_user.organization_id)
    if user is None:
        return _users_redirect("User not found.")
    if user.id == current_user.id:
        return _users_redirect("You cannot deactivate your own account.")
    user.is_active = False
    db.add(user)
    db.commit()
    return _users_redirect()


@router.post("/admin/users/{user_id}/reset-password")
async def user_reset_password(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
):
    user = _user_in_org(db, user_id, current_user.organization_id)
    if user is None:
        return _users_redirect("User not found.")
    user.hashed_password = hash_password(RESET_PASSWORD)
    db.add(user)
    db.commit()
    return RedirectResponse(
        f"/admin/users?notice={quote(f'Password reset to {RESET_PASSWORD} — share it and have them change it.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- Reports ---------------------------------------------------------------
@router.get("/admin/reports")
async def reports_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
):
    return templates.TemplateResponse(
        "admin/reports.html",
        {"request": request, "current_user": current_user},
    )


@router.get("/admin/reports/fragments/overdue-by-owner")
async def reports_overdue_fragment(
    request: Request,
    current_user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
):
    """Overdue-event counts grouped by assignee, as an htmx table fragment."""
    from datetime import date

    from sqlalchemy import func

    from app.models import Event
    from app.models.event import EventStatus

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
    emails = {
        u.id: u.email
        for u in db.query(User).filter(User.organization_id == current_user.organization_id)
    }
    data = [
        {"owner": emails.get(owner_id, "Unassigned"), "count": count}
        for owner_id, count in rows
    ]
    return templates.TemplateResponse(
        "admin/reports/_overdue_by_owner.html",
        {"request": request, "rows": data},
    )


# --- CAPA ------------------------------------------------------------------
@router.get("/admin/capa")
async def capa_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.CAPA_READ)),
    db: Session = Depends(get_db),
):
    capas = (
        db.query(Capa)
        .filter(Capa.organization_id == current_user.organization_id, Capa.is_active.is_(True))
        .order_by(Capa.created_at.desc())
        .all()
    )
    owner_emails = {
        u.id: u.email
        for u in db.query(User).filter(User.organization_id == current_user.organization_id)
    }
    return templates.TemplateResponse(
        "admin/capa/list.html",
        {
            "request": request,
            "current_user": current_user,
            "capas": capas,
            "owner_emails": owner_emails,
        },
    )
