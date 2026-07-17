"""CAPA (Corrective And Preventive Action) model.

A CAPA is a first-class entity — not an event type — with structured root-cause
analysis, corrective/preventive actions, an owner and due date, and its own
effectiveness verification. It links to one or more events.
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
    Table,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CapaStatus(str, Enum):
    DRAFT = "Draft"
    INVESTIGATION = "Investigation"
    ACTION_PLAN = "Action_Plan"
    IMPLEMENTATION = "Implementation"
    EFFECTIVENESS_CHECK = "Effectiveness_Check"
    CLOSED = "Closed"
    FAILED_EFFECTIVENESS = "Failed_Effectiveness"
    CANCELLED = "Cancelled"


class VerificationOutcome(str, Enum):
    PENDING = "Pending"
    EFFECTIVE = "Effective"
    INEFFECTIVE = "Ineffective"


# Many-to-many: a CAPA can address several events; an event can spawn several
# CAPAs.
capa_events = Table(
    "capa_events",
    Base.metadata,
    Column("capa_id", Integer, ForeignKey("capas.id", ondelete="CASCADE"), primary_key=True),
    Column("event_id", Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
)


class Capa(Base):
    """A corrective/preventive action record."""

    __tablename__ = "capas"

    __audit_entity__ = "capa"
    __audit_fields__ = (
        "title",
        "status",
        "initiating_cause",
        "containment_actions",
        "root_cause",
        "root_cause_category",
        "rca_method",
        "corrective_action",
        "preventive_action",
        "owner_id",
        "due_date",
        "verification_date",
        "verification_outcome",
        "verified_by",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    status = Column(String(30), nullable=False, default=CapaStatus.DRAFT.value)

    # Initiating cause: what triggered this CAPA. Required (directly or via a
    # linked event) before leaving Draft — see app.services.capa_workflow.
    initiating_cause = Column(Text, nullable=True)

    # Containment and structured root-cause analysis.
    containment_actions = Column(Text, nullable=True)
    root_cause = Column(Text, nullable=True)
    root_cause_category = Column(String(100), nullable=True, index=True)  # Pareto (Phase 5)
    rca_method = Column(String(50), nullable=True)  # e.g. "5-Why", "Fishbone"

    # Actions.
    corrective_action = Column(Text, nullable=True)
    preventive_action = Column(Text, nullable=True)

    # Ownership and scheduling.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    due_date = Column(Date, nullable=True)

    # Effectiveness verification.
    verification_date = Column(Date, nullable=True)
    verification_outcome = Column(String(20), nullable=True, default=VerificationOutcome.PENDING.value)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", foreign_keys=[owner_id], lazy="joined")
    verifier = relationship("User", foreign_keys=[verified_by], lazy="joined")
    events = relationship("Event", secondary=capa_events, backref="capas")

    @property
    def event_ids(self) -> list[int]:
        """Ids of linked events, for serialization."""
        return [event.id for event in self.events]

    def __repr__(self) -> str:
        return f"<Capa(id={self.id}, title={self.title}, status={self.status})>"
