"""Audit Management model.

An :class:`Audit` is a planned internal, external, or supplier audit. It carries
a checklist of :class:`AuditChecklistItem` rows (the questions/clauses evaluated
during the audit) and a set of :class:`AuditFinding` rows (nonconformities and
observations raised). A finding may optionally reference the checklist item that
surfaced it and link to a :class:`~app.models.capa.Capa` that corrects it, so an
audit ties cleanly into the existing CAPA workflow.

The audit's lifecycle (Planned → In_Progress → Completed → Closed) is a plain
status column; closing an audit is gated on its findings being resolved by the
route layer rather than encoded here.
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


class AuditType(str, Enum):
    """Whether the audit looks inward, at a certification body, or at a supplier."""

    INTERNAL = "Internal"
    EXTERNAL = "External"
    SUPPLIER = "Supplier"


class AuditStatus(str, Enum):
    """Lifecycle state of an audit."""

    PLANNED = "Planned"
    IN_PROGRESS = "In_Progress"
    COMPLETED = "Completed"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"


class ChecklistResult(str, Enum):
    """Outcome recorded against a single checklist item."""

    PENDING = "Pending"
    CONFORMS = "Conforms"
    MINOR_NC = "Minor_NC"
    MAJOR_NC = "Major_NC"
    OBSERVATION = "Observation"
    NOT_APPLICABLE = "Not_Applicable"


class FindingSeverity(str, Enum):
    """Classification of a finding raised during an audit."""

    OFI = "OFI"  # Opportunity For Improvement
    OBSERVATION = "Observation"
    MINOR = "Minor"
    MAJOR = "Major"


class FindingStatus(str, Enum):
    """Resolution state of a finding."""

    OPEN = "Open"
    IN_PROGRESS = "In_Progress"
    CLOSED = "Closed"


class Audit(Base):
    """A planned internal/external/supplier audit."""

    __tablename__ = "audits"

    __audit_entity__ = "audit"
    __audit_fields__ = (
        "reference",
        "title",
        "audit_type",
        "status",
        "scope",
        "standard",
        "lead_auditor_id",
        "auditee",
        "planned_date",
        "start_date",
        "end_date",
        "summary",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    reference = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    audit_type = Column(String(20), nullable=False, default=AuditType.INTERNAL.value)
    status = Column(String(20), nullable=False, default=AuditStatus.PLANNED.value)

    scope = Column(Text, nullable=True)
    standard = Column(String(255), nullable=True)  # e.g. "ISO 9001:2015"

    lead_auditor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    auditee = Column(String(255), nullable=True)  # audited area/department/supplier

    planned_date = Column(Date, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    summary = Column(Text, nullable=True)  # conclusion / executive summary

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    lead_auditor = relationship("User", foreign_keys=[lead_auditor_id], lazy="joined")
    checklist_items = relationship(
        "AuditChecklistItem",
        back_populates="audit",
        order_by="AuditChecklistItem.display_order",
        cascade="all, delete-orphan",
    )
    findings = relationship(
        "AuditFinding",
        back_populates="audit",
        order_by="AuditFinding.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def open_findings_count(self) -> int:
        """Findings not yet closed — the gate for closing the audit."""
        return sum(1 for f in self.findings if f.status != FindingStatus.CLOSED.value)

    def __repr__(self) -> str:
        return f"<Audit(id={self.id}, reference={self.reference}, status={self.status})>"


class AuditChecklistItem(Base):
    """A single question/clause evaluated during an audit."""

    __tablename__ = "audit_checklist_items"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    audit_id = Column(
        Integer, ForeignKey("audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause = Column(String(100), nullable=True)  # e.g. "8.5.1"
    question = Column(Text, nullable=False)
    result = Column(String(20), nullable=False, default=ChecklistResult.PENDING.value)
    notes = Column(Text, nullable=True)
    display_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    audit = relationship("Audit", back_populates="checklist_items")

    def __repr__(self) -> str:
        return f"<AuditChecklistItem(id={self.id}, audit_id={self.audit_id}, result={self.result})>"


class AuditFinding(Base):
    """A nonconformity or observation raised during an audit."""

    __tablename__ = "audit_findings"

    __audit_entity__ = "audit_finding"
    __audit_fields__ = (
        "title",
        "description",
        "severity",
        "status",
        "owner_id",
        "due_date",
        "capa_id",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    audit_id = Column(
        Integer, ForeignKey("audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checklist_item_id = Column(
        Integer,
        ForeignKey("audit_checklist_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False, default=FindingSeverity.OBSERVATION.value)
    status = Column(String(20), nullable=False, default=FindingStatus.OPEN.value)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    capa_id = Column(Integer, ForeignKey("capas.id", ondelete="SET NULL"), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    audit = relationship("Audit", back_populates="findings")
    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    checklist_item = relationship("AuditChecklistItem", lazy="joined")
    capa = relationship("Capa", lazy="joined")

    def __repr__(self) -> str:
        return f"<AuditFinding(id={self.id}, audit_id={self.audit_id}, severity={self.severity})>"
