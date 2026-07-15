"""Admin-defined custom fields per event type, and their per-event values.

An admin configures, for each event type, a flat list of custom information
fields (text / number / boolean). Those definitions live in ``custom_fields``;
the value a user enters for a given event lives in ``event_custom_values``.
Both are organization-scoped through their parent rows.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class CustomFieldType(str, Enum):
    """The value type a custom field holds."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SELECT = "select"
    DATE = "date"


class CustomField(Base):
    """A custom field definition attached to one event type within an org.

    Attributes:
        organization_id: Owning organization (access scope).
        event_type: One of :class:`app.models.event.EventType` values.
        label: Human label shown on the form and detail view.
        key: Slug derived from ``label``, unique per (organization, event_type).
        field_type: One of :class:`CustomFieldType` values.
        options: Newline-separated allowed values for ``select`` fields; null
            for other types.
        display_order: Ascending sort order on the form.
        is_active: Soft-delete flag; inactive fields are hidden everywhere.
    """

    __tablename__ = "custom_fields"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    event_type = Column(String(50), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    key = Column(String(100), nullable=False)
    field_type = Column(String(20), nullable=False, default=CustomFieldType.TEXT.value)
    options = Column(Text, nullable=True)
    required = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<CustomField(id={self.id}, org={self.organization_id}, "
            f"event_type={self.event_type}, key={self.key}, type={self.field_type})>"
        )


class EventCustomValue(Base):
    """The value entered for one custom field on one event.

    Values are stored as text: booleans as ``"true"``/``"false"``, numbers as
    their string form. Coercion/validation happens in
    :mod:`app.services.custom_fields`.
    """

    __tablename__ = "event_custom_values"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    custom_field_id = Column(
        Integer, ForeignKey("custom_fields.id"), nullable=False, index=True
    )
    value = Column(Text, nullable=True)

    custom_field = relationship("CustomField", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<EventCustomValue(event={self.event_id}, "
            f"field={self.custom_field_id}, value={self.value!r})>"
        )
