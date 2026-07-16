"""Change Control endpoints — process/product changes with an impact assessment
and implementation actions.

The JSON API under ``/api/changes`` is the machine interface; the browser UI in
``app/api/routes/change_pages.py`` reuses the same permissions and status rules so
behavior can't drift between the two surfaces.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission, user_has_permission
from app.database import get_db
from app.models import (
    ActionStatus,
    Capa,
    ChangeAction,
    ChangeImpact,
    ChangeRequest,
    ChangeStatus,
    User,
)
from app.schemas.change import (
    ActionCreate,
    ActionResponse,
    ActionUpdate,
    ChangeRequestCreate,
    ChangeRequestResponse,
    ChangeRequestUpdate,
    ImpactCreate,
    ImpactResponse,
    ImpactUpdate,
)

router = APIRouter(prefix="/api/changes", tags=["Change Control"])

# Change fields settable straight from the update payload.
_CHANGE_SCALAR_FIELDS = {
    "reference",
    "title",
    "description",
    "reason",
    "affected_area",
    "owner_id",
    "target_date",
    "implementation_date",
    "summary",
}

_IMPACT_SCALAR_FIELDS = {"description", "mitigation"}
_ACTION_SCALAR_FIELDS = {"title", "description", "owner_id", "due_date"}

# Statuses that represent an approval decision — gated on change:approve.
_APPROVAL_STATUSES = {ChangeStatus.APPROVED, ChangeStatus.REJECTED}


def _require_organization(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_change_in_org(db: Session, change_id: int, current_user: User) -> ChangeRequest:
    change = (
        db.query(ChangeRequest)
        .filter(ChangeRequest.id == change_id, ChangeRequest.is_active.is_(True))
        .first()
    )
    if not change or change.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found")
    return change


def _get_impact_in_change(db: Session, change: ChangeRequest, impact_id: int) -> ChangeImpact:
    impact = (
        db.query(ChangeImpact)
        .filter(ChangeImpact.id == impact_id, ChangeImpact.change_id == change.id)
        .first()
    )
    if not impact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Impact assessment not found"
        )
    return impact


def _get_action_in_change(db: Session, change: ChangeRequest, action_id: int) -> ChangeAction:
    action = (
        db.query(ChangeAction)
        .filter(ChangeAction.id == action_id, ChangeAction.change_id == change.id)
        .first()
    )
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return action


# --- Change request CRUD ---------------------------------------------------
@router.post("/", response_model=ChangeRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_change(
    payload: ChangeRequestCreate,
    current_user: User = Depends(require_permission(Permission.CHANGE_CREATE)),
    db: Session = Depends(get_db),
) -> ChangeRequest:
    organization_id = _require_organization(current_user)
    change = ChangeRequest(
        organization_id=organization_id,
        reference=payload.reference,
        title=payload.title,
        change_type=payload.change_type.value,
        status=ChangeStatus.DRAFT.value,
        description=payload.description,
        reason=payload.reason,
        affected_area=payload.affected_area,
        risk_level=payload.risk_level.value,
        owner_id=payload.owner_id,
        target_date=payload.target_date,
        created_by=current_user.id,
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return change


@router.get("/", response_model=List[ChangeRequestResponse])
async def list_changes(
    current_user: User = Depends(require_permission(Permission.CHANGE_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[ChangeStatus] = Query(None, alias="status"),
    change_type: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
) -> List[ChangeRequest]:
    query = db.query(ChangeRequest).filter(
        ChangeRequest.organization_id == current_user.organization_id,
        ChangeRequest.is_active.is_(True),
    )
    if status_filter:
        query = query.filter(ChangeRequest.status == status_filter.value)
    if change_type:
        query = query.filter(ChangeRequest.change_type == change_type)
    if owner_id is not None:
        query = query.filter(ChangeRequest.owner_id == owner_id)
    return (
        query.order_by(ChangeRequest.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/{change_id}", response_model=ChangeRequestResponse)
async def get_change(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_READ)),
    db: Session = Depends(get_db),
) -> ChangeRequest:
    return _get_change_in_org(db, change_id, current_user)


@router.put("/{change_id}", response_model=ChangeRequestResponse)
async def update_change(
    change_id: int,
    payload: ChangeRequestUpdate,
    current_user: User = Depends(require_permission(Permission.CHANGE_UPDATE)),
    db: Session = Depends(get_db),
) -> ChangeRequest:
    change = _get_change_in_org(db, change_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)

    if "change_type" in update_data and update_data["change_type"] is not None:
        change.change_type = update_data.pop("change_type").value
    if "risk_level" in update_data and update_data["risk_level"] is not None:
        change.risk_level = update_data.pop("risk_level").value
    if "status" in update_data and update_data["status"] is not None:
        _apply_status(db, change, update_data.pop("status"), current_user)

    for key, value in update_data.items():
        if key in _CHANGE_SCALAR_FIELDS:
            setattr(change, key, value)

    db.add(change)
    db.commit()
    db.refresh(change)
    return change


def _apply_status(
    db: Session, change: ChangeRequest, new_status: ChangeStatus, current_user: User
) -> None:
    """Apply a status change, gating approval decisions and closure.

    Approving/rejecting a change requires ``change:approve``; closing is blocked
    while any implementation action is still open.
    """
    if new_status in _APPROVAL_STATUSES and not user_has_permission(
        db, current_user, Permission.CHANGE_APPROVE
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to approve or reject changes",
        )
    if new_status == ChangeStatus.CLOSED and change.open_actions_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot close a change with open actions",
        )
    change.status = new_status.value


@router.delete("/{change_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_change(
    change_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    change = _get_change_in_org(db, change_id, current_user)
    change.is_active = False
    db.add(change)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Impact assessment rows ------------------------------------------------
@router.post(
    "/{change_id}/impacts",
    response_model=ImpactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_impact(
    change_id: int,
    payload: ImpactCreate,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> ChangeImpact:
    change = _get_change_in_org(db, change_id, current_user)
    order = max((i.display_order for i in change.impacts), default=-1) + 1
    impact = ChangeImpact(
        organization_id=change.organization_id,
        change_id=change.id,
        area=payload.area.value,
        impact_level=payload.impact_level.value,
        description=payload.description,
        mitigation=payload.mitigation,
        display_order=order,
    )
    db.add(impact)
    db.commit()
    db.refresh(impact)
    return impact


@router.put("/{change_id}/impacts/{impact_id}", response_model=ImpactResponse)
async def update_impact(
    change_id: int,
    impact_id: int,
    payload: ImpactUpdate,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> ChangeImpact:
    change = _get_change_in_org(db, change_id, current_user)
    impact = _get_impact_in_change(db, change, impact_id)
    update_data = payload.model_dump(exclude_unset=True)
    if "area" in update_data and update_data["area"] is not None:
        impact.area = update_data.pop("area").value
    if "impact_level" in update_data and update_data["impact_level"] is not None:
        impact.impact_level = update_data.pop("impact_level").value
    for key, value in update_data.items():
        if key in _IMPACT_SCALAR_FIELDS:
            setattr(impact, key, value)
    db.add(impact)
    db.commit()
    db.refresh(impact)
    return impact


@router.delete(
    "/{change_id}/impacts/{impact_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_impact(
    change_id: int,
    impact_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> Response:
    change = _get_change_in_org(db, change_id, current_user)
    impact = _get_impact_in_change(db, change, impact_id)
    db.delete(impact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Implementation actions ------------------------------------------------
@router.post(
    "/{change_id}/actions",
    response_model=ActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_action(
    change_id: int,
    payload: ActionCreate,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> ChangeAction:
    change = _get_change_in_org(db, change_id, current_user)
    action = ChangeAction(
        organization_id=change.organization_id,
        change_id=change.id,
        title=payload.title,
        description=payload.description,
        status=ActionStatus.OPEN.value,
        owner_id=payload.owner_id,
        due_date=payload.due_date,
        created_by=current_user.id,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


@router.put("/{change_id}/actions/{action_id}", response_model=ActionResponse)
async def update_action(
    change_id: int,
    action_id: int,
    payload: ActionUpdate,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> ChangeAction:
    change = _get_change_in_org(db, change_id, current_user)
    action = _get_action_in_change(db, change, action_id)
    update_data = payload.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] is not None:
        action.status = update_data.pop("status").value
    if "capa_id" in update_data:
        action.capa_id = _resolve_capa(db, update_data.pop("capa_id"), change)

    for key, value in update_data.items():
        if key in _ACTION_SCALAR_FIELDS:
            setattr(action, key, value)

    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _resolve_capa(db: Session, capa_id: Optional[int], change: ChangeRequest) -> Optional[int]:
    """Validate that ``capa_id`` (if set) is a CAPA in the same organization."""
    if capa_id is None:
        return None
    capa = (
        db.query(Capa)
        .filter(
            Capa.id == capa_id,
            Capa.organization_id == change.organization_id,
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
    "/{change_id}/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_action(
    change_id: int,
    action_id: int,
    current_user: User = Depends(require_permission(Permission.CHANGE_ASSESS)),
    db: Session = Depends(get_db),
) -> Response:
    change = _get_change_in_org(db, change_id, current_user)
    action = _get_action_in_change(db, change, action_id)
    db.delete(action)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
