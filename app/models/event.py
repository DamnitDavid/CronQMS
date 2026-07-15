"""Quality event model for manufacturing events tracking."""

from datetime import date, datetime
from enum import Enum
from sqlalchemy import Column, String, Integer, DateTime, Date, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship

from app.database import Base


class EventStatus(str, Enum):
    OPEN = "Open"
    IN_PROGRESS = "In_Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class EventPriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class EventType(str, Enum):
    NON_CONFORMANCE = "Non_Conformance"
    CAPA = "CAPA"
    AUDIT_FINDING = "Audit_Finding"
    OTHER = "Other"


class Event(Base):
    """Quality event model representing manufacturing events."""

    __tablename__ = "events"

    # Audit metadata consumed by app.core.audit.register_auditing.
    __audit_entity__ = "event"
    __audit_fields__ = (
        "title",
        "description",
        "event_type",
        "status",
        "priority",
        "assigned_to",
        "site_id",
        "organization_id",
        "reported_by",
        "target_close_date",
        "product_part_number",
        "lot_batch",
        "supplier",
        "work_order",
        "machine",
        "closed_by",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(String(50), nullable=False, default=EventType.NON_CONFORMANCE.value)
    status = Column(String(30), nullable=False, default=EventStatus.OPEN.value)
    priority = Column(String(20), nullable=False, default=EventPriority.MEDIUM.value)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=True, index=True)

    # Due date / aging.
    target_close_date = Column(Date, nullable=True)

    # Traceability (indexed: "show every event on lot 4471" must be fast).
    product_part_number = Column(String(100), nullable=True, index=True)
    lot_batch = Column(String(100), nullable=True, index=True)
    supplier = Column(String(255), nullable=True, index=True)
    work_order = Column(String(100), nullable=True, index=True)
    machine = Column(String(100), nullable=True, index=True)

    is_active = Column(Boolean, default=True, nullable=False)
    reported_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Closure approval: who approved the closure and when. A closer must be
    # distinct from the reporter and the investigator (enforced in the route).
    closed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    assigned_user = relationship("User", foreign_keys=[assigned_to], lazy="joined")
    reporter = relationship("User", foreign_keys=[reported_by], lazy="joined")
    organization = relationship("Organization")
    site = relationship("Site")

    @property
    def is_overdue(self) -> bool:
        """True when past the target close date and not yet closed."""
        if self.target_close_date is None or self.status == EventStatus.CLOSED.value:
            return False
        return date.today() > self.target_close_date

    @property
    def days_open(self) -> int:
        """Calendar days the event has been open (0 if created_at unset)."""
        if self.created_at is None:
            return 0
        return (datetime.utcnow() - self.created_at).days

    def __repr__(self) -> str:
        """String representation of Event."""
        return (
            f"<Event(id={self.id}, title={self.title}, type={self.event_type}, "
            f"status={self.status}, priority={self.priority})>"
        )
