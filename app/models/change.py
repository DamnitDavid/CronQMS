"""Change Control (Management of Change) model.

A :class:`ChangeRequest` is a proposed change to a process, product, document,
piece of equipment, facility, or supplier. It carries a set of
:class:`ChangeImpact` rows — the *impact assessment*, one row per area the change
touches (Quality, Regulatory, Safety, …) with a rated impact level and a
mitigation — and a set of :class:`ChangeAction` rows (the implementation tasks
raised to carry the change out). An action may link to a
:class:`~app.models.capa.Capa` when the work is tracked there, so change control
ties cleanly into the existing CAPA workflow.

The change's lifecycle (Draft → Submitted → Under_Review → Approved →
Implemented → Closed, plus Rejected/Cancelled) is a plain status column. Two
rules are enforced by the route layer rather than encoded here: moving to an
approval decision (Approved/Rejected) requires the ``change:approve`` permission,
and closing a change is gated on its actions being resolved.
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


class ChangeType(str, Enum):
    """What the change is being made to."""

    PROCESS = "Process"
    PRODUCT = "Product"
    EQUIPMENT = "Equipment"
    DOCUMENT = "Document"
    SUPPLIER = "Supplier"
    FACILITY = "Facility"
    OTHER = "Other"


class ChangeStatus(str, Enum):
    """Lifecycle state of a change request."""

    DRAFT = "Draft"
    SUBMITTED = "Submitted"
    UNDER_REVIEW = "Under_Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    IMPLEMENTED = "Implemented"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"


class RiskLevel(str, Enum):
    """Overall assessed risk of the change."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class ImpactArea(str, Enum):
    """The area of the business a single impact-assessment row evaluates."""

    QUALITY = "Quality"
    REGULATORY = "Regulatory"
    SAFETY = "Safety"
    PRODUCT = "Product"
    PROCESS = "Process"
    DOCUMENTATION = "Documentation"
    TRAINING = "Training"
    EQUIPMENT = "Equipment"
    SUPPLIER = "Supplier"
    VALIDATION = "Validation"
    CUSTOMER = "Customer"
    OTHER = "Other"


class ImpactLevel(str, Enum):
    """How strongly the change affects a given impact area."""

    NONE = "None"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class ActionStatus(str, Enum):
    """Resolution state of an implementation action."""

    OPEN = "Open"
    IN_PROGRESS = "In_Progress"
    CLOSED = "Closed"


class ChangeRequest(Base):
    """A proposed process/product change with impact assessment and actions."""

    __tablename__ = "change_requests"

    __audit_entity__ = "change_request"
    __audit_fields__ = (
        "reference",
        "title",
        "change_type",
        "status",
        "description",
        "reason",
        "affected_area",
        "risk_level",
        "owner_id",
        "target_date",
        "implementation_date",
        "summary",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    reference = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    change_type = Column(String(20), nullable=False, default=ChangeType.PROCESS.value)
    status = Column(String(20), nullable=False, default=ChangeStatus.DRAFT.value)

    description = Column(Text, nullable=True)  # what is changing
    reason = Column(Text, nullable=True)  # why / justification
    affected_area = Column(String(255), nullable=True)  # department/product/line affected
    risk_level = Column(String(20), nullable=False, default=RiskLevel.LOW.value)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    target_date = Column(Date, nullable=True)  # planned implementation date
    implementation_date = Column(Date, nullable=True)  # actual implementation date

    summary = Column(Text, nullable=True)  # post-implementation review / outcome

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    impacts = relationship(
        "ChangeImpact",
        back_populates="change",
        order_by="ChangeImpact.display_order",
        cascade="all, delete-orphan",
    )
    actions = relationship(
        "ChangeAction",
        back_populates="change",
        order_by="ChangeAction.created_at",
        cascade="all, delete-orphan",
    )

    @property
    def open_actions_count(self) -> int:
        """Actions not yet closed — the gate for closing the change."""
        return sum(1 for a in self.actions if a.status != ActionStatus.CLOSED.value)

    def __repr__(self) -> str:
        return f"<ChangeRequest(id={self.id}, reference={self.reference}, status={self.status})>"


class ChangeImpact(Base):
    """A single impact-assessment row against a change request."""

    __tablename__ = "change_impacts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    change_id = Column(
        Integer,
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    area = Column(String(30), nullable=False, default=ImpactArea.QUALITY.value)
    impact_level = Column(String(20), nullable=False, default=ImpactLevel.NONE.value)
    description = Column(Text, nullable=True)  # nature of the impact
    mitigation = Column(Text, nullable=True)  # how the impact is controlled
    display_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    change = relationship("ChangeRequest", back_populates="impacts")

    def __repr__(self) -> str:
        return f"<ChangeImpact(id={self.id}, change_id={self.change_id}, area={self.area})>"


class ChangeAction(Base):
    """An implementation task raised to carry out a change request."""

    __tablename__ = "change_actions"

    __audit_entity__ = "change_action"
    __audit_fields__ = (
        "title",
        "description",
        "status",
        "owner_id",
        "due_date",
        "capa_id",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    change_id = Column(
        Integer,
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=ActionStatus.OPEN.value)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)
    capa_id = Column(Integer, ForeignKey("capas.id", ondelete="SET NULL"), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    change = relationship("ChangeRequest", back_populates="actions")
    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    capa = relationship("Capa", lazy="joined")

    def __repr__(self) -> str:
        return f"<ChangeAction(id={self.id}, change_id={self.change_id}, status={self.status})>"
