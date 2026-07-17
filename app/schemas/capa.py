"""Pydantic schemas for CAPA requests and responses."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.capa import CapaStatus, VerificationOutcome

__all__ = [
    "CapaStatus",
    "VerificationOutcome",
    "CapaCreate",
    "CapaUpdate",
    "CapaStatusUpdate",
    "CapaReopen",
    "CapaCancel",
    "CapaVerify",
    "CapaResponse",
]


class CapaCreate(BaseModel):
    """Schema for opening a CAPA."""

    title: str = Field(..., min_length=3, max_length=255)
    initiating_cause: Optional[str] = Field(default=None, max_length=5000)
    containment_actions: Optional[str] = Field(default=None, max_length=5000)
    root_cause: Optional[str] = Field(default=None, max_length=5000)
    root_cause_category: Optional[str] = Field(default=None, max_length=100)
    rca_method: Optional[str] = Field(default=None, max_length=50)
    corrective_action: Optional[str] = Field(default=None, max_length=5000)
    preventive_action: Optional[str] = Field(default=None, max_length=5000)
    owner_id: Optional[int] = None
    due_date: Optional[date] = None
    event_ids: List[int] = Field(default_factory=list)


class CapaUpdate(BaseModel):
    """Partial update schema for a CAPA. Status changes go through the
    dedicated /status, /verify, /reopen, and /cancel endpoints."""

    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    initiating_cause: Optional[str] = Field(default=None, max_length=5000)
    containment_actions: Optional[str] = Field(default=None, max_length=5000)
    root_cause: Optional[str] = Field(default=None, max_length=5000)
    root_cause_category: Optional[str] = Field(default=None, max_length=100)
    rca_method: Optional[str] = Field(default=None, max_length=50)
    corrective_action: Optional[str] = Field(default=None, max_length=5000)
    preventive_action: Optional[str] = Field(default=None, max_length=5000)
    owner_id: Optional[int] = None
    due_date: Optional[date] = None
    event_ids: Optional[List[int]] = None


class CapaStatusUpdate(BaseModel):
    """Schema for advancing CAPA status (non-terminal transitions only)."""

    status: CapaStatus


class CapaReopen(BaseModel):
    """Schema for reopening a Closed or Failed_Effectiveness CAPA; a reason is mandatory."""

    reason: str = Field(..., min_length=1, max_length=2000)


class CapaCancel(BaseModel):
    """Schema for cancelling a CAPA; a reason is mandatory."""

    reason: str = Field(..., min_length=1, max_length=2000)


class CapaVerify(BaseModel):
    """Effectiveness verification of a CAPA."""

    outcome: VerificationOutcome
    verification_date: Optional[date] = None
    reason: Optional[str] = Field(default=None, max_length=2000)


class CapaResponse(BaseModel):
    """Schema for CAPA response data."""

    id: int
    organization_id: int
    title: str
    status: CapaStatus
    initiating_cause: Optional[str]
    containment_actions: Optional[str]
    root_cause: Optional[str]
    root_cause_category: Optional[str]
    rca_method: Optional[str]
    corrective_action: Optional[str]
    preventive_action: Optional[str]
    owner_id: Optional[int]
    due_date: Optional[date]
    verification_date: Optional[date]
    verification_outcome: Optional[str]
    verified_by: Optional[int]
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    event_ids: List[int] = Field(default_factory=list)

    class Config:
        from_attributes = True
