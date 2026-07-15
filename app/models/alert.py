"""Quality/Safety Alert model.

An Alert is a formal, broadcast notice raised from an Event (typically a CAPA
event). The reporter issues it to one or more responsible :class:`AssigneeGroup`
recipient groups, every member of which receives an in-app :class:`Notification`.
The open alert is printable with a wet-ink signature page; responsible parties
then upload the scanned, signed acknowledgement back against the alert
(:class:`AlertAcknowledgement`).
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


class AlertType(str, Enum):
    QUALITY = "Quality"
    SAFETY = "Safety"


class AlertSeverity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class AlertStatus(str, Enum):
    OPEN = "Open"
    ACKNOWLEDGED = "Acknowledged"
    CLOSED = "Closed"


# Many-to-many: an alert is broadcast to one or more responsible groups; a group
# can be a recipient of many alerts.
alert_recipient_groups = Table(
    "alert_recipient_groups",
    Base.metadata,
    Column("alert_id", Integer, ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("assignee_groups.id", ondelete="CASCADE"), primary_key=True),
)


class Alert(Base):
    """A Quality/Safety Alert raised from an event."""

    __tablename__ = "alerts"

    # Audit metadata consumed by app.core.audit.register_auditing.
    __audit_entity__ = "alert"
    __audit_fields__ = (
        "title",
        "alert_type",
        "severity",
        "status",
        "affected_product",
        "affected_lot_batch",
        "description",
        "containment_actions",
        "required_actions",
        "response_due_date",
        "issued_by",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)

    title = Column(String(255), nullable=False)
    alert_type = Column(String(20), nullable=False, default=AlertType.QUALITY.value)
    severity = Column(String(20), nullable=False, default=AlertSeverity.MEDIUM.value)
    status = Column(String(20), nullable=False, default=AlertStatus.OPEN.value)

    # What the alert is about.
    affected_product = Column(String(255), nullable=True)
    affected_lot_batch = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    containment_actions = Column(Text, nullable=True)
    required_actions = Column(Text, nullable=True)  # what recipients must do
    response_due_date = Column(Date, nullable=True)

    issued_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    event = relationship("Event", foreign_keys=[event_id], lazy="joined")
    issuer = relationship("User", foreign_keys=[issued_by], lazy="joined")
    organization = relationship("Organization")
    recipient_groups = relationship("AssigneeGroup", secondary=alert_recipient_groups, lazy="selectin")
    acknowledgements = relationship(
        "AlertAcknowledgement",
        back_populates="alert",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def reference(self) -> str:
        """Human-facing alert reference, e.g. ``AL-42``."""
        return f"AL-{self.id}"

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, title={self.title}, status={self.status})>"


class AlertAcknowledgement(Base):
    """A scanned, signed acknowledgement uploaded in response to an alert.

    Mirrors the metadata that :class:`~app.models.attachment.Attachment` records
    (filename/content_type/size/checksum/storage_key), but is keyed to an alert
    rather than an event, and captures which responsible group the submitter
    represents.
    """

    __tablename__ = "alert_acknowledgements"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)

    filename = Column(String(255), nullable=False)
    content_type = Column(String(255), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=False)  # SHA-256 hex
    storage_key = Column(String(255), nullable=False)

    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    group_id = Column(Integer, ForeignKey("assignee_groups.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    alert = relationship("Alert", back_populates="acknowledgements")
    submitter = relationship("User", foreign_keys=[submitted_by], lazy="joined")
    group = relationship("AssigneeGroup", foreign_keys=[group_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<AlertAcknowledgement(id={self.id}, alert={self.alert_id})>"


class Notification(Base):
    """A per-user in-app notification (the user's inbox).

    Kept generic (subject/body + optional alert link) so it can carry future
    notification kinds, but today only alerts populate it.
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=True)

    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    alert = relationship("Alert", foreign_keys=[alert_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Notification(id={self.id}, user={self.user_id}, read={self.is_read})>"
