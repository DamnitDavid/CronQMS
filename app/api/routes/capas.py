"""CAPA (Corrective And Preventive Action) endpoints."""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import set_audit_reason
from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import Capa, CapaStatus, Event, User, VerificationOutcome
from app.schemas.capa import CapaCreate, CapaResponse, CapaUpdate, CapaVerify

router = APIRouter(prefix="/api/capas", tags=["CAPA"])

# CAPA fields that are plain scalars settable straight from the update payload.
_SCALAR_FIELDS = {
    "title",
    "containment_actions",
    "root_cause",
    "root_cause_category",
    "rca_method",
    "corrective_action",
    "preventive_action",
    "owner_id",
    "due_date",
}


def _require_organization(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_capa_in_org(db: Session, capa_id: int, current_user: User) -> Capa:
    capa = (
        db.query(Capa)
        .filter(Capa.id == capa_id, Capa.is_active.is_(True))
        .first()
    )
    if not capa or capa.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CAPA not found")
    return capa


def _resolve_events(db: Session, event_ids: List[int], organization_id: int) -> List[Event]:
    """Load events by id, requiring all to exist within the organization."""
    if not event_ids:
        return []
    events = (
        db.query(Event)
        .filter(
            Event.id.in_(event_ids),
            Event.organization_id == organization_id,
            Event.is_active.is_(True),
        )
        .all()
    )
    found = {event.id for event in events}
    missing = set(event_ids) - found
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Events not found in your organization: {sorted(missing)}",
        )
    return events


@router.post("/", response_model=CapaResponse, status_code=status.HTTP_201_CREATED)
async def create_capa(
    capa_data: CapaCreate,
    current_user: User = Depends(require_permission(Permission.CAPA_CREATE)),
    db: Session = Depends(get_db),
) -> Capa:
    organization_id = _require_organization(current_user)
    capa = Capa(
        organization_id=organization_id,
        title=capa_data.title,
        status=CapaStatus.OPEN.value,
        containment_actions=capa_data.containment_actions,
        root_cause=capa_data.root_cause,
        root_cause_category=capa_data.root_cause_category,
        rca_method=capa_data.rca_method,
        corrective_action=capa_data.corrective_action,
        preventive_action=capa_data.preventive_action,
        owner_id=capa_data.owner_id,
        due_date=capa_data.due_date,
        verification_outcome=VerificationOutcome.PENDING.value,
        created_by=current_user.id,
    )
    capa.events = _resolve_events(db, capa_data.event_ids, organization_id)
    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa


@router.get("/", response_model=list[CapaResponse])
async def list_capas(
    current_user: User = Depends(require_permission(Permission.CAPA_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[CapaStatus] = Query(None, alias="status"),
    owner_id: Optional[int] = Query(None),
) -> list[Capa]:
    query = db.query(Capa).filter(
        Capa.organization_id == current_user.organization_id,
        Capa.is_active.is_(True),
    )
    if status_filter:
        query = query.filter(Capa.status == status_filter.value)
    if owner_id is not None:
        query = query.filter(Capa.owner_id == owner_id)
    return (
        query.order_by(Capa.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/{capa_id}", response_model=CapaResponse)
async def get_capa(
    capa_id: int,
    current_user: User = Depends(require_permission(Permission.CAPA_READ)),
    db: Session = Depends(get_db),
) -> Capa:
    return _get_capa_in_org(db, capa_id, current_user)


@router.put("/{capa_id}", response_model=CapaResponse)
async def update_capa(
    capa_id: int,
    capa_data: CapaUpdate,
    current_user: User = Depends(require_permission(Permission.CAPA_UPDATE)),
    db: Session = Depends(get_db),
) -> Capa:
    capa = _get_capa_in_org(db, capa_id, current_user)
    update_data = capa_data.model_dump(exclude_unset=True)

    if "event_ids" in update_data:
        capa.events = _resolve_events(
            db, update_data.pop("event_ids") or [], capa.organization_id
        )
    if "status" in update_data:
        capa.status = update_data.pop("status").value

    for key, value in update_data.items():
        if key in _SCALAR_FIELDS:
            setattr(capa, key, value)

    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa


@router.post("/{capa_id}/verify", response_model=CapaResponse)
async def verify_capa(
    capa_id: int,
    verification: CapaVerify,
    current_user: User = Depends(require_permission(Permission.CAPA_VERIFY)),
    db: Session = Depends(get_db),
) -> Capa:
    """Record an independent effectiveness verification of the CAPA.

    Verification must be performed by someone other than the CAPA owner, so the
    check is independent. An 'Effective' outcome closes the CAPA.
    """
    capa = _get_capa_in_org(db, capa_id, current_user)

    if capa.owner_id is not None and capa.owner_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Effectiveness verification must be independent of the CAPA owner",
        )

    set_audit_reason(db, verification.reason)
    capa.verification_outcome = verification.outcome.value
    capa.verification_date = verification.verification_date or date.today()
    capa.verified_by = current_user.id
    if verification.outcome == VerificationOutcome.EFFECTIVE:
        capa.status = CapaStatus.CLOSED.value
    elif verification.outcome == VerificationOutcome.INEFFECTIVE:
        capa.status = CapaStatus.IN_PROGRESS.value

    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa
