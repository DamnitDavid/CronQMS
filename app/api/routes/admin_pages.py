"""Server-rendered admin sections: Settings, Users, Reports, CAPA.

These are the browser UIs for capabilities that previously existed only as JSON
APIs (or, for Settings, not at all). They mirror the Post/Redirect/Get pattern in
``pages.py`` and reuse the same permission dependencies so behavior can't drift
from the API.
"""

import os
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.core.security import generate_temp_password, hash_password
from app.database import get_db
from app.models import AssigneeGroup, Capa, CapaStatus, User, VerificationOutcome
from app.services import nav_config, org_settings

router = APIRouter(tags=["Admin"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

# Default role assigned to a newly created user when none is chosen.
DEFAULT_NEW_USER_ROLE = "User"


@router.get("/admin/settings")
async def settings_home(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
):
    return RedirectResponse("/admin/settings/groups", status_code=status.HTTP_303_SEE_OTHER)


# --- Settings: Config (org-wide toggles) -----------------------------------
@router.get("/admin/settings/config")
async def config_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
    saved: Optional[str] = None,
):
    org_id = current_user.organization_id
    return templates.TemplateResponse(
        "admin/settings/config.html",
        {
            "request": request,
            "current_user": current_user,
            "allow_standalone_alerts": org_settings.standalone_alerts_enabled(db, org_id),
            "default_expiry_days": org_settings.default_expiry_days(db, org_id),
            "error": error,
            "saved": saved,
        },
    )


@router.post("/admin/settings/config")
async def config_save(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    allow_standalone_alerts: Optional[str] = Form(None),
    default_expiry_days: str = Form(...),
):
    org_id = current_user.organization_id
    # Checkbox: present ("on") when checked, absent otherwise.
    org_settings.set_setting(
        db, org_id, org_settings.KEY_ALLOW_STANDALONE,
        "true" if allow_standalone_alerts else "false",
    )
    try:
        days = int(default_expiry_days)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return RedirectResponse(
            "/admin/settings/config?error=" + quote("Default expiry must be a positive number of days."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    org_settings.set_setting(db, org_id, org_settings.KEY_DEFAULT_EXPIRY_DAYS, str(days))
    db.commit()
    return RedirectResponse(
        "/admin/settings/config?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


# --- Settings: Navigation (org-configurable sidebar) -----------------------
# Number of group "slots" the editor exposes. Plenty for a sidebar; unused
# slots (blank title) are ignored on save.
NAV_MAX_GROUPS = 6


def _nav_editor_context(db: Session, current_user: User) -> dict:
    """Build the current placement of every module for the navigation editor."""
    layout = nav_config.get_layout(db, current_user.organization_id)
    # key -> (group_index, order) for placed modules.
    placement: dict[str, tuple[int, int]] = {}
    group_titles = ["" for _ in range(NAV_MAX_GROUPS)]
    for gi, group in enumerate(layout.get("groups", [])[:NAV_MAX_GROUPS]):
        group_titles[gi] = group.get("title", "")
        for oi, key in enumerate(group.get("modules", [])):
            placement[key] = (gi, oi)

    # Rows for every registered module: placed ones first (in layout order),
    # then hidden ones alphabetically by label.
    rows = []
    for key, mod in nav_config.MODULES.items():
        gi, oi = placement.get(key, (None, 0))
        rows.append(
            {"key": key, "label": mod["label"], "group_index": gi, "order": oi}
        )
    rows.sort(
        key=lambda r: (
            r["group_index"] is None,
            r["group_index"] if r["group_index"] is not None else 0,
            r["order"],
            r["label"],
        )
    )
    return {
        "group_titles": group_titles,
        "group_slots": list(range(NAV_MAX_GROUPS)),
        "module_rows": rows,
    }


@router.get("/admin/settings/navigation")
async def navigation_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
    saved: Optional[str] = None,
):
    context = {
        "request": request,
        "current_user": current_user,
        "error": error,
        "saved": saved,
    }
    context.update(_nav_editor_context(db, current_user))
    return templates.TemplateResponse("admin/settings/navigation.html", context)


@router.post("/admin/settings/navigation")
async def navigation_save(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    org_id = current_user.organization_id
    form = await request.form()

    if form.get("reset"):
        nav_config.set_layout(db, org_id, nav_config.DEFAULT_LAYOUT)
        db.commit()
        return RedirectResponse(
            "/admin/settings/navigation?saved=1", status_code=status.HTTP_303_SEE_OTHER
        )

    titles = [str(form.get(f"group_title__{i}", "")).strip() for i in range(NAV_MAX_GROUPS)]

    # Collect each module's chosen group index and order.
    buckets: dict[int, list[tuple[float, str]]] = {i: [] for i in range(NAV_MAX_GROUPS)}
    for key in nav_config.MODULES:
        raw_group = form.get(f"group__{key}", "")
        if raw_group == "" or not str(raw_group).isdigit():
            continue  # hidden
        gi = int(raw_group)
        if gi < 0 or gi >= NAV_MAX_GROUPS:
            continue
        raw_order = str(form.get(f"order__{key}", "")).strip()
        try:
            order = float(raw_order)
        except (TypeError, ValueError):
            order = float("inf")
        buckets[gi].append((order, key))

    groups = []
    for i in range(NAV_MAX_GROUPS):
        if not titles[i]:
            continue
        modules = [key for _, key in sorted(buckets[i], key=lambda t: (t[0], t[1]))]
        groups.append({"title": titles[i], "modules": modules})

    if not groups:
        return RedirectResponse(
            "/admin/settings/navigation?error="
            + quote("Define at least one group with a title."),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    nav_config.set_layout(db, org_id, {"groups": groups})
    db.commit()
    return RedirectResponse(
        "/admin/settings/navigation?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


# --- Settings: assignee groups ---------------------------------------------
def _groups_redirect(error: Optional[str] = None) -> RedirectResponse:
    url = "/admin/settings/groups"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _group_in_org(db: Session, group_id: int, organization_id: int) -> Optional[AssigneeGroup]:
    return (
        db.query(AssigneeGroup)
        .filter(AssigneeGroup.id == group_id, AssigneeGroup.organization_id == organization_id)
        .first()
    )


@router.get("/admin/settings/groups")
async def groups_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    groups = (
        db.query(AssigneeGroup)
        .filter(
            AssigneeGroup.organization_id == current_user.organization_id,
            AssigneeGroup.is_active.is_(True),
        )
        .order_by(AssigneeGroup.name.asc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/settings/groups.html",
        {
            "request": request,
            "current_user": current_user,
            "groups": groups,
            "users": _org_users(db, current_user.organization_id),
            "error": error,
        },
    )


@router.post("/admin/settings/groups")
async def group_create(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    name: str = Form(...),
):
    name = name.strip()
    if not name:
        return _groups_redirect("Group name is required.")
    db.add(
        AssigneeGroup(
            organization_id=current_user.organization_id, name=name, is_active=True
        )
    )
    db.commit()
    return _groups_redirect()


@router.post("/admin/settings/groups/{group_id}/delete")
async def group_delete(
    group_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    group = _group_in_org(db, group_id, current_user.organization_id)
    if group is not None:
        group.is_active = False
        db.add(group)
        db.commit()
    return _groups_redirect()


@router.post("/admin/settings/groups/{group_id}/members/add")
async def group_member_add(
    group_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    user_id: int = Form(...),
):
    group = _group_in_org(db, group_id, current_user.organization_id)
    user = _user_in_org(db, user_id, current_user.organization_id)
    if group is None or user is None:
        return _groups_redirect("Could not add member.")
    if user not in group.members:
        group.members.append(user)
        db.add(group)
        db.commit()
    return _groups_redirect()


@router.post("/admin/settings/groups/{group_id}/members/remove")
async def group_member_remove(
    group_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    user_id: int = Form(...),
):
    group = _group_in_org(db, group_id, current_user.organization_id)
    user = _user_in_org(db, user_id, current_user.organization_id)
    if group is not None and user is not None and user in group.members:
        group.members.remove(user)
        db.add(group)
        db.commit()
    return _groups_redirect()


# --- Users -----------------------------------------------------------------
def _org_users(db: Session, organization_id: int) -> list[User]:
    return (
        db.query(User)
        .filter(User.organization_id == organization_id)
        .order_by(User.created_at.asc())
        .all()
    )


def _org_role_names(db: Session, organization_id: int) -> list[str]:
    """Role names shown in the assignment dropdown (system first, then custom)."""
    from app.models import RoleDefinition

    roles = (
        db.query(RoleDefinition)
        .filter(RoleDefinition.organization_id == organization_id)
        .order_by(RoleDefinition.is_system.desc(), RoleDefinition.name.asc())
        .all()
    )
    return [r.name for r in roles]


def _is_assignable_role(db: Session, organization_id: int, role: str) -> bool:
    """Whether ``role`` may be assigned to a user in this org.

    Accepts the org's own roles plus the legacy :class:`Role` enum names, which
    still resolve to their historical permission sets via the resolver's
    fallback (see ``app.core.permissions._resolve_from_db``). This keeps orgs
    that predate role seeding working; the dropdown itself only offers real
    DB-backed roles.
    """
    from app.models.user import Role

    if role in _org_role_names(db, organization_id):
        return True
    return role in {r.value for r in Role}


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
            "roles": _org_role_names(db, current_user.organization_id),
            "default_role": DEFAULT_NEW_USER_ROLE,
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
    role: str = Form(DEFAULT_NEW_USER_ROLE),
):
    email = email.strip().lower()
    if not _is_assignable_role(db, current_user.organization_id, role):
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
    if user is None or not _is_assignable_role(db, current_user.organization_id, role):
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
    # Random one-time password instead of a shared, guessable default.
    temp_password = generate_temp_password()
    user.hashed_password = hash_password(temp_password)
    db.add(user)
    db.commit()
    return RedirectResponse(
        f"/admin/users?notice={quote(f'Password reset to {temp_password} — share it and have them change it.')}",
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
def _to_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _to_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _capa_permission_flags(user: User) -> dict:
    granted = getattr(user, "granted_permissions", set())
    return {"can_create": Permission.CAPA_CREATE.value in granted}


def _org_users(db: Session, organization_id: int) -> list[User]:
    return db.query(User).filter(User.organization_id == organization_id).all()


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
            "perms": _capa_permission_flags(current_user),
        },
    )


@router.get("/admin/capa/create")
async def capa_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.CAPA_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/capa/create.html",
        {
            "request": request,
            "current_user": current_user,
            "users": _org_users(db, current_user.organization_id),
            "error": error,
        },
    )


@router.post("/admin/capa/create")
async def capa_create_submit(
    current_user: User = Depends(require_permission(Permission.CAPA_CREATE)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    root_cause_category: str = Form(""),
    rca_method: str = Form(""),
    containment_actions: str = Form(""),
    root_cause: str = Form(""),
    corrective_action: str = Form(""),
    preventive_action: str = Form(""),
):
    capa = Capa(
        organization_id=current_user.organization_id,
        title=title.strip(),
        status=CapaStatus.OPEN.value,
        verification_outcome=VerificationOutcome.PENDING.value,
        owner_id=_to_int(owner_id),
        due_date=_to_date(due_date),
        root_cause_category=root_cause_category or None,
        rca_method=rca_method or None,
        containment_actions=containment_actions or None,
        root_cause=root_cause or None,
        corrective_action=corrective_action or None,
        preventive_action=preventive_action or None,
        created_by=current_user.id,
    )
    db.add(capa)
    db.commit()
    return RedirectResponse("/admin/capa", status_code=status.HTTP_303_SEE_OTHER)
