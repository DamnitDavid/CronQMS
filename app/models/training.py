"""Training Management model.

The module answers a shop-floor need: baseline operation employees are trained
on an SOP (typically walked through it on a tablet by a supervisor) and then
marked as trained. Those operators frequently have **no computer or email
access**, so they cannot be system :class:`~app.models.user.User` accounts. The
:class:`Employee` entity captures them as lightweight, non-login records.

A :class:`TrainingCourse` is a defined training — usually tied to a controlled
SOP :class:`~app.models.document.Document` — with an optional recertification
period. A :class:`TrainingRecord` is one assignment of a course to one trainee
(either an :class:`Employee` or a system ``User``) and tracks its lifecycle:
Assigned → In_Progress → Trained (with a trainer sign-off and a typed trainee
acknowledgment), plus a computed expiry date when the course requires periodic
recertification. Records surface as *Expired* once that date has passed.

Cross-entity rules (exactly one trainee reference per record, expiry
computation) live in the route layer, mirroring how the Audit module gates its
own transitions rather than encoding them here.
"""

from datetime import date, datetime
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


class TrainingStatus(str, Enum):
    """Lifecycle state of a single training record."""

    ASSIGNED = "Assigned"
    IN_PROGRESS = "In_Progress"
    TRAINED = "Trained"
    WAIVED = "Waived"


# Display-only pseudo-status derived for a trained record whose expiry has
# passed. Kept out of :class:`TrainingStatus` because it is computed, never
# stored — the stored status stays ``Trained``.
EXPIRED_STATUS = "Expired"


class Employee(Base):
    """A baseline operation employee with no system (login) account.

    These are the shop-floor operators trained on SOPs via a tablet. They are
    organization-scoped and soft-deleted like every other auditable entity.
    """

    __tablename__ = "employees"

    __audit_entity__ = "employee"
    __audit_fields__ = (
        "full_name",
        "employee_number",
        "department",
        "job_title",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    employee_number = Column(String(50), nullable=True)  # badge / clock number
    department = Column(String(255), nullable=True)
    job_title = Column(String(255), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<Employee(id={self.id}, name={self.full_name})>"


class TrainingCourse(Base):
    """A defined training, usually tied to a controlled SOP document."""

    __tablename__ = "training_courses"

    __audit_entity__ = "training_course"
    __audit_fields__ = (
        "code",
        "title",
        "document_id",
        "recertification_period_months",
        "is_active",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    code = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Optional link to a controlled SOP so the trainer can open it on the tablet.
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Months until a completed training expires and must be renewed. Null means
    # the training does not require periodic recertification.
    recertification_period_months = Column(Integer, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    document = relationship("Document", lazy="joined")
    records = relationship(
        "TrainingRecord",
        back_populates="course",
        order_by="TrainingRecord.created_at",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<TrainingCourse(id={self.id}, code={self.code}, title={self.title})>"


class TrainingRecord(Base):
    """One assignment of a course to one trainee (an Employee or a User)."""

    __tablename__ = "training_records"

    __audit_entity__ = "training_record"
    __audit_fields__ = (
        "status",
        "trained_date",
        "trained_by",
        "trainee_acknowledgment",
        "expiry_date",
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    course_id = Column(
        Integer, ForeignKey("training_courses.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Exactly one of these identifies the trainee (enforced in the route layer):
    # a non-login Employee, or an existing system User.
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    status = Column(String(20), nullable=False, default=TrainingStatus.ASSIGNED.value)
    assigned_date = Column(Date, nullable=False, default=date.today)

    # Certification / sign-off, set when a trainer marks the trainee trained.
    trained_date = Column(Date, nullable=True)
    trained_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # the trainer/certifier
    trainee_acknowledgment = Column(String(255), nullable=True)  # typed name captured on tablet
    expiry_date = Column(Date, nullable=True)  # computed from course recert period

    notes = Column(Text, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    course = relationship("TrainingCourse", back_populates="records")
    employee = relationship("Employee", lazy="joined")
    user = relationship("User", foreign_keys=[user_id], lazy="joined")
    trainer = relationship("User", foreign_keys=[trained_by], lazy="joined")

    @property
    def is_expired(self) -> bool:
        """Whether a completed training's recertification date has passed."""
        return (
            self.status == TrainingStatus.TRAINED.value
            and self.expiry_date is not None
            and self.expiry_date < date.today()
        )

    @property
    def effective_status(self) -> str:
        """Status for display/filtering — ``Expired`` overrides ``Trained``."""
        return EXPIRED_STATUS if self.is_expired else self.status

    @property
    def trainee_name(self) -> str:
        """Human-readable trainee label, whichever kind of trainee this is."""
        if self.employee is not None:
            return self.employee.full_name
        if self.user is not None:
            return self.user.email
        return "Unknown"

    def __repr__(self) -> str:
        return (
            f"<TrainingRecord(id={self.id}, course_id={self.course_id}, "
            f"status={self.status})>"
        )
