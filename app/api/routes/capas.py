"""CAPA (Corrective And Preventive Action) endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import Capa, CapaStatus, Event, User, VerificationOutcome
from app.schemas.capa import (
    CapaCancel,
    CapaCreate,
    CapaReopen,
    CapaResponse,
    CapaStatusUpdate,
    CapaUpdate,
    CapaVerify,
)
from app.services.capa_workflow import apply_status_transition
from app.services.capa_workflow import cancel as cancel_workflow
from app.services.capa_workflow import reopen as reopen_workflow
from app.services.capa_workflow import verify_effectiveness
from app.services.event_workflow import WorkflowError

router = APIRouter(prefix="/api/capas", tags=["CAPA"])

# CAPA fields that are plain scalars settable straight from the update payload.
_SCALAR_FIELDS = {
    "title",
    "initiating_cause",
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
        status=CapaStatus.DRAFT.value,
        initiating_cause=capa_data.initiating_cause,
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

    for key, value in update_data.items():
        if key in _SCALAR_FIELDS:
            setattr(capa, key, value)

    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa


@router.patch("/{capa_id}/status", response_model=CapaResponse)
async def patch_capa_status(
    capa_id: int,
    status_update: CapaStatusUpdate,
    current_user: User = Depends(require_permission(Permission.CAPA_UPDATE)),
    db: Session = Depends(get_db),
) -> Capa:
    """Advance a CAPA through the non-terminal workflow stages.

    Verifying (close/fail), reopening, and cancelling are privileged actions
    with their own endpoints and are not reachable here.
    """
    capa = _get_capa_in_org(db, capa_id, current_user)
    try:
        apply_status_transition(capa, status_update.status)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
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

    Verification must be performed by someone other than the CAPA owner, and
    the CAPA must be in Effectiveness_Check. An 'Effective' outcome closes the
    CAPA; an 'Ineffective' outcome fails it.
    """
    capa = _get_capa_in_org(db, capa_id, current_user)
    try:
        verify_effectiveness(
            db,
            capa,
            current_user,
            verification.outcome,
            verification.verification_date,
            verification.reason,
        )
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa


@router.post("/{capa_id}/reopen", response_model=CapaResponse)
async def reopen_capa(
    capa_id: int,
    reopen: CapaReopen,
    current_user: User = Depends(require_permission(Permission.CAPA_REOPEN)),
    db: Session = Depends(get_db),
) -> Capa:
    """Reopen a Closed or Failed_Effectiveness CAPA into Investigation.

    Privileged, and audit-logged with a reason.
    """
    capa = _get_capa_in_org(db, capa_id, current_user)
    try:
        reopen_workflow(db, capa, reopen.reason)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa


@router.post("/{capa_id}/cancel", response_model=CapaResponse)
async def cancel_capa(
    capa_id: int,
    cancellation: CapaCancel,
    current_user: User = Depends(require_permission(Permission.CAPA_CANCEL)),
    db: Session = Depends(get_db),
) -> Capa:
    """Cancel a CAPA from any non-terminal state. Audit-logged with a reason."""
    capa = _get_capa_in_org(db, capa_id, current_user)
    try:
        cancel_workflow(db, capa, cancellation.reason)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(capa)
    db.commit()
    db.refresh(capa)
    return capa
