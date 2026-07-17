"""Pydantic schemas for Document Control requests and responses."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.document import DocumentCategory, DocumentVersionStatus

__all__ = [
    "DocumentCategory",
    "DocumentVersionStatus",
    "DocumentCreate",
    "DocumentUpdate",
    "DocumentReject",
    "DocumentObsolete",
    "DocumentVersionResponse",
    "DocumentResponse",
]


class DocumentCreate(BaseModel):
    """Schema for registering a new controlled document."""

    document_number: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=3, max_length=255)
    category: DocumentCategory = DocumentCategory.SOP
    description: Optional[str] = Field(default=None, max_length=5000)
    owner_id: Optional[int] = None
    owner_group_id: Optional[int] = None
    review_period_months: Optional[int] = Field(default=None, ge=1, le=120)
    retention_period_months: Optional[int] = Field(default=None, ge=1, le=1200)


class DocumentUpdate(BaseModel):
    """Partial update of a document's metadata (not its versions)."""

    document_number: Optional[str] = Field(default=None, min_length=1, max_length=50)
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    category: Optional[DocumentCategory] = None
    description: Optional[str] = Field(default=None, max_length=5000)
    owner_id: Optional[int] = None
    owner_group_id: Optional[int] = None
    review_period_months: Optional[int] = Field(default=None, ge=1, le=120)
    retention_period_months: Optional[int] = Field(default=None, ge=1, le=1200)


class DocumentReject(BaseModel):
    """Reject a version back to Draft, with a required reason."""

    reason: str = Field(..., min_length=1, max_length=2000)


class DocumentObsolete(BaseModel):
    """Manually obsolete the effective version, with a required reason."""

    reason: str = Field(..., min_length=1, max_length=2000)


class DocumentVersionResponse(BaseModel):
    """Schema for a single document revision."""

    id: int
    organization_id: int
    document_id: int
    version_number: int
    status: DocumentVersionStatus
    change_summary: Optional[str]
    filename: str
    content_type: Optional[str]
    size_bytes: int
    checksum: str
    author_id: int
    reviewed_by: Optional[int]
    reviewed_at: Optional[datetime]
    approved_by: Optional[int]
    approved_at: Optional[datetime]
    effective_date: Optional[date]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    """Schema for document response data, including its versions."""

    id: int
    organization_id: int
    document_number: str
    title: str
    category: DocumentCategory
    description: Optional[str]
    status: DocumentVersionStatus
    owner_id: Optional[int]
    owner_group_id: Optional[int]
    review_period_months: Optional[int]
    next_review_date: Optional[date]
    retention_period_months: Optional[int]
    retention_until: Optional[date]
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    versions: List[DocumentVersionResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
