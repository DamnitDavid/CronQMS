"""SQLAlchemy models for Proins."""

from app.models.user import User, Role
from app.models.organization import Organization, Site
from app.models.event import Event
from app.models.event_history import EventHistory

# Wire the audit choke point once, after the models are defined.
from app.core.audit import register_auditing

register_auditing(Event)

__all__ = ["User", "Role", "Organization", "Site", "Event", "EventHistory"]
