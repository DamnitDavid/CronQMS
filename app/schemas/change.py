"""Pydantic schemas for Change Control requests and responses."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.change import (
    ActionStatus,
    ChangeStatus,
    ChangeType,
    ImpactArea,
    ImpactLevel,
    RiskLevel,
)

__all__ = [
    "ChangeType",
    "ChangeStatus",
    "RiskLevel",
    "ImpactArea",
    "ImpactLevel",
    "ActionStatus",
    "ChangeRequestCreate",
    "ChangeRequestUpdate",
    "ChangeRequestResponse",
    "ImpactCreate",
    "ImpactUpdate",
    "ImpactResponse",
    "ActionCreate",
    "ActionUpdate",
    "ActionResponse",
]


# --- Change request --------------------------------------------------------
class ChangeRequestCreate(BaseModel):
    """Schema for raising a change request."""

    reference: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=3, max_length=255)
    change_type: ChangeType = ChangeType.PROCESS
    description: Optional[str] = Field(default=None, max_length=5000)
    reason: Optional[str] = Field(default=None, max_length=5000)
    affected_area: Optional[str] = Field(default=None, max_length=255)
    risk_level: RiskLevel = RiskLevel.LOW
    owner_id: Optional[int] = None
    target_date: Optional[date] = None


class ChangeRequestUpdate(BaseModel):
    """Partial update schema for a change request."""

    reference: Optional[str] = Field(default=None, min_length=1, max_length=50)
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    change_type: Optional[ChangeType] = None
    status: Optional[ChangeStatus] = None
    description: Optional[str] = Field(default=None, max_length=5000)
    reason: Optional[str] = Field(default=None, max_length=5000)
    affected_area: Optional[str] = Field(default=None, max_length=255)
    risk_level: Optional[RiskLevel] = None
    owner_id: Optional[int] = None
    target_date: Optional[date] = None
    implementation_date: Optional[date] = None
    summary: Optional[str] = Field(default=None, max_length=5000)


class ImpactResponse(BaseModel):
    id: int
    change_id: int
    area: ImpactArea
    impact_level: ImpactLevel
    description: Optional[str]
    mitigation: Optional[str]
    display_order: int

    class Config:
        from_attributes = True


class ActionResponse(BaseModel):
    id: int
    change_id: int
    title: str
    description: Optional[str]
    status: ActionStatus
    owner_id: Optional[int]
    due_date: Optional[date]
    capa_id: Optional[int]
    created_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChangeRequestResponse(BaseModel):
    """Schema for change request response data."""

    id: int
    organization_id: int
    reference: str
    title: str
    change_type: ChangeType
    status: ChangeStatus
    description: Optional[str]
    reason: Optional[str]
    affected_area: Optional[str]
    risk_level: RiskLevel
    owner_id: Optional[int]
    target_date: Optional[date]
    implementation_date: Optional[date]
    summary: Optional[str]
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    impacts: List[ImpactResponse] = Field(default_factory=list)
    actions: List[ActionResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


# --- Impact assessment rows ------------------------------------------------
class ImpactCreate(BaseModel):
    """Schema for adding an impact-assessment row to a change."""

    area: ImpactArea = ImpactArea.QUALITY
    impact_level: ImpactLevel = ImpactLevel.NONE
    description: Optional[str] = Field(default=None, max_length=5000)
    mitigation: Optional[str] = Field(default=None, max_length=5000)


class ImpactUpdate(BaseModel):
    """Partial update schema for an impact-assessment row."""

    area: Optional[ImpactArea] = None
    impact_level: Optional[ImpactLevel] = None
    description: Optional[str] = Field(default=None, max_length=5000)
    mitigation: Optional[str] = Field(default=None, max_length=5000)


# --- Implementation actions ------------------------------------------------
class ActionCreate(BaseModel):
    """Schema for raising an implementation action."""

    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    owner_id: Optional[int] = None
    due_date: Optional[date] = None


class ActionUpdate(BaseModel):
    """Partial update schema for an implementation action."""

    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[ActionStatus] = None
    owner_id: Optional[int] = None
    due_date: Optional[date] = None
    capa_id: Optional[int] = None
