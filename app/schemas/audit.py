"""Pydantic schemas for Audit Management requests and responses."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.audit import (
    AuditStatus,
    AuditType,
    ChecklistResult,
    FindingSeverity,
    FindingStatus,
)

__all__ = [
    "AuditType",
    "AuditStatus",
    "ChecklistResult",
    "FindingSeverity",
    "FindingStatus",
    "AuditCreate",
    "AuditUpdate",
    "AuditResponse",
    "ChecklistItemCreate",
    "ChecklistItemUpdate",
    "ChecklistItemResponse",
    "FindingCreate",
    "FindingUpdate",
    "FindingResponse",
]


# --- Audit -----------------------------------------------------------------
class AuditCreate(BaseModel):
    """Schema for planning an audit."""

    reference: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=3, max_length=255)
    audit_type: AuditType = AuditType.INTERNAL
    scope: Optional[str] = Field(default=None, max_length=5000)
    standard: Optional[str] = Field(default=None, max_length=255)
    lead_auditor_id: Optional[int] = None
    auditee: Optional[str] = Field(default=None, max_length=255)
    planned_date: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class AuditUpdate(BaseModel):
    """Partial update schema for an audit."""

    reference: Optional[str] = Field(default=None, min_length=1, max_length=50)
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    audit_type: Optional[AuditType] = None
    status: Optional[AuditStatus] = None
    scope: Optional[str] = Field(default=None, max_length=5000)
    standard: Optional[str] = Field(default=None, max_length=255)
    lead_auditor_id: Optional[int] = None
    auditee: Optional[str] = Field(default=None, max_length=255)
    planned_date: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    summary: Optional[str] = Field(default=None, max_length=5000)


class ChecklistItemResponse(BaseModel):
    id: int
    audit_id: int
    clause: Optional[str]
    question: str
    result: ChecklistResult
    notes: Optional[str]
    display_order: int

    class Config:
        from_attributes = True


class FindingResponse(BaseModel):
    id: int
    audit_id: int
    checklist_item_id: Optional[int]
    title: str
    description: Optional[str]
    severity: FindingSeverity
    status: FindingStatus
    owner_id: Optional[int]
    due_date: Optional[date]
    capa_id: Optional[int]
    created_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AuditResponse(BaseModel):
    """Schema for audit response data."""

    id: int
    organization_id: int
    reference: str
    title: str
    audit_type: AuditType
    status: AuditStatus
    scope: Optional[str]
    standard: Optional[str]
    lead_auditor_id: Optional[int]
    auditee: Optional[str]
    planned_date: Optional[date]
    start_date: Optional[date]
    end_date: Optional[date]
    summary: Optional[str]
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    checklist_items: List[ChecklistItemResponse] = Field(default_factory=list)
    findings: List[FindingResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


# --- Checklist items -------------------------------------------------------
class ChecklistItemCreate(BaseModel):
    """Schema for adding a checklist item to an audit."""

    question: str = Field(..., min_length=1, max_length=5000)
    clause: Optional[str] = Field(default=None, max_length=100)
    result: ChecklistResult = ChecklistResult.PENDING
    notes: Optional[str] = Field(default=None, max_length=5000)


class ChecklistItemUpdate(BaseModel):
    """Partial update schema for a checklist item."""

    question: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    clause: Optional[str] = Field(default=None, max_length=100)
    result: Optional[ChecklistResult] = None
    notes: Optional[str] = Field(default=None, max_length=5000)


# --- Findings --------------------------------------------------------------
class FindingCreate(BaseModel):
    """Schema for raising a finding."""

    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    severity: FindingSeverity = FindingSeverity.OBSERVATION
    owner_id: Optional[int] = None
    due_date: Optional[date] = None
    checklist_item_id: Optional[int] = None


class FindingUpdate(BaseModel):
    """Partial update schema for a finding."""

    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    severity: Optional[FindingSeverity] = None
    status: Optional[FindingStatus] = None
    owner_id: Optional[int] = None
    due_date: Optional[date] = None
    capa_id: Optional[int] = None
