"""Controlled document model.

A :class:`Document` is the logical controlled record (number, title, category,
owner, retention policy). Its content is versioned: each revision is a
:class:`DocumentVersion` carrying the file blob metadata and its own workflow
status. Version control means exactly one version is ``Effective`` at a time —
approving a new revision supersedes the prior effective one (see
``app/services/document_workflow.py``).

The "current" version is derived (the one whose status is ``Effective``) rather
than stored as a pointer, to avoid a circular foreign key between the two
tables.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class DocumentCategory(str, Enum):
    """The kind of controlled document."""

    SOP = "SOP"
    POLICY = "Policy"
    WORK_INSTRUCTION = "Work_Instruction"
    FORM = "Form"
    SPECIFICATION = "Specification"
    RECORD = "Record"
    OTHER = "Other"


class DocumentVersionStatus(str, Enum):
    """Lifecycle state of a single document revision."""

    DRAFT = "Draft"
    IN_REVIEW = "In_Review"
    PENDING_APPROVAL = "Pending_Approval"
    EFFECTIVE = "Effective"
    OBSOLETE = "Obsolete"
    REJECTED = "Rejected"


class Document(Base):
    """A controlled document — the container for its versions."""

    __tablename__ = "documents"

    __audit_entity__ = "document"
    __audit_fields__ = (
        "document_number",
        "title",
        "category",
        "owner_id",
        "owner_group_id",
        "review_period_months",
        "next_review_date",
        "retention_period_months",
        "retention_until",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    document_number = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(30), nullable=False, default=DocumentCategory.SOP.value)
    description = Column(Text, nullable=True)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Optional owning team, reusing the shared assignee-group primitive.
    owner_group_id = Column(Integer, ForeignKey("assignee_groups.id"), nullable=True, index=True)

    # Retention & periodic-review policy.
    review_period_months = Column(Integer, nullable=True)
    next_review_date = Column(Date, nullable=True)
    retention_period_months = Column(Integer, nullable=True)
    retention_until = Column(Date, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    owner_group = relationship("AssigneeGroup", foreign_keys=[owner_group_id], lazy="joined")
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        order_by="DocumentVersion.version_number",
        cascade="all, delete-orphan",
    )

    @property
    def current_version(self) -> "DocumentVersion | None":
        """The effective version, if any (at most one at a time)."""
        for version in self.versions:
            if version.status == DocumentVersionStatus.EFFECTIVE.value:
                return version
        return None

    @property
    def latest_version(self) -> "DocumentVersion | None":
        """The highest-numbered version, whatever its status."""
        return self.versions[-1] if self.versions else None

    @property
    def status(self) -> str:
        """Document-level status, derived from its current/latest version."""
        current = self.current_version
        if current is not None:
            return current.status
        latest = self.latest_version
        return latest.status if latest is not None else DocumentVersionStatus.DRAFT.value

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, number={self.document_number}, title={self.title})>"


class DocumentVersion(Base):
    """A single revision of a controlled document."""

    __tablename__ = "document_versions"

    __audit_entity__ = "document_version"
    __audit_fields__ = (
        "version_number",
        "status",
        "change_summary",
        "reviewed_by",
        "approved_by",
        "effective_date",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default=DocumentVersionStatus.DRAFT.value)
    change_summary = Column(Text, nullable=True)

    # File blob metadata (mirrors app/models/attachment.py; blob in storage).
    filename = Column(String(255), nullable=False)
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=False)  # SHA-256 hex digest
    storage_key = Column(String(255), nullable=False, unique=True)

    # Authorship and the two sign-off stages (segregation of duties).
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    effective_date = Column(Date, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    document = relationship("Document", back_populates="versions")
    author = relationship("User", foreign_keys=[author_id], lazy="joined")
    reviewer = relationship("User", foreign_keys=[reviewed_by], lazy="joined")
    approver = relationship("User", foreign_keys=[approved_by], lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<DocumentVersion(id={self.id}, document_id={self.document_id}, "
            f"v={self.version_number}, status={self.status})>"
        )
