"""Server-rendered Change Control pages and their form/action handlers.

The browser UI counterpart to the /api/changes JSON API. Mutations are
Post/Redirect/Get form posts that reuse the same permission dependencies and
status rules as the API, so change-control behavior can't drift between the two
surfaces.
"""

import os
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.audit import set_audit_reason
from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import (
    ActionStatus,
    Capa,
    ChangeAction,
    ChangeImpact,
    ChangeRequest,
    ChangeStatus,
    ChangeType,
    EventHistory,
    ImpactArea,
    ImpactLevel,
    RiskLevel,
    User,
)

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

TYPE_VALUES = [t.value for t in ChangeType]
STATUS_VALUES = [s.value for s in ChangeStatus]
RISK_VALUES = [r.value for r in RiskLevel]
AREA_VALUES = [a.value for a in ImpactArea]
IMPACT_LEVEL_VALUES = [i.value for i in ImpactLevel]
ACTION_STATUS_VALUES = [s.value for s in ActionStatus]

# Statuses that represent an approval decision — gated on change:approve.
_APPROVAL_STATUSES = {ChangeStatus.APPROVED.value, ChangeStatus.REJECTED.value}


# --- helpers ---------------------------------------------------------------
def _change_or_404(db: Session, change_id: int, current_user: User) -> ChangeRequest:
    change = (
        db.query(ChangeRequest)
        .filter(ChangeRequest.id == change_id, ChangeRequest.is_active.is_(True))
        .first()
    )
    if not change or change.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found")
    return change


def _impact_or_404(db: Session, change: ChangeRequest, impact_id: int) -> ChangeImpact:
    impact = (
        db.query(ChangeImpact)
        .filter(ChangeImpact.id == impact_id, ChangeImpact.change_id == change.id)
        .first()
    )
    if not impact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Impact assessment not found")
    return impact


def _action_or_404(db: Session, change: ChangeRequest, action_id: int) -> ChangeAction:
    action = (
        db.query(ChangeAction)
        .filter(ChangeAction.id == action_id, ChangeAction.change_id == change.id)
        .first()
    )
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return action


def _permission_flags(user: User) -> dict:
    """Which change-control action buttons the current user may see."""
    granted = getattr(user, "granted_permissions", set())
    checks = {
        "can_create": Permission.CHANGE_CREATE,
        "can_edit": Permission.CHANGE_UPDATE,
        "can_assess": Permission.CHANGE_ASSESS,
        "can_approve": Permission.CHANGE_APPROVE,
        "can_delete": Permission.CHANGE_DELETE,
    }
    return {name: perm.value in granted for name, perm in checks.items()}


def _org_user_emails(db: Session, organization_id: int) -> dict[int, str]:
    users = db.query(User).filter(User.organization_id == organization_id).all()
    return {u.id: u.email for u in users}


def _org_users(db: Session, organization_id: int) -> list[User]:
    return db.query(User).filter(User.organization_id == organization_id).all()


def _org_capas(db: Session, organization_id: int) -> list[Capa]:
    return (
        db.query(Capa)
        .filter(Capa.organization_id == organization_id, Capa.is_active.is_(True))
        .order_by(Capa.created_at.desc())
        .all()
    )


def _to_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _to_date(value: Optional[str]):
    return date.fromisoformat(value) if value else None


def _redirect(change_id: int, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/changes/{change_id}"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --- list ------------------------------------------------------------------
@router.get("/admin/changes")
async def changes_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.CHANGE_READ)),
    db: Session = Depends(get_db),
    change_type: Optional[str] = None,
    status_filter: Optional[str] = None,
):
    query = db.query(ChangeRequest).filter(
        ChangeRequest.organization_id == current_user.organization_id,
        ChangeRequest.is_active.is_(True),
    )
    if change_type in TYPE_VALUES:
        query = query.filter(ChangeRequest.change_type == change_type)
    if status_filter in STATUS_VALUES:
        query = query.filter(ChangeRequest.status == status_filter)
    changes = query.order_by(ChangeRequest.updated_at.desc()).limit(200).all()
    context = {
        "request": request,
        "current_user": current_user,
        "changes": changes,
        "owner_emails": _org_user_emails(db, current_user.organization_id),
        "types": TYPE_VALUES,
        "statuses": STATUS_VALUES,
        "perms": _permission_flags(current_user),
        "filters": {"change_type": change_type or "", "status": status_filter or ""},
    }
    template = (
        "admin/changes/_change_table.html"
        if "HX-Request" in request.headers
        else "admin/changes/list.html"
    )
    return templates.TemplateResponse(template, context)


# --- create ----------------------------------------------------------------
@router.get("/admin/changes/create")
async def change_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.CHANGE_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/changes/create.html",
        {
            "request": request,
            "current_user": current_user,
            "users": _org_users(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "risks": RISK_VALUES,
            "error": error,
        },
    )


@router.post("/admin/changes/create")
async def change_create_submit(
    current_user: User = Depends(require_permission(Permission.CHANGE_CREATE)),
    db: Session = Depends(get_db),
    reference: str = Form(...),
    title: str = Form(...),
    change_type: str = Form(ChangeType.PROCESS.value),
    risk_level: str = Form(RiskLevel.LOW.value),
    affected_area: str = Form(""),
    owner_id: Optional[str] = Form(None),
    description: str = Form(""),
    reason: str = Form(""),
    target_date: Optional[str] = Form(None),
):
    change = ChangeRequest(
        organization_id=current_user.organization_id,
        reference=reference.strip(),
        title=title.strip(),
        change_type=change_type if change_type in TYPE_VALUES else ChangeType.PROCESS.value,
        status=ChangeStatus.DRAFT.value,
        risk_level=risk_level if risk_level in RISK_VALUES else RiskLevel.LOW.value,
        affected_area=affected_area or None,
        owner_id=_to_int(owner_id),
        description=description or None,
        reason=reason or None,
        target_date=_to_date(target_date),
        created_by=current_user.id,
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return _redirect(change.id)


# --- edit ------------------------------------------------------------------
@router.get("/admin/changes/{change_id}/edit")
async def change_edit_page(
    change_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.CHANGE_UPDATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    change = _change_or_404(db, change_id, current_user)
    return templates.TemplateResponse(
        "admin/changes/edit.html",
        {
            "request": request,
            "current_user": current_user,
            "change": change,
            "users": _org_users(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "risks": RISK_VALUES,
            "error": error,
        },
    )


@router.post("/admin/changes/{change_id}/edit")
async def change_edit_submit(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_UPDATE)),
    db: Session = Depends(get_db),
    reference: str = Form(...),
    title: str = Form(...),
    change_type: str = Form(ChangeType.PROCESS.value),
    risk_level: str = Form(RiskLevel.LOW.value),
    affected_area: str = Form(""),
    owner_id: Optional[str] = Form(None),
    description: str = Form(""),
    reason: str = Form(""),
    target_date: Optional[str] = Form(None),
    implementation_date: Optional[str] = Form(None),
    summary: str = Form(""),
):
    change = _change_or_404(db, change_id, current_user)
    change.reference = reference.strip()
    change.title = title.strip()
    change.change_type = change_type if change_type in TYPE_VALUES else change.change_type
    change.risk_level = risk_level if risk_level in RISK_VALUES else change.risk_level
    change.affected_area = affected_area or None
    change.owner_id = _to_int(owner_id)
    change.description = description or None
    change.reason = reason or None
    change.target_date = _to_date(target_date)
    change.implementation_date = _to_date(implementation_date)
    change.summary = summary or None
    db.add(change)
    db.commit()
    return _redirect(change.id)


# --- detail ----------------------------------------------------------------
@router.get("/admin/changes/{change_id}")
async def change_detail_page(
    change_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.CHANGE_READ)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    change = _change_or_404(db, change_id, current_user)
    action_ids = [a.id for a in change.actions]
    history = (
        db.query(EventHistory)
        .filter(
            (
                (EventHistory.entity_type == "change_request")
                & (EventHistory.entity_id == change.id)
            )
            | (
                (EventHistory.entity_type == "change_action")
                & (EventHistory.entity_id.in_(action_ids or [0]))
            )
        )
        .order_by(EventHistory.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/changes/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "change": change,
            "impacts": change.impacts,
            "actions": change.actions,
            "history": history,
            "user_emails": _org_user_emails(db, current_user.organization_id),
            "users": _org_users(db, current_user.organization_id),
            "capas": _org_capas(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "statuses": STATUS_VALUES,
            "risks": RISK_VALUES,
            "areas": AREA_VALUES,
            "impact_levels": IMPACT_LEVEL_VALUES,
            "action_statuses": ACTION_STATUS_VALUES,
            "perms": _permission_flags(current_user),
            "today": date.today(),
            "error": error,
        },
    )


# --- status action ---------------------------------------------------------
@router.post("/admin/changes/{change_id}/status")
async def change_status_action(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_UPDATE)),
    db: Session = Depends(get_db),
    status_value: str = Form(..., alias="status"),
):
    change = _change_or_404(db, change_id, current_user)
    if status_value not in STATUS_VALUES:
        return _redirect(change.id, "Unknown status.")
    granted = getattr(current_user, "granted_permissions", set())
    if status_value in _APPROVAL_STATUSES and Permission.CHANGE_APPROVE.value not in granted:
        return _redirect(change.id, "You do not have permission to approve or reject changes.")
    if status_value == ChangeStatus.CLOSED.value and change.open_actions_count > 0:
        return _redirect(change.id, "Cannot close a change with open actions.")
    change.status = status_value
    db.add(change)
    db.commit()
    return _redirect(change.id)


# --- impact assessment rows ------------------------------------------------
@router.post("/admin/changes/{change_id}/impacts")
async def impact_add(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
    area: str = Form(ImpactArea.QUALITY.value),
    impact_level: str = Form(ImpactLevel.NONE.value),
    description: str = Form(""),
    mitigation: str = Form(""),
):
    change = _change_or_404(db, change_id, current_user)
    order = max((i.display_order for i in change.impacts), default=-1) + 1
    db.add(
        ChangeImpact(
            organization_id=change.organization_id,
            change_id=change.id,
            area=area if area in AREA_VALUES else ImpactArea.QUALITY.value,
            impact_level=impact_level if impact_level in IMPACT_LEVEL_VALUES else ImpactLevel.NONE.value,
            description=description or None,
            mitigation=mitigation or None,
            display_order=order,
        )
    )
    db.commit()
    return _redirect(change.id)


@router.post("/admin/changes/{change_id}/impacts/{impact_id}")
async def impact_update(
    change_id: int,
    impact_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
    area: str = Form(...),
    impact_level: str = Form(...),
    description: str = Form(""),
    mitigation: str = Form(""),
):
    change = _change_or_404(db, change_id, current_user)
    impact = _impact_or_404(db, change, impact_id)
    if area in AREA_VALUES:
        impact.area = area
    if impact_level in IMPACT_LEVEL_VALUES:
        impact.impact_level = impact_level
    impact.description = description or None
    impact.mitigation = mitigation or None
    db.add(impact)
    db.commit()
    return _redirect(change.id)


@router.post("/admin/changes/{change_id}/impacts/{impact_id}/delete")
async def impact_delete(
    change_id: int,
    impact_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, change_id, current_user)
    impact = _impact_or_404(db, change, impact_id)
    db.delete(impact)
    db.commit()
    return _redirect(change.id)


# --- implementation actions ------------------------------------------------
@router.post("/admin/changes/{change_id}/actions")
async def action_add(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
):
    change = _change_or_404(db, change_id, current_user)
    if len(title.strip()) < 3:
        return _redirect(change.id, "Action title must be at least 3 characters.")
    db.add(
        ChangeAction(
            organization_id=change.organization_id,
            change_id=change.id,
            title=title.strip(),
            description=description or None,
            status=ActionStatus.OPEN.value,
            owner_id=_to_int(owner_id),
            due_date=_to_date(due_date),
            created_by=current_user.id,
        )
    )
    db.commit()
    return _redirect(change.id)


@router.post("/admin/changes/{change_id}/actions/{action_id}")
async def action_update(
    change_id: int,
    action_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
    status_value: str = Form(..., alias="status"),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    capa_id: Optional[str] = Form(None),
    reason: str = Form(""),
):
    change = _change_or_404(db, change_id, current_user)
    action = _action_or_404(db, change, action_id)

    resolved_capa = _to_int(capa_id)
    if resolved_capa is not None:
        capa = (
            db.query(Capa)
            .filter(
                Capa.id == resolved_capa,
                Capa.organization_id == change.organization_id,
                Capa.is_active.is_(True),
            )
            .first()
        )
        if capa is None:
            return _redirect(change.id, "Linked CAPA not found in your organization.")

    if reason.strip():
        set_audit_reason(db, reason.strip())
    if status_value in ACTION_STATUS_VALUES:
        action.status = status_value
    action.owner_id = _to_int(owner_id)
    action.due_date = _to_date(due_date)
    action.capa_id = resolved_capa
    db.add(action)
    db.commit()
    return _redirect(change.id)


@router.post("/admin/changes/{change_id}/actions/{action_id}/delete")
async def action_delete(
    change_id: int,
    action_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, change_id, current_user)
    action = _action_or_404(db, change, action_id)
    db.delete(action)
    db.commit()
    return _redirect(change.id)


# --- delete ----------------------------------------------------------------
@router.post("/admin/changes/{change_id}/delete")
async def change_delete(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_DELETE)),
    db: Session = Depends(get_db),
):
    change = _change_or_404(db, change_id, current_user)
    change.is_active = False
    db.add(change)
    db.commit()
    return RedirectResponse("/admin/changes", status_code=status.HTTP_303_SEE_OTHER)
