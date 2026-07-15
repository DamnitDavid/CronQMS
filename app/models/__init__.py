"""SQLAlchemy models for Proins."""

from app.models.user import User, Role
from app.models.organization import Organization, Site
from app.models.event import Event
from app.models.capa import Capa, CapaStatus, VerificationOutcome, capa_events
from app.models.attachment import Attachment
from app.models.comment import Comment
from app.models.event_history import EventHistory
from app.models.custom_field import CustomField, CustomFieldType, EventCustomValue
from app.models.assignee_group import AssigneeGroup, assignee_group_members

# Wire the audit choke point once, after the models are defined. CAPAs are
# audited from birth alongside events.
from app.core.audit import register_auditing

register_auditing(Event)
register_auditing(Capa)

__all__ = [
    "User",
    "Role",
    "Organization",
    "Site",
    "Event",
    "Capa",
    "CapaStatus",
    "VerificationOutcome",
    "capa_events",
    "Attachment",
    "Comment",
    "EventHistory",
    "CustomField",
    "CustomFieldType",
    "EventCustomValue",
    "AssigneeGroup",
    "assignee_group_members",
]
