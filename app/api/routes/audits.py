"""Audit Management endpoints — internal/external/supplier audits with
checklists and findings tracking.

The JSON API under ``/api/audits`` is the machine interface; the browser UI in
``app/api/routes/audit_pages.py`` reuses the same permissions and status rules so
behavior can't drift between the two surfaces.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import (
    Audit,
    AuditChecklistItem,
    AuditFinding,
    AuditStatus,
    Capa,
    FindingStatus,
    User,
)
from app.schemas.audit import (
    AuditCreate,
    AuditResponse,
    AuditUpdate,
    ChecklistItemCreate,
    ChecklistItemResponse,
    ChecklistItemUpdate,
    FindingCreate,
    FindingResponse,
    FindingUpdate,
)

router = APIRouter(prefix="/api/audits", tags=["Audits"])

# Audit fields settable straight from the update payload.
_AUDIT_SCALAR_FIELDS = {
    "reference",
    "title",
    "scope",
    "standard",
    "lead_auditor_id",
    "auditee",
    "planned_date",
    "start_date",
    "end_date",
    "summary",
}

_CHECKLIST_SCALAR_FIELDS = {"question", "clause", "notes"}
_FINDING_SCALAR_FIELDS = {"title", "description", "owner_id", "due_date"}


def _require_organization(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_audit_in_org(db: Session, audit_id: int, current_user: User) -> Audit:
    audit = (
        db.query(Audit)
        .filter(Audit.id == audit_id, Audit.is_active.is_(True))
        .first()
    )
    if not audit or audit.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit not found")
    return audit


def _get_item_in_audit(db: Session, audit: Audit, item_id: int) -> AuditChecklistItem:
    item = (
        db.query(AuditChecklistItem)
        .filter(
            AuditChecklistItem.id == item_id,
            AuditChecklistItem.audit_id == audit.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checklist item not found"
        )
    return item


def _get_finding_in_audit(db: Session, audit: Audit, finding_id: int) -> AuditFinding:
    finding = (
        db.query(AuditFinding)
        .filter(AuditFinding.id == finding_id, AuditFinding.audit_id == audit.id)
        .first()
    )
    if not finding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found")
    return finding


# --- Audit CRUD ------------------------------------------------------------
@router.post("/", response_model=AuditResponse, status_code=status.HTTP_201_CREATED)
async def create_audit(
    payload: AuditCreate,
    current_user: User = Depends(require_permission(Permission.AUDIT_CREATE)),
    db: Session = Depends(get_db),
) -> Audit:
    organization_id = _require_organization(current_user)
    audit = Audit(
        organization_id=organization_id,
        reference=payload.reference,
        title=payload.title,
        audit_type=payload.audit_type.value,
        status=AuditStatus.PLANNED.value,
        scope=payload.scope,
        standard=payload.standard,
        lead_auditor_id=payload.lead_auditor_id,
        auditee=payload.auditee,
        planned_date=payload.planned_date,
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_by=current_user.id,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


@router.get("/", response_model=List[AuditResponse])
async def list_audits(
    current_user: User = Depends(require_permission(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    audit_type: Optional[str] = Query(None),
    lead_auditor_id: Optional[int] = Query(None),
) -> List[Audit]:
    query = db.query(Audit).filter(
        Audit.organization_id == current_user.organization_id,
        Audit.is_active.is_(True),
    )
    if status_filter:
        query = query.filter(Audit.status == status_filter.value)
    if audit_type:
        query = query.filter(Audit.audit_type == audit_type)
    if lead_auditor_id is not None:
        query = query.filter(Audit.lead_auditor_id == lead_auditor_id)
    return (
        query.order_by(Audit.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/{audit_id}", response_model=AuditResponse)
async def get_audit(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_READ)),
    db: Session = Depends(get_db),
) -> Audit:
    return _get_audit_in_org(db, audit_id, current_user)


@router.put("/{audit_id}", response_model=AuditResponse)
async def update_audit(
    audit_id: int,
    payload: AuditUpdate,
    current_user: User = Depends(require_permission(Permission.AUDIT_UPDATE)),
    db: Session = Depends(get_db),
) -> Audit:
    audit = _get_audit_in_org(db, audit_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)

    if "audit_type" in update_data and update_data["audit_type"] is not None:
        audit.audit_type = update_data.pop("audit_type").value
    if "status" in update_data and update_data["status"] is not None:
        _apply_status(audit, update_data.pop("status"))

    for key, value in update_data.items():
        if key in _AUDIT_SCALAR_FIELDS:
            setattr(audit, key, value)

    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def _apply_status(audit: Audit, new_status: AuditStatus) -> None:
    """Apply an audit status change, gating closure on findings being resolved."""
    if new_status == AuditStatus.CLOSED and audit.open_findings_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot close an audit with open findings",
        )
    audit.status = new_status.value


@router.delete("/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    audit_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    audit = _get_audit_in_org(db, audit_id, current_user)
    audit.is_active = False
    db.add(audit)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Checklist items -------------------------------------------------------
@router.post(
    "/{audit_id}/checklist",
    response_model=ChecklistItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_checklist_item(
    audit_id: int,
    payload: ChecklistItemCreate,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> AuditChecklistItem:
    audit = _get_audit_in_org(db, audit_id, current_user)
    order = max((i.display_order for i in audit.checklist_items), default=-1) + 1
    item = AuditChecklistItem(
        organization_id=audit.organization_id,
        audit_id=audit.id,
        question=payload.question,
        clause=payload.clause,
        result=payload.result.value,
        notes=payload.notes,
        display_order=order,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put(
    "/{audit_id}/checklist/{item_id}", response_model=ChecklistItemResponse
)
async def update_checklist_item(
    audit_id: int,
    item_id: int,
    payload: ChecklistItemUpdate,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> AuditChecklistItem:
    audit = _get_audit_in_org(db, audit_id, current_user)
    item = _get_item_in_audit(db, audit, item_id)
    update_data = payload.model_dump(exclude_unset=True)
    if "result" in update_data and update_data["result"] is not None:
        item.result = update_data.pop("result").value
    for key, value in update_data.items():
        if key in _CHECKLIST_SCALAR_FIELDS:
            setattr(item, key, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete(
    "/{audit_id}/checklist/{item_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_checklist_item(
    audit_id: int,
    item_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> Response:
    audit = _get_audit_in_org(db, audit_id, current_user)
    item = _get_item_in_audit(db, audit, item_id)
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Findings --------------------------------------------------------------
@router.post(
    "/{audit_id}/findings",
    response_model=FindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_finding(
    audit_id: int,
    payload: FindingCreate,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> AuditFinding:
    audit = _get_audit_in_org(db, audit_id, current_user)
    if payload.checklist_item_id is not None:
        # Ensure the referenced item belongs to this audit.
        _get_item_in_audit(db, audit, payload.checklist_item_id)
    finding = AuditFinding(
        organization_id=audit.organization_id,
        audit_id=audit.id,
        checklist_item_id=payload.checklist_item_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity.value,
        status=FindingStatus.OPEN.value,
        owner_id=payload.owner_id,
        due_date=payload.due_date,
        created_by=current_user.id,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


@router.put("/{audit_id}/findings/{finding_id}", response_model=FindingResponse)
async def update_finding(
    audit_id: int,
    finding_id: int,
    payload: FindingUpdate,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> AuditFinding:
    audit = _get_audit_in_org(db, audit_id, current_user)
    finding = _get_finding_in_audit(db, audit, finding_id)
    update_data = payload.model_dump(exclude_unset=True)

    if "severity" in update_data and update_data["severity"] is not None:
        finding.severity = update_data.pop("severity").value
    if "status" in update_data and update_data["status"] is not None:
        finding.status = update_data.pop("status").value
    if "capa_id" in update_data:
        finding.capa_id = _resolve_capa(db, update_data.pop("capa_id"), audit)

    for key, value in update_data.items():
        if key in _FINDING_SCALAR_FIELDS:
            setattr(finding, key, value)

    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


def _resolve_capa(db: Session, capa_id: Optional[int], audit: Audit) -> Optional[int]:
    """Validate that ``capa_id`` (if set) is a CAPA in the same organization."""
    if capa_id is None:
        return None
    capa = (
        db.query(Capa)
        .filter(
            Capa.id == capa_id,
            Capa.organization_id == audit.organization_id,
            Capa.is_active.is_(True),
        )
        .first()
    )
    if capa is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CAPA not found in your organization",
        )
    return capa.id


@router.delete(
    "/{audit_id}/findings/{finding_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_finding(
    audit_id: int,
    finding_id: int,
    current_user: User = Depends(require_permission(Permission.AUDIT_CONDUCT)),
    db: Session = Depends(get_db),
) -> Response:
    audit = _get_audit_in_org(db, audit_id, current_user)
    finding = _get_finding_in_audit(db, audit, finding_id)
    db.delete(finding)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
