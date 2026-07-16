"""Server-rendered Audit Management pages and their form/action handlers.

The browser UI counterpart to the /api/audits JSON API. Mutations are
Post/Redirect/Get form posts that reuse the same permission dependencies and
status rules as the API, so audit behavior can't drift between the two surfaces.
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
    Audit,
    AuditChecklistItem,
    AuditFinding,
    AuditStatus,
    AuditType,
    Capa,
    ChecklistResult,
    EventHistory,
    FindingSeverity,
    FindingStatus,
    User,
)

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

TYPE_VALUES = [t.value for t in AuditType]
STATUS_VALUES = [s.value for s in AuditStatus]
RESULT_VALUES = [r.value for r in ChecklistResult]
SEVERITY_VALUES = [s.value for s in FindingSeverity]
FINDING_STATUS_VALUES = [s.value for s in FindingStatus]


# --- helpers ---------------------------------------------------------------
def _audit_or_404(db: Session, audit_id: int, current_user: User) -> Audit:
    audit = (
        db.query(Audit)
        .filter(Audit.id == audit_id, Audit.is_active.is_(True))
        .first()
    )
    if not audit or audit.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit not found")
    return audit


def _item_or_404(db: Session, audit: Audit, item_id: int) -> AuditChecklistItem:
    item = (
        db.query(AuditChecklistItem)
        .filter(AuditChecklistItem.id == item_id, AuditChecklistItem.audit_id == audit.id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist item not found")
    return item


def _finding_or_404(db: Session, audit: Audit, finding_id: int) -> AuditFinding:
    finding = (
        db.query(AuditFinding)
        .filter(AuditFinding.id == finding_id, AuditFinding.audit_id == audit.id)
        .first()
    )
    if not finding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
    return finding


def _permission_flags(user: User) -> dict:
    """Which audit action buttons the current user may see."""
    granted = getattr(user, "granted_permissions", set())
    checks = {
        "can_create": Permission.AUDIT_CREATE,
        "can_edit": Permission.AUDIT_UPDATE,
        "can_conduct": Permission.AUDIT_CONDUCT,
        "can_close": Permission.AUDIT_CLOSE,
        "can_delete": Permission.AUDIT_DELETE,
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


def _redirect(audit_id: int, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/audits/{audit_id}"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --- list ------------------------------------------------------------------
@router.get("/admin/audits")
async def audits_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
    audit_type: Optional[str] = None,
    status_filter: Optional[str] = None,
):
    query = db.query(Audit).filter(
        Audit.organization_id == current_user.organization_id,
        Audit.is_active.is_(True),
    )
    if audit_type in TYPE_VALUES:
        query = query.filter(Audit.audit_type == audit_type)
    if status_filter in STATUS_VALUES:
        query = query.filter(Audit.status == status_filter)
    audits = query.order_by(Audit.updated_at.desc()).limit(200).all()
    context = {
        "request": request,
        "current_user": current_user,
        "audits": audits,
        "auditor_emails": _org_user_emails(db, current_user.organization_id),
        "types": TYPE_VALUES,
        "statuses": STATUS_VALUES,
        "perms": _permission_flags(current_user),
        "filters": {"audit_type": audit_type or "", "status": status_filter or ""},
    }
    template = (
        "admin/audits/_audit_table.html"
        if "HX-Request" in request.headers
        else "admin/audits/list.html"
    )
    return templates.TemplateResponse(template, context)


# --- create ----------------------------------------------------------------
@router.get("/admin/audits/create")
async def audit_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.AUDIT_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/audits/create.html",
        {
            "request": request,
            "current_user": current_user,
            "users": _org_users(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "error": error,
        },
    )


@router.post("/admin/audits/create")
async def audit_create_submit(
    current_user: User = Depends(require_permission(Permission.AUDIT_CREATE)),
    db: Session = Depends(get_db),
    reference: str = Form(...),
    title: str = Form(...),
    audit_type: str = Form(AuditType.INTERNAL.value),
    standard: str = Form(""),
    auditee: str = Form(""),
    lead_auditor_id: Optional[str] = Form(None),
    scope: str = Form(""),
    planned_date: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
):
    audit = Audit(
        organization_id=current_user.organization_id,
        reference=reference.strip(),
        title=title.strip(),
        audit_type=audit_type if audit_type in TYPE_VALUES else AuditType.INTERNAL.value,
        status=AuditStatus.PLANNED.value,
        standard=standard or None,
        auditee=auditee or None,
        lead_auditor_id=_to_int(lead_auditor_id),
        scope=scope or None,
        planned_date=_to_date(planned_date),
        start_date=_to_date(start_date),
        end_date=_to_date(end_date),
        created_by=current_user.id,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return _redirect(audit.id)


# --- edit ------------------------------------------------------------------
@router.get("/admin/audits/{audit_id}/edit")
async def audit_edit_page(
    audit_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.AUDIT_UPDATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    audit = _audit_or_404(db, audit_id, current_user)
    return templates.TemplateResponse(
        "admin/audits/edit.html",
        {
            "request": request,
            "current_user": current_user,
            "audit": audit,
            "users": _org_users(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "error": error,
        },
    )


@router.post("/admin/audits/{audit_id}/edit")
async def audit_edit_submit(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_UPDATE)),
    db: Session = Depends(get_db),
    reference: str = Form(...),
    title: str = Form(...),
    audit_type: str = Form(AuditType.INTERNAL.value),
    standard: str = Form(""),
    auditee: str = Form(""),
    lead_auditor_id: Optional[str] = Form(None),
    scope: str = Form(""),
    planned_date: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    summary: str = Form(""),
):
    audit = _audit_or_404(db, audit_id, current_user)
    audit.reference = reference.strip()
    audit.title = title.strip()
    audit.audit_type = audit_type if audit_type in TYPE_VALUES else audit.audit_type
    audit.standard = standard or None
    audit.auditee = auditee or None
    audit.lead_auditor_id = _to_int(lead_auditor_id)
    audit.scope = scope or None
    audit.planned_date = _to_date(planned_date)
    audit.start_date = _to_date(start_date)
    audit.end_date = _to_date(end_date)
    audit.summary = summary or None
    db.add(audit)
    db.commit()
    return _redirect(audit.id)


# --- detail ----------------------------------------------------------------
@router.get("/admin/audits/{audit_id}")
async def audit_detail_page(
    audit_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    audit = _audit_or_404(db, audit_id, current_user)
    finding_ids = [f.id for f in audit.findings]
    history = (
        db.query(EventHistory)
        .filter(
            (
                (EventHistory.entity_type == "audit")
                & (EventHistory.entity_id == audit.id)
            )
            | (
                (EventHistory.entity_type == "audit_finding")
                & (EventHistory.entity_id.in_(finding_ids or [0]))
            )
        )
        .order_by(EventHistory.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/audits/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "audit": audit,
            "checklist_items": audit.checklist_items,
            "findings": audit.findings,
            "history": history,
            "user_emails": _org_user_emails(db, current_user.organization_id),
            "users": _org_users(db, current_user.organization_id),
            "capas": _org_capas(db, current_user.organization_id),
            "types": TYPE_VALUES,
            "statuses": STATUS_VALUES,
            "results": RESULT_VALUES,
            "severities": SEVERITY_VALUES,
            "finding_statuses": FINDING_STATUS_VALUES,
            "perms": _permission_flags(current_user),
            "today": date.today(),
            "error": error,
        },
    )


# --- status action ---------------------------------------------------------
@router.post("/admin/audits/{audit_id}/status")
async def audit_status_action(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_UPDATE)),
    db: Session = Depends(get_db),
    status_value: str = Form(..., alias="status"),
):
    audit = _audit_or_404(db, audit_id, current_user)
    if status_value not in STATUS_VALUES:
        return _redirect(audit.id, "Unknown status.")
    if status_value == AuditStatus.CLOSED.value:
        if Permission.AUDIT_CLOSE.value not in getattr(current_user, "granted_permissions", set()):
            return _redirect(audit.id, "You do not have permission to close audits.")
        if audit.open_findings_count > 0:
            return _redirect(audit.id, "Cannot close an audit with open findings.")
    audit.status = status_value
    db.add(audit)
    db.commit()
    return _redirect(audit.id)


# --- checklist items -------------------------------------------------------
@router.post("/admin/audits/{audit_id}/checklist")
async def checklist_add(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
    question: str = Form(...),
    clause: str = Form(""),
):
    audit = _audit_or_404(db, audit_id, current_user)
    if not question.strip():
        return _redirect(audit.id, "Checklist question is required.")
    order = max((i.display_order for i in audit.checklist_items), default=-1) + 1
    db.add(
        AuditChecklistItem(
            organization_id=audit.organization_id,
            audit_id=audit.id,
            question=question.strip(),
            clause=clause.strip() or None,
            result=ChecklistResult.PENDING.value,
            display_order=order,
        )
    )
    db.commit()
    return _redirect(audit.id)


@router.post("/admin/audits/{audit_id}/checklist/{item_id}")
async def checklist_update(
    audit_id: int,
    item_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
    result: str = Form(...),
    notes: str = Form(""),
):
    audit = _audit_or_404(db, audit_id, current_user)
    item = _item_or_404(db, audit, item_id)
    if result in RESULT_VALUES:
        item.result = result
    item.notes = notes or None
    db.add(item)
    db.commit()
    return _redirect(audit.id)


@router.post("/admin/audits/{audit_id}/checklist/{item_id}/delete")
async def checklist_delete(
    audit_id: int,
    item_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
):
    audit = _audit_or_404(db, audit_id, current_user)
    item = _item_or_404(db, audit, item_id)
    db.delete(item)
    db.commit()
    return _redirect(audit.id)


# --- findings --------------------------------------------------------------
@router.post("/admin/audits/{audit_id}/findings")
async def finding_add(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    severity: str = Form(FindingSeverity.OBSERVATION.value),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    checklist_item_id: Optional[str] = Form(None),
):
    audit = _audit_or_404(db, audit_id, current_user)
    if len(title.strip()) < 3:
        return _redirect(audit.id, "Finding title must be at least 3 characters.")
    item_id = _to_int(checklist_item_id)
    if item_id is not None:
        _item_or_404(db, audit, item_id)
    db.add(
        AuditFinding(
            organization_id=audit.organization_id,
            audit_id=audit.id,
            checklist_item_id=item_id,
            title=title.strip(),
            description=description or None,
            severity=severity if severity in SEVERITY_VALUES else FindingSeverity.OBSERVATION.value,
            status=FindingStatus.OPEN.value,
            owner_id=_to_int(owner_id),
            due_date=_to_date(due_date),
            created_by=current_user.id,
        )
    )
    db.commit()
    return _redirect(audit.id)


@router.post("/admin/audits/{audit_id}/findings/{finding_id}")
async def finding_update(
    audit_id: int,
    finding_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
    severity: str = Form(...),
    status_value: str = Form(..., alias="status"),
    owner_id: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    capa_id: Optional[str] = Form(None),
    reason: str = Form(""),
):
    audit = _audit_or_404(db, audit_id, current_user)
    finding = _finding_or_404(db, audit, finding_id)

    resolved_capa = _to_int(capa_id)
    if resolved_capa is not None:
        capa = (
            db.query(Capa)
            .filter(
                Capa.id == resolved_capa,
                Capa.organization_id == audit.organization_id,
                Capa.is_active.is_(True),
            )
            .first()
        )
        if capa is None:
            return _redirect(audit.id, "Linked CAPA not found in your organization.")

    if reason.strip():
        set_audit_reason(db, reason.strip())
    if severity in SEVERITY_VALUES:
        finding.severity = severity
    if status_value in FINDING_STATUS_VALUES:
        finding.status = status_value
    finding.owner_id = _to_int(owner_id)
    finding.due_date = _to_date(due_date)
    finding.capa_id = resolved_capa
    db.add(finding)
    db.commit()
    return _redirect(audit.id)


@router.post("/admin/audits/{audit_id}/findings/{finding_id}/delete")
async def finding_delete(
    audit_id: int,
    finding_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
):
    audit = _audit_or_404(db, audit_id, current_user)
    finding = _finding_or_404(db, audit, finding_id)
    db.delete(finding)
    db.commit()
    return _redirect(audit.id)


# --- delete ----------------------------------------------------------------
@router.post("/admin/audits/{audit_id}/delete")
async def audit_delete(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_DELETE)),
    db: Session = Depends(get_db),
):
    audit = _audit_or_404(db, audit_id, current_user)
    audit.is_active = False
    db.add(audit)
    db.commit()
    return RedirectResponse("/admin/audits", status_code=status.HTTP_303_SEE_OTHER)
