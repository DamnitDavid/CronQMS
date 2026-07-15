"""Quality event model for manufacturing events tracking."""

from datetime import datetime
from enum import Enum
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Boolean
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
    is_active = Column(Boolean, default=True, nullable=False)
    reported_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    assigned_user = relationship("User", foreign_keys=[assigned_to], lazy="joined")
    reporter = relationship("User", foreign_keys=[reported_by], lazy="joined")
    organization = relationship("Organization")
    site = relationship("Site")

    def __repr__(self) -> str:
        """String representation of Event."""
        return (
            f"<Event(id={self.id}, title={self.title}, type={self.event_type}, "
            f"status={self.status}, priority={self.priority})>"
        )
